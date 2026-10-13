"""分块拼接的算术：段数、静音时长、采样率一致性、原子写——全部用合成 PCM，不碰任何 TTS。"""

from __future__ import annotations

import io

import pytest

np = pytest.importorskip("numpy")
sf = pytest.importorskip("soundfile")

from vibe_tts_audio import concat_mp3, write_atomic  # noqa: E402

SR = 22050


def _mp3(seconds: float, freq: float = 440.0, sr: int = SR) -> bytes:
    t = np.arange(int(seconds * sr)) / sr
    data = (0.3 * np.sin(2 * np.pi * freq * t)).astype("float32")
    buf = io.BytesIO()
    sf.write(buf, data, sr, format="MP3")
    return buf.getvalue()


def _dur(path) -> float:
    info = sf.info(str(path))
    return info.frames / info.samplerate


def test_single_chunk_is_passthrough_without_reencode(tmp_path):
    raw = _mp3(1.0)
    dst = tmp_path / "o.mp3"
    concat_mp3([raw], 0.2, dst)
    assert dst.read_bytes() == raw


def test_concat_duration_is_sum_plus_gaps(tmp_path):
    dst = tmp_path / "o.mp3"
    concat_mp3([_mp3(1.0), _mp3(1.0), _mp3(1.0)], 0.2, dst)
    # 3 段 + 2 个间隙；MP3 编解码有帧级量化，给 80 ms 容差
    assert _dur(dst) == pytest.approx(3.4, abs=0.08)


def test_concat_zero_gap_is_gapless(tmp_path):
    dst = tmp_path / "o.mp3"
    concat_mp3([_mp3(1.0), _mp3(1.0)], 0.0, dst)
    assert _dur(dst) == pytest.approx(2.0, abs=0.08)


def test_gap_is_actually_silent(tmp_path):
    a, b = _mp3(1.0, 440), _mp3(1.0, 880)
    dst = tmp_path / "o.mp3"
    concat_mp3([a, b], 0.5, dst)
    data, sr = sf.read(str(dst), dtype="float32")
    mid = data[int(1.1 * sr) : int(1.4 * sr)]  # 避开编解码边界的前后各 100 ms
    assert float(np.abs(mid).max()) < 0.02


def test_concat_mismatched_sample_rate_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="采样率"):
        concat_mp3([_mp3(0.5, sr=22050), _mp3(0.5, sr=24000)], 0.2, tmp_path / "o.mp3")


def test_concat_empty_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="没有可拼接"):
        concat_mp3([], 0.2, tmp_path / "o.mp3")


def test_concat_failure_leaves_no_partial_file(tmp_path):
    dst = tmp_path / "o.mp3"
    with pytest.raises(ValueError):
        concat_mp3([_mp3(0.5, sr=22050), _mp3(0.5, sr=24000)], 0.2, dst)
    assert not dst.exists()


# ---------------- write_atomic ----------------


def test_write_atomic_creates_parents_and_cleans_temp(tmp_path):
    dst = tmp_path / "a" / "b" / "o.bin"
    write_atomic(b"xyz", dst)
    assert dst.read_bytes() == b"xyz"
    assert not [p for p in dst.parent.iterdir() if p.name.startswith(".")]


def test_write_atomic_final_mode_is_world_readable(tmp_path):
    """NamedTemporaryFile 缺省 0600：成品须与 edge 产物同为 0644。"""
    dst = tmp_path / "o.bin"
    write_atomic(b"x", dst)
    assert (dst.stat().st_mode & 0o777) == 0o644


def test_write_atomic_overwrites_existing(tmp_path):
    dst = tmp_path / "o.bin"
    dst.write_bytes(b"old")
    write_atomic(b"new", dst)
    assert dst.read_bytes() == b"new"
