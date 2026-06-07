# -*- coding: utf-8 -*-
"""
サウナ話題 生成アプリ（窓口）
-----------------------------
スマホのブラウザからアクセスして使う。
「生成」ボタン → news_topic.py → slide_topic.py → caption_topic.py を順に実行
→ できた画像4枚とキャプションを画面に返す。

元の main_topic.py がやっていた「PCフォルダへ移動」は、
ここでは「画面に表示してダウンロードさせる」に置き換えている。
"""

import os
import re
import glob
import base64
import subprocess
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI()

# 生成済み画像を置く場所
OUTPUT_DIR = "generated"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# 画像をブラウザから見られるように公開
app.mount("/images", StaticFiles(directory=OUTPUT_DIR), name="images")


def run_script(name):
    """1つのPythonスクリプトを実行し、成否とログを返す"""
    result = subprocess.run(
        ["python", name],
        capture_output=True,
        text=True,
        timeout=300,
    )
    ok = result.returncode == 0
    log = (result.stdout or "") + (result.stderr or "")
    return ok, log


@app.get("/", response_class=HTMLResponse)
def home():
    """スマホで開くトップ画面"""
    return HTML_PAGE


@app.post("/generate")
def generate():
    """生成ボタンが押されたときの処理"""
    logs = []

    # 古い画像を消してから始める
    for f in glob.glob(os.path.join(OUTPUT_DIR, "*.jpg")):
        os.remove(f)

    # 1. ニュース取得 → posts_topic.txt / caption_source_topic.txt 生成
    ok, log = run_script("news_topic.py")
    logs.append("【1/3 ニュース取得】\n" + log[-1500:])
    if not ok:
        return JSONResponse({"ok": False, "step": "news", "log": "\n\n".join(logs)})

    # posts_topic.txt が空＝候補ゼロのときは止める
    if not os.path.exists("posts_topic.txt") or os.path.getsize("posts_topic.txt") == 0:
        return JSONResponse({"ok": False, "step": "news",
                             "log": "\n\n".join(logs) + "\n\n候補ニュースが見つかりませんでした。もう一度お試しください。"})

    # 2. スライド生成 → topic_1.jpg 〜 topic_4.jpg
    ok, log = run_script("slide_topic.py")
    logs.append("【2/3 スライド生成】\n" + log[-1500:])
    if not ok:
        return JSONResponse({"ok": False, "step": "slide", "log": "\n\n".join(logs)})

    # 3. キャプション生成 → caption_topic.txt
    ok, log = run_script("caption_topic.py")
    logs.append("【3/3 キャプション生成】\n" + log[-1500:])
    if not ok:
        return JSONResponse({"ok": False, "step": "caption", "log": "\n\n".join(logs)})

    # できた画像を generated/ に移動して、URLのリストを作る
    image_files = sorted(glob.glob("topic_*.jpg"), key=_topic_number)
    image_urls = []
    for f in image_files:
        dest = os.path.join(OUTPUT_DIR, os.path.basename(f))
        os.replace(f, dest)
        image_urls.append("/images/" + os.path.basename(f))

    # キャプションを読み込む
    caption = ""
    if os.path.exists("caption_topic.txt"):
        with open("caption_topic.txt", "r", encoding="utf-8") as fp:
            caption = fp.read()

    return JSONResponse({
        "ok": True,
        "images": image_urls,
        "caption": caption,
    })


def _topic_number(path):
    """topic_3.jpg → 3 のように番号を取り出して並べ替えに使う"""
    m = re.search(r"topic_(\d+)\.jpg", path)
    return int(m.group(1)) if m else 0


# ============ スマホで開く画面（HTML） ============
HTML_PAGE = """
<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>サウナ話題ジェネレーター</title>
<style>
  body {
    font-family: -apple-system, "Hiragino Kaku Gothic ProN", sans-serif;
    background: #0f1f1a; color: #eaf3ee; margin: 0; padding: 20px;
    max-width: 600px; margin-left: auto; margin-right: auto;
  }
  h1 { font-size: 20px; text-align: center; }
  .sub { text-align: center; color: #8fae9f; font-size: 13px; margin-bottom: 24px; }
  button#go {
    width: 100%; padding: 18px; font-size: 18px; font-weight: bold;
    background: #1f7a4d; color: white; border: none; border-radius: 14px;
    cursor: pointer;
  }
  button#go:disabled { background: #555; }
  .status { text-align: center; margin: 20px 0; color: #b7d3c5; min-height: 24px; }
  .imgs img { width: 100%; border-radius: 12px; margin-bottom: 12px; }
  .caption-box {
    background: #16302593; border: 1px solid #2c5; border-radius: 12px;
    padding: 16px; white-space: pre-wrap; font-size: 14px; line-height: 1.7;
    margin-top: 16px;
  }
  button.copy {
    width: 100%; padding: 12px; margin-top: 10px; font-size: 15px;
    background: #2c5; color: #0f1f1a; border: none; border-radius: 10px;
    font-weight: bold; cursor: pointer;
  }
  details { margin-top: 20px; color: #7a978a; font-size: 12px; }
  pre { white-space: pre-wrap; word-break: break-all; }
</style>
</head>
<body>
  <h1>♨️ サウナ話題ジェネレーター</h1>
  <div class="sub">ボタンを押すと記事を選んで画像とキャプションを作ります</div>
  <button id="go" onclick="generate()">記事を生成する</button>
  <div class="status" id="status"></div>
  <div class="imgs" id="imgs"></div>
  <div id="caption-area"></div>
  <details>
    <summary>ログを見る（うまくいかないとき）</summary>
    <pre id="log"></pre>
  </details>

<script>
async function generate() {
  const btn = document.getElementById('go');
  const status = document.getElementById('status');
  const imgs = document.getElementById('imgs');
  const captionArea = document.getElementById('caption-area');
  const log = document.getElementById('log');

  btn.disabled = true;
  imgs.innerHTML = '';
  captionArea.innerHTML = '';
  log.textContent = '';
  status.textContent = '生成中… 30秒ほどかかります ☕';

  try {
    const res = await fetch('/generate', { method: 'POST' });
    const data = await res.json();

    if (!data.ok) {
      status.textContent = '⚠️ ' + (data.step || '') + ' でつまずきました';
      log.textContent = data.log || '';
      btn.disabled = false;
      return;
    }

    status.textContent = '完成！画像を長押しで保存できます ✨';

    data.images.forEach(url => {
      const img = document.createElement('img');
      img.src = url + '?t=' + Date.now();  // キャッシュ避け
      imgs.appendChild(img);
    });

    if (data.caption) {
      const box = document.createElement('div');
      box.className = 'caption-box';
      box.id = 'caption-text';
      box.textContent = data.caption;
      captionArea.appendChild(box);

      const copyBtn = document.createElement('button');
      copyBtn.className = 'copy';
      copyBtn.textContent = '📋 キャプションをコピー';
      copyBtn.onclick = () => {
        navigator.clipboard.writeText(data.caption);
        copyBtn.textContent = '✅ コピーしました';
        setTimeout(() => copyBtn.textContent = '📋 キャプションをコピー', 2000);
      };
      captionArea.appendChild(copyBtn);
    }
  } catch (e) {
    status.textContent = '⚠️ エラーが起きました';
    log.textContent = String(e);
  }
  btn.disabled = false;
}
</script>
</body>
</html>
"""
