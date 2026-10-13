"""vendored 服务端的 HTTP 契约：不加载模型（TestClient 不进 lifespan），直接注入 STATE。

覆盖的是服务端「在碰模型之前」就拒绝请求的全部分支，以及 /health 的形状——
这些是客户端（dub 与 vibe-video）依赖的契约面，改动即意味着两边都要跟着变。
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("numpy")
pytest.importorskip("soundfile")

from dub_tts_server import STATE, app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(app)  # 不用 with：避免触发 lifespan（加载模型）


@pytest.fixture
def ready(tmp_path):
    """注入一个「已就绪」的 STATE；用例结束清空，避免污染别的用例。"""
    ref = tmp_path / "ref.wav"
    ref.write_bytes(b"RIFF")
    STATE.update(
        version="2.5",
        device="mps",
        dtype="fp32",
        encoder="soundfile-lame",
        supports_duration_factor=True,
        supports_text_normalization=True,
        supports_blocks=True,
        supports_emo_text=False,
        low_vram=False,
        mps_mem_limit_gib=15.98,
        infer_lock=asyncio.Lock(),
    )
    yield ref
    STATE.clear()


def _post(**kw):
    return client.post("/synthesize", json=kw)


# ---------------- /health ----------------


def test_health_shape(ready):
    body = client.get("/health").json()
    assert body["ok"] is True
    for key in (
        "version",
        "device",
        "mps_mem_limit_gib",
        "synthesizing",
        "dtype",
        "encoder",
        "supports_duration_factor",
        "supports_emo_text",
        "supports_text_normalization",
        "supports_sampling_params",
        "supports_seed",
        "supports_blocks",
        "low_vram",
    ):
        assert key in body, key
    assert body["version"] == "2.5"
    assert body["supports_sampling_params"] is True and body["supports_seed"] is True


def test_health_synthesizing_reflects_lock(ready):
    """watchdog 判忙依赖这个字段：锁被占即 true。"""
    assert client.get("/health").json()["synthesizing"] is False

    async def held():
        async with STATE["infer_lock"]:
            return client.get("/health").json()["synthesizing"]

    assert asyncio.run(held()) is True


# ---------------- /synthesize：模型加载前的拒绝路径 ----------------


def test_503_when_model_not_loaded():
    STATE.clear()
    r = _post(text="x", ref_path="/nonexistent.wav")
    assert r.status_code == 503


def test_400_when_ref_missing(ready):
    r = _post(text="x", ref_path="/definitely/not/here.wav")
    assert r.status_code == 400 and "参考音频不存在" in r.json()["detail"]


def test_400_emotion_sources_are_mutually_exclusive(ready):
    r = _post(
        text="x",
        ref_path=str(ready),
        emo_vector=[0.1] * 8,
        emo_text="开心",
        emo_alpha=0.1,
    )
    assert r.status_code == 400 and "互斥" in r.json()["detail"]


def test_400_emo_text_needs_qwen(ready):
    r = _post(text="x", ref_path=str(ready), emo_text="轻快")
    assert r.status_code == 400 and "--use-qwen-emo" in r.json()["detail"]


def test_400_effective_sum_over_guard(ready):
    """Σvec×alpha > 0.8 即拒：story 预设的有效和是 0.28，远在线内。"""
    r = _post(
        text="x",
        ref_path=str(ready),
        emo_vector=[1, 0, 0, 0, 0, 0, 0, 0],
        emo_alpha=0.9,
    )
    assert r.status_code == 400 and "0.8" in r.json()["detail"]


def test_story_effective_sum_is_within_guard():
    from dub_profiles import STYLE_PRESETS

    p = STYLE_PRESETS["story"]
    assert sum(p["vec"]) * p["alpha"] < 0.8


def test_400_blocks_unsupported(ready):
    STATE["supports_blocks"] = False
    r = _post(
        text="x",
        ref_path=str(ready),
        block={"weights": [1.0, 1.0], "discard_sec": 0.32, "tail_pad_sec": 0.18},
    )
    assert r.status_code == 400 and "块切分不可用" in r.json()["detail"]


def test_400_duration_factor_needs_v25(ready):
    STATE["supports_duration_factor"] = False
    r = _post(text="x", ref_path=str(ready), duration_factor=1.2)
    assert r.status_code == 400 and "duration_factor" in r.json()["detail"]


def test_400_text_normalization_off_needs_v25(ready):
    STATE["supports_text_normalization"] = False
    r = _post(text="x", ref_path=str(ready), text_normalization=False)
    assert r.status_code == 400 and "text_normalization" in r.json()["detail"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("num_beams", 0),
        ("num_beams", 6),
        ("top_k", 1),  # 束搜索下不安全，被显式拒收
        ("max_mel_tokens", 1816),
        ("temperature", 0.0),
        ("emo_alpha", 1.5),
        ("duration_factor", 0.4),
    ],
)
def test_422_out_of_range_fields(ready, field, value):
    r = _post(text="x", ref_path=str(ready), **{field: value})
    assert r.status_code == 422, (field, value, r.status_code)


def test_missing_required_fields_is_422():
    assert client.post("/synthesize", json={}).status_code == 422


# ---------------- 与客户端共享的常量 ----------------


def test_server_sampling_defaults_come_from_dub_engine():
    """服务端字段默认值与客户端「是否被改过」判定必须是同一份数据。"""
    import dub_engine_indextts as ix
    import dub_tts_server as srv

    assert srv.SAMPLING_DEFAULTS is ix.SAMPLING_PASSTHROUGH_DEFAULTS
    d = srv.SynthesizeRequest(text="x", ref_path="/x")
    for k, v in ix.SAMPLING_PASSTHROUGH_DEFAULTS.items():
        assert getattr(d, k) == v, k


def test_server_has_no_dependency_on_vibe_video_modules():
    import ast
    import pathlib

    src = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "dub_tts_server.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    mods = {
        (n.module or "").split(".")[0]
        for n in ast.walk(tree)
        if isinstance(n, ast.ImportFrom)
    } | {
        a.name.split(".")[0]
        for n in ast.walk(tree)
        if isinstance(n, ast.Import)
        for a in n.names
    }
    assert "tts" not in mods, "服务端不得再依赖 vibe-video 的 tts.py"
    assert {m for m in mods if m.startswith("dub_")} <= {"dub_tts_blocks"}
