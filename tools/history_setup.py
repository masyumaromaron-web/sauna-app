# -*- coding: utf-8 -*-
"""Supabaseの履歴まわりの様子を見る／手元の履歴を引っ越す。

    .venv/bin/python tools/history_setup.py            … 今の状態を見るだけ
    .venv/bin/python tools/history_setup.py --import   … 手元のJSONを流し込む

SUPABASE_URL / SUPABASE_KEY が要る。手元なら .env に書いておけばよい。
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import history
import pipelines

# 手元に残っている履歴の置き場所。~/kansai-sauna 側にしか無いこともある。
LOCAL_DIRS = [".", os.path.expanduser("~/kansai-sauna")]


def find_local(filename):
    for directory in LOCAL_DIRS:
        path = os.path.join(directory, filename)
        if os.path.exists(path):
            return path
    return None


def show():
    print("接続先:", os.getenv("SUPABASE_URL") or "(未設定)")
    if not history.is_enabled():
        print()
        print("SUPABASE_URL と SUPABASE_KEY が設定されていません。")
        print("この状態でもアプリは動きますが、履歴は残りません。")
        return False

    print()
    print(f"{'ジャンル':<12}{'Supabase':>10}{'手元のJSON':>14}")
    for key, pipeline in pipelines.PIPELINES.items():
        remote = history.count(key)
        local_path = find_local(pipeline.used_news_file)
        local = "-"
        if local_path:
            try:
                local = str(len(json.load(open(local_path, encoding="utf-8"))))
            except Exception:
                local = "読めず"
        remote_text = "つながらず" if remote is None else str(remote)
        print(f"{pipeline.label:<12}{remote_text:>10}{local:>14}")
    return True


def import_local():
    if not history.is_enabled():
        sys.exit("SUPABASE_URL と SUPABASE_KEY を設定してから実行してください。")

    total = 0
    for key, pipeline in pipelines.PIPELINES.items():
        path = find_local(pipeline.used_news_file)
        if not path:
            print(f"  {pipeline.label}: 手元にファイルがありません（{pipeline.used_news_file}）")
            continue
        try:
            entries = json.load(open(path, encoding="utf-8"))
        except Exception as e:
            print(f"  {pipeline.label}: 読めませんでした（{e}）")
            continue

        saved = history.save_entries(key, entries)
        total += saved
        print(f"  {pipeline.label}: {len(entries)}件を送信（{path}）")

    print()
    print(f"送信は完了しました（重複は自動で捨てられます）。合計 {total} 件を送りました。")
    print()
    show()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--import", dest="do_import", action="store_true",
                        help="手元の used_news_*.json をSupabaseに流し込む")
    args = parser.parse_args()

    if args.do_import:
        import_local()
    else:
        if show():
            print()
            print("手元の履歴を引っ越すなら: .venv/bin/python tools/history_setup.py --import")


if __name__ == "__main__":
    main()
