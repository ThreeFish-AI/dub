"""风格预设的不变量：只增不改——任何已上线预设的数值变化都会让缓存全部失效。"""

from __future__ import annotations

import math

import pytest
from dub_profiles import (
    DEFAULT_RATE,
    DEFAULT_VOICE,
    EDGE_VOICE_BY_LANG,
    EMO_KEYS,
    STYLE_PRESETS,
    parse_emo_vector,
)


def test_story_preset_is_frozen():
    p = STYLE_PRESETS["story"]
    assert p["vec"] == [1 / 3, 0, 0, 0, 0, 0, 2 / 3, 0]
    assert (p["alpha"], p["df"], p["beams"], p["seed"]) == (0.28, 1.0, 1, 4242)
    assert p["block"] == {
        "max_sentences": 3,
        "max_chars": 90,
        "tail_pad_sec": 0.18,
        "perform_punct": True,
    }


def test_production_presets_are_frozen():
    pas = STYLE_PRESETS["passionate"]
    assert (pas["vec"], pas["alpha"], pas["df"]) == (
        [0.70, 0, 0, 0, 0, 0, 0.20, 0.10],
        0.7,
        0.97,
    )
    for name, beams in (("sunny", 1), ("sunny-steady", 3)):
        p = STYLE_PRESETS[name]
        assert p["vec"] == [0.95, 0, 0, 0, 0, 0, 0.02, 0.03]
        assert (p["alpha"], p["df"], p.get("beams", 1)) == (0.35, 0.95, beams)


def test_neutral_injects_no_emotion():
    assert STYLE_PRESETS["neutral"]["vec"] is None


@pytest.mark.parametrize("name", [n for n in STYLE_PRESETS if n != "neutral"])
def test_effective_sum_within_server_guard(name):
    """服务端护栏 Σvec×alpha ≤ 0.8，预设越线会在合成时才 400。"""
    p = STYLE_PRESETS[name]
    assert sum(p["vec"]) * p["alpha"] <= 0.8 + 1e-9, name
    assert len(p["vec"]) == len(EMO_KEYS) == 8


def test_every_preset_has_required_keys():
    for name, p in STYLE_PRESETS.items():
        assert {"label", "vec", "alpha", "df"} <= p.keys(), name


def test_edge_defaults():
    assert DEFAULT_VOICE == "zh-CN-YunxiNeural"
    assert DEFAULT_RATE == "+4%"
    assert EDGE_VOICE_BY_LANG == {"zh": DEFAULT_VOICE, "en": "en-US-AndrewNeural"}


def test_parse_emo_vector_ok():
    assert parse_emo_vector("happy:0.6,calm:0.2") == [0.6, 0, 0, 0, 0, 0, 0, 0.2]


@pytest.mark.parametrize(
    "spec",
    ["", "joy:1", "happy:", "happy:-1", "happy:nan", "happy:inf", "happy:1,happy:2"],
)
def test_parse_emo_vector_rejects(spec):
    with pytest.raises(ValueError):
        parse_emo_vector(spec)


def test_parse_emo_vector_values_are_finite():
    assert all(math.isfinite(x) for x in parse_emo_vector("sad:0.5"))
