import os
import random

from jptext import wrap_japanese_text
from PIL import Image, ImageDraw, ImageFont

# フォントの場所を環境に応じて自動判定する（Win/Mac/Linuxどれでも文字化けしないように）
def _find_font_path():
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(here, "fonts", "NotoSansJP-Regular.ttf"),   # 同梱フォント（最優先・全OS共通）
        "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",         # Mac
        "C:/Windows/Fonts/meiryo.ttc",                           # Windows
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",# Linux
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return candidates[0]

FONT_PATH = _find_font_path()
BACKGROUND_DIR = "backgrounds"

title_font = ImageFont.truetype(FONT_PATH, 50)
tag_font = ImageFont.truetype(FONT_PATH, 42)

with open("posts_sento.txt", "r", encoding="utf-8") as f:
    content = f.read()

posts = content.split("====================")
posts = [post.strip() for post in posts if post.strip()]




def select_background(text):
    # レトロ・昭和系
    if any(w in text for w in ["レトロ", "昭和", "老舗", "歴史"]):
        return random.choice([
            f"{BACKGROUND_DIR}/retro_inside.png",
            f"{BACKGROUND_DIR}/retro_outside.png"
        ])

    # イベント・変わり湯系
    if any(w in text for w in ["イベント", "期間限定", "花湯", "薬湯", "コラボ"]):
        return f"{BACKGROUND_DIR}/retro_inside.png"

    # 天然温泉系
    if any(w in text for w in ["天然温泉", "源泉"]):
        return f"{BACKGROUND_DIR}/ryokan.png"

    # サウナ付き銭湯系（個室・夜系の新背景も活用）
    if any(w in text for w in ["サウナ", "水風呂", "ととのい"]):
        return random.choice([
            f"{BACKGROUND_DIR}/sauna_room.png",
            f"{BACKGROUND_DIR}/privatesauna.png",
            f"{BACKGROUND_DIR}/night_sauna.png",
        ])

    # デフォルトは銭湯系背景でランダム
    return random.choice([
        f"{BACKGROUND_DIR}/retro_inside.png",
        f"{BACKGROUND_DIR}/retro_outside.png",
        f"{BACKGROUND_DIR}/ryokan.png",
    ])


def fit_font(draw, text, max_width, max_height, start_size=88, min_size=48, spacing=25):
    font_size = start_size

    while font_size >= min_size:
        font = ImageFont.truetype(FONT_PATH, font_size)

        bbox = draw.multiline_textbbox(
            (0, 0),
            text,
            font=font,
            spacing=spacing
        )

        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]

        if text_width <= max_width and text_height <= max_height:
            return font

        font_size -= 4

    return ImageFont.truetype(FONT_PATH, min_size)


for index, original_text in enumerate(posts):

    print(f"{index + 1}件目処理中")

    parts = original_text.split("\n")
    parts = [p.strip() for p in parts if p.strip()]

    title_text = parts[0]
    tag_text = parts[-1]
    main_text = "\n".join(parts[1:-1])

    main_text = wrap_japanese_text(main_text, width=11)

    bg = select_background(original_text)

    img = Image.open(bg).convert("RGBA")
    img = img.resize((1080, 1920))

    draw = ImageDraw.Draw(img)

    card_x1 = 70
    card_y1 = 650
    card_x2 = 1010
    card_y2 = 1300

    # 白カード用の透明レイヤーを作る
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)

    # 白カード
    overlay_draw.rounded_rectangle(
        [card_x1, card_y1, card_x2, card_y2],
        radius=45,
        fill=(245, 245, 235, 220)
    )

    # 背景画像と透明カードを合成
    img = Image.alpha_composite(img, overlay)

    # 合成後に描画モードを作り直す
    draw = ImageDraw.Draw(img)

    draw.text(
        (540, 760),
        title_text,
        font=title_font,
        fill="black",
        anchor="mm"
    )

    main_font = fit_font(
        draw,
        main_text,
        max_width=800,
        max_height=300,
        start_size=88,
        min_size=50,
        spacing=25
    )

    draw.multiline_text(
        (540, 1020),
        main_text,
        font=main_font,
        fill="black",
        spacing=25,
        align="center",
        anchor="mm"
    )

    draw.text(
        (540, 1180),
        tag_text,
        font=tag_font,
        fill=(60, 60, 60),
        anchor="mm"
    )

    img.convert("RGB").save(
        f"sento_{index + 1}.jpg",
        quality=95
    )


# -----------------------------
# 締めスライド生成
# -----------------------------

img = Image.open(f"{BACKGROUND_DIR}/retro_outside.png").convert("RGBA")
img = img.resize((1080, 1920))

draw = ImageDraw.Draw(img)

overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
overlay_draw = ImageDraw.Draw(overlay)

card_x1 = 90
card_y1 = 620
card_x2 = 990
card_y2 = 1350
overlay_draw.rounded_rectangle(
    [card_x1, card_y1, card_x2, card_y2],
    radius=45,
    fill=(245, 245, 235, 220)
)

img = Image.alpha_composite(img, overlay)
draw = ImageDraw.Draw(img)

draw.text(
    (540, 760),
    "最後まで見てくれてありがとう！",
    font=title_font,
    fill="black",
    anchor="mm"
)

end_main_font = ImageFont.truetype(FONT_PATH, 72)

draw.multiline_text(
    (540, 940),
    "明日の銭湯ニュースも\nお楽しみに！",
    font=end_main_font,
    fill="black",
    anchor="mm",
    align="center",
    spacing=25
)

draw.multiline_text(
    (540, 1120),
    "フォローして\n一緒に銭湯を楽しもう！",
    font=title_font,
    fill=(50, 50, 50),
    anchor="mm",
    align="center",
    spacing=20
)

img.convert("RGB").save(
    f"sento_{len(posts) + 1}.jpg",
    quality=95
)

print("全銭湯ニュース画像完成")
