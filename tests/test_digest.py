"""缓存摘要的黄金哈希——与 vibe-video tests/test_digest.py 的期望值逐字相同。

摘要格式是跨仓缓存契约：这里任何一个哈希变了，意味着已缓存的音频全部失效。
vibe-video 的同名测试保留在它自己的仓里，两边一起防漂移。
"""

from __future__ import annotations

import hashlib

import pytest
from dub_engine_edge import digest_edge
from dub_engine_indextts import digest_indextts, sampling_suffix

VEC = (0.95, 0, 0, 0, 0, 0, 0.02, 0.03)
ARGS = ("3ed0d9d60d4b", "passionate", VEC, 0.7, 0.97, "ZH", "indextts", "测试句。")

GOLDEN_BEAMS1 = "b6ccdeea2d913069c87bb679d67043a602bddfcb"
GOLDEN_BEAMS3 = "e0750477d554fb14bf9753c4363ab27869df6cac"
GOLDEN_SUNNY_ZH = "4fd5b37d1eae790297e25abc850dd72470b8ba9e"
GOLDEN_SUNNY_EN = "1f6d6b11f904c47a14a9b89f26d663648f07e6e2"


def _manual(suffix: str) -> str:
    vec_str = ",".join(repr(x) for x in VEC)
    raw = f"indextts|indextts|3ed0d9d60d4b|ZH|passionate|{vec_str}|{0.7!r}|{0.97!r}|测试句。{suffix}"
    return hashlib.sha1(raw.encode()).hexdigest()


def test_beams_golden():
    d1 = digest_indextts(*ARGS, 1, None, None)
    d3 = digest_indextts(*ARGS, 3, None, None)
    assert d1 == GOLDEN_BEAMS1, d1
    assert d3 == GOLDEN_BEAMS3, d3
    assert d1 == _manual(""), "beams=1 不得带后缀"
    assert d3 == _manual("|beams=3"), "beams=3 须带 |beams=3"
    assert d1 != d3, "束宽必须参与摘要"


def test_emotion_source_participates():
    base = digest_indextts(*ARGS, 1, None, None)
    assert digest_indextts(*ARGS, 1, "aabbccddeeff", None) != base
    assert digest_indextts(*ARGS, 1, None, "轻快爽朗") != base


def test_vec_none_serialization():
    d = digest_indextts(
        "abc", "emoref", None, 1.0, 1.0, "ZH", "indextts", "x", 2, "ff", None
    )
    raw = "indextts|indextts|abc|ZH|emoref|none|1.0|1.0|x|beams=2|emoref=ff"
    assert d == hashlib.sha1(raw.encode()).hexdigest()


def test_digest_edge_golden():
    assert digest_edge("v", "r", "t") == hashlib.sha1(b"v|r|t").hexdigest()


def test_lang_participates_and_zh_is_frozen():
    kw = ("3ed0d9d60d4b", "sunny", VEC, 0.35, 0.95)
    zh = digest_indextts(*kw, "ZH", "indextts", "测试句。", 1, None, None)
    en = digest_indextts(*kw, "EN", "indextts", "测试句。", 1, None, None)
    assert zh == GOLDEN_SUNNY_ZH, zh
    assert en == GOLDEN_SUNNY_EN, en
    assert en != zh


def test_duration_factor_participates():
    base = ("3ed0d9d60d4b", "sunny", VEC, 0.35)
    slow = digest_indextts(*base, 1.24, "ZH", "indextts", "测试句。", 1, None, None, {})
    fast = digest_indextts(*base, 1.0, "ZH", "indextts", "测试句。", 1, None, None, {})
    assert slow != fast


@pytest.mark.parametrize("empty", [None, {}])
def test_empty_sampling_keeps_historical_digest(empty):
    """空 sampling 必须产出空后缀——存量缓存零失效的唯一依据。"""
    assert sampling_suffix(empty) == ""
    assert digest_indextts(*ARGS, 1, None, None, empty) == GOLDEN_BEAMS1


def test_sampling_suffix_key_order_is_stable():
    a = sampling_suffix({"top_p": 0.7, "length_penalty": 0.8, "seed": 1})
    b = sampling_suffix({"seed": 1, "top_p": 0.7, "length_penalty": 0.8})
    assert a == b == "|length_penalty=0.8|seed=1|top_p=0.7"


def test_any_sampling_change_invalidates_cache():
    base = digest_indextts(*ARGS, 1, None, None)
    for k, v in {"temperature": 0.9, "top_p": 0.7, "seed": 1}.items():
        assert digest_indextts(*ARGS, 1, None, None, {k: v}) != base, k


def test_block_suffix_is_versioned_and_stable():
    from dub_engine_indextts import block_digest_suffix

    a = block_digest_suffix(["甲。", "乙。"], 0.32, 0.18)
    assert a == block_digest_suffix(["甲。", "乙。"], 0.32, 0.18)
    assert a != block_digest_suffix(["甲。", "丙。"], 0.32, 0.18), (
        "任一成员句变化即换键"
    )
    payload = "\x1f".join(["甲。", "乙。"]) + f"\x1f{0.32!r}\x1f{0.18!r}\x1fsplit=v3"
    assert a == hashlib.sha1(payload.encode()).hexdigest()[:12], (
        "split=v3 版本标记不得悄悄变"
    )
