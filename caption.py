import re

SOURCE_FILE = "caption_source.txt"
OUTPUT_FILE = "caption.txt"

def read_source():
    with open(SOURCE_FILE, "r", encoding="utf-8") as f:
        return f.read()

def get_value(block, label):
    pattern = rf"{label}:\n(.*?)(\n\n|$)"
    match = re.search(pattern, block, re.DOTALL)
    if match:
        return match.group(1).strip()
    return ""

def make_tags(all_text):
    tags = [
        "#関西サウナ",
        "#サウナ",
        "#スーパー銭湯",
        "#銭湯",
        "#ととのう"
    ]

    area_tags = {
        "大阪": "#大阪サウナ",
        "京都": "#京都サウナ",
        "兵庫": "#兵庫サウナ",
        "神戸": "#神戸サウナ",
        "奈良": "#奈良サウナ",
        "滋賀": "#滋賀サウナ",
        "和歌山": "#和歌山サウナ",
    }

    feature_tags = {
        "ロウリュ": "#ロウリュ",
        "熱波": "#熱波",
        "熱波師": "#熱波師",
        "アウフグース": "#アウフグース",
        "外気浴": "#外気浴",
        "リニューアル": "#リニューアル",
        "オープン": "#オープン",
        "イベント": "#サウナイベント",
        "ホテル": "#ホテルサウナ",
        "宿泊": "#サウナ旅",
    }

    for word, tag in area_tags.items():
        if word in all_text and tag not in tags:
            tags.append(tag)

    for word, tag in feature_tags.items():
        if word in all_text and tag not in tags:
            tags.append(tag)

    return " ".join(tags)

content = read_source()

blocks = content.split("====================")
blocks = [block.strip() for block in blocks if block.strip()]

caption_parts = []

caption_parts.append("関西サウナニュース🔥\n")

all_text_for_tags = content

for index, block in enumerate(blocks, start=1):

    title = get_value(block, "タイトル")
    description = get_value(block, "説明")
    facility = get_value(block, "施設名")

    facility_text = f"📍{facility}\n" if facility else ""

    if description and description != "説明なし":
        summary = f"{facility_text}{description}"
    else:
        summary = f"{facility_text}{title}"

    caption_parts.append(f"【{index}枚目】")
    caption_parts.append(summary)
    caption_parts.append("")

caption_parts.append("気になった施設は保存してチェック♨️")
caption_parts.append("フォローして一緒にととのいましょう！")
caption_parts.append("")
caption_parts.append(make_tags(all_text_for_tags))

caption_text = "\n".join(caption_parts)

with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    f.write(caption_text)

print("caption.txt 生成完了🔥")
