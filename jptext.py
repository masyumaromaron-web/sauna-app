# -*- coding: utf-8 -*-
"""日本語の折り返し。スライド生成の3系統（サウナ／銭湯／話題）で共用する。

もとは各スクリプトが textwrap.wrap で機械的に決まった文字数ずつ折っていたため、
「？」が行頭に落ちたり単語が途中で割れたりしていた。

    旧: サウナ、買う人増加中 / ？ / なぜテントサウナが人 / 気？
    新: サウナ、買う人増加中？ / なぜテントサウナが / 人気？

禁則を守り、なるべく文節で折る。戻って探す範囲は幅の半分までなので、
短い文言では旧実装と同じ結果になり、既存の見た目は変わらない。
"""

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
