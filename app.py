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

URLを知られると誰でもGeminiを叩けてしまうので、環境変数 APP_PASSCODE に
合言葉を入れておくと入口で1回だけ聞くようになる。厳密な認証ではなく、
API濫用を防ぐ目隠しという位置づけ。
"""

import hmac
import os
import threading
import time
import uuid
from fastapi import FastAPI, Header
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import pipelines

app = FastAPI()

# ホーム画面に追加するためのアイコンと manifest.json
app.mount("/static", StaticFiles(directory="static"), name="static")

# 合言葉。未設定なら誰でも使える（手元で動かすときに困らないように）。
APP_PASSCODE = os.getenv("APP_PASSCODE", "").strip()
if not APP_PASSCODE:
    print("APP_PASSCODE が未設定です。合言葉なしで誰でも生成できる状態です。")

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


def _passcode_ok(value):
    """合言葉が合っているか。未設定なら誰でも通す。"""
    if not APP_PASSCODE:
        return True
    return hmac.compare_digest((value or "").strip(), APP_PASSCODE)


@app.get("/api/config")
def api_config():
    """画面の組み立てに要る情報。合言葉が要るかと、選べるジャンル。"""
    return {
        "needs_passcode": bool(APP_PASSCODE),
        "kinds": [{"key": key, "label": label}
                  for key, label in pipelines.available_kinds()],
    }


@app.post("/api/verify")
async def api_verify(payload: dict = None):
    """合言葉が合っているかだけを返す。合っていれば画面側が覚えておく。"""
    if _passcode_ok((payload or {}).get("passcode")):
        return {"ok": True}
    return JSONResponse({"ok": False, "error": "合言葉が違います"}, status_code=401)


@app.post("/api/generate")
async def api_generate(payload: dict = None, x_passcode: str = Header(default="")):
    """生成を始めて、すぐ job_id を返す。"""
    # 費用がかかるのはここだけなので、合言葉はこの入口で確かめる
    if not _passcode_ok(x_passcode):
        return JSONResponse({"error": "合言葉が違います"}, status_code=401)

    kind = (payload or {}).get("type", "topic")
    if kind not in pipelines.PIPELINES:
        return JSONResponse({"error": f"知らないジャンルです: {kind}"}, status_code=400)

    _sweep_old_jobs()

    job = _new_job(kind)
    with _jobs_lock:
        # 生成は一度に1件だけ。Renderの無料プランはメモリ512MBしかなく、
        # 同時に走らせると外部への接続も詰まる（実際3本同時でGeminiの
        # 接続が切られた）。画面もタブを増やせば二重に押せてしまう。
        if any(j["state"] in ("queued", "running") for j in _jobs.values()):
            return JSONResponse(
                {"error": "いま別の生成が動いています。終わってから試してください。"},
                status_code=409,
            )
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
# 見た目の作り込みは Web Share API の実機確認が済んでから行う。
# ここでは「保存できること」を最優先にしている。
HTML_PAGE = """
<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#12303f">
<link rel="manifest" href="/static/manifest.json">
<link rel="apple-touch-icon" href="/static/apple-touch-icon.png">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="サウナ生成">
<title>サウナニュース ジェネレーター</title>
<style>
  /* 関西サウナニュースの色に合わせる。濃紺 × 生成り、差し色にオレンジ。
     読み込むものを増やすと表示が遅くなるので、Webフォントも画像も使わない。 */
  :root {
    --navy:   #12303f;   /* 地の色 */
    --cream:  #e9e2cf;   /* 文字 */
    --orange: #e8862d;   /* 差し色。保存ボタンと進捗バーだけに使う */
    --panel:  #1a3f52;   /* 一段明るい面 */
    --muted:  #93a7b3;   /* 補足文字 */
    --line:   rgba(233,226,207,.16);
  }
  * { box-sizing: border-box; }
  body {
    font-family: -apple-system, "Hiragino Kaku Gothic ProN", sans-serif;
    background: var(--navy); color: var(--cream);
    margin: 0 auto; max-width: 600px;
    /* ホーム画面から開くと画面の端まで使うので、切り欠きの分だけ内側に寄せる */
    padding: calc(20px + env(safe-area-inset-top)) 20px
             calc(48px + env(safe-area-inset-bottom));
    -webkit-text-size-adjust: 100%;
  }
  h1 { font-size: 19px; text-align: center; letter-spacing: .04em; margin: 8px 0 4px; }
  .sub { text-align: center; color: var(--muted); font-size: 13px; margin-bottom: 22px; }

  button { font-family: inherit; cursor: pointer; }
  button#go {
    width: 100%; padding: 18px; font-size: 18px; font-weight: bold;
    background: var(--cream); color: var(--navy);
    border: none; border-radius: 14px;
  }
  button#go:disabled { background: var(--panel); color: var(--muted); }

  .status { text-align: center; margin: 20px 0 6px; font-size: 15px; min-height: 24px; }
  .detail {
    text-align: center; color: var(--muted); font-size: 12px; min-height: 18px;
    margin-bottom: 12px; word-break: break-all;
  }
  .bar {
    height: 6px; background: var(--panel); border-radius: 3px;
    overflow: hidden; display: none;
  }
  .bar > i {
    display: block; height: 100%; width: 0;
    background: var(--orange); transition: width .4s;
  }

  .toolbar {
    display: none; align-items: center; justify-content: space-between;
    margin: 20px 0 10px; font-size: 14px;
  }
  .toolbar label { display: flex; align-items: center; gap: 8px; cursor: pointer; }
  input[type=checkbox] {
    width: 20px; height: 20px; accent-color: var(--orange); margin: 0;
  }
  .count { color: var(--muted); font-size: 13px; }

  .shot { position: relative; margin-bottom: 12px; }
  .shot img {
    width: 100%; border-radius: 12px; display: block;
    border: 1px solid var(--line);
  }
  .shot.off img { opacity: .3; }
  .shot label {
    position: absolute; top: 10px; left: 10px;
    display: flex; align-items: center; gap: 8px;
    background: rgba(18,48,63,.85); color: var(--cream);
    padding: 8px 12px; border-radius: 10px; font-size: 14px; cursor: pointer;
  }

  button.save {
    display: none; width: 100%; padding: 18px; margin-top: 4px;
    font-size: 17px; font-weight: bold;
    background: var(--orange); color: var(--navy);
    border: none; border-radius: 14px;
  }
  button.save:disabled { background: var(--panel); color: var(--muted); }
  .hint {
    text-align: center; color: var(--muted); font-size: 12px;
    min-height: 18px; margin-top: 8px; line-height: 1.5;
  }

  .caption-box {
    background: var(--panel); border: 1px solid var(--line); border-radius: 12px;
    padding: 16px; white-space: pre-wrap; font-size: 14px; line-height: 1.75;
    margin-top: 20px;
  }
  button.copy {
    width: 100%; padding: 13px; margin-top: 10px; font-size: 15px; font-weight: bold;
    background: transparent; color: var(--cream);
    border: 1px solid var(--line); border-radius: 10px;
  }

  details { margin-top: 24px; color: var(--muted); font-size: 12px; }
  summary { cursor: pointer; }
  pre { white-space: pre-wrap; word-break: break-all; }

  /* 合言葉の入口 */
  #gate { display: none; margin-top: 40px; text-align: center; }
  #gate input {
    width: 100%; padding: 16px; font-size: 17px; text-align: center;
    background: var(--panel); color: var(--cream);
    border: 1px solid var(--line); border-radius: 12px;
    font-family: inherit; margin-bottom: 12px;
  }
  #gate button {
    width: 100%; padding: 16px; font-size: 17px; font-weight: bold;
    background: var(--cream); color: var(--navy);
    border: none; border-radius: 12px;
  }
  #gate .hint { margin-top: 12px; }
  #main { display: none; }

  /* ジャンル選択 */
  .kinds { display: flex; gap: 8px; margin-bottom: 14px; }
  .kinds button {
    flex: 1; padding: 12px 6px; font-size: 14px; font-weight: bold;
    background: transparent; color: var(--muted);
    border: 1px solid var(--line); border-radius: 12px;
  }
  .kinds button.on { background: var(--panel); color: var(--cream); border-color: var(--cream); }
  .kinds:empty { display: none; }
</style>
</head>
<body>
  <h1>♨️ サウナニュース ジェネレーター</h1>

  <div id="gate">
    <div class="sub">合言葉を入れてください</div>
    <input id="passcode" type="password" inputmode="text"
           autocomplete="off" placeholder="合言葉">
    <button onclick="unlock()">はじめる</button>
    <div class="hint" id="gate-hint"></div>
  </div>

<div id="main">
  <div class="sub">ジャンルを選んで、生成ボタンを押してください</div>
  <div class="kinds" id="kinds"></div>
  <button id="go" onclick="start()">記事を生成する</button>
  <div class="status" id="status"></div>
  <div class="detail" id="detail"></div>
  <div class="bar" id="bar"><i id="bar-fill"></i></div>

  <div class="toolbar" id="toolbar">
    <label><input type="checkbox" id="select-all" checked onchange="toggleAll()"> 全選択</label>
    <span class="count" id="count"></span>
  </div>

  <div class="imgs" id="imgs"></div>

  <button class="save" id="save" onclick="saveSelected()" disabled>選択した画像を保存</button>
  <div class="hint" id="hint"></div>

  <div id="caption-area"></div>
  <details>
    <summary>ログを見る（うまくいかないとき）</summary>
    <pre id="log"></pre>
  </details>
</div>

<script>
const $ = (id) => document.getElementById(id);
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

// 合言葉は一度入れたら localStorage に覚えておく。
// PWAで開くたびに聞かれると実用に耐えないため。
const PASS_KEY = 'sauna-app-passcode';
let passcode = localStorage.getItem(PASS_KEY) || '';
let kind = 'topic';

// 共有用のFileを先に作っておく。
// iOSのSafariは「タップから navigator.share までの間に重い処理を挟む」と
// ユーザー操作とみなさなくなり、共有シートが出ない。だから画像を表示した
// 時点でblobを取り切っておき、タップ時は組み立てるだけにする。
let sharableFiles = [];

function setStatus(text, detail) {
  $('status').textContent = text;
  $('detail').textContent = detail || '';
}

function setProgress(value) {
  $('bar').style.display = 'block';
  $('bar-fill').style.width = value + '%';
}

function setHint(text) {
  $('hint').textContent = text || '';
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

// ---- 入口 ----------------------------------------------------------

async function boot() {
  let config = { needs_passcode: false, kinds: [] };
  try {
    const res = await fetch('/api/config', { cache: 'no-store' });
    config = await res.json();
  } catch (e) { /* 起きていないだけかもしれない。合言葉なしとして進む */ }

  buildKinds(config.kinds);

  if (config.needs_passcode && !passcode) {
    showGate();
  } else {
    showMain();
  }
}

function showGate(message) {
  $('gate').style.display = 'block';
  $('main').style.display = 'none';
  $('gate-hint').textContent = message || '';
  $('passcode').focus();
}

function showMain() {
  $('gate').style.display = 'none';
  $('main').style.display = 'block';
}

async function unlock() {
  const value = $('passcode').value.trim();
  if (!value) { $('gate-hint').textContent = '合言葉を入れてください'; return; }

  $('gate-hint').textContent = '確認しています…';
  try {
    const res = await fetch('/api/verify', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ passcode: value })
    });
    if (!res.ok) { $('gate-hint').textContent = '合言葉が違います'; return; }
  } catch (e) {
    $('gate-hint').textContent = 'サーバーにつながりません。少し待って試してください';
    return;
  }

  passcode = value;
  localStorage.setItem(PASS_KEY, value);
  $('passcode').value = '';
  showMain();
}

function buildKinds(kinds) {
  const box = $('kinds');
  box.innerHTML = '';
  if (!kinds || kinds.length < 2) return;   // 1種類しかないなら選ばせない

  kind = kinds[0].key;
  kinds.forEach((k, i) => {
    const btn = document.createElement('button');
    btn.textContent = k.label;
    btn.className = i === 0 ? 'on' : '';
    btn.onclick = () => {
      kind = k.key;
      Array.from(box.children).forEach(c => c.classList.remove('on'));
      btn.classList.add('on');
    };
    box.appendChild(btn);
  });
}

// ---- 生成 ----------------------------------------------------------

async function start() {
  const btn = $('go');
  btn.disabled = true;
  $('imgs').innerHTML = '';
  $('caption-area').innerHTML = '';
  $('log').textContent = '';
  $('toolbar').style.display = 'none';
  $('save').style.display = 'none';
  $('save').disabled = true;
  sharableFiles = [];
  setHint('');
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
      headers: { 'Content-Type': 'application/json', 'X-Passcode': passcode },
      body: JSON.stringify({ type: kind })
    });

    if (res.status === 401) {
      // 合言葉が変わったか、覚えていたものが古い。入口に戻す
      localStorage.removeItem(PASS_KEY);
      passcode = '';
      setStatus('', '');
      $('bar').style.display = 'none';
      showGate('合言葉をもう一度入れてください');
      btn.disabled = false;
      return;
    }

    if (res.status === 409) {
      // 別の生成が動いている（タブを2つ開いた等）
      const data = await res.json();
      setStatus('⚠️ ' + data.error, '');
      $('bar').style.display = 'none';
      btn.disabled = false;
      return;
    }

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
      await render(data);
      return;
    }
  }
}

async function render(data) {
  setStatus('完成しました ✨', '');

  data.images.forEach((url, i) => {
    const shot = document.createElement('div');
    shot.className = 'shot';
    shot.id = 'shot-' + i;

    const img = document.createElement('img');
    img.src = url;
    shot.appendChild(img);

    const label = document.createElement('label');
    const box = document.createElement('input');
    box.type = 'checkbox';
    box.checked = true;
    box.dataset.index = i;
    box.onchange = onPickChange;
    label.appendChild(box);
    label.appendChild(document.createTextNode((i + 1) + '枚目'));
    shot.appendChild(label);

    $('imgs').appendChild(shot);
  });

  $('toolbar').style.display = 'flex';
  $('save').style.display = 'block';
  renderCaption(data.caption);

  // ここで先読み。終わるまで保存ボタンは押せないようにしておく。
  setHint('画像を保存できる形に準備しています…');
  updateCount();
  try {
    sharableFiles = await prepareFiles(data.images);
    $('save').disabled = false;
    setHint(shareSupported()
      ? '共有シートから「画像を保存」でカメラロールに入ります'
      : 'この端末では1枚ずつダウンロードします');
  } catch (e) {
    setHint('⚠️ 画像の準備に失敗しました: ' + e);
  }
  updateCount();
}

async function prepareFiles(urls) {
  const files = [];
  for (let i = 0; i < urls.length; i++) {
    const res = await fetch(urls[i], { cache: 'no-store' });
    if (!res.ok) throw new Error(urls[i] + ' が取得できません');
    const blob = await res.blob();
    files.push(new File([blob], 'sauna_' + (i + 1) + '.jpg', { type: 'image/jpeg' }));
  }
  return files;
}

function pickBoxes() {
  return Array.from(document.querySelectorAll('.shot input[type=checkbox]'));
}

function selectedFiles() {
  return pickBoxes()
    .filter(b => b.checked)
    .map(b => sharableFiles[Number(b.dataset.index)])
    .filter(Boolean);
}

function onPickChange() {
  const boxes = pickBoxes();
  boxes.forEach(b => {
    const shot = $('shot-' + b.dataset.index);
    if (shot) shot.classList.toggle('off', !b.checked);
  });
  $('select-all').checked = boxes.every(b => b.checked);
  updateCount();
}

function toggleAll() {
  const on = $('select-all').checked;
  pickBoxes().forEach(b => { b.checked = on; });
  onPickChange();
}

function updateCount() {
  const n = pickBoxes().filter(b => b.checked).length;
  $('count').textContent = n + ' / ' + pickBoxes().length + ' 枚を選択中';
  $('save').textContent = n > 0 ? ('選択した' + n + '枚を保存') : '選択した画像を保存';
}

function shareSupported() {
  // ファイル共有ができるかどうか。HTTPSでないと navigator.share 自体が無い。
  if (!navigator.canShare || !navigator.share) return false;
  try {
    const probe = new File([new Blob(['x'])], 'probe.jpg', { type: 'image/jpeg' });
    return navigator.canShare({ files: [probe] });
  } catch (e) {
    return false;
  }
}

function saveSelected() {
  const files = selectedFiles();
  if (files.length === 0) {
    setHint('保存する画像を選んでください');
    return;
  }

  // ここから navigator.share までは非同期処理を挟まない（ジェスチャーを保つため）
  if (navigator.canShare && navigator.canShare({ files })) {
    navigator.share({ files })
      .then(() => setHint('共有しました'))
      .catch(err => {
        if (err && err.name === 'AbortError') { setHint(''); return; }  // 閉じただけ
        setHint('⚠️ 共有できませんでした: ' + (err && err.message ? err.message : err));
      });
    return;
  }

  // PCなど files 共有に対応していない環境は1枚ずつダウンロードに落とす
  files.forEach(downloadFile);
  setHint(files.length + '枚をダウンロードしました');
}

function downloadFile(file) {
  const url = URL.createObjectURL(file);
  const a = document.createElement('a');
  a.href = url;
  a.download = file.name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

function renderCaption(caption) {
  if (!caption) return;

  const box = document.createElement('div');
  box.className = 'caption-box';
  box.textContent = caption;
  $('caption-area').appendChild(box);

  const copyBtn = document.createElement('button');
  copyBtn.className = 'copy';
  copyBtn.textContent = '📋 キャプションをコピー';
  copyBtn.onclick = () => {
    navigator.clipboard.writeText(caption);
    copyBtn.textContent = '✅ コピーしました';
    setTimeout(() => copyBtn.textContent = '📋 キャプションをコピー', 2000);
  };
  $('caption-area').appendChild(copyBtn);
}

$('passcode').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') unlock();
});

boot();
</script>
</body>
</html>
"""
