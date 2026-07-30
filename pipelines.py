# -*- coding: utf-8 -*-
"""ジャンルごとの生成パイプラインの窓口。

公開APIは generate() ひとつだけ:

    images, caption = generate("topic", on_progress=lambda msg: ...)

    images  … 生成された画像の絶対パスの配列（topic_1.jpg → topic_4.jpg の順）
    caption … キャプション本文の文字列

■ 既存スクリプトには一切手を加えていない
news_topic.py / slide_topic.py / caption_topic.py は、中間ファイルを
「カレントディレクトリ固定のファイル名」でやりとりする作りになっている。
そこでジョブごとに専用の作業ディレクトリを作り、subprocess の cwd= に
渡して走らせている。こうすると:

  - posts_topic.txt などの名前が固定でも、ジョブ同士がぶつからない
    （slide_monthly.py が posts_topic.txt を共有している問題も自動的に避けられる）
  - スクリプト側を書き換えずに済むので、生成ロジックが変質しない

os.chdir() を使っていないのは、あれがプロセス全体に効いてしまい、
ジョブをスレッドで並行させたときに壊れるため。cwd= ならジョブ単位で閉じる。

■ 相対パスの面倒を見る
スクリプトは backgrounds/ とフォントも相対パスで探す。作業ディレクトリからは
見えないので、シンボリックリンクを張って見せている（実体はコピーしない）。
"""

import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 1ステップあたりの上限。ニュース取得はGemini待ちがあるので長めに取る。
STEP_TIMEOUT_SEC = 420

# 作業ディレクトリから見えるようにするもの（リポジトリ直下からの相対パス）
LINKED_ASSETS = ("backgrounds", "fonts")

# 作業ディレクトリに引き継ぐ状態ファイル。
# Renderではディスクが揮発するため実際には育たないが、リポジトリに置けば
# 種として効く。恒久対応は Phase 7（Supabase）で行う。
STATE_FILES = (
    "used_news.json", "used_theme.json",
    "used_news_sento.json", "used_theme_sento.json",
    "used_news_topic.json", "used_theme_topic.json",
)


class Pipeline:
    """1ジャンル分の「どのスクリプトを、どの順で走らせるか」の定義。"""

    def __init__(self, key, label, news, slide, caption,
                 posts_file, caption_file, image_prefix):
        self.key = key
        self.label = label
        self.news = news
        self.slide = slide
        self.caption = caption
        self.posts_file = posts_file
        self.caption_file = caption_file
        self.image_prefix = image_prefix


# 対応ジャンル。Phase 5 で sauna / sento をここに足す。
PIPELINES = {
    "topic": Pipeline(
        key="topic",
        label="サウナ話題",
        news="news_topic.py",
        slide="slide_topic.py",
        caption="caption_topic.py",
        posts_file="posts_topic.txt",
        caption_file="caption_topic.txt",
        image_prefix="topic",
    ),
}


class PipelineError(Exception):
    """生成が途中で失敗したときに投げる。message はそのまま画面に出せる文言にする。"""

    def __init__(self, message, log=""):
        super().__init__(message)
        self.message = message
        self.log = log


def available_kinds():
    """UIのジャンル選択に出す [(key, label), ...] を返す。"""
    return [(p.key, p.label) for p in PIPELINES.values()]


def _make_workspace(kind):
    """ジョブ専用の作業ディレクトリを作り、必要なものを見えるようにする。"""
    workspace = tempfile.mkdtemp(prefix=f"sauna-{kind}-")

    # backgrounds/ と fonts/ はシンボリックリンクで見せる（28MBのコピーを避ける）
    for name in LINKED_ASSETS:
        source = os.path.join(BASE_DIR, name)
        if os.path.exists(source):
            os.symlink(source, os.path.join(workspace, name))

    # slide_topic.py は「リポジトリ直下に置いた場合」のフォントも候補にしているので、
    # そちらの名前でも引けるようにしておく
    font = os.path.join(BASE_DIR, "fonts", "NotoSansJP-Regular.ttf")
    if os.path.exists(font):
        os.symlink(font, os.path.join(workspace, "NotoSansJP-Regular.ttf"))

    # 状態ファイルはコピー（スクリプトが書き換えるのでリンクにはしない）
    for name in STATE_FILES:
        source = os.path.join(BASE_DIR, name)
        if os.path.exists(source):
            shutil.copy2(source, os.path.join(workspace, name))

    return workspace


def _run_script(script, workspace, on_progress=None):
    """スクリプトを1本、作業ディレクトリの中で実行する。

    標準出力は1行ずつ拾って on_progress に渡す。スクリプト側が
    「本文取得中: ...」などを print しているので、そのまま進捗表示に使える。
    """
    script_path = os.path.join(BASE_DIR, script)
    if not os.path.exists(script_path):
        raise PipelineError(f"{script} が見つかりません")

    lines = []
    process = subprocess.Popen(
        [sys.executable, script_path],
        cwd=workspace,
        env=os.environ.copy(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    try:
        for line in process.stdout:
            line = line.rstrip()
            lines.append(line)
            if line and on_progress:
                on_progress(line)
        process.wait(timeout=STEP_TIMEOUT_SEC)
    except subprocess.TimeoutExpired:
        process.kill()
        raise PipelineError(
            f"{script} が時間内に終わりませんでした",
            "\n".join(lines[-40:]),
        )

    log = "\n".join(lines)
    if process.returncode != 0:
        raise PipelineError(
            f"{script} でエラーが発生しました",
            "\n".join(lines[-40:]),
        )
    return log


def _image_sort_key(path):
    """topic_10.jpg が topic_2.jpg より後ろに来るように番号で並べる。"""
    match = re.search(r"_(\d+)\.jpg$", os.path.basename(path))
    return int(match.group(1)) if match else 0


def generate(kind, on_progress=None):
    """指定ジャンルの画像とキャプションを作る。

    戻り値は (画像の絶対パスの配列, キャプション文字列)。
    失敗したときは PipelineError を投げる。

    作業ディレクトリは消さずに残す。画像がその中にあるため、
    呼び出し側が使い終わったら cleanup() で片付けること。
    """
    pipeline = PIPELINES.get(kind)
    if pipeline is None:
        raise PipelineError(f"知らないジャンルです: {kind}")

    def notify(message):
        if on_progress:
            on_progress(message)

    workspace = _make_workspace(kind)

    # 1. ニュース取得
    notify(f"{pipeline.label}のニュースを探しています…")
    _run_script(pipeline.news, workspace, on_progress)

    # ニュースが0件でもスクリプトは正常終了してしまうので、ここで止める。
    # 素通りさせると締めスライド1枚だけができて、成功したように見えてしまう。
    posts_path = os.path.join(workspace, pipeline.posts_file)
    if not os.path.exists(posts_path) or os.path.getsize(posts_path) == 0:
        raise PipelineError(
            "使えるニュースが見つかりませんでした。少し時間をおいて試してください。"
        )

    # 2. スライド生成
    notify("スライドを作っています…")
    _run_script(pipeline.slide, workspace, on_progress)

    images = sorted(
        glob.glob(os.path.join(workspace, f"{pipeline.image_prefix}_*.jpg")),
        key=_image_sort_key,
    )
    if not images:
        raise PipelineError("画像が1枚も作れませんでした")

    # 3. キャプション生成（無ければ飛ばす）
    caption = ""
    if pipeline.caption:
        notify("キャプションを作っています…")
        _run_script(pipeline.caption, workspace, on_progress)

        caption_path = os.path.join(workspace, pipeline.caption_file)
        if os.path.exists(caption_path):
            with open(caption_path, "r", encoding="utf-8") as f:
                caption = f.read()

    notify(f"完成しました（画像{len(images)}枚）")
    return images, caption


def cleanup(paths):
    """generate() が返した画像パスから作業ディレクトリを割り出して消す。"""
    if not paths:
        return
    workspace = os.path.dirname(paths[0])
    if os.path.basename(workspace).startswith("sauna-"):
        shutil.rmtree(workspace, ignore_errors=True)
