"""版本库的异常路径与原子发布——自 vibe-video tests/test_tts_store.py 迁入。

读失败降级为 miss、回收失败降级为 miss、入库失败不覆盖已发布条目、同目录 replace 发布。
与 test_store.py（回收/失配/历史并存/禁用/环境变量）互补。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from dub_engine_indextts import store_deposit, store_entry, store_has, store_restore


def _mk(root: Path, name: str, size: int = 64) -> Path:
    """占位音频：store 只搬运字节不解析，内容无语义。"""
    p = root / name
    p.write_bytes(bytes(range(size % 251)))
    return p


def test_same_digest_deposit_refreshes(tmp_path):
    store = tmp_path / "store"
    old, new = _mk(tmp_path, "old.mp3", 10), _mk(tmp_path, "new.mp3", 230)
    d = "e" * 40
    store_deposit(old, "p3-01", d, store, "ep")
    store_deposit(
        new, "p3-01", d, store, "ep"
    )  # 同 digest 重跑（--force）→ 刷新为新音频
    assert store_entry(store, "ep", "p3-01", d).read_bytes() == new.read_bytes()


def test_store_read_error_degrades_to_miss(monkeypatch, tmp_path):
    store = tmp_path / "store"
    src = _mk(tmp_path, "p2-01.mp3")
    digest = "f" * 40
    store_deposit(src, "p2-01", digest, store, "ep")
    original = Path.read_text

    def broken_read(path, *args, **kwargs):
        if path.suffix == ".sha" and store in path.parents:
            raise OSError("store unavailable")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", broken_read)
    assert not store_has(store, "ep", "p2-01", digest)


def test_store_restore_error_degrades_to_miss(monkeypatch, tmp_path):
    audio, store = tmp_path / "audio", tmp_path / "store"
    audio.mkdir()
    src = _mk(tmp_path, "p2-02.mp3")
    digest = "a" * 40
    store_deposit(src, "p2-02", digest, store, "ep")
    monkeypatch.setattr(
        shutil,
        "copyfile",
        lambda _src, _dst: (_ for _ in ()).throw(OSError("read failed")),
    )
    assert not store_restore("p2-02", digest, audio, store, "ep")


def test_deposit_failure_does_not_replace_published_entry(monkeypatch, tmp_path):
    store = tmp_path / "store"
    old = _mk(tmp_path, "old-atomic.mp3", 20)
    new = _mk(tmp_path, "new-atomic.mp3", 200)
    digest = "b" * 40
    store_deposit(old, "p3-02", digest, store, "ep")
    dst = store_entry(store, "ep", "p3-02", digest)
    before = dst.read_bytes()

    def broken_copy(_src, target):
        Path(target).write_bytes(b"partial")
        raise OSError("disk full")

    monkeypatch.setattr(shutil, "copyfile", broken_copy)
    store_deposit(new, "p3-02", digest, store, "ep")

    assert dst.read_bytes() == before
    assert dst.with_suffix(".sha").read_text() == digest


def test_deposit_publishes_with_same_directory_replace(monkeypatch, tmp_path):
    store = tmp_path / "store"
    src = _mk(tmp_path, "atomic.mp3", 80)
    real_replace = os.replace
    replacements: list[tuple[Path, Path]] = []

    def record_replace(source, target):
        source, target = Path(source), Path(target)
        replacements.append((source, target))
        assert source.parent == target.parent
        real_replace(source, target)

    monkeypatch.setattr(os, "replace", record_replace)
    store_deposit(src, "p3-03", "c" * 40, store, "ep")

    assert len(replacements) == 2
