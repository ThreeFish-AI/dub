"""路径解析：env 优先级与「找不到即大声退出」。"""

from __future__ import annotations

import dub_paths
import pytest


def test_output_dir_flag_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("DUB_OUTPUT_DIR", str(tmp_path / "env"))
    assert dub_paths.output_dir(str(tmp_path / "flag")) == tmp_path / "flag"


def test_output_dir_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("DUB_OUTPUT_DIR", str(tmp_path / "env"))
    assert dub_paths.output_dir(None) == tmp_path / "env"


def test_output_dir_missing_exits_loudly():
    with pytest.raises(SystemExit) as e:
        dub_paths.output_dir(None)
    assert "DUB_OUTPUT_DIR" in str(e.value) and "--out-dir" in str(e.value)


def test_output_dir_expands_tilde(monkeypatch):
    monkeypatch.setenv("DUB_OUTPUT_DIR", "~/Documents/dub-output")
    assert "~" not in str(dub_paths.output_dir(None))


def test_data_dir_env_and_default(monkeypatch, tmp_path):
    assert dub_paths.data_dir() == tmp_path / "data"  # conftest 注入
    monkeypatch.delenv("DUB_DATA_DIR")
    assert str(dub_paths.data_dir()).endswith("Library/Application Support/dub")


def test_index_tts_root_order(monkeypatch, tmp_path):
    assert str(dub_paths.index_tts_root()).endswith("tools/index-tts")
    monkeypatch.setenv("DUB_INDEX_TTS_ROOT", str(tmp_path / "env"))
    assert dub_paths.index_tts_root() == tmp_path / "env"
    assert dub_paths.index_tts_root(str(tmp_path / "arg")) == tmp_path / "arg"


def _fake_skill(root):
    (root / "scripts").mkdir(parents=True)
    (root / "SKILL.md").write_text("x")
    (root / "scripts" / "dub.py").write_text("x")
    return root


def test_find_dub_root_prefers_dub_home(monkeypatch, tmp_path):
    home = _fake_skill(tmp_path / "home")
    monkeypatch.setenv("DUB_HOME", str(home))
    assert dub_paths.find_dub_root() == home.resolve()


def test_find_dub_root_rejects_dir_without_sentinels(monkeypatch, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("DUB_HOME", str(empty))
    monkeypatch.setenv(
        "HOME", str(tmp_path / "nohome")
    )  # 屏蔽真实 ~/.claude/skills/dub
    with pytest.raises(SystemExit) as e:
        dub_paths.find_dub_root()
    msg = str(e.value)
    assert str(empty) in msg and "ln -sfn" in msg, "须列出已检查候选并给出安装命令"


def test_skill_root_is_repo_root():
    assert (dub_paths.skill_root() / "SKILL.md").is_file()
    assert (dub_paths.skill_root() / "scripts" / "dub.py").is_file()
