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
GEMINI_MODEL = "gemini-2.5-flash"

MAX_SELECTED_NEWS = 3

POSTS_FILE = "posts.txt"
CAPTION_SOURCE_FILE = "caption_source.txt"
USED_NEWS_FILE = "used_news.json"
USED_THEME_FILE = "used_theme.json"  # テーマローテーション管理

MAX_NEWS_AGE_DAYS = 120  # これより古いニュースはスキップ


# =========================
# テーマ定義
# =========================

# テーマ名 → 合致キーワード のマッピング
THEME_DEFINITIONS = {
    "アウフグース":       ["アウフグース", "熱波師", "熱波イベント", "ゲスト熱波"],
    "ロウリュ":           ["ロウリュ", "オートロウリュ", "セルフロウリュ"],
    "水風呂":             ["水風呂", "シングル", "深水風呂", "冷水"],
    "外気浴":             ["外気浴", "ととのい", "インフィニティチェア", "ととのいスペース"],
    "イベント":           ["イベント", "フェス", "期間限定", "コラボ", "大会", "熱波師", "ゲスト"],
    "オープン":           ["オープン", "開業", "開店", "新施設", "誕生", "グランドオープン"],
    "リニューアル":       ["リニューアル", "改装", "刷新", "リノベ"],
    "閉店":               ["閉店", "閉館", "廃業", "営業終了", "惜しまれ"],
    "銭湯":               ["銭湯", "公衆浴場", "町の銭湯"],
    "スーパー銭湯":       ["スーパー銭湯", "温浴施設", "スパ銭"],
    "ホテルサウナ":       ["ホテル", "宿泊", "泊まれる", "滞在", "旅館"],
    "女性・カップル向け": ["女子", "女性", "レディース", "カップル", "ペア", "デート", "女性限定", "カップルプラン"],
    "貸切サウナ":         ["貸切", "貸し切り", "個室サウナ", "プライベートサウナ", "完全個室"],
    "温泉旅館":           ["温泉旅館", "温泉宿", "宿泊プラン", "湯宿", "湯治"],
    "岩盤浴":             ["岩盤浴", "ホットヨガ", "温活", "よもぎ蒸し"],
    "朝ウナ":             ["朝ウナ", "早朝", "深夜営業", "24時間", "朝風呂", "モーニングサウナ"],
    "個室サウナ":         ["個室サウナ", "プライベートサウナ", "完全個室", "一人サウナ"],
}

# テーマの順番（ローテーション順）
THEME_ORDER = [
    "オープン",
    "アウフグース",
    "水風呂",
    "イベント",
    "ロウリュ",
    "女性・カップル向け",
    "銭湯",
    "外気浴",
    "貸切サウナ",
    "個室サウナ",
    "ホテルサウナ",
    "温泉旅館",
    "リニューアル",
    "スーパー銭湯",
    "岩盤浴",
    "朝ウナ",
    "閉店",
]

# 補完に使うテーマ優先順（メインテーマで3件揃わなかった場合）
FALLBACK_THEME_ORDER = ["オープン", "リニューアル", "イベント", "アウフグース", "ロウリュ"]


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
    "サウナ", "ロウリュ", "アウフグース", "熱波師",
    "水風呂", "ととのい", "外気浴", "岩盤浴",
]

SAUNA_SUB_WORDS = [
    "銭湯", "スーパー銭湯", "温浴施設", "浴場", "岩盤浴",
]

sauna_words = SAUNA_CORE_WORDS + SAUNA_SUB_WORDS

# 女性・カップル・貸切向けワード（スコア加点用）
AUDIENCE_WORDS = [
    "女子", "女性", "レディース", "カップル", "ペア", "デート",
    "女性限定", "カップルプラン", "貸切", "貸し切り",
    "個室サウナ", "プライベートサウナ", "完全個室",
]

EXCLUDE_WORDS = [
    "不動産", "マンション", "分譲", "建設予定地", "跡地", "土地活用",
    "エステ", "美容", "ネイル", "まつげ", "フィットネス", "ジム", "ヨガ",
    "韓国", "中国", "海外", "タイ", "バリ", "ハワイ", "シンガポール",
    "熱中症", "危険な暑さ", "猛暑日", "酷暑", "気温上昇",
    "カジノ", "競馬", "パチンコ",
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
    keywords = THEME_DEFINITIONS[theme]
    return [n for n in candidates if any(kw in n["combined_text"] for kw in keywords)]


def fill_with_fallback(selected, candidates, theme):
    selected_titles = {n["title"] for n in selected}
    used_facilities = {n.get("facility", "") for n in selected if n.get("facility", "")}

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
            facility = news.get("facility", "")
            if facility and facility in used_facilities:
                print(f"  施設重複スキップ（補完）: {facility}")
                continue
            selected.append(news)
            selected_titles.add(news["title"])
            if facility:
                used_facilities.add(facility)
            print(f"補完テーマ【{fallback_theme}】から追加: {news['title']}")

    if len(selected) < MAX_SELECTED_NEWS:
        remaining = [n for n in candidates if n["title"] not in selected_titles]
        remaining.sort(key=lambda x: x["score"], reverse=True)
        for news in remaining:
            if len(selected) >= MAX_SELECTED_NEWS:
                break
            facility = news.get("facility", "")
            if facility and facility in used_facilities:
                print(f"  施設重複スキップ（スコア補完）: {facility}")
                continue
            selected.append(news)
            selected_titles.add(news["title"])
            if facility:
                used_facilities.add(facility)
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

def is_similar_title(new_title, seen_titles):
    new_norm = normalize_for_compare(new_title)

    for seen in seen_titles:
        seen_norm = normalize_for_compare(seen)

        if not new_norm or not seen_norm:
            continue

        if new_norm in seen_norm or seen_norm in new_norm:
            return True

        common_len = min(len(new_norm), len(seen_norm))
        if common_len >= 18:
            same_count = sum(1 for a, b in zip(new_norm, seen_norm) if a == b)
            if same_count / common_len >= 0.75:
                return True

    return False

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
        "スパワールド", "奈良健康ランド", "大東洋", "ニュージャパン", "白玉温泉",
        "延羽の湯", "神戸サウナ＆スパ", "なにわ健康ランド 湯〜トピア",
        "サウナ&カプセル アムザ", "サウナの梅湯", "五香湯", "ルーマプラザ",
        "スパワールド 世界の大温泉", "空庭温泉 OSAKA BAY TOWER",
        "延羽の湯 鶴橋店", "延羽の湯 羽曳野本店", "アムザ", "堺浜楽天温泉 祥福",
        "天然露天温泉 スパスミノエ", "天然温泉 ひなたの湯", "湯快のゆ 寝屋川店",
        "湯快のゆ 門真店", "湯源郷 太平のゆ 忠岡店", "箕面温泉スパーガーデン",
        "美人湯 祥風苑", "水春 箕面湯元", "水春 松井山手", "水春 鶴見緑地湯元",
        "源気温泉 万博おゆば", "極楽湯 吹田店", "極楽湯 茨木店",
        "虹の湯 大阪狭山店", "虹の湯 二色の浜店", "さらさのゆ",
        "ユーバス 守口店", "ユーバス 堺浜寺店", "蔵前温泉 さらさのゆ",
        "石切温泉 ホテルセイリュウ", "天然温泉 延羽の湯 本店",
        "天然温泉 風の湯 河内長野店", "天然温泉 風の湯 新石切店",
        "犬鳴山温泉 不動口館", "伏尾温泉 不死王閣", "牛滝温泉 四季まつり",
        "くつろぎの郷 湯楽", "花園温泉 sauna kukka", "DESSE", "サウナシャン",
        "カプセル&スパ グランドサウナ心斎橋", "入船温泉", "ヘルシー温泉タテバ",
        "朝日温泉", "辰巳温泉", "姫松温泉", "テルメ龍宮", "船岡温泉", "栄湯",
        "源ヶ橋温泉", "大正湯", "錦温泉", "清水湯", "宝湯", "日の本湯",
        "玉乃湯", "戎湯", "新温泉", "第一敷島湯", "福助温泉", "桜湯",
        "千鳥温泉", "朝日湯", "みどり温泉", "旭温泉", "新朝日温泉", "昭和湯",
        "幸福温泉", "パークプラザ大東洋", "ホテルモントレ ラ・スール大阪 スパ・トリニテ",
        "アートホテル大阪ベイタワー 空庭温泉",
        "リーベルホテル アット ユニバーサル・スタジオ・ジャパン",
        "御堂筋ホテル 天然温泉", "関空温泉ホテルガーデンパレス", "SPA専 太平のゆ",
        "かいづか温泉 ほの字の里", "能勢温泉", "神戸クアハウス", "湯櫻", "水春",
        "空庭温泉", "スパバレイ枚方南", "白山湯 高辻店", "白山湯 六条店",
        "源湯", "玉の湯", "旭湯", "誠の湯", "鴨川湯", "大黒湯", "錦湯", "力湯",
        "仁左衛門の湯", "伏見 力の湯", "天翔の湯 大門", "壬生温泉 はなの湯",
        "上方温泉 一休 京都本館", "さがの温泉 天山の湯", "京都るり渓温泉",
        "スッカマ 源氏の湯", "宇治天然温泉 源氏の湯", "福知山温泉 養老の湯",
        "くらま温泉", "不動温泉", "夕日ヶ浦温泉 花ゆうみ", "天橋立温泉 智恵の湯",
        "比良とぴあ", "ホテルモントレ京都 スパ・トリニテ", "天然温泉 御所の湯",
        "京乃湯", "軍人湯", "山城温泉", "小町湯", "栄盛湯", "若の湯", "明田湯",
        "初音湯", "洛陽湯", "東山湯", "松葉湯", "山城湯", "日の出湯", "五色湯",
        "大宮温泉", "桃山温泉 月見館", "神戸サウナ&スパ",
        "万葉倶楽部 神戸ハーバーランド温泉", "有馬温泉 太閤の湯",
        "有馬温泉 銀の湯", "有馬温泉 金の湯", "潮芦屋温泉 SPA水春",
        "吟湯 湯治聚落", "華の湯", "湯あそびひろば 森温泉", "蓬莱湯",
        "灘温泉 水道筋店", "灘温泉 六甲道店", "灘温泉 篠原南店", "湊山温泉",
        "二宮温泉", "クア武庫川", "えびすの湯 一休", "美健SPA 湯櫻", "寿ノ湯",
        "熊野の郷", "天然温泉 あぐろの湯", "天然温泉 延羽の湯 野天 閑雅山荘",
        "名湯 宝乃湯", "天然温泉 石道", "天然温泉 石道 こやぶ",
        "姫路ゆめさき川温泉 夢乃井", "姫路市休養センター 香寺荘",
        "赤穂温泉 祥吉", "赤穂御崎温泉 鹿久居荘", "洲本温泉 海月館",
        "南あわじ温泉郷", "ホテルニューアワジ", "あまみ温泉 南天苑",
        "東条湖グランド赤坂", "ネスタリゾート神戸 延羽の湯", "天然温泉 湯庵",
        "かすみ・矢田川温泉", "浜坂温泉保養荘", "城崎温泉 一の湯",
        "城崎温泉 御所の湯", "城崎温泉 鴻の湯", "城崎温泉 柳湯",
        "城崎温泉 地蔵湯", "城崎温泉 まんだら湯", "城崎温泉 さとの湯",
        "尼崎センタープール前 みずきの湯", "ユートピア琴浦", "極楽湯 尼崎店",
        "天然温泉 ぷくぷく", "加古川温泉 みとろ荘", "小野温泉 夢の森公園",
        "チムジルバンスパ神戸", "ジェームス山天然温泉 月の湯舟",
        "有馬街道温泉 すずらんの湯", "こうべ花時計温泉", "祇園温泉",
        "湯あそびひろば 柚耶の里", "扇港湯", "橘湯", "第一平和温泉",
        "ゆとなみ社 大箇湯", "松の湯", "湯あそびひろば 芦原温泉", "元町サウナ",
        "幸福湯", "新町温泉", "住吉湯", "有本温泉", "美園湯", "大谷温泉",
        "田辺元湯", "えびす湯", "本町温泉", "築港湯", "初音湯", "中之島温泉",
        "花の湯", "長生湯", "福島湯", "湯川温泉", "きよもん湯", "むろの湯",
        "ゆりの山温泉", "夏山温泉", "女神の湯 アイリスパーク", "丹敷の湯",
        "しみず温泉", "たまゆらの里", "かわべ温泉 きさくの湯", "美山療養温泉館",
        "大西湯", "花園新温泉", "敷島温泉", "梅の湯", "第二阪奈温泉",
        "やはた温泉", "長柄温泉", "天然温泉 吉野桜の湯 御宿 野乃", "かしはらの湯",
        "宮滝温泉 まつや", "洞川温泉 ビジターセンター", "十津川温泉 星の湯",
        "上湯温泉", "下湯温泉", "天の川温泉", "金剛乃湯", "下市温泉 秋津荘",
        "黒滝の湯", "杉の湯", "平成榛原子供のもり公園温泉", "天然温泉たかすみの里",
        "フォレストかみきた薬師湯", "末広温泉", "昭和湯", "此花温泉", "日之出湯",
        "新柏原温泉", "豊中温泉", "錦水湯", "第二寿温泉", "八幡温泉", "千成湯",
        "三宝湯", "福徳温泉", "新森温泉", "菊水温泉", "千鳥温泉", "平和温泉",
        "大福湯", "日の出温泉", "玉出浴場", "栄湯温泉", "生野温泉", "戎湯温泉",
        "宝温泉", "寿楽温泉", "月見湯", "弁天湯", "第二白鶴温泉", "新朝日温泉",
        "衣笠温泉", "大徳寺温泉", "紫野温泉", "加茂湯", "小倉湯", "山城湯",
        "松湯", "銀座湯", "東雲湯", "京極湯", "都湯", "御所湯", "西陣京極湯",
        "長者湯", "若松湯", "島原温泉", "桃山浴場", "龍宮温泉", "柳湯", "亀湯",
        "常磐湯", "井筒湯", "むらさき湯", "稲荷湯", "湯あそびひろば ぶくぶく",
        "浜田温泉 甲子園旭泉の湯", "六甲おとめ塚温泉", "湯あそびひろば 二宮温泉",
        "湯あそびひろば 柴崎温泉", "新開地温泉", "金平湯", "みどり湯", "春日湯",
        "共栄温泉", "寿湯", "千歳湯", "湯の町浴場", "銀水湯", "平和湯",
        "浜乃湯", "高砂湯", "みなと湯", "祇園湯", "奈良プラザホテル",
        "かもきみの湯", "虹の湯 西大和店", "音の花温泉",
        "天然湧出温泉 ゆららの湯 奈良店", "天然湧出温泉 ゆららの湯 押熊店",
        "曽爾高原温泉 お亀の湯", "十津川温泉 庵の湯", "上北山温泉 薬師湯",
        "入之波温泉 山鳩湯", "天川薬湯センター みずはの湯", "洞川温泉センター",
        "吉野温泉元湯", "天然温泉 宝来温泉", "信貴の湯", "大和平群温泉",
        "梅の郷 月ヶ瀬温泉", "平城宮温泉", "天平の湯",
        "スーパー銭湯ユーバス 奈良店", "極楽湯 奈良店", "橿原ぽかぽか温泉",
        "はり温泉らんど", "紀州黒潮温泉", "花山温泉 薬師の湯", "きのくに温泉",
        "天然温泉 ゆの里", "川湯温泉 仙人風呂", "白浜温泉 崎の湯",
        "白浜温泉 牟婁の湯", "白浜温泉 しらすな", "とれとれの湯", "龍神温泉 元湯",
        "丹生ヤマセミ温泉館", "えびね温泉", "かなや明恵峡温泉", "美里の湯 かじか荘",
        "野半の里 蔵乃湯 老鶴館", "ユーバス 和歌山店", "有田川温泉 鮎茶屋",
        "天然温泉 ふくろうの湯", "グランパスinn白浜", "南紀勝浦温泉 ホテル浦島",
        "クアハウス白浜", "椿はなの湯", "高野山温泉 福智院",
        "うめきた温泉 蓮 Wellbeing Park", "神州温泉 あるごの湯",
        "八尾温泉 喜多の湯", "天然温泉 なにわの湯", "湯源郷 太平のゆ なんば",
        "大阪サウナ DESSE", "M's sauna", "IZA SAUNA osaka", "真大阪サウナ",
        "想 ‐SOU- SAUNA", "わがまちサウナ", "天翔SAUNA", "道頓堀サウナ",
        "個室サウナとと 東梅田店", "sauna９", "サウナリゾートオリエンタル神戸",
        "SAUNA & SPA 花の湯 HANAKITA", "パルシェ香りの湯",
        "伊勢本街道 みつえ温泉 姫石の湯", "和み 温もり ふくろうの湯",
        "休暇村紀州加太", "メルキュール京都宮津リゾート＆スパ",
        "神戸 ホテル フルーツ・フラワー", "奈良ロイヤルホテル",
        "サウナリゾートオリエンタル梅田", "サウナリゾートオリエンタルなんば",
        "天然温泉コロナの湯", "四日市温泉 おふろcafe湯守座",
        "光の温泉メディカルサウナ", "ラジウム温泉",
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
    if any(word in text for word in ["閉店", "閉館", "休館", "営業終了"]):
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


def calc_score(text, category, area, facility=""):
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

    audience_hits = sum(1 for w in AUDIENCE_WORDS if w in text)
    score += audience_hits * 8

    for word in ["話題", "人気", "注目", "新", "限定", "イベント"]:
        if word in text:
            score += 3

    # 施設名ありボーナス（TikTokでは施設名があると記憶に残りやすい）
    if facility:
        score += 15

    return score


# =========================
# Geminiなし短文生成
# =========================

def extract_features(text):
    features = []
    feature_words = [
        "天然温泉", "地下1000m", "露天風呂", "天空大温泉", "重曹泉", "高濃度炭酸泉",
        "オートロウリュ", "セルフロウリュ", "ロウリュ", "アウフグース", "熱波", "熱波師",
        "外気浴", "インフィニティチェア", "ととのいスペース", "休憩スペース",
        "水風呂", "深水風呂", "シングル水風呂",
        "貸切サウナ", "個室サウナ", "フィンランドサウナ", "高温サウナ", "塩サウナ", "ミストサウナ",
        "岩盤浴", "宿泊", "ホテル", "リニューアル", "オープン",
        "女性限定", "レディースデー", "カップルプラン", "貸切", "プライベートサウナ",
    ]
    for word in feature_words:
        if word in text and word not in features:
            features.append(word)
    return features


def make_short_title(title, description, category, area, theme=None):
    text = title + " " + description
    facility = extract_facility_name(text)
    features = extract_features(text)
    main_area = area if area != "関西" else ""

    if facility and features:
        return f"{main_area}「{facility}」\n{features[0]}に注目"
    if facility:
        return f"{main_area}「{facility}」\nサウナ好きは要チェック"
    if features:
        return f"{main_area}のサウナ情報\n{features[0]}が気になる"
    if category == "新店":
        return f"{main_area}に新サウナ情報\n週末候補かも"
    if category == "リニューアル":
        return f"{main_area}の施設が進化\nリニューアルに注目"
    if category == "閉店・休館":
        return f"{main_area}のサウナ情報\n終了前に要チェック"
    if category == "イベント":
        return f"{main_area}で熱波情報\nサウナ民は注目"
    if category == "外気浴":
        return f"{main_area}で外気浴情報\nこれは整いそう"
    if category == "宿泊サウナ":
        return f"{main_area}で泊まれるサウナ\n週末旅にもよさそう"

    short = title
    if len(short) > 32:
        short = short[:32] + "…"
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
        response = requests.get(google_url, timeout=5, allow_redirects=True)
        return response.url
    except Exception:
        return google_url


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
    print(f"  解決後URL: {real_url[:90]}")
    if not real_url:
        print("  URLリダイレクト未解決 → スキップ")
        return ""

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
あなたはTikTokの関西サウナニュース投稿を作る編集者です。

以下のニュースから、画像用の短い見出しと、TikTok説明文を作ってください。{facility_line}{area_line}{theme_hint}

条件:
- 必ずJSON形式だけで返す
- 前置き、説明、マークダウン、コードブロックは禁止
- 「はい」「承知しました」「以下」などの返事は禁止
- short_title は2行。全体で40文字以内
- short_title は画像に載せるため短く、煽りすぎない
- 地名が分かる場合はshort_titleに入れる
- summary は180〜280文字程度
- summary はですます調で書く
- 読者に話しかける形（〜だよ、〜だね、〜だって、〜だと思うよ）は使わない
- テーマ名やシステムの内部情報は文中に出さない
- 施設名・場所が分かる場合はsummary冒頭に入れる
- 1文目で何のニュースかを伝える
-「魅力」「人気」「提供」「注目」という言葉はなるべく使わない
- 2文目以降で、設備・特徴・開催時期・注目ポイントを具体的に入れる
- 本文やタイトルにないことは書かない
- 宣伝文ではなく、友達に共有するニュース文にする
- 「リラックス」「癒し」「心身」など抽象語を多用しない
- 本文にない雰囲気表現を足さない
- 効能、品質、希少性、人気の理由を推測で書かない
- 「期待されます」「最適です」「ぜひ一度」「～となっています」など広告っぽい表現を避ける
- 絵文字なし
- ハッシュタグなし

返答形式:
{{
  "short_title": "1行目\\n2行目",
  "summary": "180〜280文字の説明文"
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


def generate_gemini_summary(title, description, body="", facility="", area="", category="", theme=None):
    if not USE_GEMINI or not gemini_client:
        return make_python_summary(title, description)

    closing_hint = ""
    if category in ["新店", "リニューアル"]:
        closing_hint = "- 最後に「週末の候補にどうぞ」など軽くひと押しを添える"
    elif category == "閉店・休館":
        closing_hint = "- 閉店・終了の事実を丁寧に伝えて締める。ひと押しは不要"
    elif category == "イベント" or theme in ["アウフグース", "ロウリュ", "イベント"]:
        closing_hint = "- イベントの日程や詳細が分かれば優先して入れる"
    elif theme in ["女性・カップル向け", "貸切サウナ", "ホテルサウナ"]:
        closing_hint = "- ターゲット（女性・カップルなど）に刺さる視点でまとめる"

    facility_line = f"\n施設名: {facility}" if facility else ""
    area_line = f"\n場所: {area}" if area else ""
    body_line = f"\n\n記事本文（参考）:\n{body}" if body else ""

    no_body_hint = ""
    if not body:
        no_body_hint = """
- 記事本文は取得できていないが、タイトル・説明・施設名・特徴から読み取れる情報を最大限活用して250〜380文字になるよう書く
- 施設の特徴・設備・立地・ターゲット層・注目理由などを推測せず、タイトルから読み取れる範囲で肉付けする
"""

    prompt = f"""
あなたはTikTokの関西サウナニュース投稿を作る編集者です。

以下のニュースをTikTokのキャプション（説明文）として自然な日本語でまとめてください。{facility_line}{area_line}

条件:
- 250〜380文字程度
- TikTokの説明文として読みやすくまとめる
- 施設名・場所が分かる場合は冒頭に入れる
- 1文目で「何のニュースか」を伝える
- 2文目以降で、設備・特徴・開催時期・注目ポイントを具体的に入れる
- 本文やタイトルにないことは書かない
- 効能、品質、希少性、人気の理由を推測で書かない
- 「期待されます」「最適です」「ぜひ一度」など広告っぽい表現を避ける
- 「注目です」「チェックしておきたい」などの定型文を多用しない
- 文章は3〜5文くらい
- 絵文字なし
- ハッシュタグなし
{closing_hint}{no_body_hint}

タイトル:
{title}

説明:
{description}{body_line}
"""
    try:
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt
        )
        result = response.text.strip()
        result = re.sub(r"\s+", " ", result)
        return result
    except Exception as e:
        print("Gemini要約失敗。Python要約に切り替え:", e)
        return make_python_summary(title, description)


def generate_gemini_short_title(title, description, category, area, theme=None):
    if not USE_GEMINI or not gemini_client:
        return make_short_title(title, description, category, area, theme)

    theme_hint = f"\n- 今日のテーマは「{theme}」なので、それが伝わる見出しにする" if theme else ""

    prompt = f"""
あなたはTikTokカルーセル画像の見出しを作る編集者です。

以下のニュースを、画像に載せる短い見出しにしてください。

条件:
- 2行に分ける
- 全体で40文字以内
- 絵文字なし
- 煽りすぎない
- サウナ好きが続きを読みたくなる
- 地名が分かる場合は入れる{theme_hint}

タイトル:
{title}

説明:
{description}
"""
    try:
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt
        )
        result = response.text.strip()

        # Geminiの返事文・前置きを除去
        ng_phrases = [
            "はい",
            "承知しました",
            "承知いたしました",
            "以下の見出し案",
            "以下の見出し",
            "ご提案します",
            "提案します",
            "---",
        ]
        for phrase in ng_phrases:
            result = result.replace(phrase, "")

        result = result.strip()
        result = result.replace("。", "")
        result = re.sub(r"\s+", "\n", result)
        lines = [line.strip() for line in result.splitlines() if line.strip()]
        result = "\n".join(lines[:2])

        if len(result.replace("\n", "")) > 40:
            return make_short_title(title, description, category, area, theme)
        return result
    except Exception as e:
        print("Gemini短タイトル失敗。Python短タイトルに切り替え:", e)
        return make_short_title(title, description, category, area, theme)


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
        "関西 サウナ", "大阪 サウナ", "京都 サウナ",
        "兵庫 サウナ", "滋賀 サウナ", "奈良 サウナ", "和歌山 サウナ",
        "関西 銭湯", "関西 スーパー銭湯", "関西 温浴施設",
        "関西 ロウリュ", "関西 外気浴",
        "関西 個室サウナ", "関西 貸切サウナ", "関西 プライベートサウナ",
        "関西 ホテル サウナ", "関西 温泉旅館 サウナ",
        "関西 サウナイベント", "関西 アウフグース",
        "関西 水風呂", "関西 岩盤浴",
        "大阪 個室サウナ", "京都 銭湯 サウナ", "兵庫 温泉 サウナ",
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
        if is_similar_title(cleaned_title, seen_titles):
            print("類似タイトルのためスキップ:", cleaned_title)
            continue

        seen_titles.add(cleaned_title)

        if description and compare_title in compare_description:
            description = ""

        combined_text = cleaned_title + " " + description

        if not any(word in combined_text for word in kansai_words):
            continue

        has_core = any(w in combined_text for w in SAUNA_CORE_WORDS)
        bath_context_words = [
            "銭湯", "スーパー銭湯", "温浴施設", "浴場",
            "岩盤浴","スパ銭","日帰り温泉","大浴場","温泉施設"
        ]

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
        facility = extract_facility_name(combined_text)
        score = calc_score(combined_text, category, area, facility)
        features = extract_features(combined_text)

        news_candidates.append({
            "title": cleaned_title,
            "description": description,
            "combined_text": combined_text,
            "link": link,
            "pub_date": pub_date,
            "category": category,
            "area": area,
            "features": features,
            "facility": facility,
            "score": score,
        })

    print(f"フィルタ後の候補数: {len(news_candidates)}件")

    today_theme, theme_index = get_today_theme(news_candidates, used_theme_data)

    if today_theme is None:
        print("今日は投稿できるニュースがありませんでした")
        return

    theme_matched = filter_by_theme(news_candidates, today_theme)
    theme_matched.sort(key=lambda x: x["score"], reverse=True)

    # スコア上位グループ（最大8件）をシャッフルして、毎回違う記事が選ばれやすくする。
    # スコアの高い良記事の中で順番をランダム化するので、質は保ちつつ被りを減らせる。
    top_pool = theme_matched[:8]
    random.shuffle(top_pool)
    theme_matched = top_pool + theme_matched[8:]

    # 施設名の重複を除いて選定
    selected_news = []
    used_facilities = set()
    for news in theme_matched:
        facility = news.get("facility", "")
        if facility and facility in used_facilities:
            print(f"  施設重複スキップ: {facility}（{news['title'][:30]}）")
            continue
        selected_news.append(news)
        if facility:
            used_facilities.add(facility)
        if len(selected_news) >= MAX_SELECTED_NEWS:
            break

    print(f"テーマ【{today_theme}】でマッチ: {len(theme_matched)}件 → 選定: {len(selected_news)}件")

    if len(selected_news) < MAX_SELECTED_NEWS:
        print(f"{MAX_SELECTED_NEWS - len(selected_news)}件不足のため補完します")
        selected_news = fill_with_fallback(selected_news, news_candidates, today_theme)

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

    with open(POSTS_FILE, "w", encoding="utf-8") as file:
        for post in posts:
            file.write("====================\n")
            file.write(f"関西サウナ速報【{post['theme']}】\n")
            file.write(f"{post['short_title']}\n")

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
