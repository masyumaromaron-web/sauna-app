import os
import random
import re
import html
import json
import time
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote, unquote
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

# =========================
# 設定
# =========================

USE_GEMINI = True
GEMINI_MODEL = "gemini-2.5-flash-lite"

MAX_SELECTED_NEWS = 3

POSTS_FILE = "posts_sento.txt"
CAPTION_SOURCE_FILE = "caption_source_sento.txt"
USED_NEWS_FILE = "used_news_sento.json"
USED_THEME_FILE = "used_theme_sento.json"

MAX_NEWS_AGE_DAYS = 120  # これより古いニュースはスキップ


# =========================
# テーマ定義
# =========================

# テーマ名 → 合致キーワード のマッピング
THEME_DEFINITIONS = {
    "レトロ銭湯":       ["レトロ", "昭和", "古民家", "タイル", "老舗", "歴史"],
    "サウナ付き銭湯":   ["サウナ", "水風呂", "ととのい", "ロウリュ"],
    "イベント湯":       ["イベント", "期間限定", "コラボ", "特別", "花湯", "薬湯", "変わり湯"],
    "リニューアル":     ["リニューアル", "改装", "刷新", "リノベ", "復活"],
    "閉店":             ["閉店", "閉館", "廃業", "終了", "惜しまれ"],
    "駅近":             ["駅近", "駅チカ", "徒歩", "アクセス"],
    "親子向け":         ["子ども", "子供", "ファミリー", "親子", "赤ちゃん", "キッズ"],
    "天然温泉銭湯":     ["天然温泉", "源泉", "温泉銭湯"],
    "新店":             ["オープン", "開業", "開店", "新規", "誕生", "グランドオープン"],
    "女性向け":         ["女性", "女子", "レディース", "女湯", "女性専用"],
}

# テーマの順番（ローテーション順）
THEME_ORDER = [
    "新店",
    "レトロ銭湯",
    "サウナ付き銭湯",
    "イベント湯",
    "天然温泉銭湯",
    "女性向け",
    "リニューアル",
    "駅近",
    "親子向け",
    "閉店",
]

# 補完に使うテーマ優先順（メインテーマで3件揃わなかった場合）
FALLBACK_THEME_ORDER = ["新店", "リニューアル", "イベント湯", "サウナ付き銭湯", "レトロ銭湯"]


# =========================
# Gemini設定
# =========================

gemini_client = None


def init_gemini():
    global gemini_client, USE_GEMINI

    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        print("GEMINI_API_KEY が未設定です。Gemini要約は使いません。")
        USE_GEMINI = False
        return None

    try:
        from google import genai
        gemini_client = genai.Client(api_key=api_key)
        print("Gemini初期化OK")
        return gemini_client
    except Exception as e:
        print("Gemini初期化失敗。Python処理のみで続行します:", e)
        USE_GEMINI = False
        gemini_client = None
        return None


# =========================
# 基本ワード
# =========================

kansai_words = [
    "大阪", "京都", "兵庫", "奈良", "滋賀", "和歌山",
    "神戸", "関西", "堺", "枚方", "高槻", "東大阪",
    "尼崎", "西宮", "姫路", "明石", "大津", "草津",
    "奈良市", "橿原", "和歌山市"
]

SAUNA_CORE_WORDS = [
    "銭湯", "公衆浴場", "町の銭湯", "薪炊き", "薪風呂",
]

SAUNA_SUB_WORDS = [
    "浴場", "湯", "入浴", "温泉", "サウナ", "岩盤浴",
]

sauna_words = SAUNA_CORE_WORDS + SAUNA_SUB_WORDS

# 女性・親子向けワード（スコア加点用）
AUDIENCE_WORDS = [
    "女性", "女子", "レディース", "女性専用",
    "子ども", "子供", "ファミリー", "親子", "赤ちゃん", "キッズ",
]

EXCLUDE_WORDS = [
    "不動産", "マンション", "分譲", "建設予定地", "跡地", "土地活用",
    "エステ", "美容", "ネイル", "まつげ", "フィットネス", "ジム", "ヨガ",
    "韓国", "中国", "海外", "タイ", "バリ", "ハワイ", "シンガポール",
    "熱中症", "危険な暑さ", "猛暑日", "酷暑", "気温上昇",
    "IR", "カジノ", "競馬", "パチンコ",
    "スーパー銭湯", "温浴施設", "スパ銭", "健康ランド",
]


# =========================
# 日付フィルタ
# =========================

def is_recent_news(pub_date, max_days=MAX_NEWS_AGE_DAYS):
    if not pub_date:
        return True
    try:
        published = parsedate_to_datetime(pub_date)
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        days_old = (now - published).days
        if days_old > max_days:
            return False
        return True
    except Exception:
        return True


# =========================
# テーマローテーション管理
# =========================

def load_used_theme():
    if not os.path.exists(USED_THEME_FILE):
        return {"last_index": -1}
    try:
        with open(USED_THEME_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"last_index": -1}


def save_used_theme(index):
    with open(USED_THEME_FILE, "w", encoding="utf-8") as f:
        json.dump({"last_index": index}, f, ensure_ascii=False, indent=2)


def get_today_theme(candidates, used_theme_data):
    # 候補記事があるテーマだけを集める
    available = []
    for index, theme in enumerate(THEME_ORDER):
        keywords = THEME_DEFINITIONS[theme]
        matched = [n for n in candidates if any(kw in n["combined_text"] for kw in keywords)]
        if matched:
            available.append((theme, index, len(matched)))

    if available:
        # 候補のあるテーマからランダムに選ぶ（毎回違うテーマになりやすくする）。
        # Renderではused_theme_*.jsonが揮発して順番送りが効かないため、
        # 話題版と同じやり方に揃えている。
        theme, index, count = random.choice(available)
        print(f"今日のテーマ: 【{theme}】（候補 {count} 件・ランダム選択）")
        return theme, index

    print("全テーマで候補が見つかりませんでした")
    return None, used_theme_data.get("last_index", -1)


def filter_by_theme(candidates, theme):
    """テーマキーワードに1つでもマッチするニュースを返す"""
    keywords = THEME_DEFINITIONS[theme]
    return [n for n in candidates if any(kw in n["combined_text"] for kw in keywords)]


def fill_with_fallback(selected, candidates, theme):
    """
    selectedが3件未満のとき、補完テーマ順に候補を足して3件にする。
    すでにselectedにあるニュースは除外。
    """
    selected_titles = {n["title"] for n in selected}

    for fallback_theme in FALLBACK_THEME_ORDER:
        if fallback_theme == theme:
            continue
        if len(selected) >= MAX_SELECTED_NEWS:
            break
        keywords = THEME_DEFINITIONS[fallback_theme]
        fallback_candidates = [
            n for n in candidates
            if any(kw in n["combined_text"] for kw in keywords)
            and n["title"] not in selected_titles
        ]
        fallback_candidates.sort(key=lambda x: x["score"], reverse=True)
        for news in fallback_candidates:
            if len(selected) >= MAX_SELECTED_NEWS:
                break
            selected.append(news)
            selected_titles.add(news["title"])
            print(f"補完テーマ【{fallback_theme}】から追加: {news['title']}")

    # それでも足りない場合はスコア上位で埋める
    if len(selected) < MAX_SELECTED_NEWS:
        remaining = [n for n in candidates if n["title"] not in selected_titles]
        remaining.sort(key=lambda x: x["score"], reverse=True)
        for news in remaining:
            if len(selected) >= MAX_SELECTED_NEWS:
                break
            selected.append(news)
            selected_titles.add(news["title"])
            print(f"スコア上位から補完追加: {news['title']}")

    return selected


# =========================
# 整形関数
# =========================

def clean_title(title):
    title = html.unescape(title)
    title = re.sub(r"<.*?>", "", title)
    title = re.sub(r"\s+", " ", title).strip()
    title = re.sub(r"\s*-\s*[^-]+$", "", title)
    title = re.sub(r'\(\d+ページ目\)|\d+ページ目', '', title)
    return title.strip()


def clean_description(description):
    if not description:
        return ""
    description = html.unescape(description)
    description = re.sub(r"<.*?>", "", description)
    description = re.sub(r"\s+", " ", description).strip()

    media_words = [
        "Yahoo!ニュース", "All About ニュース", "MBSニュース",
        "読売新聞オンライン", "産経ニュース", "神戸新聞NEXT",
        "京都新聞", "PR TIMES", "時事通信", "朝日新聞",
        "毎日新聞", "日経新聞"
    ]
    for word in media_words:
        description = description.replace(word, "")

    description = description.strip(" -｜|　")
    if len(description) > 140:
        description = description[:140].rstrip() + "…"
    return description


def normalize_for_compare(text):
    text = re.sub(r"[【】「」『』（）()\[\]\s　、。,.・!！?？:：\-ー|｜]", "", text)
    return text


# =========================
# 判定関数
# =========================

def detect_area(text):
    for word in kansai_words:
        if word in text:
            return word
    return "関西"


def extract_facility_name(text):
    BAD_FACILITY_WORDS = [
        "ととのい", "サウナ", "水風呂", "外気浴", "ロウリュ",
        "アウフグース", "熱波", "天然温泉", "オープン", "リニューアル",
        "イベント", "期間限定", "コラボ", "フェス", "閉店", "閉館",
        "休館", "銭湯", "温泉", "スパ", "お知らせ", "情報",
    ]

    quote_patterns = [
        r"「([^」]+)」",
        r"『([^』]+)』",
        r"\u201c([^\u201d]+)\u201d",
        r"\"([^\"]+)\"",
    ]
    for pattern in quote_patterns:
        match = re.search(pattern, text)
        if match:
            candidate = match.group(1).strip()
            if candidate not in BAD_FACILITY_WORDS and len(candidate) >= 3:
                return candidate

    facility_dictionary = [
        "スパワールド",
        "奈良健康ランド",
        "大東洋",
        "ニュージャパン",
        "白玉温泉",
        "延羽の湯",
        "神戸サウナ＆スパ",
        "なにわ健康ランド 湯〜トピア",
        "サウナ&カプセル アムザ",
        "サウナの梅湯",
        "五香湯",
        "ルーマプラザ",
        "スパワールド 世界の大温泉",
        "空庭温泉 OSAKA BAY TOWER",
        "延羽の湯 鶴橋店",
        "延羽の湯 羽曳野本店",
        "大東洋",
        "アムザ",
        "堺浜楽天温泉 祥福",
        "天然露天温泉 スパスミノエ",
        "天然温泉 ひなたの湯",
        "湯快のゆ 寝屋川店",
        "湯快のゆ 門真店",
        "湯源郷 太平のゆ 忠岡店",
        "箕面温泉スパーガーデン",
        "美人湯 祥風苑",
        "水春 箕面湯元",
        "水春 松井山手",
        "水春 鶴見緑地湯元",
        "源気温泉 万博おゆば",
        "極楽湯 吹田店",
        "極楽湯 茨木店",
        "虹の湯 大阪狭山店",
        "虹の湯 二色の浜店",
        "さらさのゆ",
        "ユーバス 守口店",
        "ユーバス 堺浜寺店",
        "蔵前温泉 さらさのゆ",
        "石切温泉 ホテルセイリュウ",
        "天然温泉 延羽の湯 本店",
        "天然温泉 風の湯 河内長野店",
        "天然温泉 風の湯 新石切店",
        "犬鳴山温泉 不動口館",
        "伏尾温泉 不死王閣",
        "牛滝温泉 四季まつり",
        "くつろぎの郷 湯楽",
        "花園温泉 sauna kukka",
        "DESSE",
        "サウナシャン",
        "カプセル&スパ グランドサウナ心斎橋",
        "白玉温泉",
        "入船温泉",
        "ヘルシー温泉タテバ",
        "朝日温泉",
        "辰巳温泉",
        "姫松温泉",
        "テルメ龍宮",
        "船岡温泉",
        "栄湯",
        "源ヶ橋温泉",
        "大正湯",
        "錦温泉",
        "清水湯",
        "宝湯",
        "日の本湯",
        "玉乃湯",
        "戎湯",
        "新温泉",
        "第一敷島湯",
        "福助温泉",
        "桜湯",
        "千鳥温泉",
        "朝日湯",
        "みどり温泉",
        "旭温泉",
        "新朝日温泉",
        "昭和湯",
        "幸福温泉",
        "パークプラザ大東洋",
        "ホテルモントレ ラ・スール大阪 スパ・トリニテ",
        "アートホテル大阪ベイタワー 空庭温泉",
        "リーベルホテル アット ユニバーサル・スタジオ・ジャパン",
        "御堂筋ホテル 天然温泉",
        "関空温泉ホテルガーデンパレス",
        "SPA専 太平のゆ",
        "かいづか温泉 ほの字の里",
        "能勢温泉",
        "神戸クアハウス",
        "湯櫻",
        "水春",
        "空庭温泉",
        "スパバレイ枚方南",
        "サウナの梅湯",
        "白山湯 高辻店",
        "白山湯 六条店",
        "五香湯",
        "船岡温泉",
        "源湯",
        "玉の湯",
        "旭湯",
        "誠の湯",
        "鴨川湯",
        "桜湯",
        "大黒湯",
        "錦湯",
        "力湯",
        "ルーマプラザ",
        "仁左衛門の湯",
        "伏見 力の湯",
        "天翔の湯 大門",
        "壬生温泉 はなの湯",
        "上方温泉 一休 京都本館",
        "さがの温泉 天山の湯",
        "京都るり渓温泉",
        "スッカマ 源氏の湯",
        "宇治天然温泉 源氏の湯",
        "福知山温泉 養老の湯",
        "くらま温泉",
        "不動温泉",
        "夕日ヶ浦温泉 花ゆうみ",
        "天橋立温泉 智恵の湯",
        "比良とぴあ",
        "ホテルモントレ京都 スパ・トリニテ",
        "天然温泉 御所の湯",
        "京乃湯",
        "軍人湯",
        "山城温泉",
        "小町湯",
        "栄盛湯",
        "若の湯",
        "明田湯",
        "初音湯",
        "洛陽湯",
        "東山湯",
        "桜湯",
        "松葉湯",
        "大正湯",
        "山城湯",
        "日の出湯",
        "五色湯",
        "宝湯",
        "大宮温泉",
        "桃山温泉 月見館",
        "神戸サウナ&スパ",
        "神戸クアハウス",
        "万葉倶楽部 神戸ハーバーランド温泉",
        "有馬温泉 太閤の湯",
        "有馬温泉 銀の湯",
        "有馬温泉 金の湯",
        "潮芦屋温泉 SPA水春",
        "吟湯 湯治聚落",
        "華の湯",
        "湯あそびひろば 森温泉",
        "蓬莱湯",
        "灘温泉 水道筋店",
        "灘温泉 六甲道店",
        "灘温泉 篠原南店",
        "湊山温泉",
        "二宮温泉",
        "クア武庫川",
        "えびすの湯 一休",
        "美健SPA 湯櫻",
        "寿ノ湯",
        "熊野の郷",
        "天然温泉 あぐろの湯",
        "天然温泉 延羽の湯 野天 閑雅山荘",
        "名湯 宝乃湯",
        "天然温泉 石道",
        "天然温泉 石道 こやぶ",
        "姫路ゆめさき川温泉 夢乃井",
        "姫路市休養センター 香寺荘",
        "赤穂温泉 祥吉",
        "赤穂御崎温泉 鹿久居荘",
        "洲本温泉 海月館",
        "南あわじ温泉郷",
        "ホテルニューアワジ",
        "あまみ温泉 南天苑",
        "東条湖グランド赤坂",
        "ネスタリゾート神戸 延羽の湯",
        "天然温泉 湯庵",
        "かすみ・矢田川温泉",
        "浜坂温泉保養荘",
        "城崎温泉 一の湯",
        "城崎温泉 御所の湯",
        "城崎温泉 鴻の湯",
        "城崎温泉 柳湯",
        "城崎温泉 地蔵湯",
        "城崎温泉 まんだら湯",
        "城崎温泉 さとの湯",
        "尼崎センタープール前 みずきの湯",
        "ユートピア琴浦",
        "極楽湯 尼崎店",
        "天然温泉 ぷくぷく",
        "加古川温泉 みとろ荘",
        "小野温泉 夢の森公園",
        "チムジルバンスパ神戸",
        "ジェームス山天然温泉 月の湯舟",
        "有馬街道温泉 すずらんの湯",
        "こうべ花時計温泉",
        "日の本湯",
        "祇園温泉",
        "大黒湯",
        "朝日温泉",
        "戎湯",
        "湯あそびひろば 柚耶の里",
        "栄湯",
        "扇港湯",
        "橘湯",
        "第一平和温泉",
        "ゆとなみ社 大箇湯",
        "松の湯",
        "湯あそびひろば 芦原温泉",
        "元町サウナ",
    ]
    for facility in facility_dictionary:
        if facility in text:
            return facility

    guess_patterns = [
        r"([一-龥ぁ-んァ-ヶA-Za-z0-9&＆・〜～ー\s]+温泉)",
        r"([一-龥ぁ-んァ-ヶA-Za-z0-9&＆・〜～ー\s]+健康ランド)",
        r"([一-龥ぁ-んァ-ヶA-Za-z0-9&＆・〜～ー\s]+の湯)",
        r"([一-龥ぁ-んァ-ヶA-Za-z0-9&＆・〜～ー\s]+サウナ)",
        r"([一-龥ぁ-んァ-ヶA-Za-z0-9&＆・〜～ー\s]+スパ)",
    ]
    for pattern in guess_patterns:
        match = re.search(pattern, text)
        if match:
            name = match.group(1).strip()
            if len(name) <= 30:
                return name

    return ""


def detect_category(text):
    if any(word in text for word in ["オープン", "開業", "開店", "新施設", "誕生"]):
        return "新店"
    if any(word in text for word in ["リニューアル", "改装", "刷新"]):
        return "リニューアル"
    if any(word in text for word in ["閉店", "閉館", "休館", "終了"]):
        return "閉店・休館"
    if any(word in text for word in ["ロウリュ", "熱波", "アウフグース", "熱波師"]):
        return "イベント"
    if "外気浴" in text:
        return "外気浴"
    if any(word in text for word in ["銭湯", "スーパー銭湯"]):
        return "銭湯"
    if any(word in text for word in ["温浴施設", "スパ", "健康ランド"]):
        return "温浴施設"
    if any(word in text for word in ["ホテル", "宿泊", "泊まれる", "滞在"]):
        return "宿泊サウナ"
    return "general"


def calc_score(text, category, area):
    score = 0
    if area != "関西":
        score += 20
    if category in ["新店", "リニューアル"]:
        score += 30
    elif category in ["イベント", "外気浴"]:
        score += 20
    elif category in ["銭湯", "温浴施設", "宿泊サウナ"]:
        score += 15
    elif category == "閉店・休館":
        score += 10

    core_hits = sum(1 for w in SAUNA_CORE_WORDS if w in text)
    score += core_hits * 10
    sub_hits = sum(1 for w in SAUNA_SUB_WORDS if w in text)
    score += sub_hits * 3
    if core_hits == 0:
        score -= 15

    # 女性・カップル・貸切ワード加点（1語につき8点）
    audience_hits = sum(1 for w in AUDIENCE_WORDS if w in text)
    score += audience_hits * 8

    for word in ["話題", "人気", "注目", "新", "限定", "イベント"]:
        if word in text:
            score += 3

    return score


# =========================
# Geminiなし短文生成
# =========================

def extract_features(text):
    features = []
    feature_words = [
        "天然温泉", "源泉", "露天風呂", "重曹泉", "高濃度炭酸泉",
        "薪炊き", "薪風呂", "レトロ", "昭和", "タイル",
        "サウナ", "水風呂", "ととのい",
        "岩盤浴", "リニューアル", "オープン",
        "女性専用", "レディースデー", "親子", "ファミリー",
        "イベント湯", "花湯", "薬湯", "変わり湯",
    ]
    for word in feature_words:
        if word in text and word not in features:
            features.append(word)
    return features


def make_short_title(title, description, category, area, theme=None):
    """Gemini失敗時のフォールバック。新聞見出しをTikTok用に削った感じに統一"""
    text = title + " " + description
    facility = extract_facility_name(text)
    main_area = area if area != "関西" else ""

    # カテゴリ別：出来事が分かる動詞句を使う
    if category == "新店" or theme == "新店":
        if facility:
            return f"{facility}\nオープンへ"
        return f"{main_area}に\n新銭湯誕生"

    if category == "リニューアル" or theme == "リニューアル":
        if facility:
            return f"{facility}\nリニューアルへ"
        return f"{main_area}の銭湯\nリニューアルへ"

    if category in ("閉店", "閉店・休館") or theme == "閉店":
        if facility:
            return f"{facility}\n閉店へ"
        return f"{main_area}の銭湯\n閉店へ"

    if category == "イベント" or theme == "イベント湯":
        if facility:
            return f"{facility}\n期間限定イベント"
        return f"{main_area}の銭湯\n期間限定イベント"

    if category == "天然温泉銭湯" or theme == "天然温泉銭湯":
        if facility:
            return f"{facility}\n天然温泉"
        return f"{main_area}\n天然温泉銭湯"

    if category == "レトロ銭湯" or theme == "レトロ銭湯":
        if facility:
            return f"{facility}\nレトロ銭湯"
        return f"{main_area}\nレトロ銭湯"

    if category == "サウナ付き銭湯" or theme == "サウナ付き銭湯":
        if facility:
            return f"{facility}\nサウナも"
        return f"{main_area}の銭湯\nサウナ付き"

    # 汎用：施設名があれば使う
    if facility:
        short = title.replace(facility, "").strip("　 【】「」『』・,、。")
        if short and len(short) <= 15:
            return f"{facility}\n{short}"
        return f"{facility}\n銭湯情報"

    # 最終フォールバック：タイトルを削る
    short = title
    if len(short) > 20:
        short = short[:20] + "…"
    return short


def make_python_summary(title, description):
    if description:
        return description
    return title


# =========================
# 本文取得
# =========================

def resolve_google_news_url(google_url):
    """GoogleニュースURLを実記事URLに解決。googlenewsdecoder優先、ダメならリダイレクト"""
    # 1. googlenewsdecoderで実URLをデコード
    if "news.google.com" in google_url:
        try:
            from googlenewsdecoder import gnewsdecoder
            result = gnewsdecoder(google_url, interval=1)
            if result.get("status") and result.get("decoded_url"):
                decoded = result["decoded_url"]
                if "news.google.com" not in decoded:
                    return decoded
        except ImportError:
            print("  googlenewsdecoder未インストール（pip install googlenewsdecoder）")
        except Exception as e:
            print(f"  デコード失敗: {e}")

    # 2. フォールバック：リダイレクトを追う
    try:
        response = requests.get(
            google_url,
            timeout=8,
            allow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        final_url = response.url
        if "news.google.com" in final_url:
            print("  GoogleニュースURL解決失敗（リダイレクト未解決）")
            return ""
        return final_url
    except Exception:
        return ""


def extract_jsonld_article_body(soup):
    """JSON-LDのarticleBodyを再帰的に取得（@graph対応）"""
    def find_article_body(data):
        if isinstance(data, dict):
            if data.get("articleBody"):
                return data["articleBody"]
            if "@graph" in data and isinstance(data["@graph"], list):
                for item in data["@graph"]:
                    found = find_article_body(item)
                    if found:
                        return found
            for value in data.values():
                if isinstance(value, (dict, list)):
                    found = find_article_body(value)
                    if found:
                        return found
        elif isinstance(data, list):
            for item in data:
                found = find_article_body(item)
                if found:
                    return found
        return ""

    scripts = soup.find_all("script", type="application/ld+json")
    for script in scripts:
        try:
            raw = script.string
            if not raw:
                continue
            data = json.loads(raw)
            body = find_article_body(data)
            if body:
                body = re.sub(r"\s+", " ", body).strip()
                return body
        except Exception:
            continue
    return ""


def fetch_article_body(url, timeout=5):
    """記事本文を取得して最大1000文字返す。trafilatura → newspaper3k → JSON-LD → pタグ → meta の順で試みる"""
    print("  [本文取得]")
    real_url = resolve_google_news_url(url)

    if not real_url:
        print("  URL解決失敗。本文取得をスキップしRSSのdescriptionを使用します")
        return ""

    print(f"  解決後URL: {real_url[:90]}")

    # ① trafilaturaで試みる
    try:
        import trafilatura
        downloaded = trafilatura.fetch_url(real_url)
        body = trafilatura.extract(downloaded) if downloaded else ""
        if body:
            body = re.sub(r"\s+", " ", body).strip()
            if len(body) > 50:
                print(f"  trafilatura取得成功: {len(body)}文字")
                print(f"  採用: trafilatura")
                return body[:1000]
    except Exception as e:
        print(f"  trafilatura失敗: {e}")

    # ② newspaper3kで試みる
    try:
        from newspaper import Article
        article = Article(real_url, language="ja")
        article.download()
        article.parse()
        body = article.text.strip()
        body = re.sub(r"\s+", " ", body)
        if body and len(body) > 50:
            print(f"  newspaper3k取得成功: {len(body)}文字")
            print(f"  採用: newspaper3k")
            return body[:1000]
    except Exception as e:
        print(f"  newspaper3k失敗: {e}")

    # ③ JSON-LD → pタグ → meta description（本文優先）
    try:
        response = requests.get(
            real_url,
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        soup = BeautifulSoup(response.text, "html.parser")

        # JSON-LD articleBody（本文が長いので最優先）
        jsonld_body = extract_jsonld_article_body(soup)
        if jsonld_body and len(jsonld_body) > 100:
            print(f"  JSON-LD取得成功: {len(jsonld_body)}文字")
            print(f"  採用: JSON-LD")
            return jsonld_body[:1000]

        for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
            tag.decompose()

        # pタグ本文
        paragraphs = soup.find_all("p")
        body = " ".join(
            p.get_text(strip=True)
            for p in paragraphs
            if len(p.get_text(strip=True)) > 20
        )
        body = re.sub(r"\s+", " ", body).strip()
        if body and len(body) > 50:
            print(f"  BeautifulSoup取得成功: {len(body)}文字")
            print(f"  採用: BeautifulSoup")
            return body[:1000]

        # meta description（短いので最後の砦）
        meta_candidates = []
        for attrs in [
            {"property": "og:description"},
            {"name": "description"},
            {"name": "twitter:description"},
        ]:
            tag = soup.find("meta", attrs=attrs)
            if tag and tag.get("content"):
                meta_candidates.append(tag.get("content").strip())

        if meta_candidates:
            meta_text = " ".join(meta_candidates)
            meta_text = re.sub(r"\s+", " ", meta_text).strip()
            if len(meta_text) > 30:
                print(f"  meta description取得成功: {len(meta_text)}文字")
                print(f"  採用: meta description")
                return meta_text[:1000]

    except Exception as e:
        print(f"  BeautifulSoup失敗: {e}")

    print("  本文取得失敗（スキップ）")
    return ""


# =========================
# Gemini処理
# =========================

def call_gemini_with_retry(prompt, retries=3, wait_seconds=5):
    for attempt in range(retries):
        try:
            response = gemini_client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt
            )
            return response.text.strip()
        except Exception as e:
            print(f"Gemini呼び出し失敗 {attempt + 1}/{retries}: {e}")
            if attempt < retries - 1:
                time.sleep(wait_seconds)
    return ""


def generate_gemini_content(title, description, body="", facility="", area="", category="", theme=None):
    """short_titleとsummaryを1回のGemini呼び出しで同時生成する"""
    if not USE_GEMINI or not gemini_client:
        return (
            make_short_title(title, description, category, area, theme),
            make_python_summary(title, description)
        )

    facility_line = f"\n施設名: {facility}" if facility else ""
    area_line = f"\n場所: {area}" if area else ""
    body_line = f"\n\n記事本文・補足情報:\n{body}" if body else ""
    theme_hint = f"\n今日のテーマ: {theme}" if theme else ""

    prompt = f"""
あなたはTikTokの関西銭湯ニュース投稿を作る編集者です。

以下のニュースから、画像用の短い見出しと、TikTok説明文を作ってください。{facility_line}{area_line}{theme_hint}

条件:
- 必ずJSON形式だけで返す
- 前置き、説明、マークダウン、コードブロックは禁止
- 「はい」「承知しました」「以下」などの返事は禁止
- short_title は2行。全体で40文字以内
- short_title は画像に載せるため短く、煽りすぎない
- 地名が分かる場合はshort_titleに入れる
- summary は180〜260文字程度
- summary はですます調で書く
- 読者に話しかける形（〜だよ、〜だね、〜だって、〜だと思うよ）は使わない
- テーマ名やシステムの内部情報は文中に出さない
- 施設名・場所が分かる場合はsummary冒頭に入れる
- 1文目で何のニュースかを伝える
- 2文目以降で、設備・特徴・開催時期・注目ポイントを具体的に入れる
- 本文やタイトルにないことは書かない
- 「魅力」「人気」「提供」「注目」という言葉はなるべく使わない
- 効能、品質、希少性、人気の理由を推測で書かない
- 「期待されます」「最適です」「ぜひ一度」「〜となっています」など広告っぽい表現を避ける
- 絵文字なし
- ハッシュタグなし

返答形式:
{{
  "short_title": "1行目\\n2行目",
  "summary": "180〜260文字の説明文"
}}

タイトル:
{title}

説明:
{description}{body_line}
"""

    result = call_gemini_with_retry(prompt)

    if not result:
        print("Gemini統合生成失敗。Python生成に切り替え")
        return (
            make_short_title(title, description, category, area, theme),
            make_python_summary(title, description)
        )

    try:
        result = result.strip()
        result = result.replace("```json", "").replace("```", "").strip()

        data = json.loads(result)

        short_title = data.get("short_title", "").strip()
        summary = data.get("summary", "").strip()

        ng_words = ["はい", "承知", "以下", "提案"]
        if any(ng in short_title for ng in ng_words):
            print("短タイトルに返事文混入のためPython短タイトルへ切り替え")
            short_title = make_short_title(title, description, category, area, theme)

        if not short_title:
            short_title = make_short_title(title, description, category, area, theme)
        if not summary:
            summary = make_python_summary(title, description)

        return short_title, summary

    except Exception as e:
        print("Gemini JSON解析失敗。Python生成に切り替え:", e)
        return (
            make_short_title(title, description, category, area, theme),
            make_python_summary(title, description)
        )


# =========================
# ニュース取得
# =========================

def load_used_news():
    if not os.path.exists(USED_NEWS_FILE):
        return []
    try:
        with open(USED_NEWS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_used_news(used_news):
    with open(USED_NEWS_FILE, "w", encoding="utf-8") as f:
        json.dump(used_news, f, ensure_ascii=False, indent=2)


def fetch_google_news():
    NEWS_QUERIES = [
        "関西 銭湯",
        "大阪 銭湯",
        "京都 銭湯",
        "兵庫 銭湯",
        "奈良 銭湯",
        "滋賀 銭湯",
        "和歌山 銭湯",
        "関西 公衆浴場",
        "関西 町銭湯",
        "関西 レトロ銭湯",
    ]
    all_items = []
    for query in NEWS_QUERIES:
        encoded_query = quote(query)
        url = (
            "https://news.google.com/rss/search?"
            f"q={encoded_query}&hl=ja&gl=JP&ceid=JP:ja"
        )
        try:
            response = requests.get(url, timeout=15)
            response.raise_for_status()
            root = ET.fromstring(response.content)
            items = root.findall(".//item")
            print(f"取得: {query} → {len(items)}件")
            all_items.extend(items)
            time.sleep(0.5)
        except Exception as e:
            print(f"取得失敗: {query}", e)
    return all_items


# =========================
# メイン処理
# =========================

def main():
    print("ニュース取得開始")

    open(POSTS_FILE, "w", encoding="utf-8").close()
    open(CAPTION_SOURCE_FILE, "w", encoding="utf-8").close()

    items = fetch_google_news()
    used_news = load_used_news()
    used_theme_data = load_used_theme()

    news_candidates = []
    seen_titles = set()

    for item in items:
        title = item.findtext("title", default="")
        description = item.findtext("description", default="")
        link = item.findtext("link", default="")
        pub_date = item.findtext("pubDate", default="")

        cleaned_title = clean_title(title)
        description = clean_description(description)

        if not cleaned_title:
            continue

        if not is_recent_news(pub_date):
            print("古いニュースのためスキップ:", cleaned_title)
            continue

        compare_title = normalize_for_compare(cleaned_title)
        if compare_title in used_news:
            print("過去に使用済みのためスキップ:", cleaned_title)
            continue

        compare_description = normalize_for_compare(description)
        if compare_title in seen_titles:
            continue
        seen_titles.add(compare_title)

        if description and compare_title in compare_description:
            description = ""

        combined_text = cleaned_title + " " + description

        if not any(word in combined_text for word in kansai_words):
            continue

        has_core = any(w in combined_text for w in SAUNA_CORE_WORDS)
        bath_context_words = ["銭湯", "スーパー銭湯", "温浴施設", "浴場", "岩盤浴"]
        has_bath_context = any(w in combined_text for w in bath_context_words)

        if not has_core and not has_bath_context:
            print("サウナ・銭湯文脈が弱いためスキップ:", cleaned_title)
            continue

        if (
            "熱波" in combined_text
            and "サウナ" not in combined_text
            and "ロウリュ" not in combined_text
            and "アウフグース" not in combined_text
            and "熱波師" not in combined_text
        ):
            print("天気系の熱波っぽいためスキップ:", cleaned_title)
            continue

        matched_exclude = [w for w in EXCLUDE_WORDS if w in combined_text]
        if matched_exclude:
            print(f"除外ワード {matched_exclude} 該当のためスキップ:", cleaned_title)
            continue

        category = detect_category(combined_text)
        area = detect_area(combined_text)
        score = calc_score(combined_text, category, area)
        features = extract_features(combined_text)
        facility = extract_facility_name(combined_text)

        news_candidates.append({
            "title": cleaned_title,
            "description": description,
            "combined_text": combined_text,  # テーマ判定用
            "link": link,
            "pub_date": pub_date,
            "category": category,
            "area": area,
            "features": features,
            "facility": facility,
            "score": score,
        })

    print(f"フィルタ後の候補数: {len(news_candidates)}件")

    # ── テーマ決定 ──
    today_theme, theme_index = get_today_theme(news_candidates, used_theme_data)

    if today_theme is None:
        print("今日は投稿できるニュースがありませんでした")
        return

    # ── テーマで絞り込み → スコア順 ──
    theme_matched = filter_by_theme(news_candidates, today_theme)
    theme_matched.sort(key=lambda x: x["score"], reverse=True)

    # スコア上位グループ（最大8件）をシャッフルして、毎回違う記事が選ばれやすくする。
    # スコアの高い良記事の中で順番をランダム化するので、質は保ちつつ被りを減らせる。
    top_pool = theme_matched[:8]
    random.shuffle(top_pool)
    theme_matched = top_pool + theme_matched[8:]
    selected_news = theme_matched[:MAX_SELECTED_NEWS]

    print(f"テーマ【{today_theme}】でマッチ: {len(theme_matched)}件 → 選定: {len(selected_news)}件")

    # ── 3件未満なら補完 ──
    if len(selected_news) < MAX_SELECTED_NEWS:
        print(f"{MAX_SELECTED_NEWS - len(selected_news)}件不足のため補完します")
        selected_news = fill_with_fallback(selected_news, news_candidates, today_theme)

    # ── used_news / used_theme 更新 ──
    for news in selected_news:
        normalized_title = normalize_for_compare(news["title"])
        if normalized_title not in used_news:
            used_news.append(normalized_title)
    used_news = used_news[-100:]
    save_used_news(used_news)
    save_used_theme(theme_index)

    print(f"最終選定数: {len(selected_news)}件")
    print(f"テーマインデックスを {theme_index}（{today_theme}）に保存しました")
    print("本文取得 → Gemini処理を開始します")
    init_gemini()

    posts = []
    caption_sources = []

    for index, news in enumerate(selected_news, start=1):
        title = news["title"]
        description = news["description"]
        category = news["category"]
        area = news["area"]
        facility = news.get("facility", "")

        print(f"{index}件目 本文取得中: {title}")
        body = fetch_article_body(news["link"])

        if not body and description:
            print("  本文なし。RSS descriptionを本文代わりに使用")
            body = description

        print(f"{index}件目 Gemini処理中: {title}")
        short_title, summary = generate_gemini_content(
            title, description,
            body=body,
            facility=facility,
            area=area,
            category=category,
            theme=today_theme,
        )

        posts.append({
            "index": index,
            "short_title": short_title,
            "summary": summary,
            "title": title,
            "description": description,
            "category": category,
            "area": area,
            "features": news.get("features", []),
            "facility": news.get("facility", ""),
            "link": news["link"],
            "theme": today_theme,
        })

        caption_sources.append({
            "index": index,
            "title": title,
            "summary": summary,
            "description": description,
            "link": news["link"],
            "theme": today_theme,
        })

        time.sleep(1)

    # posts.txt 出力
    with open(POSTS_FILE, "w", encoding="utf-8") as file:
        for post in posts:
            file.write("====================\n")
            file.write(f"関西銭湯速報【{post['theme']}】\n")
            file.write(f"{post['short_title']}\n")

    # caption_source.txt 出力
    with open(CAPTION_SOURCE_FILE, "w", encoding="utf-8") as file:
        for post in posts:
            file.write("====================\n")
            file.write(f"テーマ:\n{post['theme']}\n\n")
            file.write(f"タイトル:\n{post['title']}\n\n")
            file.write(f"説明:\n{post['summary']}\n\n")
            file.write(f"カテゴリ:\n{post['category']}\n\n")
            file.write(f"地域:\n{post['area']}\n\n")
            file.write(f"施設名:\n{post['facility']}\n\n")
            file.write("特徴:\n")
            file.write("、".join(post["features"]))
            file.write("\n\n")
            if post["link"]:
                file.write(f"URL:\n{post['link']}\n\n")

    print("完了")
    print(f"{POSTS_FILE} を生成しました")
    print(f"{CAPTION_SOURCE_FILE} を生成しました")


if __name__ == "__main__":
    main()
