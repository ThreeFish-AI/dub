"""digest 黄金值与预设不变量的补充用例——自 vibe-video tests/test_digest.py 迁入。

「TTS 缓存零失效」的守门人：钉死 digest_indextts 的「未使用即省略」后缀规则。机制住
vibe-tts 仓，这些用例随之迁入，vibe-video 不再保留副本。与 test_digest.py（黄金哈希）互补。
"""

from __future__ import annotations

from vibe_tts_engine_edge import digest_edge  # noqa: F401
import pytest
from vibe_tts_engine_indextts import (
    digest_indextts,
    sampling_suffix,
)
from vibe_tts_profiles import STYLE_PRESETS


def test_digest_indextts_golden():
    vec = (0.95, 0, 0, 0, 0, 0, 0.02, 0.03)
    # beams=1（省略后缀）——与已上线 passionate 句同构
    d1 = digest_indextts(
        "3ed0d9d60d4b",
        "passionate",
        vec,
        0.7,
        0.97,
        "ZH",
        "indextts",
        "测试句。",
        1,
        None,
        None,
    )
    # beams=3（带 |beams=3）
    d3 = digest_indextts(
        "3ed0d9d60d4b",
        "passionate",
        vec,
        0.7,
        0.97,
        "ZH",
        "indextts",
        "测试句。",
        3,
        None,
        None,
    )
    # emoref / emotext 后缀
    dr = digest_indextts(
        "3ed0d9d60d4b",
        "emoref",
        None,
        1.0,
        1.0,
        "ZH",
        "indextts",
        "测试句。",
        1,
        "aabbccddeeff",
        None,
    )
    dt = digest_indextts(
        "3ed0d9d60d4b",
        "emotext",
        None,
        1.0,
        1.0,
        "ZH",
        "indextts",
        "测试句。",
        1,
        None,
        "轻快爽朗",
    )

    # 黄金值按实现真实序列化拍死：vec 用 repr(x) 逐项、None→"none"、alpha/df 用 !r。
    # 若此处失败且你有意修改摘要公式：须全量评估三集已上线缓存失效，并同步更新 VOICE-CLONING.md §六。
    import hashlib

    def manual(suffix: str) -> str:
        vec_str = ",".join(repr(x) for x in vec)
        raw = f"indextts|indextts|3ed0d9d60d4b|ZH|passionate|{vec_str}|{0.7!r}|{0.97!r}|测试句。{suffix}"
        return hashlib.sha1(raw.encode()).hexdigest()

    assert d1 == manual(""), "beams=1 不得带后缀"
    assert d3 == manual("|beams=3"), "beams=3 须带 |beams=3"
    assert d1 != d3, "束宽必须参与摘要（换束宽=换缓存）"
    assert dr and dr != d1
    assert dt and dt not in (d1, dr)
    # 确定性
    assert d1 == digest_indextts(
        "3ed0d9d60d4b",
        "passionate",
        vec,
        0.7,
        0.97,
        "ZH",
        "indextts",
        "测试句。",
        1,
        None,
        None,
    )

    # 逐字黄金（防 repr 语义漂移，如 0.7 → 0.70）
    assert d1 == "b6ccdeea2d913069c87bb679d67043a602bddfcb", d1
    assert d3 == "e0750477d554fb14bf9753c4363ab27869df6cac", d3


def test_digest_indextts_vec_none_serialization():
    import hashlib

    d = digest_indextts(
        "abc", "emoref", None, 1.0, 1.0, "ZH", "indextts", "x", 2, "ff", None
    )
    raw = "indextts|indextts|abc|ZH|emoref|none|1.0|1.0|x|beams=2|emoref=ff"
    assert d == hashlib.sha1(raw.encode()).hexdigest()


def test_duration_factor_participates_in_digest():
    """df 进摘要（RSI-044 的回归前提）：同句两档 df 摘要必不同——这正是「df 走
    编排器默认会整集缓存失配」的机制依据（缺省回落预设 df=1.0 vs 校准 1.24）。"""
    vec = (1 / 3, 0, 0, 0, 0, 0, 2 / 3, 0)
    base = ("aa", "story", vec, 0.28)
    slow = digest_indextts(*base, 1.24, "ZH", "indextts", "测试句。", 1, None, None, {})
    fast = digest_indextts(*base, 1.0, "ZH", "indextts", "测试句。", 1, None, None, {})
    assert slow != fast
    # repr 口径防漂移（与 alpha 同款：1.20 ≠ 1.2 表示）
    assert slow == digest_indextts(
        *base, 1.24, "ZH", "indextts", "测试句。", 1, None, None, {}
    )


def test_digest_indextts_lang_participates():
    """lang 参与摘要：同输入 ZH/EN 摘要不同——zh/en 槽位天然隔离、互不覆盖缓存
    的依据（双语改造的 digest 硬约束：zh 值逐字节不变，见上方黄金）。"""
    vec = (0.95, 0, 0, 0, 0, 0, 0.02, 0.03)
    zh = digest_indextts(
        "3ed0d9d60d4b",
        "sunny",
        vec,
        0.35,
        0.95,
        "ZH",
        "indextts",
        "测试句。",
        1,
        None,
        None,
    )
    en = digest_indextts(
        "3ed0d9d60d4b",
        "sunny",
        vec,
        0.35,
        0.95,
        "EN",
        "indextts",
        "测试句。",
        1,
        None,
        None,
    )
    assert zh == "4fd5b37d1eae790297e25abc850dd72470b8ba9e", zh  # zh 黄金钉死
    assert en == "1f6d6b11f904c47a14a9b89f26d663648f07e6e2", en
    assert en != zh


def test_sampling_suffix_omitted_when_default():
    """空 sampling 必须产出空后缀 —— 这是存量三集 604 句缓存零失效的唯一依据。"""
    assert sampling_suffix(None) == ""
    assert sampling_suffix({}) == ""
    vec = (0.95, 0, 0, 0, 0, 0, 0.02, 0.03)
    args = ("3ed0d9d60d4b", "passionate", vec, 0.7, 0.97, "ZH", "indextts", "测试句。")
    # 三种写法（不传 / 传 None / 传空 dict）必须与历史摘要逐字节相同
    base = digest_indextts(*args, 1, None, None)
    assert digest_indextts(*args, 1, None, None, None) == base
    assert digest_indextts(*args, 1, None, None, {}) == base
    assert base == "b6ccdeea2d913069c87bb679d67043a602bddfcb", base


def test_sampling_participates_in_digest():
    """任一采样参数改动都必须失效缓存，否则会得出「改了参数没效果」的假结论。"""
    vec = (0.95, 0, 0, 0, 0, 0, 0.02, 0.03)
    args = ("aa", "sunny", vec, 0.35, 0.95, "ZH", "indextts", "测试句。")
    base = digest_indextts(*args, 1, None, None, {})
    seen = {base}
    for key, val in (
        ("temperature", 0.6),
        ("top_p", 0.7),
        ("top_k", 0),
        ("length_penalty", 0.8),
        ("repetition_penalty", 2.0),
        ("max_mel_tokens", 1815),
        ("interval_silence", 0),
        ("text_normalization", False),
        ("seed", 1234),
    ):
        d = digest_indextts(*args, 1, None, None, {key: val})
        assert d != base, f"{key} 未参与摘要"
        assert d not in seen, f"{key} 与其它参数摘要碰撞"
        seen.add(d)


def test_sampling_suffix_uses_repr_not_str():
    """用 repr 而非 str：防 0.8 与 0.80 算出不同摘要（与 alpha/df 同口径）。"""
    assert sampling_suffix({"length_penalty": 0.80}) == "|length_penalty=0.8"
    assert sampling_suffix({"text_normalization": False}) == "|text_normalization=False"
    assert sampling_suffix({"top_k": 0}) == "|top_k=0"


def test_sampling_suffix_repr_differs_from_str_for_numpy_scalars():
    """repr 与 str 对内置 float/int 输出相同，上面的用例因此区分不了二者。

    真正会分叉的是 numpy 标量：repr 是 `np.float64(0.7)`、str 是 `0.7`。采样值一旦从
    numpy 算出再进摘要，口径换成 str 就会静默改键、整集缓存失效——这条把 repr 钉死。
    """
    np = pytest.importorskip("numpy")
    assert sampling_suffix({"top_p": np.float64(0.7)}) == "|top_p=np.float64(0.7)"
    assert sampling_suffix({"top_p": np.float64(0.7)}) != sampling_suffix(
        {"top_p": 0.7}
    )


def _upstream_trunc(vec, alpha):
    """复刻上游 infer_v2_5.py:608 的量化：int(x*alpha*10000)/10000（**截断**非四舍五入）。"""
    return [int(x * alpha * 10000) / 10000 for x in vec]


def test_preset_nominal_sums_are_one():
    """只有 Σvec=1.0 时 alpha 才等于「替换掉本人语调的百分比」，否则跨预设不可比。"""
    for name, p in STYLE_PRESETS.items():
        if p["vec"] is None:
            continue
        assert abs(sum(p["vec"]) - 1.0) < 1e-6, f"{name} 的名义向量和不是 1.0"


def test_preset_normalization_preserves_injection():
    """归一化前后的**有效注入**必须在上游量化的 1 个单位（1e-4）内一致。

    不能断言「逐项完全一致」：上游用 int() 截断，而 0.33 / 0.455 恰在截断边界上，
    故 lively/confident/positive 各有一个分量差 1e-4（相对 0.03%，远低于听感阈）。
    这三档未被任何已上线剧集使用，即便真有差异也无影响。
    """
    # (预设名, 归一化前的 vec, 归一化前的 alpha)
    before = {
        "lively": ([0.55, 0, 0, 0, 0, 0, 0.15, 0.15], 0.6),
        "confident": ([0.25, 0, 0, 0, 0, 0, 0, 0.65], 0.7),
        "positive": ([0.75, 0, 0, 0, 0, 0, 0, 0.2], 0.7),
        "passionate": ([0.70, 0, 0, 0, 0, 0, 0.20, 0.10], 0.7),
        "sunny": ([0.95, 0, 0, 0, 0, 0, 0.02, 0.03], 0.35),
    }
    for name, (old_vec, old_alpha) in before.items():
        p = STYLE_PRESETS[name]
        w_old = _upstream_trunc(old_vec, old_alpha)
        w_new = _upstream_trunc(p["vec"], p["alpha"])
        for a, b in zip(w_old, w_new, strict=True):
            assert abs(a - b) <= 1e-4, f"{name}: 有效注入偏移 {abs(a - b):.2e} > 1e-4"


def test_production_presets_untouched():
    """已上线三集用的两档必须逐字不变 —— 改它们即整集重录（ISSUE-161 的教训）。"""
    assert STYLE_PRESETS["passionate"]["vec"] == [0.70, 0, 0, 0, 0, 0, 0.20, 0.10]
    assert STYLE_PRESETS["passionate"]["alpha"] == 0.7
    assert STYLE_PRESETS["passionate"]["df"] == 0.97
    for name in ("sunny", "sunny-steady"):
        assert STYLE_PRESETS[name]["vec"] == [0.95, 0, 0, 0, 0, 0, 0.02, 0.03]
        assert STYLE_PRESETS[name]["alpha"] == 0.35
        assert STYLE_PRESETS[name]["df"] == 0.95
    assert STYLE_PRESETS["sunny-steady"]["beams"] == 3


def test_candidate_presets_are_additive_only():
    """#10/#11 的候选档是**新增**，不得改动任何生产档的数值。"""
    assert set(STYLE_PRESETS) >= {"sunny-pure", "sunny-clear"}
    assert STYLE_PRESETS["sunny-pure"]["vec"] == [1.0, 0, 0, 0, 0, 0, 0, 0]
    assert STYLE_PRESETS["sunny-clear"]["df"] == 1.05, "候选 #11 的要点是 df>1"
    # 候选档未定档前不得带 sampling 覆盖（那会改摘要）
    for name in ("sunny-pure", "sunny-clear"):
        assert "sampling" not in STYLE_PRESETS[name]
