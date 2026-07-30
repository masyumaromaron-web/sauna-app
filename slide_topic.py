import os
import random
from PIL import Image, ImageDraw, ImageFont

# フォントの場所を環境に応じて決める。
# Windows（手元のPC）ならメイリオ、Linux（Render）なら同梱のNotoSansを使う。
def _find_font_path():
    candidates = [
        "C:/Windows/Fonts/meiryo.ttc",                 # Windows（手元）
        "fonts/NotoSansJP-Regular.ttf",                # Render用に同梱するフォント
        "fonts/NotoSansJP-Regular.ttf",                # Render用に同梱するフォント
        "NotoSansJP-Regular.ttf",                      # 一番外側に置いた場合
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",  # Linux標準にあれば
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    # どれも無ければ最初の候補を返す（エラーメッセージで気づけるように）
    return candidates[0]

FONT_PATH = _find_font_path()
BACKGROUND_DIR = "backgrounds"

title_font   = ImageFont.truetype(FONT_PATH, 50)
tag_font     = ImageFont.truetype(FONT_PATH, 42)

with open("posts_topic.txt", "r", encoding="utf-8") as f:
    content = f.read()

posts = content.split("====================")
posts = [p.strip() for p in posts if p.strip()]

SLIDE_STYLES = [
    # 1枚目：フック → アクセント色（深いサウナグリーン）
    {"card_fill": (230, 245, 235, 225), "main_fill": (20, 80, 50),   "title_fill": "black"},
    # 2枚目：補足 → 落ち着いたグレー系
    {"card_fill": (240, 240, 235, 220), "main_fill": (40, 40, 40),   "title_fill": "black"},
    # 3枚目：コメント誘導 → 温かみのあるクリーム
    {"card_fill": (255, 248, 225, 225), "main_fill": (120, 60, 0),   "title_fill": "black"},
]

# --- 折り返し ---------------------------------------------------------
# もとは textwrap.wrap で機械的に10文字ずつ折っていたため、「？」が行頭に
# 落ちたり単語が途中で割れたりしていた。禁則を守り、なるべく文節で折る。

# 行頭に置かない文字（句読点・閉じ括弧・伸ばし棒・小書き仮名）
NO_LINE_START = (
    "、。，．,.!?！？）)]｝」』】〕〉》”’…・ー"
    "ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮヵヶ"
)
# 行末に置かない文字（開き括弧）
NO_LINE_END = "（([｛「『【〔〈《“‘"
# この文字の直後は意味の切れ目になりやすい
BREAK_AFTER_CHARS = "、。！？!?…・）」』"
# 文節の切れ目になりやすい助詞。直後で折ると読みやすい
BREAK_AFTER_WORDS = (
    "でも", "では", "には", "から", "まで", "より", "ので", "のに", "けど", "って",
    "は", "が", "を", "に", "で", "と", "も", "や", "へ", "の", "ね", "よ",
)


def _is_hiragana(ch):
    return "ぁ" <= ch <= "ゟ"


def _is_break_point(line, pos):
    """line[:pos] と line[pos:] に分けてよいかどうか。"""
    if pos <= 0 or pos >= len(line):
        return False
    if line[pos] in NO_LINE_START:
        return False
    if line[pos - 1] in NO_LINE_END:
        return False
    if line[pos - 1] in BREAK_AFTER_CHARS:
        return True
    for word in BREAK_AFTER_WORDS:
        if line[:pos].endswith(word):
            # 次が平仮名なら、助詞ではなく長い語の途中である可能性が高い。
            # 「という」を「と／いう」、「一部です」を「一部で／す」と
            # 割ってしまうのを防ぐ。
            if _is_hiragana(line[pos]):
                return False
            return True
    return False


def _wrap_one_line(line, width):
    """1行を折り返す。禁則を守りつつ、なるべく意味の切れ目で折る。"""
    out = []
    start = 0
    # 意味の切れ目を探しに戻る範囲。広げすぎると行が短くなりすぎる
    lookback = max(1, width // 2)

    while len(line) - start > width:
        cut = start + width

        # 行頭に来てはいけない文字は、前の行に押し込む
        while cut < len(line) and line[cut] in NO_LINE_START:
            cut += 1
        # 行末に来てはいけない文字は、次の行に送る
        while cut > start + 1 and line[cut - 1] in NO_LINE_END:
            cut -= 1

        # 禁則で伸ばしていないときだけ、意味の切れ目を探しに戻る。
        # 句読点の切れ目を優先し、無ければ助詞の切れ目を使う。
        if cut - start <= width:
            found = None
            for back in range(lookback + 1):
                pos = cut - back
                if (start < pos < len(line)
                        and line[pos - 1] in BREAK_AFTER_CHARS
                        and line[pos] not in NO_LINE_START):
                    found = pos
                    break
            if found is None:
                for back in range(lookback + 1):
                    if _is_break_point(line, cut - back):
                        found = cut - back
                        break
            if found:
                cut = found

        if cut <= start:
            cut = start + width
        out.append(line[start:cut])
        start = cut

    if start < len(line):
        rest = line[start:]
        # 1文字だけ次の行に残るのは見苦しい。禁則文字なら前の行へ寄せ、
        # そうでなければ前の行から1文字もらって2文字にする。
        if out and len(rest) == 1:
            if rest in NO_LINE_START:
                out[-1] += rest
                rest = ""
            elif len(out[-1]) > 2:
                rest = out[-1][-1] + rest
                out[-1] = out[-1][:-1]
        if rest:
            out.append(rest)
    return out


def wrap_japanese_text(text, width=10):
    lines = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        lines.extend(_wrap_one_line(line, width))
    return "\n".join(lines)

def select_background(text, slide_index):
    # フィンランド・海外・テントサウナ系
    if "フィンランド" in text or "海外" in text or "世界" in text:
        return random.choice([
            f"{BACKGROUND_DIR}/finland.png",
            f"{BACKGROUND_DIR}/tento.png",
            f"{BACKGROUND_DIR}/nature.png",
        ])
    # サ飯・グルメ系
    if "サ飯" in text or "グルメ" in text or "飯" in text:
        return f"{BACKGROUND_DIR}/sameshi.png"
    # 個室・貸切・スパ系
    if "個室" in text or "貸切" in text or "プライベート" in text or "スパ" in text:
        return f"{BACKGROUND_DIR}/privatesauna.png"
    # 夜・朝ウナ系
    if "夜" in text or "朝ウナ" in text or "深夜" in text:
        return f"{BACKGROUND_DIR}/night_sauna.png"
    # 研究・健康系
    if "研究" in text or "健康" in text:
        return f"{BACKGROUND_DIR}/sauna_room.png"
    # 銭湯・文化系
    if "銭湯" in text or "文化" in text:
        return random.choice([f"{BACKGROUND_DIR}/retro_inside.png", f"{BACKGROUND_DIR}/retro_outside.png"])
    # デフォルト（新背景も混ぜる）
    return random.choice([
        f"{BACKGROUND_DIR}/finland.png", f"{BACKGROUND_DIR}/night_sauna.png",
        f"{BACKGROUND_DIR}/privatesauna.png", f"{BACKGROUND_DIR}/sauna_room.png",
        f"{BACKGROUND_DIR}/nature.png", f"{BACKGROUND_DIR}/hotel.png",
    ])

def fit_font(draw, text, max_width, max_height, start_size=88, min_size=44, spacing=25):
    font_size = start_size
    while font_size >= min_size:
        font = ImageFont.truetype(FONT_PATH, font_size)
        bbox = draw.multiline_textbbox((0, 0), text, font=font, spacing=spacing)
        if (bbox[2] - bbox[0]) <= max_width and (bbox[3] - bbox[1]) <= max_height:
            return font
        font_size -= 4
    return ImageFont.truetype(FONT_PATH, min_size)

for index, original_text in enumerate(posts):
    print(f"{index + 1}枚目処理中")

    parts = original_text.split("\n")
    parts = [p.strip() for p in parts if p.strip()]

    title_text = parts[0].replace("🔥", "")
    main_text  = "\n".join(parts[1:])
    main_text  = wrap_japanese_text(main_text, width=10)

    style      = SLIDE_STYLES[index % len(SLIDE_STYLES)]
    bg_path    = select_background(original_text, index)

    img = Image.open(bg_path).convert("RGBA")
    img = img.resize((1080, 1920))

    overlay      = Image.new("RGBA", img.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_draw.rounded_rectangle(
        [70, 620, 1010, 1320],
        radius=50,
        fill=style["card_fill"]
    )
    img  = Image.alpha_composite(img, overlay)
    draw = ImageDraw.Draw(img)

    # スライド番号バッジ（左上）
    badge_font = ImageFont.truetype(FONT_PATH, 34)
    draw.rounded_rectangle([80, 635, 200, 685], radius=20, fill=(50, 50, 50, 200))
    draw.text((140, 660), f"{index + 1} / {len(posts)}", font=badge_font, fill="white", anchor="mm")

    # タイトル（ヘッダー）
    draw.text((540, 750), title_text, font=title_font, fill=style["title_fill"], anchor="mm")

    # メインテキスト
    main_font = fit_font(draw, main_text, max_width=820, max_height=340, start_size=92, min_size=48, spacing=28)
    draw.multiline_text(
        (540, 1010), main_text,
        font=main_font, fill=style["main_fill"],
        spacing=28, align="center", anchor="mm"
    )

    # 3枚目はコメント誘導を強調
    if index == 2:
        comment_font = ImageFont.truetype(FONT_PATH, 36)
        # 同梱フォントに絵文字の字形が無く、👇 は豆腐（□）になってしまう。
        # 同じ「下を指す」意味で、フォントが持っている記号に置き換える。
        draw.text((540, 1245), "▼ コメントで教えてください", font=comment_font, fill=(100, 60, 0), anchor="mm")

    img.convert("RGB").save(f"topic_{index + 1}.jpg", quality=95)

# -----------------------------
# 締めスライド
# -----------------------------
img = Image.open(f"{BACKGROUND_DIR}/ending.png").convert("RGBA")
img = img.resize((1080, 1920))

overlay      = Image.new("RGBA", img.size, (0, 0, 0, 0))
overlay_draw = ImageDraw.Draw(overlay)
overlay_draw.rounded_rectangle([90, 620, 990, 1350], radius=45, fill=(245, 245, 235, 220))
img  = Image.alpha_composite(img, overlay)
draw = ImageDraw.Draw(img)

draw.text((540, 760), "最後まで見てくれてありがとう！", font=title_font, fill="black", anchor="mm")

end_font = ImageFont.truetype(FONT_PATH, 72)
draw.multiline_text((540, 950), "次のサウナ話題も\n見たい人は", font=end_font, fill="black", anchor="mm", align="center", spacing=25)
# ♨ そのものは同梱フォントに字形があるが、後ろに付く変化セレクタ（U+FE0F）が
# 豆腐（□）になってしまうので外している。見た目は ♨ のまま変わらない。
draw.text((540, 1150), "フォロー♨", font=ImageFont.truetype(FONT_PATH, 80), fill=(50, 50, 50), anchor="mm")

img.convert("RGB").save(f"topic_{len(posts) + 1}.jpg", quality=95)
print("全話題画像完成")
