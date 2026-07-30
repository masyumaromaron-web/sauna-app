# -*- coding: utf-8 -*-
"""gemini-2.5-flash と flash-lite で、出来上がる文章がどれくらい違うかを見る。

同じ記事を両方のモデルに通して並べる。実際に使っているプロンプトを
そのまま使うので（news_gemini.py の関数を呼んでいる）、本番と同じ条件になる。

    .venv/bin/python tools/compare_models.py

消費するGemini枠は「記事数 × 2回」。既定の2記事なら flash 2回・flash-lite 2回。

判断の目安:
  - 見出し … 40文字以内に収まっているか、記事の要点を突いているか
  - 説明文 … 事実を取り違えていないか、日本語が不自然でないか
どちらも大差ないなら、枠が別勘定の flash-lite に寄せる価値がある。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import news_gemini
from jptext import wrap_japanese_text

MODELS = ["gemini-2.5-flash", "gemini-2.5-flash-lite"]

# 比較用の記事。実際のニュースに近いものを2本。
# 差し替えたいときはここを書き換える。
SAMPLES = [
    {
        "title": "京都・天橋立に1日1組限定のプライベートサウナ付き宿「SOSO」が本格始動",
        "description": "海を望むロケーションで貸切サウナと水風呂を楽しめる宿泊施設",
        "facility": "SOSO",
        "area": "京都",
        "category": "新規オープン",
        "theme": "個室・貸切",
        "body": (
            "京都府宮津市の天橋立近くに、1日1組限定の宿泊施設「SOSO」が本格始動した。"
            "客室にはフィンランド式サウナと水風呂を備え、宿泊者は時間を気にせず利用できる。"
            "セルフロウリュに対応し、外気浴スペースからは天橋立を望む。"
            "オーナーは「サウナのために旅をする人に、静かに過ごせる場所を用意したかった」と話す。"
            "料金は1泊2名で5万円台から。当面は週末を中心に営業する。"
        ),
    },
    {
        "title": "大阪の老舗銭湯が改装し、薪サウナと外気浴スペースを新設",
        "description": "築60年の銭湯が地域住民の要望を受けてリニューアル",
        "facility": "泉の湯",
        "area": "大阪",
        "category": "リニューアル",
        "theme": "文化・歴史",
        "body": (
            "大阪市内で60年続く銭湯「泉の湯」が、改装工事を終えて営業を再開した。"
            "従来の電気式サウナに加えて薪サウナを新設し、中庭には外気浴用のととのい椅子を8脚並べた。"
            "改装費の一部はクラウドファンディングで集め、目標額の2倍にあたる支援が集まった。"
            "3代目店主は「昔から通ってくれている常連さんと、新しく来てくれる若い人の両方が"
            "居心地よく過ごせる場所にしたい」と語る。番台の意匠やタイル絵は当時のまま残した。"
        ),
    },
]


# generate_gemini_content は、Geminiが失敗すると黙ってPython生成の簡易文に
# 差し替えて返す。それを知らずに並べると「Python生成 vs flash-lite」を
# 比べてしまい、結論を誤る。実際にモデルが答えたかどうかを控えておく。
_answered = {"ok": False}
_original_call = news_gemini.call_gemini_with_retry


def _tracked_call(prompt, retries=2, wait_seconds=5):
    result = _original_call(prompt, retries=retries, wait_seconds=wait_seconds)
    _answered["ok"] = bool(result)
    return result


news_gemini.call_gemini_with_retry = _tracked_call


def run(model, sample):
    news_gemini.GEMINI_MODEL = model          # ここを差し替えるだけで切り替わる
    _answered["ok"] = False
    short_title, summary = news_gemini.generate_gemini_content(
        title=sample["title"],
        description=sample["description"],
        body=sample["body"],
        facility=sample["facility"],
        area=sample["area"],
        category=sample["category"],
        theme=sample["theme"],
    )
    return short_title, summary, _answered["ok"]


def main():
    if not os.getenv("GEMINI_API_KEY"):
        sys.exit("GEMINI_API_KEY が設定されていません（.env を確認してください）")

    news_gemini.init_gemini()
    if not news_gemini.gemini_client:
        sys.exit("Geminiの初期化に失敗しました")

    failures = []

    for n, sample in enumerate(SAMPLES, 1):
        print("=" * 74)
        print(f"記事{n}: {sample['title']}")
        print("=" * 74)

        for model in MODELS:
            print(f"\n■ {model}")
            try:
                short_title, summary, answered = run(model, sample)
            except Exception as e:
                print(f"   呼び出し失敗: {e}")
                failures.append(model)
                continue

            if not answered:
                print("   × このモデルは答えていません（枠切れか混雑）。")
                print("     下はPython生成の代替文なので、比較には使えません。")
                failures.append(model)
                continue

            print(f"   [見出し] {len(short_title.replace(chr(10), ''))}文字")
            for line in wrap_japanese_text(short_title, width=11).split("\n"):
                print(f"        {line}")
            print(f"   [説明文] {len(summary)}文字")
            for line in summary.split("\n"):
                if line.strip():
                    print(f"        {line.strip()}")
        print()

    if failures:
        print("-" * 74)
        print("答えなかったモデルがあるため、この結果では比較できません:",
              ", ".join(sorted(set(failures))))
        print("枠が戻る時間（日本時間の夕方ごろ）を過ぎてから、もう一度実行してください。")


if __name__ == "__main__":
    main()
