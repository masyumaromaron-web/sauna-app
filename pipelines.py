# -*- coding: utf-8 -*-
"""ジャンルごとの生成パイプラインの窓口。

公開APIは generate() ひとつだけ:

    images, caption = generate("topic", on_progress=lambda phase, msg: ...)

    images  … 生成された画像の絶対パスの配列（topic_1.jpg → topic_4.jpg の順）
    caption … キャプション本文の文字列

on_progress は (phase, message) で呼ばれる。phase は PHASE_LABELS のキーで、
画面の進捗バーに使う。message はそのまま出せる日本語か、スクリプトの標準出力
1行（「本文取得中: ...」など）。

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
import threading
import time

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

# 進捗表示に使うフェーズ。画面側はこのキーを見て「今どこか」を出す。
PHASE_LABELS = {
    "news": "ニュースを探しています",
    "slide": "スライドを作っています",
    "caption": "キャプションを作っています",
    "done": "完成しました",
}


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


# 対応ジャンル。画面のジャンル選択もこの順で並ぶ。
# 月末まとめ（monthly）は載せていない。slide_monthly.py が話題版と同じ
# posts_topic.txt を読むうえ、集計元の used_news_topic.json がRenderでは
# 揮発するため。月イチの作業なので手元のPCで走らせる。
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
    "sauna": Pipeline(
        key="sauna",
        label="サウナ速報",
        news="news_gemini.py",
        slide="slide.py",
        caption="caption.py",
        posts_file="posts.txt",
        caption_file="caption.txt",
        image_prefix="news",
    ),
    "sento": Pipeline(
        key="sento",
        label="銭湯速報",
        news="news_sento.py",
        slide="slide_sento.py",
        caption="caption_sento.py",
        posts_file="posts_sento.txt",
        caption_file="caption_sento.txt",
        image_prefix="sento",
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


# 画面に出しても意味がない行。Pythonの警告やトレースバックの断片が
# 進捗表示に紛れ込むと、動いているのか壊れたのか分からなくなる。
# 失敗時のログには全行残すので、ここで捨てるのは表示だけ。
_NOISE_PATTERN = re.compile(
    r"warnings?\.warn"
    r"|FutureWarning|DeprecationWarning|UserWarning|NotOpenSSLWarning|ResourceWarning"
    r"|^Traceback"
    r"|^\s*File \""
)


def _is_noise(line):
    return not line.strip() or bool(_NOISE_PATTERN.search(line))


# Geminiの1日の無料枠を使い切ったときの目印。エラー本文が長大なJSONなので、
# そのまま画面に出すと何が起きたのか分からなくなる。
_QUOTA_PATTERN = re.compile(r"RESOURCE_EXHAUSTED|429|quota|rate.?limit", re.IGNORECASE)

QUOTA_MESSAGE = (
    "Geminiの無料枠を今日の分だけ使い切りました。"
    "日付が変わると戻ります（銭湯速報は別のモデルを使うので、まだ試せます）。"
)


def _looks_like_quota_error(text):
    return bool(text) and bool(_QUOTA_PATTERN.search(text))


def _shorten(line, limit=110):
    """進捗欄に出す用に切り詰める。エラーJSONで画面が埋まるのを防ぐ。"""
    line = line.strip()
    if _looks_like_quota_error(line) and len(line) > limit:
        return "Geminiの呼び出しが混み合っています…"
    return line if len(line) <= limit else line[:limit] + "…"


def _run_script(script, workspace, phase, on_progress=None):
    """スクリプトを1本、作業ディレクトリの中で実行する。

    標準出力は1行ずつ拾って on_progress(phase, 行) に渡す。スクリプト側が
    「本文取得中: ...」などを print しているので、そのまま進捗表示に使える。
    """
    script_path = os.path.join(BASE_DIR, script)
    if not os.path.exists(script_path):
        raise PipelineError(f"{script} が見つかりません")

    # 標準出力がパイプにつながると、Pythonは既定でまとめ書き（ブロックバッファ）に
    # なる。それだと print が実行の終わりまで届かず、進捗表示が一気に飛ぶ。
    # PYTHONUNBUFFERED を立てて1行ずつ流れるようにする。
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    lines = []
    process = subprocess.Popen(
        [sys.executable, script_path],
        cwd=workspace,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    # 見張り役。標準出力を読む for 文は、子が黙り込んだまま生きていると
    # そこで止まりっぱなしになり、後ろの wait(timeout=...) まで進まない。
    # 実際 news_gemini.py がGeminiの接続断をリトライし続けて20分固まった。
    # 時間で確実に打ち切れるよう、別スレッドから kill する。
    timed_out = threading.Event()

    def _give_up():
        timed_out.set()
        process.kill()

    watchdog = threading.Timer(STEP_TIMEOUT_SEC, _give_up)
    watchdog.start()

    try:
        for line in process.stdout:
            line = line.rstrip()
            lines.append(line)
            if on_progress and not _is_noise(line):
                on_progress(phase, _shorten(line))
        process.wait()
    finally:
        watchdog.cancel()

    if timed_out.is_set():
        raise PipelineError(
            f"{script} が {STEP_TIMEOUT_SEC // 60} 分たっても終わらないので中断しました。"
            "時間をおいて試してください。",
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

    def notify(phase, message):
        if on_progress:
            on_progress(phase, message)

    workspace = _make_workspace(kind)

    # 1. ニュース取得
    notify("news", f"{pipeline.label}のニュースを探しています")
    news_log = _run_script(pipeline.news, workspace, "news", on_progress)

    # ニュースが0件でもスクリプトは正常終了してしまうので、ここで止める。
    # 素通りさせると締めスライド1枚だけができて、成功したように見えてしまう。
    posts_path = os.path.join(workspace, pipeline.posts_file)
    if not os.path.exists(posts_path) or os.path.getsize(posts_path) == 0:
        # Geminiの枠切れが原因のことがある。「ニュースが無い」と言われると
        # 手の打ちようがないので、そのときは理由をそのまま伝える。
        if _looks_like_quota_error(news_log):
            raise PipelineError(QUOTA_MESSAGE, "\n".join(news_log.splitlines()[-20:]))
        raise PipelineError(
            "使えるニュースが見つかりませんでした。少し時間をおいて試してください。"
        )

    # 2. スライド生成
    notify("slide", "スライドを作っています")
    _run_script(pipeline.slide, workspace, "slide", on_progress)

    images = sorted(
        glob.glob(os.path.join(workspace, f"{pipeline.image_prefix}_*.jpg")),
        key=_image_sort_key,
    )
    if not images:
        raise PipelineError("画像が1枚も作れませんでした")

    # 3. キャプション生成（無ければ飛ばす）
    caption = ""
    if pipeline.caption:
        notify("caption", "キャプションを作っています")
        _run_script(pipeline.caption, workspace, "caption", on_progress)

        caption_path = os.path.join(workspace, pipeline.caption_file)
        if os.path.exists(caption_path):
            with open(caption_path, "r", encoding="utf-8") as f:
                caption = f.read()

    notify("done", f"完成しました（画像{len(images)}枚）")
    return images, caption


def cleanup(paths):
    """generate() が返した画像パスから作業ディレクトリを割り出して消す。"""
    if not paths:
        return
    remove_workspace(os.path.dirname(paths[0]))


def remove_workspace(workspace):
    """作業ディレクトリを片付ける。見当違いの場所を消さないよう名前を確かめる。"""
    if not workspace:
        return
    if os.path.basename(workspace).startswith("sauna-"):
        shutil.rmtree(workspace, ignore_errors=True)


def sweep_orphan_workspaces(older_than_sec=3600):
    """前回のプロセスが残していった作業ディレクトリを片付ける。

    サーバーが再起動するとジョブ一覧が消えるので、作業ディレクトリだけが
    取り残される。起動時に一度だけ呼ぶ。実行中のものを巻き込まないよう、
    1ステップの上限（STEP_TIMEOUT_SEC）より十分に古いものだけを対象にする。
    """
    now = time.time()
    removed = 0
    for path in glob.glob(os.path.join(tempfile.gettempdir(), "sauna-*")):
        if not os.path.isdir(path):
            continue
        try:
            if now - os.path.getmtime(path) < older_than_sec:
                continue
        except OSError:
            continue
        shutil.rmtree(path, ignore_errors=True)
        removed += 1
    return removed
