"""块切分、块摘要与 story 预设不变量——自 vibe-video tests/test_tts_blocks.py 迁入。

只含被测对象是 dub 原语的用例（切分纯函数、digest 块后缀、story 预设）；
依赖台本结构的块规划（plan_blocks 等）留在 vibe-video。
"""

from __future__ import annotations

from dub_engine_indextts import block_digest_suffix, digest_indextts
from dub_profiles import STYLE_PRESETS
from dub_textprep import tts_text
from dub_tts_blocks import frame_db, plan_block_cuts, silence_runs, split_block_pcm


def test_plan_block_cuts_prefers_expected_positions():
    """3 句块：句界期望位 1/3、2/3 处各放长静音；逗号短静音落在别处干扰。"""
    runs = [
        (1.2, 1.5),  # 逗号停顿（离期望远）
        (2.0, 2.4),  # 期望 ~2.1：句界 1
        (3.1, 3.2),  # 逗号停顿
        (4.1, 4.5),  # 期望 ~4.2：句界 2
    ]
    picked = plan_block_cuts(
        runs, [1.0, 1.0, 1.0], total_sec=6.0, speech_on=0.0, speech_off=6.0
    )
    assert picked == [(2.0, 2.4), (4.1, 4.5)]


def test_plan_block_cuts_fails_when_no_feasible_match():
    runs = [(0.1, 0.2)]  # 唯一候选远离子数所需
    assert (
        plan_block_cuts(runs, [1.0] * 3, total_sec=6.0, speech_on=0.0, speech_off=6.0)
        is None
    )


def test_split_block_pcm_discard_from_middle():
    import numpy as np

    sr = 1000
    pcm = np.concatenate(
        [
            np.ones(1000, np.float32),
            np.zeros(500, np.float32),
            np.ones(1000, np.float32),
        ]
    )
    clips = split_block_pcm(pcm, sr, [(1.0, 1.5)], discard_sec=0.32)
    assert len(clips) == 2
    # 前段保留静音前半 (500-320)/2=90ms；总停顿 = 90 + 320(时间轴补) + 90 = 自然停顿 500ms
    assert clips[0][0].shape[0] == 1090  # 1000 语音 + 90 静音前半（淡出只缩幅不改长度）
    assert abs(clips[0][1] - 0.5) < 1e-6  # 自然停顿回填值


def test_split_block_pcm_floor_when_silence_short():
    import numpy as np

    sr = 1000
    pcm = np.concatenate(
        [
            np.ones(1000, np.float32),
            np.zeros(200, np.float32),
            np.ones(1000, np.float32),
        ]
    )
    clips = split_block_pcm(pcm, sr, [(1.0, 1.2)], discard_sec=0.32)
    # 静音 0.2 < 0.32+2×0.03 → 退化为丢 S−0.06=0.14，各留 30ms
    assert clips[0][0].shape[0] == 1030


def test_split_block_pcm_last_clip_fades_in_at_cut():
    """末段起点即末个切点：静音只是低于阈值（非零底噪），须与中间段同样淡入，否则起播阶跃。"""
    import numpy as np

    sr = 1000
    pcm = np.full(2500, 0.01, np.float32)  # 全程底噪：切点处样本非零
    clips = split_block_pcm(pcm, sr, [(1.0, 1.5)], discard_sec=0.32)
    fade = int(sr * 0.005)
    for clip, _ in clips:
        assert clip[0] == 0.0 and clip[-1] == 0.0
        assert clip[fade] == np.float32(0.01)  # 淡变只罩 5ms，不改长度与正文幅度


def test_split_block_pcm_tail_pad():
    import numpy as np

    sr = 1000
    pcm = np.ones(1000, np.float32)
    clips = split_block_pcm(pcm, sr, [], discard_sec=0.32, tail_pad_sec=0.18)
    assert clips[0][0].shape[0] == 1000 + 180


def test_split_block_pcm_tail_pad_defaults_to_none():
    """缺省不补尾垫：服务端对未给 tail_pad_sec 的请求走这条默认，改了即改所有块的时长。"""
    import numpy as np

    pcm = np.ones(1000, np.float32)
    clips = split_block_pcm(pcm, 1000, [], discard_sec=0.32)
    assert clips[0][0].shape[0] == 1000


def test_digest_block_none_is_byte_identical_to_legacy():
    base = digest_indextts(
        "aa" * 6, "sunny", [1.0] + [0] * 7, 0.35, 0.95, "ZH", "indextts", "x"
    )
    again = digest_indextts(
        "aa" * 6,
        "sunny",
        [1.0] + [0] * 7,
        0.35,
        0.95,
        "ZH",
        "indextts",
        "x",
        block=None,
        pos=None,
    )
    assert base == again


def test_digest_block_suffix_changes_and_is_deterministic():
    a = digest_indextts(
        "aa" * 6,
        "story",
        [0.5] + [0] * 7,
        0.28,
        1.0,
        "ZH",
        "indextts",
        "x",
        block="ab12cd34ef56",
        pos="1/3",
    )
    b = digest_indextts(
        "aa" * 6,
        "story",
        [0.5] + [0] * 7,
        0.28,
        1.0,
        "ZH",
        "indextts",
        "x",
        block="ab12cd34ef56",
        pos="1/3",
    )
    c = digest_indextts(
        "aa" * 6,
        "story",
        [0.5] + [0] * 7,
        0.28,
        1.0,
        "ZH",
        "indextts",
        "x",
        block="ab12cd34ef56",
        pos="2/3",
    )
    assert a == b and a != c


def test_block_digest_suffix_tracks_member_text():
    s1 = block_digest_suffix(["a", "b"], 0.32, 0.18)
    s2 = block_digest_suffix(["a", "c"], 0.32, 0.18)
    s3 = block_digest_suffix(["a", "b"], 0.4, 0.18)
    assert len(s1) == 12 and s1 != s2 and s1 != s3


def test_story_preset_invariants():
    p = STYLE_PRESETS["story"]
    assert abs(sum(p["vec"]) - 1.0) < 1e-6  # nominal-sum 门（test_digest 同口径）
    assert p["alpha"] * sum(p["vec"]) <= 0.8
    assert isinstance(p["seed"], int)
    for k in ("max_sentences", "max_chars", "tail_pad_sec", "perform_punct"):
        assert k in p["block"]


def test_tts_text_perform_keeps_ellipsis():
    assert tts_text("可这一试……", "ZH") == "可这一试。"
    assert tts_text("可这一试……", "ZH", perform=True) == "可这一试…"
    assert tts_text("破折——号", "ZH", perform=True) == "破折，号"
    assert tts_text("x……", "EN", perform=True) == "x……"  # en 不做映射


def test_block_suffix_versioned_past_v2():
    """v3 改了块文本拼接与兜底尾垫：同成员文本也必须换键（旧 v2 产物不得命中）。"""
    import hashlib

    v2 = hashlib.sha1(("a\x1fb\x1f0.32\x1f0.18\x1fsplit=v2").encode()).hexdigest()[:12]
    assert block_digest_suffix(["a", "b"], 0.32, 0.18) != v2


def test_frame_db_and_silence_runs_roundtrip():
    import numpy as np

    sr = 22050
    pcm = np.concatenate(
        [
            np.random.default_rng(0).normal(0, 0.3, int(sr * 1.0)).astype(np.float32),
            np.zeros(int(sr * 0.4), np.float32),
            np.random.default_rng(1).normal(0, 0.3, int(sr * 1.0)).astype(np.float32),
        ]
    )
    db = frame_db(pcm, sr)
    runs, on, off = silence_runs(db, sr)
    assert (
        len(runs) == 1
        and abs(runs[0][0] - 1.0) < 0.05
        and abs((runs[0][1] - runs[0][0]) - 0.4) < 0.05
    )
    assert abs(on) < 0.05
