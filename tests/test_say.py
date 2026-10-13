"""vibe_tts.py say 的编排：授权闸、输出目录、缓存、分块与服务收尾——网络与服务全部 mock。"""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

import vibe_tts
import vibe_tts_engine_indextts as ix
import pytest

np = pytest.importorskip("numpy")
sf = pytest.importorskip("soundfile")
pytest.importorskip("mutagen")

SR = 22050


def _mp3(seconds: float = 0.5) -> bytes:
    t = np.arange(int(seconds * SR)) / SR
    buf = io.BytesIO()
    sf.write(
        buf, (0.3 * np.sin(2 * np.pi * 440 * t)).astype("float32"), SR, format="MP3"
    )
    return buf.getvalue()


def _run(capsys, *argv):
    rc = vibe_tts.main(["say", *argv])
    out, err = capsys.readouterr()
    return rc, out, err


def _receipt(out: str) -> dict:
    return json.loads(out.strip().splitlines()[-1])


# ---------------- 纯函数 ----------------


@pytest.mark.parametrize(
    ("text", "slug"),
    [
        ("今天我们拆解的是：Agent Skills", "今天我们拆解的是-agent-skills"),
        ("  Hello,   World!  ", "hello-world"),
        ("！！！", "vibe-tts"),
        ("", "vibe-tts"),
        ("甲" * 100, "甲" * vibe_tts.SLUG_MAX),
    ],
)
def test_slugify(text, slug):
    assert vibe_tts.slugify(text) == slug


def test_say_digest_changes_with_any_part():
    base = vibe_tts.say_digest("edge", ["v", "r", "ZH", "t"])
    assert base == vibe_tts.say_digest("edge", ["v", "r", "ZH", "t"])
    assert base != vibe_tts.say_digest("edge", ["v2", "r", "ZH", "t"])
    assert base != vibe_tts.say_digest("index-tts", ["v", "r", "ZH", "t"])


def test_sha1_12_matches_shasum(tmp_path):
    import hashlib

    f = tmp_path / "a.wav"
    f.write_bytes(b"hello")
    assert vibe_tts.sha1_12(f) == hashlib.sha1(b"hello").hexdigest()[:12]


# ---------------- 公共：文本与输出目录 ----------------


def test_empty_text_is_usage_error(capsys, tmp_path):
    rc, _, err = _run(capsys, "--text", "   ", "--out-dir", str(tmp_path))
    assert rc == 2 and "文本为空" in err


def test_missing_output_dir_exits_loudly(capsys):
    with pytest.raises(SystemExit) as e:
        vibe_tts.main(["say", "--text", "你好"])
    assert "VIBE_TTS_OUTPUT_DIR" in str(e.value)


def test_text_file_is_read(capsys, tmp_path, monkeypatch):
    f = tmp_path / "t.txt"
    f.write_text("来自文件的文本", encoding="utf-8")
    seen = {}

    async def fake(text, voice, rate, dst, lang="ZH", retries=4):
        seen["text"] = text
        dst.write_bytes(_mp3())

    monkeypatch.setattr(vibe_tts.vibe_tts_engine_edge, "synth_text", fake)
    rc, out, _ = _run(capsys, "--text-file", str(f), "--out-dir", str(tmp_path / "o"))
    assert rc == 0 and seen["text"] == "来自文件的文本"


# ---------------- edge ----------------


@pytest.fixture
def fake_edge(monkeypatch):
    calls = []

    async def fake(text, voice, rate, dst, lang="ZH", retries=4):
        calls.append({"text": text, "voice": voice, "rate": rate, "lang": lang})
        dst.write_bytes(_mp3())

    monkeypatch.setattr(vibe_tts.vibe_tts_engine_edge, "synth_text", fake)
    return calls


def test_edge_receipt_and_defaults(capsys, tmp_path, fake_edge):
    rc, out, _ = _run(
        capsys, "--text", "今天我们拆解的是：Agent Skills", "--out-dir", str(tmp_path)
    )
    r = _receipt(out)
    assert rc == 0
    assert (
        r["engine"] == "edge" and r["style"] == "zh-CN-YunxiNeural" and r["chunks"] == 1
    )
    assert r["cached"] is False and r["durationSec"] > 0
    assert r["path"].startswith(str(tmp_path)) and r["path"].endswith(".mp3")
    assert (
        fake_edge[0]["rate"] == "+4%" and fake_edge[0]["voice"] == "zh-CN-YunxiNeural"
    )


def test_edge_en_uses_en_default_voice(capsys, tmp_path, fake_edge):
    _run(capsys, "--text", "Hello world", "--lang", "en", "--out-dir", str(tmp_path))
    assert (
        fake_edge[0]["voice"] == "en-US-AndrewNeural" and fake_edge[0]["lang"] == "EN"
    )


def test_edge_voice_and_rate_override(capsys, tmp_path, fake_edge):
    _run(
        capsys,
        "--text",
        "你好",
        "--voice",
        "zh-CN-XiaoxiaoNeural",
        "--rate=-10%",
        "--out-dir",
        str(tmp_path),
    )
    assert (fake_edge[0]["voice"], fake_edge[0]["rate"]) == (
        "zh-CN-XiaoxiaoNeural",
        "-10%",
    )


def test_edge_second_run_is_cached_and_skips_synth(capsys, tmp_path, fake_edge):
    args = ("--text", "你好", "--out-dir", str(tmp_path))
    _run(capsys, *args)
    rc, out, _ = _run(capsys, *args)
    assert _receipt(out)["cached"] is True and len(fake_edge) == 1


def test_edge_force_resynthesizes(capsys, tmp_path, fake_edge):
    args = ("--text", "你好", "--out-dir", str(tmp_path))
    _run(capsys, *args)
    _run(capsys, *args, "--force")
    assert len(fake_edge) == 2


def test_edge_different_params_do_not_share_cache(capsys, tmp_path, fake_edge):
    _run(capsys, "--text", "你好", "--out-dir", str(tmp_path))
    _run(capsys, "--text", "你好", "--rate=+20%", "--out-dir", str(tmp_path))
    assert len(fake_edge) == 2 and len(list(tmp_path.glob("*.mp3"))) == 2


def test_out_name_is_respected(capsys, tmp_path, fake_edge):
    _, out, _ = _run(
        capsys, "--text", "你好", "--out-name", "intro", "--out-dir", str(tmp_path)
    )
    assert _receipt(out)["path"] == str(tmp_path / "intro.mp3")


@pytest.mark.parametrize("name", ["../escape", "a/b", "/abs/x", "a\\b", "..", ".", ""])
def test_out_name_cannot_leave_out_dir(capsys, tmp_path, fake_edge, name):
    out_dir = tmp_path / "out"
    rc, _, err = _run(
        capsys, "--text", "你好", "--out-name", name, "--out-dir", str(out_dir)
    )
    assert rc == 2 and "--out-name" in err
    assert fake_edge == [], "校验必须发生在联网合成之前"
    assert not list(tmp_path.rglob("*.mp3")), "不得在任何位置落盘"


@pytest.mark.parametrize("name", ["intro", "v1.2", "配音-01"])
def test_out_name_allows_plain_names(capsys, tmp_path, fake_edge, name):
    rc, out, _ = _run(
        capsys, "--text", "你好", "--out-name", name, "--out-dir", str(tmp_path)
    )
    assert rc == 0 and _receipt(out)["path"] == str(tmp_path / f"{name}.mp3")


# ---------------- index-tts：授权与前置校验 ----------------


@pytest.fixture
def ref(tmp_path):
    p = tmp_path / "ref.wav"
    p.write_bytes(b"not-a-real-wav-but-fingerprintable")
    return p


def test_index_tts_requires_final_voice(capsys, tmp_path, ref):
    rc, _, err = _run(
        capsys,
        "--engine",
        "index-tts",
        "--text",
        "你好",
        "--ref",
        str(ref),
        "--out-dir",
        str(tmp_path),
    )
    assert rc == 2 and "--final-voice" in err and "Agent 不得" in err


def test_index_tts_requires_ref(capsys, tmp_path):
    rc, _, err = _run(
        capsys,
        "--engine",
        "index-tts",
        "--final-voice",
        "--text",
        "你好",
        "--out-dir",
        str(tmp_path),
    )
    assert rc == 2 and "--ref" in err


def test_index_tts_missing_ref_file(capsys, tmp_path):
    rc, _, err = _run(
        capsys, "--engine", "index-tts", "--final-voice", "--text", "你好",
        "--ref", str(tmp_path / "nope.wav"), "--out-dir", str(tmp_path),
    )  # fmt: skip
    assert rc == 2 and "不存在" in err


def test_index_tts_fingerprint_mismatch(capsys, tmp_path, ref):
    rc, _, err = _run(
        capsys, "--engine", "index-tts", "--final-voice", "--text", "你好", "--ref", str(ref),
        "--expect-ref-sha1", "000000000000", "--out-dir", str(tmp_path),
    )  # fmt: skip
    assert rc == 2 and "指纹不符" in err


def test_index_tts_unknown_style(capsys, tmp_path, ref):
    rc, _, err = _run(
        capsys, "--engine", "index-tts", "--final-voice", "--text", "你好", "--ref", str(ref),
        "--style", "nope", "--out-dir", str(tmp_path),
    )  # fmt: skip
    assert rc == 2 and "未知风格" in err and "story" in err


# ---------------- index-tts：编排（服务与 HTTP 全 mock） ----------------


@pytest.fixture
def fake_service(monkeypatch):
    """替掉服务起停与 HTTP：记录每次调用，返回合成的 mp3 字节。"""
    log = {"synth": [], "start": 0, "stop": 0, "started_flag": True}

    def start_server(server, **kw):
        log["start"] += 1
        return {
            "started": log["started_flag"],
            "reused": not log["started_flag"],
            "port": 8766,
        }

    def stop_or_watch(server, **kw):
        log["stop"] += 1
        return {"action": "stopped", "port": 8766}

    def http_synthesize(server, text, ref, vec, alpha, df, lang, num_beams=1, **kw):
        log["synth"].append(
            {
                "text": text,
                "vec": vec,
                "alpha": alpha,
                "df": df,
                "lang": lang,
                "beams": num_beams,
                "kw": kw,
            }
        )
        return _mp3(0.5), "mp3"

    monkeypatch.setattr(vibe_tts.ctl, "start_server", start_server)
    monkeypatch.setattr(vibe_tts.ctl, "stop_or_watch", stop_or_watch)
    monkeypatch.setattr(vibe_tts.ix, "http_synthesize", http_synthesize)
    return log


def _ix_args(tmp_path, ref, *extra):
    return (
        "--engine", "index-tts", "--final-voice", "--ref", str(ref),
        "--out-dir", str(tmp_path / "out"), *extra,
    )  # fmt: skip


def test_index_tts_story_single_sentence(capsys, tmp_path, ref, fake_service):
    rc, out, _ = _run(
        capsys, "--text", "今天我们拆解的是：Agent Skills", *_ix_args(tmp_path, ref)
    )
    r = _receipt(out)
    assert (
        rc == 0
        and r["engine"] == "index-tts"
        and r["style"] == "story"
        and r["chunks"] == 1
    )
    s = fake_service["synth"][0]
    assert s["text"] == "今天我们拆解的是：Agent Skills。", "块末补 。"
    assert (
        s["vec"] == [1 / 3, 0, 0, 0, 0, 0, 2 / 3, 0]
        and s["alpha"] == 0.28
        and s["df"] == 1.0
    )
    assert s["kw"]["sampling"] == {"seed": 4242}, "story 预设自带 seed 必须下发"
    assert (fake_service["start"], fake_service["stop"]) == (1, 1)


def test_index_tts_long_text_is_chunked_and_joined(capsys, tmp_path, ref, fake_service):
    text = "这是第一句话。" * 12
    rc, out, _ = _run(capsys, "--text", text, *_ix_args(tmp_path, ref))
    r = _receipt(out)
    assert r["chunks"] == len(fake_service["synth"]) > 1
    assert all(len(c["text"]) <= 90 + 1 for c in fake_service["synth"])
    assert "".join(c["text"] for c in fake_service["synth"]) == text
    # 块间补 200 ms 静音：总时长 ≈ n×0.5 + (n-1)×0.2
    n = r["chunks"]
    assert r["durationSec"] == pytest.approx(n * 0.5 + (n - 1) * 0.2, abs=0.15)


def test_index_tts_cached_run_does_not_touch_service(
    capsys, tmp_path, ref, fake_service
):
    args = ("--text", "你好。", *_ix_args(tmp_path, ref))
    _run(capsys, *args)
    fake_service["synth"].clear()
    fake_service.update(start=0, stop=0)
    _, out, _ = _run(capsys, *args)
    assert _receipt(out)["cached"] is True
    assert fake_service["synth"] == [] and fake_service["start"] == 0, (
        "整段缓存命中不得拉起服务"
    )


def test_index_tts_store_restores_chunks_without_service(
    capsys, tmp_path, ref, fake_service
):
    """成品被删但版本库还在：回收各块拼接，仍不拉起服务。"""
    args = ("--text", "你好。", *_ix_args(tmp_path, ref))
    _, out, _ = _run(capsys, *args)
    produced = _receipt(out)["path"]
    import os

    os.remove(produced)
    fake_service["synth"].clear()
    fake_service.update(start=0, stop=0)
    _, out, _ = _run(capsys, *args)
    assert _receipt(out)["cached"] is False and os.path.exists(produced)
    assert fake_service["synth"] == [] and fake_service["start"] == 0


def test_index_tts_force_bypasses_store(capsys, tmp_path, ref, fake_service):
    args = ("--text", "你好。", *_ix_args(tmp_path, ref))
    _run(capsys, *args)
    fake_service["synth"].clear()
    _run(capsys, *args, "--force")
    assert len(fake_service["synth"]) == 1


def test_index_tts_no_store_flag(capsys, tmp_path, ref, fake_service):
    import os

    _run(capsys, "--text", "你好。", *_ix_args(tmp_path, ref), "--no-store")
    store = ix.store_root()
    assert store is not None and not os.path.exists(store / "say"), (
        "--no-store 不得写版本库"
    )


def test_index_tts_stops_only_instance_it_started(capsys, tmp_path, ref, fake_service):
    fake_service["started_flag"] = False  # 复用别人起的服务
    _run(capsys, "--text", "你好。", *_ix_args(tmp_path, ref))
    assert fake_service["stop"] == 0, "复用的服务不归我们停"


def test_index_tts_keep_server_flag(capsys, tmp_path, ref, fake_service):
    _run(capsys, "--text", "你好。", *_ix_args(tmp_path, ref), "--keep-server")
    assert fake_service["stop"] == 0


def test_index_tts_stops_server_even_when_synthesis_fails(
    capsys, tmp_path, ref, fake_service, monkeypatch
):
    def boom(*a, **k):
        raise ix.NonRetryableError("HTTP 400: 参考音频不存在")

    monkeypatch.setattr(vibe_tts.ix, "http_synthesize", boom)
    rc, _, err = _run(capsys, "--text", "你好。", *_ix_args(tmp_path, ref))
    assert rc == 1 and "400" in err
    assert fake_service["stop"] == 1, "失败也必须收尾我们拉起的服务"


def test_index_tts_rejects_non_mp3_response(
    capsys, tmp_path, ref, fake_service, monkeypatch
):
    monkeypatch.setattr(
        vibe_tts.ix, "http_synthesize", lambda *a, **k: (b"RIFF", "wav")
    )
    rc, _, err = _run(capsys, "--text", "你好。", *_ix_args(tmp_path, ref))
    assert rc == 1 and "wav" in err


def test_index_tts_retries_transient_errors_but_not_4xx(
    capsys, tmp_path, ref, fake_service, monkeypatch
):
    monkeypatch.setattr("time.sleep", lambda s: None)
    attempts = {"n": 0}

    def flaky(*a, **k):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("连接失败")
        return _mp3(0.5), "mp3"

    monkeypatch.setattr(vibe_tts.ix, "http_synthesize", flaky)
    rc, _, _ = _run(capsys, "--text", "你好。", *_ix_args(tmp_path, ref))
    assert rc == 0 and attempts["n"] == 3

    attempts["n"] = 0

    def fatal(*a, **k):
        attempts["n"] += 1
        raise ix.NonRetryableError("HTTP 400: x")

    monkeypatch.setattr(vibe_tts.ix, "http_synthesize", fatal)
    _run(capsys, "--text", "另一句。", *_ix_args(tmp_path, ref))
    assert attempts["n"] == 1, "4xx 不可重试"


def test_index_tts_server_error_becomes_usage_error(capsys, tmp_path, ref, monkeypatch):
    def refuse(server, **kw):
        raise vibe_tts.ctl.ServerError("端口 8766 被非 tts 服务的进程占用")

    monkeypatch.setattr(vibe_tts.ctl, "start_server", refuse)
    rc, _, err = _run(capsys, "--text", "你好。", *_ix_args(tmp_path, ref))
    assert rc == 2 and "占用" in err


def test_index_tts_out_name_escape_is_rejected_before_service_start(
    capsys, tmp_path, ref, fake_service
):
    rc, _, err = _run(
        capsys, "--text", "你好。", *_ix_args(tmp_path, ref), "--out-name", "../x"
    )
    assert rc == 2 and "--out-name" in err
    assert fake_service["start"] == 0, "名字不合法不该白拉起一个 10 GiB 的服务"


# ---------------- index-tts：收尾失败不得吞掉已成功的结果 ----------------


@pytest.mark.parametrize(
    "exc",
    [vibe_tts.ctl.ServerError("lsof 不可用"), subprocess.TimeoutExpired("lsof", 10)],
    ids=["ServerError", "TimeoutExpired"],
)
def test_index_tts_teardown_failure_does_not_mask_success(
    capsys, tmp_path, ref, fake_service, monkeypatch, exc
):
    def boom(server, **kw):
        raise exc

    monkeypatch.setattr(vibe_tts.ctl, "stop_or_watch", boom)
    rc, out, err = _run(capsys, "--text", "你好。", *_ix_args(tmp_path, ref))
    assert rc == 0, "音频已落盘，收尾失败只该警告"
    r = _receipt(out)
    assert r["cached"] is False and Path(r["path"]).is_file()
    assert "服务收尾失败" in err


def test_index_tts_teardown_failure_keeps_original_error(
    capsys, tmp_path, ref, fake_service, monkeypatch
):
    def synth_fail(*a, **k):
        raise ix.NonRetryableError("HTTP 400: 参考音频不存在")

    def stop_fail(server, **kw):
        raise vibe_tts.ctl.ServerError("lsof 不可用")

    monkeypatch.setattr(vibe_tts.ix, "http_synthesize", synth_fail)
    monkeypatch.setattr(vibe_tts.ctl, "stop_or_watch", stop_fail)
    rc, _, err = _run(capsys, "--text", "你好。", *_ix_args(tmp_path, ref))
    assert rc == 1 and "400" in err, "报的应是合成失败这个根因，而非收尾的次生错误"


# ---------------- index-tts：整段缓存键 ----------------


def test_index_tts_whole_cache_key_includes_chunk_gap(
    capsys, tmp_path, ref, fake_service, monkeypatch
):
    """块间静音时长决定拼接产物；改它必须换整段文件名，不能命中旧成品。"""
    args = ("--text", "这是第一句话。" * 12, *_ix_args(tmp_path, ref))
    _, out, _ = _run(capsys, *args)
    first = _receipt(out)
    monkeypatch.setattr(vibe_tts, "CHUNK_GAP_SEC", 0.5)
    _, out, _ = _run(capsys, *args)
    second = _receipt(out)
    assert second["cached"] is False and second["path"] != first["path"]
    assert second["durationSec"] > first["durationSec"], "新静音时长必须真的生效"


# ---------------- styles / server 子命令 ----------------


def test_styles_lists_every_preset(capsys):
    assert vibe_tts.main(["styles"]) == 0
    out = capsys.readouterr().out
    for name in vibe_tts.vibe_tts_profiles.STYLE_PRESETS:
        assert name in out
    assert "zh-CN-YunxiNeural" in out


def test_server_status_when_down(capsys, monkeypatch):
    monkeypatch.setattr(vibe_tts.ctl, "health", lambda s, timeout=5.0: None)
    monkeypatch.setattr(vibe_tts.ctl, "port_listeners", lambda p: [])
    assert vibe_tts.main(["server", "status"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["healthy"] is False and body["listeners"] == []


def test_server_start_error_returns_1(capsys, monkeypatch):
    def refuse(server, **kw):
        raise vibe_tts.ctl.ServerError("nope")

    monkeypatch.setattr(vibe_tts.ctl, "start_server", refuse)
    assert vibe_tts.main(["server", "start"]) == 1
    assert "nope" in capsys.readouterr().err


def test_server_doctor_never_fails(capsys, monkeypatch):
    monkeypatch.setattr(vibe_tts.ctl, "server_healthy", lambda s, timeout=5.0: False)
    assert vibe_tts.main(["server", "doctor"]) == 0
