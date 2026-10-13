"""路径与环境变量解析——机器属性只走 env，找不到即大声退出，绝不回退猜测。

环境变量注册表见 references/ENV.md：
  DUB_HOME           skill 根覆盖（供外部消费者定位本仓，如 vibe-video）
  DUB_OUTPUT_DIR     say 的统一输出根（--out-dir 优先）
  DUB_DATA_DIR       tts-store / logs 的数据根
  DUB_INDEX_TTS_ROOT index-tts checkout（服务运行环境）
（DUB_INDEXTTS_SERVER / DUB_STORE 分别由 dub_engine_indextts 读取。）
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

#: 本仓作为 skill 的存在性哨兵：根目录须同时含 SKILL.md 与 scripts/dub.py。
SENTINELS = ("SKILL.md", "scripts/dub.py")

DEFAULT_DATA_DIR = "~/Library/Application Support/dub"
DEFAULT_INDEX_TTS_ROOT = "~/tools/index-tts"


def skill_root() -> Path:
    """本仓根目录（scripts/ 的上一级）。"""
    return Path(__file__).resolve().parents[1]


def _is_dub_root(path: Path) -> bool:
    return all((path / s).is_file() for s in SENTINELS)


def find_dub_root() -> Path:
    """外部消费者定位 dub：DUB_HOME → ~/.claude/skills/dub → ~/.agents/skills/dub。

    候选必须通过哨兵校验；全部落空则列出所有候选与安装命令后退出。
    """
    candidates: list[Path] = []
    env = os.environ.get("DUB_HOME")
    if env:
        candidates.append(Path(env).expanduser())
    candidates += [
        Path("~/.claude/skills/dub").expanduser(),
        Path("~/.agents/skills/dub").expanduser(),
    ]
    for c in candidates:
        if _is_dub_root(c):
            return c.resolve()
    listing = "\n".join(f"  - {c}" for c in candidates)
    sys.exit(
        "找不到 dub skill（须含 SKILL.md 与 scripts/dub.py）。已检查：\n"
        f"{listing}\n"
        "安装：git clone https://github.com/ThreeFish-AI/dub ~/Documents/projects/aurelius/dub"
        " && ln -sfn ~/Documents/projects/aurelius/dub ~/.claude/skills/dub"
        "（或设 DUB_HOME 指向 checkout）"
    )


def data_dir() -> Path:
    """数据根：DUB_DATA_DIR → ~/Library/Application Support/dub（不存在则原样返回，首写即建）。"""
    return Path(os.environ.get("DUB_DATA_DIR") or DEFAULT_DATA_DIR).expanduser()


def logs_dir() -> Path:
    return data_dir() / "logs"


def index_tts_root(override: str | None = None) -> Path:
    """服务运行环境（uv --frozen 的 cwd）：参数 → DUB_INDEX_TTS_ROOT → ~/tools/index-tts。"""
    return Path(
        override or os.environ.get("DUB_INDEX_TTS_ROOT") or DEFAULT_INDEX_TTS_ROOT
    ).expanduser()


def output_dir(flag: str | None) -> Path:
    """输出根：--out-dir > DUB_OUTPUT_DIR > 大声退出（不替用户选默认落点）。"""
    raw = flag or os.environ.get("DUB_OUTPUT_DIR")
    if not raw:
        sys.exit(
            "未指定输出目录：请传 --out-dir，或设环境变量 DUB_OUTPUT_DIR"
            "（建议写进 ~/.claude/settings.json 的 env 块，如 ~/Documents/dub-output）"
        )
    return Path(raw).expanduser()
