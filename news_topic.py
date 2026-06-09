import os
import re
import html
import json
import time
import random
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

# =========================
# 設定
# =========================

USE_GEMINI = True
GEMINI_MODEL = "gemini-2.5-flash"

POSTS_FILE = "posts_topic.txt"
CAPTION_SOURCE_FILE = "caption_source_topic.txt"
USED_NEWS_FILE = "used_news_topic.json"
USED_THEME_FILE = "used_theme_topic.json"

MAX_NEWS_AGE_DAYS = 60
BODY_MIN_CHARS = 400  # これ未満はスキップ寄り

# =========================
# テーマ定義
# =========================

THEME_DEFINITIONS = {
    "健康・研究":     ["研究", "調査", "効果", "睡眠", "血流", "健康", "医学", "科学", "論文", "データ"],
    "海外トピックス": ["フィンランド", "海外", "世界", "ドイツ", "北欧", "国際", "エストニア"],
    "トレンド":       ["流行", "人気", "急増", "Z世代", "若者", "女性サウナー", "サウナー", "ブーム"],
    "芸能・メディア": ["芸能人", "タレント", "俳優", "アーティスト", "テレビ", "雑誌", "インタビュー"],
    "グルメ":         ["サ飯", "サウナ飯", "グルメ", "食事", "レストラン", "ランキング", "飲食", "カフェ"],
    "グッズ":         ["サウナハット", "サウナポンチョ", "テントサウナ", "グッズ", "アイテム", "ギア"],
    "文化・歴史":     ["銭湯文化", "歴史", "文化", "条例", "法律", "制度", "保存", "継承"],
    "女性サウナー":   ["女性専用", "女性サウナー", "レディース", "女子サウナ", "女性向け", "女子"],
}

THEME_ORDER = [
    "健康・研究",
    "海外トピックス",
    "トレンド",
    "グルメ",
    "芸能・メディア",
    "文化・歴史",
    "グッズ",
    "女性サウナー",
]

FALLBACK_THEME_ORDER = ["健康・研究", "トレンド", "文化・歴史", "グルメ"]

SAUNA_WORDS = [
    "サウナ", "ロウリュ", "アウフグース", "水風呂", "外気浴", "ととのう",
    "熱波", "サウナー", "銭湯", "岩盤浴", "温浴",
]

EXCLUDE_WORDS = [
    "不動産", "株価", "IR情報", "決算", "有価証券",
    "政治", "選挙", "事件", "事故", "火災",
]

# 2択コメント誘導のテンプレ（テーマ別・骨組み）
# Geminiがこれを参考に記事内容に合わせて微調整する
QUESTION_TEMPLATES = {
    "健康・研究":     [
        "サウナ後すぐ寝る派？\n少し空ける派？\nあなたはどっち？",
        "週1〜2回派？\n週3回以上派？\nあなたはどっち？",
        "高温短時間派？\n低温長時間派？\nあなたはどっち？",
    ],
    "海外トピックス": [
        "本場ロウリュ派？\n日本式サウナ派？\nあなたはどっち？",
        "海外サウナ派？\n国内サウナ派？\nあなたはどっち？",
        "フィンランド式派？\n日本スーパー銭湯派？\nあなたはどっち？",
    ],
    "トレンド":       [
        "黙浴派？\nTVあり派？\nあなたはどっち？",
        "朝ウナ派？\n夜サウナ派？\nあなたはどっち？",
        "ひとりサウナ派？\n友達と派？\nあなたはどっち？",
    ],
    "グルメ":         [
        "オロポ派？\nコーヒー牛乳派？\nあなたはどっち？",
        "サ飯こだわる派？\nなんでもいい派？\nあなたはどっち？",
    ],
    "芸能・メディア": [
        "サウナ番組\nチェックしてる派？\nノーチェック派？",
        "サウナ情報\nSNS派？\n口コミ派？\nあなたはどっち？",
    ],
    "文化・歴史":     [
        "レトロ銭湯派？\nきれいな新施設派？\nあなたはどっち？",
        "昔ながらの銭湯派？\nスーパー銭湯派？\nあなたはどっち？",
    ],
    "グッズ":         [
        "サウナハット\n使う派？\n使わない派？\nあなたはどっち？",
        "グッズこだわる派？\n手ぶら派？\nあなたはどっち？",
    ],
    "女性サウナー":   [
        "ひとりサウナ派？\n友達と派？\nあなたはどっち？",
        "女性専用派？\n混合施設派？\nあなたはどっち？",
    ],
}


# =========================
# Gemini初期化
# =========================

gemini_client = None

def init_gemini():
    global gemini_client, USE_GEMINI
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("GEMINI_API_KEY が未設定です。Gemini要約は使いません。")
        USE_GEMINI = False
        return
    try:
        from google import genai
        gemini_client = genai.Client(api_key=api_key)
        print("Gemini初期化OK")
    except Exception as e:
        print("Gemini初期化失敗:", e)
        USE_GEMINI = False


# =========================
# ユーティリティ
# =========================

def clean_title(title):
    title = html.unescape(title)
    title = re.sub(r'\s+', ' ', title).strip()
    return title

def clean_description(description):
    if not description:
        return ""
    description = html.unescape(description)
    description = re.sub(r'<[^>]+>', '', description)
    description = re.sub(r'\s+', ' ', description).strip()
    return description

def normalize_for_compare(text):
    text = re.sub(r'[\s　]+', '', text)
    return text.lower()

def is_similar_title(title, seen_titles, threshold=0.7):
    t1 = normalize_for_compare(title)
    for seen in seen_titles:
        t2 = normalize_for_compare(seen)
        shorter = min(len(t1), len(t2))
        if shorter == 0:
            continue
        common = sum(c in t2 for c in t1)
        if common / shorter >= threshold:
            return True
    return False

def is_recent_news(pub_date_str):
    if not pub_date_str:
        return True
    try:
        pub_dt = parsedate_to_datetime(pub_date_str)
        now = datetime.now(timezone.utc)
        return (now - pub_dt).days <= MAX_NEWS_AGE_DAYS
    except Exception:
        return True

def detect_area(text):
    area_map = {
        "大阪": "大阪", "京都": "京都", "兵庫": "兵庫", "神戸": "兵庫",
        "奈良": "奈良", "滋賀": "滋賀", "和歌山": "和歌山",
    }
    for word, area in area_map.items():
        if word in text:
            return area
    return ""

def detect_category(text):
    for theme, keywords in THEME_DEFINITIONS.items():
        if any(kw in text for kw in keywords):
            return theme
    return "その他"

def calc_score(text, category, area):
    score = 0
    for word in SAUNA_WORDS:
        if word in text:
            score += 5
    if area:
        score += 10
    return score


# =========================
# テーマローテーション
# =========================

def load_used_theme():
    if os.path.exists(USED_THEME_FILE):
        try:
            with open(USED_THEME_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"last_index": -1}

def save_used_theme(index):
    with open(USED_THEME_FILE, "w", encoding="utf-8") as f:
        json.dump({"last_index": index}, f, ensure_ascii=False)

def get_today_theme(candidates, used_theme_data):
    # 候補記事が存在するテーマだけを集める
    available = []
    for index, theme in enumerate(THEME_ORDER):
        keywords = THEME_DEFINITIONS[theme]
        matched = [n for n in candidates if any(kw in n["combined_text"] for kw in keywords)]
        if matched:
            available.append((theme, index, len(matched)))

    if available:
        # 候補のあるテーマからランダムに選ぶ（毎回違うテーマになりやすくする）
        theme, index, count = random.choice(available)
        print(f"テーマ決定: {theme}（マッチ{count}件・ランダム選択）")
        return theme, index

    # どのテーマにも候補がなければ従来どおり順番送り
    last_index = used_theme_data.get("last_index", -1)
    index = (last_index + 1) % len(THEME_ORDER)
    return THEME_ORDER[index], index


# =========================
# used_news 管理
# =========================

def load_used_news():
    if os.path.exists(USED_NEWS_FILE):
        try:
            with open(USED_NEWS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []

def save_used_news(used_news):
    with open(USED_NEWS_FILE, "w", encoding="utf-8") as f:
        json.dump(used_news, f, ensure_ascii=False, indent=2)


def used_titles_set(used_news):
    """
    used_news の各要素から「正規化済みタイトル文字列」の集合を作る。
    旧フォーマット（文字列）・新フォーマット（{"title":..,"date":..}）どちらにも対応。
    重複チェックはこの集合に対して行う。
    """
    titles = set()
    for item in used_news:
        if isinstance(item, str):
            titles.add(item)
        elif isinstance(item, dict):
            titles.add(item.get("title", ""))
    return titles


# =========================
# テーマフィルタ・補完
# =========================

def filter_by_theme(candidates, theme):
    keywords = THEME_DEFINITIONS.get(theme, [])
    return [n for n in candidates if any(kw in n["combined_text"] for kw in keywords)]

def fill_with_fallback(selected, candidates, theme):
    selected_titles = {n["title"] for n in selected}
    for fallback_theme in FALLBACK_THEME_ORDER:
        if fallback_theme == theme or len(selected) >= 1:
            break
        keywords = THEME_DEFINITIONS[fallback_theme]
        fallback_candidates = [
            n for n in candidates
            if any(kw in n["combined_text"] for kw in keywords)
            and n["title"] not in selected_titles
        ]
        fallback_candidates.sort(key=lambda x: x["score"], reverse=True)
        for news in fallback_candidates[:1]:
            selected.append(news)
            selected_titles.add(news["title"])
            print(f"補完テーマ【{fallback_theme}】から追加: {news['title']}")
    return selected


# =========================
# 本文取得（可視化ログ付き）
# =========================

def extract_source_domain(title):
    """タイトル末尾の「- サイト名」からドメインを推定する"""
    match = re.search(r' - ([a-zA-Z0-9\-\.]+\.[a-zA-Z]{2,})\s*$', title)
    if match:
        return match.group(1)
    return ""


def resolve_via_rss_search(title, domain):
    """タイトル＋ドメインでGoogleニュースRSSを再検索して元URLを取得する"""
    # タイトルからサイト名部分を除去してキーワード化
    keyword = re.sub(r' - [^-]+$', '', title).strip()
    keyword = keyword[:50]  # 長すぎるとヒットしない
    query = f"{keyword} site:{domain}"
    encoded = quote(query)
    rss_url = f"https://news.google.com/rss/search?q={encoded}&hl=ja&gl=JP&ceid=JP:ja"
    try:
        response = requests.get(rss_url, timeout=10)
        root = ET.fromstring(response.content)
        items = root.findall(".//item")
        for item in items[:3]:
            link = item.findtext("link", "")
            if link and "news.google.com" in link:
                # リダイレクト解決を試みる
                resolved = resolve_google_news_url(link)
                if resolved and domain in resolved:
                    return resolved
    except Exception as e:
        print(f"  RSS再検索エラー: {e}")
    return ""


def resolve_google_news_url(url):
    """GoogleニュースURLを実際の記事URLに解決する。
    まずgooglenewsdecoderでデコード、ダメならリダイレクトを試す"""
    # 1. googlenewsdecoderで実URLをデコード
    if "news.google.com" in url:
        try:
            from googlenewsdecoder import gnewsdecoder
            result = gnewsdecoder(url, interval=1)
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
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        response = requests.get(url, timeout=10, allow_redirects=True, headers=headers)
        return response.url
    except Exception as e:
        print(f"  URL解決エラー: {e}")
        return url


def fetch_article_body(url, title=""):
    print("  [本文取得]")
    print(f"  元URL: {url[:100]}")

    real_url = resolve_google_news_url(url)
    print(f"  解決後URL: {real_url[:100]}")

    # URL解決失敗 → タイトルのドメインでRSS再検索
    if not real_url and title:
        domain = extract_source_domain(title)
        if domain:
            print(f"  RSS再検索を試みます（ドメイン: {domain}）")
            real_url = resolve_via_rss_search(title, domain)
            if real_url:
                print(f"  RSS再検索成功: {real_url[:60]}")

    # real_urlは必ずURLが入る（resolve_google_news_url が元URLをフォールバックで返すため）

    # 1. trafilatura
    try:
        import trafilatura
        downloaded = trafilatura.fetch_url(real_url)
        if downloaded:
            text = trafilatura.extract(downloaded)
            chars = len(text) if text else 0
            print(f"  trafilatura: {chars}文字")
            if text and chars > 100:
                print(f"  採用: trafilatura")
                return text[:3000], "trafilatura"
    except Exception as e:
        print(f"  trafilatura: エラー ({e})")

    # 2. newspaper3k
    try:
        from newspaper import Article
        article = Article(real_url, language='ja')
        article.download()
        article.parse()
        chars = len(article.text) if article.text else 0
        print(f"  newspaper3k: {chars}文字")
        if article.text and chars > 100:
            print(f"  採用: newspaper3k")
            return article.text[:3000], "newspaper3k"
    except Exception as e:
        print(f"  newspaper3k: エラー ({e})")

    # 3. JSON-LD → BeautifulSoup → meta description（本文優先・共通リクエスト）
    print(f"  meta/BS取得先URL: {real_url[:80]}")
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        response = requests.get(real_url, timeout=10, headers=headers)
        response.encoding = response.apparent_encoding or "utf-8"
        soup = BeautifulSoup(response.text, "html.parser")

        # JSON-LD articleBody（@graph対応版）← 本文が長いので最優先
        jsonld_body = extract_jsonld_article_body(soup)
        chars = len(jsonld_body)
        print(f"  JSON-LD articleBody: {chars}文字")
        if chars > 100:
            print(f"  採用: JSON-LD")
            return jsonld_body[:3000], "jsonld"

        # pタグ
        paragraphs = soup.find_all("p")
        text = " ".join(p.get_text() for p in paragraphs[:10])
        chars = len(text)
        print(f"  BeautifulSoup pタグ: {chars}文字")
        if chars > 100:
            print(f"  採用: BeautifulSoup")
            return text[:3000], "beautifulsoup"

        # meta description（最後の砦・短いので最後に回す）
        meta = (
            soup.find("meta", attrs={"property": "og:description"}) or
            soup.find("meta", attrs={"name": "description"}) or
            soup.find("meta", attrs={"name": "twitter:description"})
        )
        if meta and meta.get("content"):
            text = meta["content"]
            chars = len(text)
            print(f"  meta description: {chars}文字")
            if chars >= 50:
                print(f"  採用: meta description")
                return text[:500], "meta"
    except Exception as e:
        print(f"  BeautifulSoup/meta: エラー ({e})")

    print("  全手段失敗")
    return "", "failed"


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


# =========================
# Gemini処理
# =========================

def call_gemini_with_retry(prompt, retries=3, wait_seconds=10):
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

def make_fallback_slides(theme):
    """Gemini失敗時のフォールバック（二択形式統一）"""
    templates = {
        "健康・研究":     ("サウナ後は\n睡眠が深くなる？", "研究で分かった\n意外な効果", "高温短時間派？\n低温長時間派？\nあなたはどっち？"),
        "海外トピックス": ("本場フィンランド\nどう違う？", "日本式 vs 海外式\n何が変わる？", "本場ロウリュ派？\n日本式サウナ派？\nあなたはどっち？"),
        "トレンド":       ("サウナブーム\nあなたは何年目？", "急増するサウナー\nきっかけは？", "黙浴派？\nTVあり派？\nあなたはどっち？"),
        "グルメ":         ("サ飯\n何食べてる？", "サウナ後の一杯\n何が最高？", "オロポ派？\nコーヒー牛乳派？\nあなたはどっち？"),
        "芸能・メディア": ("芸能人も\nサウナにハマり中", "メディアで話題\nのサウナ事情", "サウナ情報\nSNS派？口コミ派？\nあなたはどっち？"),
        "文化・歴史":     ("銭湯文化\n消えていくのか？", "残したい\n日本の銭湯文化", "レトロ銭湯派？\n新しい施設派？\nあなたはどっち？"),
        "グッズ":         ("サウナハット\n使ってる？", "持ち込みグッズ\n何が正解？", "グッズこだわる派？\n手ぶら派？\nあなたはどっち？"),
        "女性サウナー":   ("女性サウナー\n急増のワケ", "女性向け施設\n何が違う？", "ひとりサウナ派？\n友達と派？\nあなたはどっち？"),
    }
    s1, s2, s3 = templates.get(theme, ("サウナ話題\n最新情報", "知っておきたい\nサウナのこと", "黙浴派？\nTVあり派？\nあなたはどっち？"))
    return s1, s2, s3

def generate_gemini_content(title, description, body="", category="", theme=None):
    """slide1/2/3タイトルとsummaryを1回のGemini呼び出しで生成"""
    theme_hint = theme or category

    if not USE_GEMINI or not gemini_client:
        s1, s2, s3 = make_fallback_slides(theme_hint)
        summary = description[:400] if description else title
        return s1, s2, s3, summary

    # 2択コメント誘導の例
    import random
    question_examples = QUESTION_TEMPLATES.get(theme_hint, ["あなたはどっち？\nコメントで教えて"])
    question_example = random.choice(question_examples)

    prompt = f"""
あなたはTikTok用の「サウナ話題」編集者です。

1つの話題を3枚のスライドで深掘りする投稿を作ります。
以下のJSONだけを返してください。前置き・説明・コードブロックは禁止です。

{{
  "slide1_title": "1枚目",
  "slide2_title": "2枚目",
  "slide3_question": "3枚目",
  "summary": "caption用要約"
}}

【slide1_title：フック】
TikTokは0.5秒勝負。「気になる」を最優先にする。
・疑問形・数字・意外性・対立のどれかを必ず入れる
・抽象的なタイトル禁止
・1〜2行、改行OK
悪い例: サウナと健康\\n最新研究
良い例: サウナ後は\\n睡眠が深くなる？
良い例: 週3回サウナで\\n何が変わる？
良い例: 日本式 vs 海外式\\nどっちが効く？

【slide2_title：答え・補足】
slide1の流れを受けて、具体的な内容を伝える。
・数字・研究結果・事例があれば必ず入れる
・1〜2行、改行OK
例: 研究では\\n深睡眠が増加した例も
例: フィンランドでは\\n週2回が標準

【slide3_question：二択コメント誘導】
正解がない問いで、コメントしたくなる・派閥が分かれる内容にする。
・必ず「A派？\\nB派？\\nあなたはどっち？」の3行形式にする
・単なる質問だけは禁止（「〜したい？」「〜ある？」はNG）
・AとBは対立する選択肢にする（どちらが正解かわからないもの）
・改行OK
・参考テンプレ（記事内容に合わせて微調整してよい）:
{question_example}
良い例: 黙浴派？\\nTVあり派？\\nあなたはどっち？
良い例: 朝ウナ派？\\n夜サウナ派？\\nあなたはどっち？
良い例: シングル派？\\n17度派？\\nあなたはどっち？
悪い例: 海外サウナ\\n行ってみたい？　←質問だけでNG
悪い例: サウナ好き？\\nコメントで教えて　←対立がなくNG

【summary】
・250〜400文字、ですます調
・1文目で話題を伝える
・数字・具体例を入れる
・本文にないことは書かない
・絵文字・ハッシュタグなし

【入力情報】
テーマ: {theme_hint}

タイトル:
{title}

説明:
{description}

本文:
{body[:3000]}
"""

    result = call_gemini_with_retry(prompt)

    if not result:
        print("Gemini生成失敗。フォールバックに切り替え")
        s1, s2, s3 = make_fallback_slides(theme_hint)
        summary = description[:400] if description else title
        return s1, s2, s3, summary

    try:
        result = result.strip().replace("```json", "").replace("```", "").strip()
        data = json.loads(result)

        s1 = data.get("slide1_title", "").strip()
        s2 = data.get("slide2_title", "").strip()
        s3 = data.get("slide3_question", "").strip()
        summary = data.get("summary", "").strip()

        ng_words = ["はい", "承知", "以下", "提案"]
        fb1, fb2, fb3 = make_fallback_slides(theme_hint)
        if not s1 or any(ng in s1 for ng in ng_words):
            s1 = fb1
        if not s2 or any(ng in s2 for ng in ng_words):
            s2 = fb2
        if not s3 or any(ng in s3 for ng in ng_words):
            s3 = fb3
        if not summary:
            summary = description[:400] if description else title

        return s1, s2, s3, summary

    except Exception as e:
        print("JSONパース失敗:", e)
        s1, s2, s3 = make_fallback_slides(theme_hint)
        summary = description[:400] if description else title
        return s1, s2, s3, summary


# =========================
# RSS取得
# =========================

def fetch_google_news():
    NEWS_QUERIES = [
        "サウナ 健康", "サウナ 研究", "サウナ 効果", "サウナ 睡眠",
        "サウナ 海外", "フィンランド サウナ", "サウナ 世界",
        "サ飯", "サウナ グルメ", "サウナ ランキング",
        "サウナ トレンド", "サウナ 若者", "サウナ 女性",
        "サウナ グッズ", "サウナハット", "テントサウナ",
        "銭湯 文化", "銭湯 歴史", "サウナ 条例",
        "サウナ 芸能人", "サウナ メディア",
    ]
    all_items = []
    rss_debug_done = False
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
            if not rss_debug_done and items:
                dbg = items[0]
                print("[RSSデバッグ] 最初のアイテム構造:")
                print(f"  link: {dbg.findtext('link', '')[:120]}")
                print(f"  source: {dbg.findtext('source', '')}")
                rss_debug_done = True
            all_items.extend(items)
            time.sleep(0.5)
        except Exception as e:
            print(f"取得失敗: {query}", e)
    return all_items


# =========================
# メイン処理
# =========================

def main():
    print("ニュース取得開始（話題系）")

    open(POSTS_FILE, "w", encoding="utf-8").close()
    open(CAPTION_SOURCE_FILE, "w", encoding="utf-8").close()

    items = fetch_google_news()
    used_news = load_used_news()
    used_news_titles = used_titles_set(used_news)
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
            continue
        compare_title = normalize_for_compare(cleaned_title)
        if compare_title in used_news_titles:
            continue
        if is_similar_title(cleaned_title, seen_titles):
            continue

        seen_titles.add(cleaned_title)
        combined_text = cleaned_title + " " + description

        if not any(word in combined_text for word in SAUNA_WORDS):
            continue
        if any(w in combined_text for w in EXCLUDE_WORDS):
            continue

        category = detect_category(combined_text)
        area = detect_area(combined_text)
        score = calc_score(combined_text, category, area)

        news_candidates.append({
            "title": cleaned_title,
            "description": description,
            "combined_text": combined_text,
            "link": link,
            "pub_date": pub_date,
            "category": category,
            "area": area,
            "score": score,
        })

    print(f"候補: {len(news_candidates)}件")

    if not news_candidates:
        print("候補ニュースが0件でした")
        return

    today_theme, theme_index = get_today_theme(news_candidates, used_theme_data)

    theme_matched = filter_by_theme(news_candidates, today_theme)
    theme_matched.sort(key=lambda x: x["score"], reverse=True)
    if not theme_matched:
        theme_matched = news_candidates
        theme_matched.sort(key=lambda x: x["score"], reverse=True)

    # スコア上位グループ（最大8件）をシャッフルして、毎回違う記事が選ばれやすくする。
    # スコアの高い良記事の中で順番をランダム化するので、質は保ちつつ被りを減らせる。
    top_pool = theme_matched[:8]
    random.shuffle(top_pool)

    # 本文が取れる記事を優先して採用
    selected_news = None
    selected_body = ""
    selected_method = ""

    print("本文取得 → Gemini処理を開始します")
    init_gemini()
    print("本文取得できる候補を探します")

    for candidate in top_pool:
        print(f"本文取得中: {candidate['title']}")
        body, method = fetch_article_body(candidate["link"], title=candidate["title"])

        if len(body) >= BODY_MIN_CHARS and method not in ["meta", "rss_fallback", "failed", "url_failed"]:
            selected_news = candidate
            selected_body = body
            selected_method = method
            print(f"  本文あり記事を採用: {len(body)}文字 / {method}")
            break

        if method == "meta" and not selected_news:
            selected_news = candidate
            selected_body = body
            selected_method = method
            print(f"  meta記事を仮採用: {len(body)}文字 / {method}")

    if not selected_news:
        print("本文もmetaも取れる記事がありませんでした")
        return

    news = selected_news
    title = news["title"]
    description = news["description"]
    category = news["category"]
    area = news.get("area", "")
    theme = today_theme
    body = selected_body if selected_body else description
    method = selected_method

    normalized_title = normalize_for_compare(title)
    if normalized_title not in used_news_titles:
        used_news.append({
            "title": normalized_title,
            "date": datetime.now().strftime("%Y-%m-%d"),
        })
    used_news = used_news[-100:]
    save_used_news(used_news)
    save_used_theme(theme_index)

    print(f"テーマインデックスを {theme_index}（{today_theme}）に保存しました")
    print(f"最終採用: {title[:40]} / {method}")

    print(f"Gemini処理中: {title}")
    slide1, slide2, slide3, summary = generate_gemini_content(
        title=title,
        description=description,
        body=body,
        category=category,
        theme=theme,
    )

    print("完了")

    # posts_topic.txt 出力（3枚分）
    with open(POSTS_FILE, "w", encoding="utf-8") as file:
        # 1枚目：フック
        file.write("====================\n")
        file.write(f"サウナ話題【{theme}】\n")
        file.write(f"{slide1}\n")
        # 2枚目：補足
        file.write("====================\n")
        file.write(f"サウナ話題【{theme}】\n")
        file.write(f"{slide2}\n")
        # 3枚目：コメント誘導
        file.write("====================\n")
        file.write(f"サウナ話題【{theme}】\n")
        file.write(f"{slide3}\n")

    # caption_source_topic.txt 出力
    with open(CAPTION_SOURCE_FILE, "w", encoding="utf-8") as file:
        file.write("====================\n")
        file.write(f"テーマ:\n{theme}\n\n")
        file.write(f"タイトル:\n{title}\n\n")
        file.write(f"説明:\n{summary}\n\n")
        file.write(f"カテゴリ:\n{category}\n\n")
        file.write(f"地域:\n{area}\n\n")
        file.write(f"スライド1:\n{slide1}\n\n")
        file.write(f"スライド2:\n{slide2}\n\n")
        file.write(f"スライド3（質問）:\n{slide3}\n\n")
        file.write(f"本文取得方法:\n{method}\n\n")

    print(f"{POSTS_FILE} を生成しました")
    print(f"{CAPTION_SOURCE_FILE} を生成しました")


if __name__ == "__main__":
    main()
