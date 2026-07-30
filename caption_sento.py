import re

SOURCE_FILE = "caption_source_sento.txt"
OUTPUT_FILE = "caption_sento.txt"

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
        "#関西銭湯",
        "#銭湯",
        "#銭湯好きな人と繋がりたい",
        "#銭湯巡り",
        "#公衆浴場",
    ]

    area_tags = {
        "大阪": "#大阪銭湯",
        "京都": "#京都銭湯",
        "兵庫": "#兵庫銭湯",
        "神戸": "#神戸銭湯",
        "奈良": "#奈良銭湯",
        "滋賀": "#滋賀銭湯",
        "和歌山": "#和歌山銭湯",
    }

    feature_tags = {
        "レトロ": "#レトロ銭湯",
        "昭和": "#昭和レトロ",
        "サウナ": "#銭湯サウナ",
        "天然温泉": "#天然温泉",
        "リニューアル": "#リニューアル",
        "オープン": "#新規オープン",
        "イベント": "#銭湯イベント",
        "薪": "#薪炊き銭湯",
        "子ども": "#ファミリー銭湯",
        "親子": "#ファミリー銭湯",
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

caption_parts.append("関西銭湯ニュース♨️\n")

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

caption_parts.append("気になった銭湯は保存してチェック♨️")
caption_parts.append("フォローして一緒に銭湯を楽しもう！")
caption_parts.append("")
caption_parts.append(make_tags(all_text_for_tags))

caption_text = "\n".join(caption_parts)

with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    f.write(caption_text)

print("caption_sento.txt 生成完了♨️")
