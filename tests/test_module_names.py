"""命名防撞：vibe-video 会把 dub 的 scripts/ 插进 sys.path，dub 的库模块名绝不能与
vibe-video 的兄弟模块同名，否则 `import paths` 之类会静默拿到另一仓的文件。

dub 的库模块一律 `dub_` 前缀；唯一的无前缀文件是入口 `dub.py`。
"""

from __future__ import annotations

from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"

#: vibe-video scripts/ 里已知的模块名（不含 dub 自己导入的标准库名）。
VIBE_VIDEO_SIBLINGS = {
    "paths", "config", "pipeline", "tts", "tts_server", "tts_resume", "tts_sample", "tts_bench",
    "langs", "build_narration", "check_script", "captions", "deliver", "scaffold", "timeline",
    "refs", "pron_marks", "prepare_ref", "prospect_ref", "qa_frames", "check_series",
    "check_archify", "check_playbook", "archify_lead", "record_archify", "record_archify_all",
}  # fmt: skip


def _stems() -> set[str]:
    return {p.stem for p in SCRIPTS.glob("*.py")}


def test_every_library_module_is_dub_prefixed():
    loose = sorted(s for s in _stems() if not s.startswith("dub"))
    assert not loose, f"无 dub 前缀的模块：{loose}"


def test_no_collision_with_vibe_video_siblings():
    clash = _stems() & VIBE_VIDEO_SIBLINGS
    assert not clash, f"与 vibe-video 兄弟模块同名：{sorted(clash)}"


def test_entrypoint_is_the_only_bare_dub_module():
    assert (SCRIPTS / "dub.py").is_file()
    bare = sorted(s for s in _stems() if s == "dub")
    assert bare == ["dub"]
