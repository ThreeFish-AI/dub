"""路径与环境变量解析——机器属性只走 env，找不到即大声退出，绝不回退猜测。

环境变量注册表见 references/ENV.md：
  VIBE_TTS_HOME           skill 根覆盖（供外部消费者定位本仓，如 vibe-video）
  VIBE_TTS_OUTPUT_DIR     say 的统一输出根（--out-dir 优先）
  VIBE_TTS_DATA_DIR       tts-store / logs 的数据根
  VIBE_TTS_INDEX_TTS_ROOT index-tts checkout（服务运行环境）
（VIBE_TTS_INDEXTTS_SERVER / VIBE_TTS_STORE 分别由 vibe_tts_engine_indextts 读取。）
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

#: 本仓作为 skill 的存在性哨兵：根目录须同时含 SKILL.md 与 scripts/vibe_tts.py。
SENTINELS = ("SKILL.md", "scripts/vibe_tts.py")

DEFAULT_DATA_DIR = "~/Library/Application Support/vibe-tts"
DEFAULT_INDEX_TTS_ROOT = "~/tools/index-tts"


def skill_root() -> Path:
    """本仓根目录（scripts/ 的上一级）。"""
    return Path(__file__).resolve().parents[1]


def _is_vibe_tts_root(path: Path) -> bool:
    return all((path / s).is_file() for s in SENTINELS)


def find_vibe_tts_root() -> Path:
    """外部消费者定位 vibe-tts：VIBE_TTS_HOME → ~/.claude/skills/vibe-tts → ~/.agents/skills/vibe-tts。

    候选必须通过哨兵校验；全部落空则列出所有候选与安装命令后退出。
    """
    candidates: list[Path] = []
    env = os.environ.get("VIBE_TTS_HOME")
    if env:
        candidates.append(Path(env).expanduser())
    candidates += [
        Path("~/.claude/skills/vibe-tts").expanduser(),
        Path("~/.agents/skills/vibe-tts").expanduser(),
    ]
    for c in candidates:
        if _is_vibe_tts_root(c):
            return c.resolve()
    listing = "\n".join(f"  - {c}" for c in candidates)
    sys.exit(
        "找不到 vibe-tts skill（须含 SKILL.md 与 scripts/vibe_tts.py）。已检查：\n"
        f"{listing}\n"
        "安装：git clone https://github.com/ThreeFish-AI/vibe-tts ~/Documents/projects/aurelius/vibe-tts"
        " && ln -sfn ~/Documents/projects/aurelius/vibe-tts ~/.claude/skills/vibe-tts"
        "（或设 VIBE_TTS_HOME 指向 checkout）"
    )


def data_dir() -> Path:
    """数据根：VIBE_TTS_DATA_DIR → ~/Library/Application Support/vibe-tts（不存在则原样返回，首写即建）。"""
    return Path(os.environ.get("VIBE_TTS_DATA_DIR") or DEFAULT_DATA_DIR).expanduser()


def logs_dir() -> Path:
    return data_dir() / "logs"


def index_tts_root(override: str | None = None) -> Path:
    """服务运行环境（uv --frozen 的 cwd）：参数 → VIBE_TTS_INDEX_TTS_ROOT → ~/tools/index-tts。"""
    return Path(
        override or os.environ.get("VIBE_TTS_INDEX_TTS_ROOT") or DEFAULT_INDEX_TTS_ROOT
    ).expanduser()


def output_dir(flag: str | None) -> Path:
    """输出根：--out-dir > VIBE_TTS_OUTPUT_DIR > 大声退出（不替用户选默认落点）。"""
    raw = flag or os.environ.get("VIBE_TTS_OUTPUT_DIR")
    if not raw:
        sys.exit(
            "未指定输出目录：请传 --out-dir，或设环境变量 VIBE_TTS_OUTPUT_DIR"
            "（建议写进 ~/.claude/settings.json 的 env 块，如 ~/Documents/vibe-tts-output）"
        )
    return Path(raw).expanduser()
