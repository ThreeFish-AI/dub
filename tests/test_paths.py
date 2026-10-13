"""路径解析：env 优先级与「找不到即大声退出」。"""

from __future__ import annotations

import vibe_tts_paths
import pytest


def test_output_dir_flag_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_TTS_OUTPUT_DIR", str(tmp_path / "env"))
    assert vibe_tts_paths.output_dir(str(tmp_path / "flag")) == tmp_path / "flag"


def test_output_dir_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_TTS_OUTPUT_DIR", str(tmp_path / "env"))
    assert vibe_tts_paths.output_dir(None) == tmp_path / "env"


def test_output_dir_missing_exits_loudly():
    with pytest.raises(SystemExit) as e:
        vibe_tts_paths.output_dir(None)
    assert "VIBE_TTS_OUTPUT_DIR" in str(e.value) and "--out-dir" in str(e.value)


def test_output_dir_expands_tilde(monkeypatch):
    monkeypatch.setenv("VIBE_TTS_OUTPUT_DIR", "~/Documents/vibe-tts-output")
    assert "~" not in str(vibe_tts_paths.output_dir(None))


def test_data_dir_env_and_default(monkeypatch, tmp_path):
    assert vibe_tts_paths.data_dir() == tmp_path / "data"  # conftest 注入
    monkeypatch.delenv("VIBE_TTS_DATA_DIR")
    assert str(vibe_tts_paths.data_dir()).endswith(
        "Library/Application Support/vibe-tts"
    )


def test_index_tts_root_order(monkeypatch, tmp_path):
    assert str(vibe_tts_paths.index_tts_root()).endswith("tools/index-tts")
    monkeypatch.setenv("VIBE_TTS_INDEX_TTS_ROOT", str(tmp_path / "env"))
    assert vibe_tts_paths.index_tts_root() == tmp_path / "env"
    assert vibe_tts_paths.index_tts_root(str(tmp_path / "arg")) == tmp_path / "arg"


def _fake_skill(root):
    (root / "scripts").mkdir(parents=True)
    (root / "SKILL.md").write_text("x")
    (root / "scripts" / "vibe_tts.py").write_text("x")
    return root


def test_find_vibe_tts_root_prefers_vibe_tts_home(monkeypatch, tmp_path):
    home = _fake_skill(tmp_path / "home")
    monkeypatch.setenv("VIBE_TTS_HOME", str(home))
    assert vibe_tts_paths.find_vibe_tts_root() == home.resolve()


def test_find_vibe_tts_root_rejects_dir_without_sentinels(monkeypatch, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("VIBE_TTS_HOME", str(empty))
    monkeypatch.setenv(
        "HOME", str(tmp_path / "nohome")
    )  # 屏蔽真实 ~/.claude/skills/vibe-tts
    with pytest.raises(SystemExit) as e:
        vibe_tts_paths.find_vibe_tts_root()
    msg = str(e.value)
    assert str(empty) in msg and "ln -sfn" in msg, "须列出已检查候选并给出安装命令"


def test_skill_root_is_repo_root():
    assert (vibe_tts_paths.skill_root() / "SKILL.md").is_file()
    assert (vibe_tts_paths.skill_root() / "scripts" / "vibe_tts.py").is_file()
