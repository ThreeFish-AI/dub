"""内容寻址版本库：回收等价命中、digest 失配必 miss、历史并存、禁用直通、环境变量解析。"""

from __future__ import annotations

from pathlib import Path

import dub_engine_indextts as ix
from dub_engine_indextts import store_deposit, store_entry, store_has, store_restore


def _mk(root: Path, name: str, size: int = 64) -> Path:
    p = root / name
    p.write_bytes(bytes(range(size % 251)))
    return p


def test_roundtrip_deposit_then_restore(tmp_path):
    audio, store = tmp_path / "audio", tmp_path / "store"
    audio.mkdir()
    src = _mk(tmp_path, "c0.mp3")
    store_deposit(src, "c0", "d" * 40, store, "say")
    assert store_has(store, "say", "c0", "d" * 40)
    assert store_restore("c0", "d" * 40, audio, store, "say")
    assert (audio / "c0.mp3").read_bytes() == src.read_bytes()
    assert (audio / "c0.sha").read_text() == "d" * 40


def test_digest_mismatch_is_miss(tmp_path):
    audio, store = tmp_path / "audio", tmp_path / "store"
    audio.mkdir()
    store_deposit(_mk(tmp_path, "c1.mp3"), "c1", "a" * 40, store, "say")
    assert not store_has(store, "say", "c1", "b" * 40)
    assert not store_restore("c1", "b" * 40, audio, store, "say")
    assert not (audio / "c1.mp3").exists()


def test_history_versions_coexist(tmp_path):
    store = tmp_path / "store"
    v1, v2 = _mk(tmp_path, "v1.mp3", 10), _mk(tmp_path, "v2.mp3", 200)
    store_deposit(v1, "c", "1" * 40, store, "s")
    store_deposit(v2, "c", "2" * 40, store, "s")
    assert store_entry(store, "s", "c", "1" * 40).read_bytes() == v1.read_bytes()
    assert store_entry(store, "s", "c", "2" * 40).read_bytes() == v2.read_bytes()


def test_sha_sidecar_guards_against_prefix_collision(tmp_path):
    """文件名只用 digest 前 12 位：前缀相同但全量不同必须判 miss。"""
    store = tmp_path / "store"
    store_deposit(_mk(tmp_path, "x.mp3"), "c", "a" * 12 + "1" * 28, store, "s")
    assert not store_has(store, "s", "c", "a" * 12 + "2" * 28)


def test_disabled_store_is_passthrough(tmp_path):
    audio = tmp_path / "audio"
    audio.mkdir()
    src = _mk(tmp_path, "c.mp3")
    store_deposit(src, "c", "d" * 40, None, "s")  # 不抛、不落盘
    assert store_entry(None, "s", "c", "d" * 40) is None
    assert not store_has(None, "s", "c", "d" * 40)
    assert not store_restore("c", "d" * 40, audio, None, "s")


def test_deposit_leaves_no_temp_files(tmp_path):
    store = tmp_path / "store"
    store_deposit(_mk(tmp_path, "c.mp3"), "c", "d" * 40, store, "s")
    assert not list((store / "s").glob(".*.tmp"))


# ---------------- store_root 解析 ----------------


def test_store_root_default_under_data_dir(tmp_path):
    assert ix.store_root() == tmp_path / "data" / "tts-store"


def test_store_root_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("DUB_STORE", str(tmp_path / "elsewhere"))
    assert ix.store_root() == tmp_path / "elsewhere"


def test_store_root_empty_env_disables(monkeypatch):
    monkeypatch.setenv("DUB_STORE", "")
    assert ix.store_root() is None


def test_store_root_disabled_flag_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("DUB_STORE", str(tmp_path / "x"))
    assert ix.store_root(disabled=True) is None


def test_default_server_env_override(monkeypatch):
    assert ix.default_server() == "http://127.0.0.1:8766"
    monkeypatch.setenv("DUB_INDEXTTS_SERVER", "http://127.0.0.1:9999")
    assert ix.default_server() == "http://127.0.0.1:9999"
