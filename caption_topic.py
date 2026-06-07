import re

SOURCE_FILE = "caption_source_topic.txt"
OUTPUT_FILE = "caption_topic.txt"

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
    tags = ["#サウナ", "#サウナー", "#ととのう", "#サウナ好きと繋がりたい", "#サウナ情報"]
    topic_tags = {
        "研究": "#サウナ効果", "健康": "#サウナ健康", "睡眠": "#サウナ睡眠",
        "フィンランド": "#フィンランドサウナ", "海外": "#海外サウナ",
        "サ飯": "#サ飯", "グルメ": "#サウナグルメ",
        "銭湯": "#銭湯", "文化": "#銭湯文化",
        "女性": "#女性サウナー", "グッズ": "#サウナグッズ",
        "若者": "#サウナブーム",
    }
    for word, tag in topic_tags.items():
        if word in all_text and tag not in tags:
            tags.append(tag)
    return " ".join(tags)

content = read_source()
blocks = content.split("====================")
blocks = [b.strip() for b in blocks if b.strip()]

if not blocks:
    print("caption_source_topic.txt が空です")
    exit()

block = blocks[0]

theme    = get_value(block, "テーマ")
summary  = get_value(block, "説明")
slide1   = get_value(block, "スライド1")
slide2   = get_value(block, "スライド2")
slide3   = get_value(block, "スライド3（質問）")
title    = get_value(block, "タイトル")

if not summary:
    summary = title

def format_summary(text):
    """句点で区切って2文ごとに段落化、読みやすく改行を入れる"""
    # 文単位に分割（。を保持）
    sentences = re.split(r'(?<=。)', text)
    sentences = [s.strip() for s in sentences if s.strip()]
    # 2文ごとに段落をまとめる
    paragraphs = []
    for i in range(0, len(sentences), 2):
        chunk = "".join(sentences[i:i+2])
        paragraphs.append(chunk)
    return "\n\n".join(paragraphs)

summary = format_summary(summary)

caption_parts = []
caption_parts.append(f"サウナ話題🔥\n")
caption_parts.append(summary)
caption_parts.append("")
caption_parts.append("━━━━━━━━━━━━━━")
caption_parts.append("💬 3枚目の質問、コメントで教えてください！")
caption_parts.append("気になったら保存してね♨️")
caption_parts.append("フォローして一緒にととのいましょう！")
caption_parts.append("")
caption_parts.append(make_tags(content))

caption_text = "\n".join(caption_parts)

with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    f.write(caption_text)

print("caption_topic.txt 生成完了🔥")
