"""音频原语：MP3 时长、分块拼接、原子写。

时长一律用 mutagen（与 vibe-video 同码，manifest 行为逐字节一致）；soundfile 仅用于
拼接时的解码/重编码——本机无系统 ffmpeg，libsndfile ≥1.1 可直接读写 MP3。
第三方库全部惰性 import：只做 edge 单请求时零重依赖。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def mp3_duration(path: Path) -> float:
    """mutagen 实测 MP3 时长（两引擎共用）。"""
    from mutagen.mp3 import MP3

    return MP3(str(path)).info.length


def mp3_info(path: Path) -> tuple[float, int]:
    """(时长秒, 采样率 Hz)——say 的 stdout JSON 与实机核验用。"""
    from mutagen.mp3 import MP3

    info = MP3(str(path)).info
    return info.length, info.sample_rate


def write_atomic(data: bytes, dst: Path) -> None:
    """临时文件 + os.replace：读者永远看不到半截文件。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=dst.parent, prefix=f".{dst.name}.", suffix=".tmp", delete=False
        ) as f:
            tmp = Path(f.name)
            f.write(data)
        os.chmod(tmp, 0o644)  # NamedTemporaryFile 缺省 0600，成品须与 edge 产物同权限
        os.replace(tmp, dst)
        tmp = None
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def concat_mp3(chunks: list[bytes], gap_sec: float, dst: Path) -> None:
    """把多段 MP3 字节按序解码 → 段间插 gap_sec 静音 → 重编码成单个 MP3 原子落盘。

    - 全部段须同采样率（同一服务同一次运行恒成立），否则 ValueError 而非静默重采样；
    - 单段直通（零重编码损耗）；
    - gap_sec=0 即无缝拼接。
    """
    if not chunks:
        raise ValueError("concat_mp3: 没有可拼接的音频段")
    if len(chunks) == 1:
        write_atomic(chunks[0], dst)
        return
    import io

    import numpy as np
    import soundfile as sf

    decoded: list = []
    sr: int | None = None
    for i, raw in enumerate(chunks):
        data, rate = sf.read(io.BytesIO(raw), dtype="float32", always_2d=False)
        if data.ndim > 1:  # 保险：折成单声道
            data = data.mean(axis=1)
        if sr is None:
            sr = rate
        elif rate != sr:
            raise ValueError(f"第 {i} 段采样率 {rate} ≠ 首段 {sr}，拒绝静默重采样")
        decoded.append(data)
    assert sr is not None
    gap = np.zeros(int(round(gap_sec * sr)), dtype=np.float32)
    parts: list = []
    for i, d in enumerate(decoded):
        if i:
            parts.append(gap)
        parts.append(d)
    merged = np.concatenate(parts)
    buf = io.BytesIO()
    sf.write(buf, merged, sr, format="MP3")
    write_atomic(buf.getvalue(), dst)
