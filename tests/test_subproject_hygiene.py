"""skill 仓自身的工程卫生：本仓刻意不是可构建的 Python 包。

- 无 `uv.lock`：漏打 `--no-project` 的 `uv run` 会把 CWD 当项目根并生成锁文件；
- 无 `.venv/`：同一事故的另一形态（`uv venv` / `uv sync` 的产物）；
- pyproject 无 `[project]` 段：全部脚本走 `uv run --no-project`，依赖在调用点 `--with` 注入。
"""

from __future__ import annotations

import tomllib

from dub_paths import skill_root

SKILL_ROOT = skill_root()


def test_no_uv_lock_in_skill_repo():
    assert not (SKILL_ROOT / "uv.lock").is_file(), (
        "uv.lock 存在——大概率是漏打 --no-project 的产物，删掉即可"
    )


def test_no_venv_in_skill_repo():
    assert not (SKILL_ROOT / ".venv").exists(), (
        ".venv 存在——本仓无 [project]，依赖一律 --with 注入"
    )


def test_pyproject_is_not_a_package():
    data = tomllib.loads((SKILL_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    stray = sorted(k for k in data if k != "tool")
    assert not stray, (
        f"pyproject.toml 出现顶层表 {stray}——本仓刻意不是可构建的 Python 包"
    )


def test_pinned_pytest_command_covers_every_test_dependency():
    """pyproject 顶注释里的测试命令是 SSOT：测试用到的第三方库必须全部出现在 --with 里。"""
    text = (SKILL_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for dep in (
        "pytest",
        "numpy",
        "mutagen",
        "soundfile",
        "fastapi",
        "httpx",
        "pydantic",
        "uvicorn",
    ):
        assert f"--with {dep}" in text, f"pinned 命令缺 --with {dep}"
