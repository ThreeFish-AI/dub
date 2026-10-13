"""标点映射与句子分块器。"""

from __future__ import annotations

import pytest
from dub_textprep import close_chunk, split_chunks, split_sentences, tts_text


def test_tts_text_zh_mapping():
    assert tts_text("甲——乙") == "甲，乙"
    assert tts_text("甲……乙") == "甲。乙"
    assert tts_text("甲……乙", perform=True) == "甲…乙"


def test_tts_text_non_zh_is_passthrough():
    """en 稿不得被全角映射污染。"""
    assert tts_text("a——b……c", lang="EN") == "a——b……c"
    assert tts_text("a——b……c", lang="EN", perform=True) == "a——b……c"


# ---------------- split_sentences ----------------


def test_split_sentences_keeps_terminal_punct_with_sentence():
    assert split_sentences("你好。再见！真的？") == ["你好。", "再见！", "真的？"]


def test_split_sentences_keeps_closing_quote_with_sentence():
    assert split_sentences("他说：“不可能！”然后走了。") == [
        "他说：“不可能！”",
        "然后走了。",
    ]


def test_split_sentences_ascii_end_only_before_space_or_end():
    """2.5.1 / e.g. 里的点不是句界。"""
    assert split_sentences("版本 2.5.1 发布了。") == ["版本 2.5.1 发布了。"]
    assert split_sentences("ok. next") == ["ok.", "next"]


def test_split_sentences_trailing_text_without_punct():
    assert split_sentences("没有句号的结尾") == ["没有句号的结尾"]


def test_split_sentences_drops_blank():
    assert split_sentences("") == []
    assert split_sentences("  \n \n ") == []


# ---------------- close_chunk ----------------


@pytest.mark.parametrize(
    ("src", "want"),
    [
        ("没有标点", "没有标点。"),
        ("已有句号。", "已有句号。"),
        ("续接，", "续接，"),  # 有意续接：补 。会拼出 ，。
        ("冒号：", "冒号："),
        ("他说：“不可能！”", "他说：“不可能！”"),  # 收引号不算末字
        ("他说：“不可能”", "他说：“不可能”。"),
    ],
)
def test_close_chunk(src, want):
    assert close_chunk(src) == want


# ---------------- split_chunks ----------------


def test_split_chunks_short_text_is_single_chunk_and_closed():
    assert split_chunks("今天我们拆解的是：Agent Skills") == [
        "今天我们拆解的是：Agent Skills。"
    ]


def test_split_chunks_empty():
    assert split_chunks("") == []
    assert split_chunks("   \n ") == []


def test_split_chunks_respects_max_sentences():
    text = "一。二。三。四。五。"
    assert split_chunks(text, max_sentences=3, max_chars=999) == [
        "一。二。三。",
        "四。五。",
    ]


def test_split_chunks_respects_max_chars():
    text = "甲" * 40 + "。" + "乙" * 40 + "。"
    chunks = split_chunks(text, max_sentences=3, max_chars=50)
    assert len(chunks) == 2
    assert all(len(c) <= 50 for c in chunks)


def test_split_chunks_content_is_preserved():
    """块拼接即原文（仅可能补句末 。）。"""
    text = "这是第一句话。这是第二句话！第三句话来了？第四句话在这里；第五句。"
    joined = "".join(split_chunks(text, max_sentences=2, max_chars=20))
    assert joined == text


def test_split_chunks_overlong_sentence_splits_on_soft_pauses():
    text = "，".join(["很长的一段话"] * 30) + "。"
    chunks = split_chunks(text, max_sentences=3, max_chars=40)
    assert len(chunks) > 1
    assert "".join(chunks) == text
    assert all(len(c) <= 41 for c in chunks)  # 软停顿切点允许带一个标点


def test_split_chunks_unsplittable_long_sentence_is_kept_whole():
    """宁长勿断词：没有任何停顿可切时整句独占一块，不截断内容。"""
    text = "长" * 200
    chunks = split_chunks(text, max_chars=50)
    assert chunks == ["长" * 200 + "。"]


@pytest.mark.parametrize(("ms", "mc"), [(0, 10), (3, 0), (-1, 5)])
def test_split_chunks_rejects_bad_limits(ms, mc):
    with pytest.raises(ValueError):
        split_chunks("一。", max_sentences=ms, max_chars=mc)
