"""edge-tts 引擎：单次 Communicate 合成整段文本（库内自行按 4096 字节切分长文本）。

输出格式由 edge-tts 写死为 audio-24khz-48kbitrate-mono-mp3，不可配置。
已知故障面（edge-tts 上游 issue #490/#473/#487）：Sec-MS-GEC 403 回归（地域性）、
NoAudioReceived 间歇、空文本写出 0 字节文件且退出 0、save() 失败残留半文件
——本模块对应：有界重试退避 + 0 字节检查 + 失败清残留 + 403 时给出 --proxy 指引。
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from vibe_tts_profiles import DEFAULT_RATE, DEFAULT_VOICE, EDGE_VOICE_BY_LANG
from vibe_tts_textprep import tts_text

RETRIES = 4


def resolve_voice(voice: str | None, lang: str) -> str:
    """--voice 显式给值优先，否则按 --lang 取缺省音色。"""
    return voice or EDGE_VOICE_BY_LANG.get(lang, DEFAULT_VOICE)


def digest_edge(voice: str, rate: str, text: str) -> str:
    return hashlib.sha1(f"{voice}|{rate}|{text}".encode()).hexdigest()


def _hint_for(err: Exception) -> str:
    """把已知网络故障签名翻译成可操作的提示（原始异常仍保留在消息末尾）。"""
    msg = f"{type(err).__name__}: {err}"
    if "403" in msg or "Sec-MS-GEC" in msg or "WSServerHandshakeError" in msg:
        return (
            msg + "\n  提示：edge-tts 握手 403（上游 rany2/edge-tts#490，地域性回归）——"
            "可 `uvx edge-tts@latest` 升级后重试，或经 HTTPS_PROXY 环境变量走代理。"
        )
    if "NoAudioReceived" in msg:
        return (
            msg
            + "\n  提示：音色名/参数非法，或文本为空/仅标点；也可能是间歇性故障（上游 #473）。"
        )
    return msg


async def synth_text(
    text: str,
    voice: str,
    rate: str,
    dst: Path,
    lang: str = "ZH",
    retries: int = RETRIES,
) -> None:
    """合成 text 到 dst（mp3）。失败退避重试，终败抛 RuntimeError；绝不留半文件。"""
    import edge_tts  # 惰性：仅 edge 引擎需要

    if not text.strip():
        raise ValueError("文本为空：edge-tts 对空文本会写出 0 字节文件，拒绝合成")
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f".{dst.name}.part")
    last: Exception | None = None
    for attempt in range(retries):
        try:
            communicate = edge_tts.Communicate(
                tts_text(text, lang.upper()), voice, rate=rate
            )
            await communicate.save(str(tmp))
            if not tmp.exists() or tmp.stat().st_size == 0:
                raise RuntimeError("空音频文件")
            tmp.replace(dst)
            return
        except Exception as e:  # noqa: BLE001 - 网络服务需要整体重试
            last = e
            tmp.unlink(missing_ok=True)
            if attempt < retries - 1:
                await asyncio.sleep(1.5 * (attempt + 1))
    assert last is not None
    raise RuntimeError(f"edge 合成失败（已重试 {retries} 次）：{_hint_for(last)}")


__all__ = [
    "DEFAULT_RATE",
    "DEFAULT_VOICE",
    "digest_edge",
    "resolve_voice",
    "synth_text",
]
