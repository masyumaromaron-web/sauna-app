# -*- coding: utf-8 -*-
"""使ったニュースの履歴を Supabase に置く。

Renderのディスクは揮発するので used_news_*.json が育たず、同じネタが
繰り返し選ばれてしまう。そこで履歴だけ外に出す。

既存スクリプト（news_topic.py など）には手を入れていない。
pipelines.py が

  1. 走らせる前に、Supabaseの履歴を作業ディレクトリの used_news_*.json に書く
  2. 走り終わったら、増えた分をSupabaseに戻す

という形で面倒を見るので、スクリプトから見ると「いつもの場所にいつもの
ファイルがある」だけになる。

SUPABASE_URL / SUPABASE_KEY が未設定なら何もしない。その場合はこれまで
どおりリポジトリに置いた履歴が種になる（＝Phase 7 以前の挙動）。

依存は増やしていない。requests でRESTを直接叩く。
"""

import os

import requests

TABLE = "used_news"

# 種として渡す件数。スクリプト側は重複判定の集合に使うだけなので、
# 多いほど被りを防げる。保存時に直近100件へ切り詰められるが、
# こちらは増えた分を足すだけで消さないので、DB側は減らない。
SEED_LIMIT = 300

TIMEOUT_SEC = 10

def _config():
    """接続先を毎回読む。

    import した時点で読むと、.env を後から読み込む使い方（手元での実行）で
    取りこぼす。呼ぶたびに読めばその心配がない。
    """
    url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    key = os.getenv("SUPABASE_KEY", "").strip()
    return url, key


def is_enabled():
    url, key = _config()
    return bool(url and key)


def _headers(extra=None):
    _, key = _config()
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if extra:
        headers.update(extra)
    return headers


def load_entries(kind):
    """そのジャンルの履歴を新しい順に取る。

    戻り値は used_news_*.json にそのまま書ける形
    （[{"title": ..., "date": ...}, ...]）。
    Supabaseが使えないときは None を返す。呼び出し側はそれを見て、
    これまでどおりの動きに落とす。
    """
    if not is_enabled():
        return None

    try:
        res = requests.get(
            f"{_config()[0]}/rest/v1/{TABLE}",
            headers=_headers(),
            params={
                "kind": f"eq.{kind}",
                "select": "title,used_on",
                "order": "id.desc",
                "limit": str(SEED_LIMIT),
            },
            timeout=TIMEOUT_SEC,
        )
        res.raise_for_status()
        rows = res.json()
    except Exception as e:
        print(f"履歴の読み込みに失敗しました（履歴なしで続けます）: {e}")
        return None

    # スクリプトは古い順に並んでいる前提で末尾を切るので、古い順に直す
    rows.reverse()
    return [{"title": r["title"], "date": r.get("used_on") or ""} for r in rows]


def save_entries(kind, entries):
    """増えた履歴を書き足す。同じものは (kind, title) の一意制約で弾かれる。"""
    if not is_enabled() or not entries:
        return 0

    payload = []
    for entry in entries:
        title = entry.get("title") if isinstance(entry, dict) else entry
        if not title:
            continue
        row = {"kind": kind, "title": title}
        date = entry.get("date") if isinstance(entry, dict) else ""
        if date:
            row["used_on"] = date
        payload.append(row)

    if not payload:
        return 0

    try:
        res = requests.post(
            f"{_config()[0]}/rest/v1/{TABLE}",
            headers=_headers({"Prefer": "resolution=ignore-duplicates,return=minimal"}),
            json=payload,
            timeout=TIMEOUT_SEC,
        )
        res.raise_for_status()
    except Exception as e:
        # 履歴が残らなくても生成そのものは成功しているので、止めない
        print(f"履歴の保存に失敗しました（生成は成功しています）: {e}")
        return 0

    return len(payload)


def titles_of(entries):
    """履歴のリストからタイトルの集合を作る。文字列形式の旧データにも合わせる。"""
    titles = set()
    for entry in entries or []:
        if isinstance(entry, str):
            titles.add(entry)
        elif isinstance(entry, dict) and entry.get("title"):
            titles.add(entry["title"])
    return titles


def count(kind):
    """そのジャンルに何件たまっているか。動作確認用。"""
    if not is_enabled():
        return None
    try:
        res = requests.get(
            f"{_config()[0]}/rest/v1/{TABLE}",
            headers=_headers({"Prefer": "count=exact", "Range": "0-0"}),
            params={"kind": f"eq.{kind}", "select": "id"},
            timeout=TIMEOUT_SEC,
        )
        res.raise_for_status()
        # Content-Range は "0-0/42" の形
        return int(res.headers.get("Content-Range", "0-0/0").split("/")[-1])
    except Exception as e:
        print(f"件数の取得に失敗しました: {e}")
        return None
