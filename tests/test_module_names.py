"""命名防撞：vibe-video 会把 vibe-tts 的 scripts/ 插进 sys.path，vibe-tts 的库模块名绝不能与
vibe-video 的兄弟模块同名，否则 `import paths` 之类会静默拿到另一仓的文件。

vibe-tts 的库模块一律 `vibe_tts_` 前缀；唯一的无前缀文件是入口 `vibe_tts.py`。
"""

from __future__ import annotations

from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"

#: vibe-video scripts/ 里已知的模块名（不含 vibe-tts 自己导入的标准库名）。
VIBE_VIDEO_SIBLINGS = {
    "paths", "config", "pipeline", "tts", "tts_server", "tts_resume", "tts_sample", "tts_bench",
    "langs", "build_narration", "check_script", "captions", "deliver", "scaffold", "timeline",
    "refs", "pron_marks", "prepare_ref", "prospect_ref", "qa_frames", "check_series",
    "check_archify", "check_playbook", "archify_lead", "record_archify", "record_archify_all",
}  # fmt: skip


def _stems() -> set[str]:
    return {p.stem for p in SCRIPTS.glob("*.py")}


def test_every_library_module_is_vibe_tts_prefixed():
    loose = sorted(s for s in _stems() if not s.startswith("vibe_tts"))
    assert not loose, f"无 vibe-tts 前缀的模块：{loose}"


def test_no_collision_with_vibe_video_siblings():
    clash = _stems() & VIBE_VIDEO_SIBLINGS
    assert not clash, f"与 vibe-video 兄弟模块同名：{sorted(clash)}"


def test_entrypoint_is_the_only_bare_vibe_tts_module():
    assert (SCRIPTS / "vibe_tts.py").is_file()
    bare = sorted(s for s in _stems() if s == "vibe_tts")
    assert bare == ["vibe_tts"]
