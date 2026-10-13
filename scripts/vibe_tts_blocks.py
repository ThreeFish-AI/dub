"""块音频切分的纯函数——服务端（vibe_tts_server.py）持有 mp3 编码前的 float32 PCM，
在此按句界静音切回 N 段。numpy 惰性 import，本模块 import 零第三方依赖。

SAMPLING_PASSTHROUGH_DEFAULTS 从 vibe_tts_engine_indextts 重导出：服务端只需 import 本模块
一个兄弟文件即可同时拿到采样默认值与切分函数（耦合面收敛到单点）。
"""

from __future__ import annotations

from vibe_tts_engine_indextts import SAMPLING_PASSTHROUGH_DEFAULTS  # noqa: F401


def frame_db(pcm, sr: int, hop_sec: float = 0.01, win_sec: float = 0.025):
    """帧 RMS → dB（10 ms hop / 25 ms win，与实测标定一致）。pcm: float32 1-D。"""
    import numpy as np

    hop, win = int(sr * hop_sec), int(sr * win_sec)
    n = 1 + max(0, (len(pcm) - win) // hop)
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    frames = pcm[idx]
    rms = np.sqrt(np.mean(frames * frames, axis=1))
    return 20 * np.log10(np.maximum(rms, 1e-9))


def silence_runs(
    db, sr: int, hop_sec: float = 0.01, min_sec: float = 0.12, below_db: float = 35.0
):
    """低于 p95−below_db 的连续帧 → (内部静音段列表, 语音起, 语音止)。

    只保留首尾语音之间的内部段（块首/块尾静音不是切点候选）；返回三元组，
    语音起止供 plan_block_cuts 内插期望位置。
    """
    import numpy as np

    thr = np.percentile(db, 95) - below_db
    quiet = db < thr
    idx = np.flatnonzero(np.diff(quiet.astype(int)) != 0) + 1
    bounds = np.concatenate(([0], idx, [len(quiet)]))
    runs = [
        (bounds[k] * hop_sec, bounds[k + 1] * hop_sec)
        for k in range(len(bounds) - 1)
        if quiet[bounds[k]] and bounds[k + 1] - bounds[k] >= min_sec / hop_sec
    ]
    speech = np.flatnonzero(~quiet)
    if not len(speech):
        return [], 0.0, len(db) * hop_sec
    on, off = speech[0] * hop_sec, (speech[-1] + 1) * hop_sec
    return [(s, e) for s, e in runs if s > on and e < off], float(on), float(off)


def plan_block_cuts(
    runs, weights: list[float], total_sec: float, speech_on: float, speech_off: float
):
    """在候选静音里为 N−1 个句界各选一个切点。

    期望位置 exp_k 按累计权重占比内插；DP 选严格递增候选最小化
    Σ((mid−exp)/σ)² − 0.3·ln(段长/0.1)（长静音略优先）；门：|mid−exp| ≤ max(1.0, 0.15T)
    且各句语音占比 ∈[0.5,1.8]×期望。返回 (mid_1..mid_{N-1}, cut_runs) 或 None。
    """
    import math

    n = len(weights)
    if n < 2:
        return None
    total_w = sum(weights)
    cum = [sum(weights[:k]) / total_w for k in range(1, n)]
    exp = [speech_on + (speech_off - speech_on) * c for c in cum]
    sigma = max(0.35, 0.06 * total_sec)
    tol = max(1.0, 0.15 * total_sec)

    INF = float("inf")
    m = len(runs)
    # dp[j][k]：第 k 个界用候选 j 的最小代价
    dp = [[INF] * (n - 1) for _ in range(m)]
    par = [[-1] * (n - 1) for _ in range(m)]
    for j, (s, e) in enumerate(runs):
        mid = (s + e) / 2
        cost = ((mid - exp[0]) / sigma) ** 2 - 0.3 * math.log((e - s) / 0.1)
        if abs(mid - exp[0]) <= tol:
            dp[j][0] = cost
    for k in range(1, n - 1):
        best = INF
        best_j = -1
        for j, (s, e) in enumerate(runs):
            if best_j >= 0:
                mid = (s + e) / 2
                if abs(mid - exp[k]) <= tol:
                    cost = (
                        best
                        + ((mid - exp[k]) / sigma) ** 2
                        - 0.3 * math.log((e - s) / 0.1)
                    )
                    if cost < dp[j][k]:
                        dp[j][k] = cost
                        par[j][k] = best_j
            if dp[j][k - 1] < best:
                best, best_j = dp[j][k - 1], j
    end_j = min(range(m), key=lambda j: dp[j][n - 2]) if m else -1
    if m == 0 or dp[end_j][n - 2] == INF:
        return None
    chosen = [0] * (n - 1)
    j = end_j
    for k in range(n - 2, -1, -1):
        chosen[k] = j
        j = par[j][k]
    picked = [runs[j] for j in chosen]
    # 语音占比门：切后各段语音时长与期望占比偏离 ∈[0.5,1.8]
    spans = []
    prev = speech_on
    for s, e in picked + [(speech_off, speech_off)]:
        spans.append((prev, s))
        prev = e
    for k, (a, b) in enumerate(spans):
        share = (b - a) / (speech_off - speech_on)
        want = weights[k] / total_w
        if not 0.5 <= share / want <= 1.8:
            return None
    return picked


def split_block_pcm(pcm, sr: int, runs, discard_sec: float, tail_pad_sec: float = 0.0):
    """按切点把块 PCM 切成 N 段：每界从静音正中丢弃 discard_sec，每段两端 5ms 淡入淡出。

    discard 的语义：时间轴随后会加回 sentenceGapSec ⇒ 听感＝自然停顿原值；
    静音不足 gap+2m 时退化为丢 S−2m（下限 0.06 s 余量）。末段统一补尾垫。
    返回 [(clip_pcm, 自然停顿 S_k)]。
    """
    import numpy as np

    margin = 0.03
    fade = int(sr * 0.005)
    out = []
    cursor = 0
    for s, e in runs:
        nat = e - s
        d = min(discard_sec, max(0.0, nat - 2 * margin))
        cut_start = s + (nat - d) / 2
        clip = pcm[int(cursor * sr) : int(cut_start * sr)].copy()
        if len(clip) > 2 * fade:
            clip[:fade] *= np.linspace(0, 1, fade)
            clip[-fade:] *= np.linspace(1, 0, fade)
        out.append((clip, nat))
        cursor = cut_start + d
    clip = pcm[int(cursor * sr) :].copy()
    if len(clip) > 2 * fade:  # 末段起点即末个切点（静音≠零值）：与中间段同口径两端淡变
        clip[:fade] *= np.linspace(0, 1, fade)
        clip[-fade:] *= np.linspace(1, 0, fade)
    if tail_pad_sec > 0:
        clip = np.concatenate([clip, np.zeros(int(tail_pad_sec * sr), np.float32)])
    out.append((clip, 0.0))
    return out
