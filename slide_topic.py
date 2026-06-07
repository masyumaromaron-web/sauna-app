import os
import random
import textwrap
from PIL import Image, ImageDraw, ImageFont

# フォントの場所を環境に応じて決める。
# Windows（手元のPC）ならメイリオ、Linux（Render）なら同梱のNotoSansを使う。
def _find_font_path():
    candidates = [
        "C:/Windows/Fonts/meiryo.ttc",                 # Windows（手元）
        "fonts/NotoSansJP-Regular.ttf",                # Render用に同梱するフォント
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

def wrap_japanese_text(text, width=10):
    lines = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        wrapped = textwrap.wrap(line, width=width)
        lines.extend(wrapped)
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
        draw.text((540, 1245), "👇 コメントで教えてください", font=comment_font, fill=(100, 60, 0), anchor="mm")

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
draw.text((540, 1150), "フォロー♨️", font=ImageFont.truetype(FONT_PATH, 80), fill=(50, 50, 50), anchor="mm")

img.convert("RGB").save(f"topic_{len(posts) + 1}.jpg", quality=95)
print("全話題画像完成")
