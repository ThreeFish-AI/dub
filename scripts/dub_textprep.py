"""口播文本预处理：标点映射 + 句子分块器。"""

from __future__ import annotations


def tts_text(text: str, lang: str = "ZH", perform: bool = False) -> str:
    """口播文本微调：破折号换为逗号停顿，避免 TTS 念成怪音。

    全角映射仅对主语言（ZH）生效：en 稿经 check_script 禁全角标点门，此处原样
    透传是纵深防御——`——` 会被换成全角逗号混进英文文本。

    perform=True（段落演绎档）：`……` 保留为单个 `…` 而非压成 `。`——上游把 `…`
    当独立 token 并给拖长停顿（front.py 标点表），是「表演标点」的一部分；默认
    映射不动（存量 2 集含 `……`，改全局会令其重合成出不同音频而摘要不变）。

    注意这是**唯一**的程序化文本预处理：数字/百分号/量词的读法由上游中文归一化
    （wetext）承担，多音字与英文专名读音由逐字稿里的发音标注 `<字|读音>` 承担。
    归一化的已知陷阱（4 位年份与「年」之间不能有空格）由 check_script.py 的写稿 lint 拦。
    """
    if lang != "ZH":
        return text
    text = text.replace("——", "，")
    return text.replace("……", "…") if perform else text.replace("……", "。")


# ---------------- 句子分块器（index-tts 长文本用） ----------------
#
# 为什么要分块：上游单请求 mel token 上限 1815（≈36 s），超出的尾部文本**静默不读**；
# 且 >118 token 会被上游按标点二次切段并插 200 ms 硬静音。分块让每次请求落在
# 「单段连续生成」的舒适区（story 档：≤3 句 / ≤90 字），再由 dub_audio.concat_mp3 拼回。
# 与 vibe-video 的 plan_blocks 刻意分叉：后者靠台本的 blockStart/scene 硬边界与
# DP 窗口控制改稿爆炸半径（视频特定）；这里只有一段纯文本，只需按句界贪心装箱。

#: 句末停顿标点（含 ASCII .!? 仅在后随空白/结尾时才算句界，避免切碎 2.5.1 / e.g. 这类）。
_SENT_END = "。！？…；"
_PAUSE = "。！？…；，：、—"
_CLOSERS = "”’」』）)》】\"'"
_SOFT = "，：、"  # 超长单句的次级切点


def split_sentences(text: str) -> list[str]:
    """按句界切句，句末标点与其后的收引号/括号留在本句内；空白折叠，空句丢弃。"""
    text = text.replace("\r\n", "\n")
    out: list[str] = []
    buf: list[str] = []
    n = len(text)
    i = 0
    while i < n:
        ch = text[i]
        buf.append(ch)
        is_end = ch in _SENT_END or ch == "\n"
        if not is_end and ch in ".!?":
            nxt = text[i + 1] if i + 1 < n else " "
            is_end = nxt.isspace() or nxt in _CLOSERS
        if is_end:
            while i + 1 < n and (text[i + 1] in _CLOSERS or text[i + 1] in _SENT_END):
                i += 1
                buf.append(text[i])
            s = "".join(buf).strip()
            if s:
                out.append(s)
            buf = []
        i += 1
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


def _split_long(sentence: str, max_chars: int) -> list[str]:
    """单句超长时按软停顿（，：、）切成 ≤max_chars 的片段；无软停顿则原样保留（宁长勿断词）。"""
    if len(sentence) <= max_chars:
        return [sentence]
    parts: list[str] = []
    cur = ""
    for piece in _iter_soft(sentence):
        if cur and len(cur) + len(piece) > max_chars:
            parts.append(cur)
            cur = piece
        else:
            cur += piece
    if cur:
        parts.append(cur)
    return parts


def _iter_soft(sentence: str):
    start = 0
    for i, ch in enumerate(sentence):
        if ch in _SOFT:
            yield sentence[start : i + 1]
            start = i + 1
    if start < len(sentence):
        yield sentence[start:]


def close_chunk(text: str) -> str:
    """块文本末尾无停顿标点者补 `。`（上游按标点断句合并段）。

    `，：、—` 结尾视为有意续接，原样保留（补 `。` 会拼出 `，。` 双标点）；收引号/括号不算末字。
    """
    body = text.rstrip(_CLOSERS)
    if body and body[-1] not in _PAUSE:
        return text + "。"
    return text


def split_chunks(text: str, max_sentences: int = 3, max_chars: int = 90) -> list[str]:
    """把任意长文本贪心装箱成合成块：每块 ≤max_sentences 句且 ≤max_chars 字。

    - 单句本身超 max_chars：先按软停顿切开，切不开则独占一块（不截断内容）；
    - 返回的块已 close_chunk；空文本返回 []；块拼接即原文（仅可能补句末 `。`）。
    """
    if max_sentences < 1 or max_chars < 1:
        raise ValueError("max_sentences / max_chars 必须 ≥ 1")
    units: list[str] = []
    for s in split_sentences(text):
        units.extend(_split_long(s, max_chars))
    chunks: list[str] = []
    cur: list[str] = []
    cur_len = 0
    for u in units:
        if cur and (len(cur) >= max_sentences or cur_len + len(u) > max_chars):
            chunks.append(close_chunk("".join(cur)))
            cur, cur_len = [], 0
        cur.append(u)
        cur_len += len(u)
    if cur:
        chunks.append(close_chunk("".join(cur)))
    return chunks
