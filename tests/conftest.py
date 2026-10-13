"""测试公共设施：把 scripts/ 放进 sys.path，并隔离会影响结果的环境变量。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    """每个用例都在干净的 VIBE_TTS_* 环境里跑：不读到开发机真实配置，也不写到真实数据目录。"""
    for k in (
        "VIBE_TTS_HOME",
        "VIBE_TTS_OUTPUT_DIR",
        "VIBE_TTS_INDEXTTS_SERVER",
        "VIBE_TTS_INDEX_TTS_ROOT",
        "VIBE_TTS_STORE",
    ):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("VIBE_TTS_DATA_DIR", str(tmp_path / "data"))
