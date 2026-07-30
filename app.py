# -*- coding: utf-8 -*-
"""
サウナ話題 生成アプリ（窓口）
-----------------------------
スマホのブラウザからアクセスして使う。

生成には1分前後かかるので、リクエストの中で待たせずジョブ方式にしている。

    POST /api/generate        → job_id をすぐ返す（生成は裏のスレッドで進む）
    GET  /api/status/<job_id> → 進み具合を返す。画面はこれを繰り返し見に行く
    GET  /api/image/<job_id>/<n> → できた画像を1枚返す
    GET  /api/ping            → 起きているかの確認だけ

どのスクリプトをどの順で走らせるかは pipelines.py が持っている。
ここは「受け取って画面に渡す」だけに徹する。

生成物は作業ディレクトリに置いたまま配信し、古いジョブから順に消す。
Renderのディスクは揮発するので、そもそも永続保存はしない。
"""

import os
import threading
import time
import uuid
from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

import pipelines

app = FastAPI()

# 前回のプロセスが残した作業ディレクトリを片付けてから始める。
# サーバーが再起動するとジョブ一覧が消え、作業ディレクトリだけが取り残されるため。
pipelines.sweep_orphan_workspaces()

# ジョブ置き場。単独利用なのでメモリ内のdictで足りる。
_jobs = {}
_jobs_lock = threading.Lock()

# 古いジョブを片付ける条件
JOB_TTL_SEC = 60 * 60
MAX_JOBS = 5

# フェーズごとの進捗の目安（%）。実際の残り時間は測れないので、
# 「止まっていない」ことが伝わればよいという方針。
PHASE_PROGRESS = {
    "queued": 3,
    "news": 10,
    "slide": 70,
    "caption": 90,
    "done": 100,
}
PHASE_ORDER = ["queued", "news", "slide", "caption", "done"]


def _new_job(kind):
    return {
        "id": uuid.uuid4().hex,
        "kind": kind,
        "state": "queued",          # queued / running / done / error
        "phase": "queued",
        "message": "準備しています",
        "detail": "",
        "progress": PHASE_PROGRESS["queued"],
        "images": [],
        "caption": "",
        "error": "",
        "log": "",
        "created_at": time.time(),
    }


def _sweep_old_jobs():
    """古いジョブと、その作業ディレクトリを片付ける。"""
    now = time.time()
    with _jobs_lock:
        expired = [
            job_id for job_id, job in _jobs.items()
            if now - job["created_at"] > JOB_TTL_SEC and job["state"] in ("done", "error")
        ]
        finished = sorted(
            (j for j in _jobs.values() if j["state"] in ("done", "error")),
            key=lambda j: j["created_at"],
        )
        overflow = [j["id"] for j in finished[:-MAX_JOBS]] if len(finished) > MAX_JOBS else []

        for job_id in set(expired + overflow):
            job = _jobs.pop(job_id, None)
            if job and job["images"]:
                pipelines.cleanup(job["images"])


def _advance_progress(job, phase):
    """フェーズの範囲内で少しずつ進める。同じフェーズが長くても動いて見えるように。"""
    base = PHASE_PROGRESS.get(phase, job["progress"])
    try:
        nxt = PHASE_PROGRESS[PHASE_ORDER[PHASE_ORDER.index(phase) + 1]]
    except (ValueError, IndexError):
        nxt = base
    ceiling = max(base, nxt - 2)
    job["progress"] = min(ceiling, max(base, job["progress"] + 1))


def _run_job(job_id):
    """裏のスレッドで生成を回す。"""
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return
        job["state"] = "running"

    def on_progress(phase, message):
        with _jobs_lock:
            current = _jobs.get(job_id)
            if current is None:
                return
            label = pipelines.PHASE_LABELS.get(phase)
            if message == label or phase != current["phase"]:
                # フェーズの切り替わり。見出しを差し替える
                current["phase"] = phase
                current["message"] = label or message
                current["detail"] = ""
            else:
                # スクリプトの標準出力。細かい動きとして下に添える
                current["detail"] = message
            _advance_progress(current, phase)

    try:
        images, caption = pipelines.generate(job["kind"], on_progress=on_progress)
    except pipelines.PipelineError as e:
        with _jobs_lock:
            current = _jobs.get(job_id)
            if current is not None:
                current["state"] = "error"
                current["error"] = e.message
                current["log"] = e.log
        return
    except Exception as e:  # 想定外。画面に出しつつログにも残す
        with _jobs_lock:
            current = _jobs.get(job_id)
            if current is not None:
                current["state"] = "error"
                current["error"] = "予期しないエラーが起きました"
                current["log"] = f"{type(e).__name__}: {e}"
        return

    with _jobs_lock:
        current = _jobs.get(job_id)
        if current is None:
            # 待っている間に片付けられていた
            pipelines.cleanup(images)
            return
        current["images"] = images
        current["caption"] = caption
        current["state"] = "done"
        current["phase"] = "done"
        current["message"] = pipelines.PHASE_LABELS["done"]
        current["detail"] = ""
        current["progress"] = 100


@app.get("/", response_class=HTMLResponse)
def home():
    """スマホで開くトップ画面"""
    return HTML_PAGE


@app.get("/api/ping")
def ping():
    """スリープから起きているかの確認だけ。中身は何もしない。"""
    return {"ok": True}


@app.post("/api/generate")
async def api_generate(payload: dict = None):
    """生成を始めて、すぐ job_id を返す。"""
    kind = (payload or {}).get("type", "topic")
    if kind not in pipelines.PIPELINES:
        return JSONResponse({"error": f"知らないジャンルです: {kind}"}, status_code=400)

    _sweep_old_jobs()

    job = _new_job(kind)
    with _jobs_lock:
        _jobs[job["id"]] = job

    threading.Thread(target=_run_job, args=(job["id"],), daemon=True).start()
    return {"job_id": job["id"]}


@app.get("/api/status/{job_id}")
def api_status(job_id: str):
    """進み具合を返す。画面はこれを1.5秒おきに見に行く。"""
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return JSONResponse({"error": "そのジョブは見つかりません"}, status_code=404)

        return {
            "state": job["state"],
            "progress": job["progress"],
            "message": job["message"],
            "detail": job["detail"],
            "images": [
                f"/api/image/{job_id}/{i + 1}" for i in range(len(job["images"]))
            ],
            "caption": job["caption"],
            "error": job["error"],
            "log": job["log"],
        }


@app.get("/api/image/{job_id}/{index}")
def api_image(job_id: str, index: int):
    """できた画像を1枚返す。"""
    with _jobs_lock:
        job = _jobs.get(job_id)
        images = list(job["images"]) if job else []

    if not images or index < 1 or index > len(images):
        return JSONResponse({"error": "画像が見つかりません"}, status_code=404)

    path = images[index - 1]
    if not os.path.exists(path):
        return JSONResponse({"error": "画像が見つかりません"}, status_code=404)

    return FileResponse(path, media_type="image/jpeg",
                        filename=os.path.basename(path))


# ============ スマホで開く画面（HTML） ============
# 見た目の作り込みは Phase 3 で行う。ここでは「今何をしているか文字で出る」ことを優先。
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
  .status { text-align: center; margin: 20px 0 6px; color: #b7d3c5; min-height: 24px; }
  .detail {
    text-align: center; color: #6f8f7f; font-size: 12px; min-height: 18px;
    margin-bottom: 12px; word-break: break-all;
  }
  .bar { height: 6px; background: #1c3a2e; border-radius: 3px; overflow: hidden; display: none; }
  .bar > i { display: block; height: 100%; width: 0; background: #2c5; transition: width .4s; }
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
  <button id="go" onclick="start()">記事を生成する</button>
  <div class="status" id="status"></div>
  <div class="detail" id="detail"></div>
  <div class="bar" id="bar"><i id="bar-fill"></i></div>
  <div class="imgs" id="imgs"></div>
  <div id="caption-area"></div>
  <details>
    <summary>ログを見る（うまくいかないとき）</summary>
    <pre id="log"></pre>
  </details>

<script>
const $ = (id) => document.getElementById(id);
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

function setStatus(text, detail) {
  $('status').textContent = text;
  $('detail').textContent = detail || '';
}

function setProgress(value) {
  $('bar').style.display = 'block';
  $('bar-fill').style.width = value + '%';
}

async function wakeServer() {
  // Renderの無料プランは15分で寝てしまい、起きるのに1分近くかかる。
  // 無言で固まって見えるのが一番まずいので、起こしている間もそう表示する。
  setStatus('サーバーを起こしています…', '初回は1分ほどかかることがあります');
  const deadline = Date.now() + 120000;
  while (Date.now() < deadline) {
    try {
      const res = await fetch('/api/ping', { cache: 'no-store' });
      if (res.ok) return true;
    } catch (e) { /* まだ寝ている。待って再挑戦 */ }
    await sleep(3000);
  }
  return false;
}

async function start() {
  const btn = $('go');
  btn.disabled = true;
  $('imgs').innerHTML = '';
  $('caption-area').innerHTML = '';
  $('log').textContent = '';
  setProgress(0);

  try {
    if (!await wakeServer()) {
      setStatus('⚠️ サーバーが起きませんでした', 'しばらくしてからもう一度お試しください');
      btn.disabled = false;
      return;
    }

    setStatus('生成を開始しています…', '');
    const res = await fetch('/api/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: 'topic' })
    });
    const { job_id } = await res.json();

    await poll(job_id);
  } catch (e) {
    setStatus('⚠️ エラーが起きました', '');
    $('log').textContent = String(e);
  }
  btn.disabled = false;
}

async function poll(jobId) {
  while (true) {
    await sleep(1500);

    let data;
    try {
      const res = await fetch('/api/status/' + jobId, { cache: 'no-store' });
      data = await res.json();
    } catch (e) {
      // 通信が一瞬切れただけかもしれないので、あきらめずに続ける
      continue;
    }

    setStatus(data.message || '', data.detail || '');
    setProgress(data.progress || 0);

    if (data.state === 'error') {
      setStatus('⚠️ ' + (data.error || 'エラーが起きました'), '');
      $('log').textContent = data.log || '';
      return;
    }
    if (data.state === 'done') {
      render(data);
      return;
    }
  }
}

function render(data) {
  setStatus('完成しました ✨', '画像を長押しで保存できます');

  data.images.forEach(url => {
    const img = document.createElement('img');
    img.src = url;
    $('imgs').appendChild(img);
  });

  if (!data.caption) return;

  const box = document.createElement('div');
  box.className = 'caption-box';
  box.textContent = data.caption;
  $('caption-area').appendChild(box);

  const copyBtn = document.createElement('button');
  copyBtn.className = 'copy';
  copyBtn.textContent = '📋 キャプションをコピー';
  copyBtn.onclick = () => {
    navigator.clipboard.writeText(data.caption);
    copyBtn.textContent = '✅ コピーしました';
    setTimeout(() => copyBtn.textContent = '📋 キャプションをコピー', 2000);
  };
  $('caption-area').appendChild(copyBtn);
}
</script>
</body>
</html>
"""
