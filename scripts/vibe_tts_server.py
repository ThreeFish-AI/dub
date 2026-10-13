#!/usr/bin/env python3
"""IndexTTS 声音克隆推理服务——运行于 index-tts 工程环境内的本地 HTTP 服务。

- 位置约定：本脚本属于公共管线（SSOT），但必须在 index-tts checkout 的 uv 环境内运行
  （默认 ~/tools/index-tts；可经 VIBE_TTS_INDEX_TTS_ROOT 另指。torch/indextts 等
  重依赖不进入本 skill）；
- 启动（在 index-tts 根目录）：
    uv run --frozen --with fastapi --with uvicorn --with soundfile --with numpy --with lameenc \
        python $T/scripts/vibe_tts_server.py --model-dir checkpoints --port 8766
- MPS 显存上限（--mps-mem-limit-gib，缺省 min(0.90×recommended, 16) GiB，缺省额度不足
  ~11 GiB 的小机型自动不设限，--use-qwen-emo 时随常驻上移至 ~12.5）：进程级水位线，防 torch
  默认 1.7×recommended（本机 ≈30 GiB，超 24 GB 物理统一内存）把系统内存拉爆致卡顿（RSI-017，
  机制循证见 INDEXTTS-2.5-ADVANCED §6.8）；0=禁用 high watermark（unlimited，承担系统内存风险）；--device cpu 不设不上报。
- 端点：
    GET  /health     —— 服务与模型元信息（version/device/dtype/encoder + 四个 supports_* 能力位）
    POST /synthesize —— JSON 请求合成，返回 MP3 bytes（X-Audio-Format 头）
- 情感三来源（互斥，只能给一个）：
    emo_vector   —— 8 维显式向量（有效和 Σvec×alpha ≤ 0.8）
    emo_ref_path —— 情感参考音频：音色仍取 ref_path，语调/情绪迁移自这段录音（无合成味）
    emo_text     —— 自然语言描述（需 --use-qwen-emo），服务端转向量并在 X-Emo-Vector 头回显
- 采样参数族（temperature/top_p/top_k/length_penalty/repetition_penalty/max_mel_tokens）：
  上游经 **generation_kwargs 透传给 HF generate，全部生效（唯一例外是 do_sample——上游
  infer_v2_5.py:780 用字面量 True 覆盖，故本服务不暴露它）。缺省一律取上游默认值，
  见 SAMPLING_DEFAULTS。
- seed：上游全链路无种子且 do_sample 恒 True，同句每次合成韵律都不同；给 seed 即可复现，
  这是任何参数 A/B 可信的前提。
- 安全：仅监听 127.0.0.1，无鉴权，勿暴露公网；ref_path / emo_ref_path 为服务端本地绝对路径。

完整契约与排障见 references/ENGINES.md。
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import math
import os
import re
import sys
import tempfile
import traceback
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
import soundfile as sf
import uvicorn
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

# 客户端与本服务分属两个运行环境（skill 侧轻依赖 vs index-tts venv）；本服务按绝对路径运行，
# sys.path[0] 即本目录——采样默认值等共享常量以 vibe_tts_engine_indextts 为 SSOT（经 vibe_tts_blocks 重导出）。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from vibe_tts_blocks import SAMPLING_PASSTHROUGH_DEFAULTS as SAMPLING_DEFAULTS  # noqa: E402


def ensure_indextts_import(index_tts_root: Path) -> None:
    """优先依赖 venv 已安装的 indextts；仅源码未安装时把 checkout 根目录塞进 sys.path 兜底。"""
    try:
        import indextts  # noqa: F401
    except ImportError:
        sys.path.insert(0, str(index_tts_root.resolve()))


def load_model(
    version: str, model_dir: Path, dtype: str, device: str, use_qwen_emo: bool = False
):
    """按版本构造 IndexTTS2；构造器差异以 webui.py build_tts() 为锚点：
    v2.5 仅 use_bf16（MPS 分支内部强制关闭），v2 为 use_fp16。
    use_qwen_emo 决定是否加载 QwenEmotion（自然语言情感描述→向量，约 +1.5 GB 内存）。

    返回 (tts 对象, 元信息 dict)。
    """
    if version == "2.5":
        from indextts.infer_v2_5 import IndexTTS2

        use_bf16 = dtype in ("auto", "bf16")  # MPS 分支内部会强制 False → 实际 fp32
        tts = IndexTTS2(
            cfg_path=str(model_dir / "config.yaml"),
            model_dir=str(model_dir),
            use_bf16=use_bf16,
            use_cuda_kernel=False,
            use_deepspeed=False,
            use_qwen_emo=use_qwen_emo,
            device=None if device == "auto" else device,
        )
        return tts, {
            "version": "2.5",
            "supports_duration_factor": True,
            # v2 的 infer() 签名里没有 text_normalization（infer_v2.py 无该形参），v2.5 才有
            "supports_text_normalization": True,
            # 块切分（story 档）：low_vram 路径 >40 字即按标点分段（段间 interval_silence
            # 定长零值静音），句界会被污染，不具备块切分条件
            "supports_blocks": not getattr(tts, "low_vram", False),
            # 从对象实际状态派生：MPS 分支构造器内部强制 use_bf16=False（实际 fp32）
            "dtype_flag": "bf16" if getattr(tts, "use_bf16", use_bf16) else "fp32",
        }

    from indextts.infer_v2 import IndexTTS2

    use_fp16 = dtype in ("auto", "fp16")
    tts = IndexTTS2(
        cfg_path=str(model_dir / "config.yaml"),
        model_dir=str(model_dir),
        use_fp16=use_fp16,
        use_cuda_kernel=False,
        use_deepspeed=False,
        use_qwen_emo=use_qwen_emo,
        device=None if device == "auto" else device,
    )
    return tts, {
        "version": "2",
        "supports_duration_factor": False,
        "supports_text_normalization": False,
        "dtype_flag": "fp16" if getattr(tts, "use_fp16", use_fp16) else "fp32",
    }


# ---------------- 编码层：WAV(float32) → MP3 bytes ----------------

_ENCODER: str | None = None


def _probe_encoders() -> str:
    """启动时一次性探测可用 MP3 编码器：soundfile（libsndfile≥1.1 自带 LAME）→ lameenc。"""
    global _ENCODER
    sr = 22050
    tone = (np.sin(2 * np.pi * 440 * np.arange(sr) / sr) * 0.5).astype(np.float32)
    try:
        buf = io.BytesIO()
        sf.write(buf, tone, sr, format="MP3", subtype="MPEG_LAYER_III")
        if buf.tell() > 0:
            _ENCODER = "soundfile"
            return _ENCODER
    except Exception:  # noqa: BLE001 - 探测失败换下一档
        pass
    try:
        import lameenc

        enc = lameenc.Encoder()
        enc.set_bit_rate(128)
        enc.set_in_sample_rate(sr)
        enc.set_channels(1)
        enc.set_quality(2)
        out = enc.encode(tone.tobytes()) + enc.flush()
        if len(out) > 0:
            _ENCODER = "lameenc"
            return _ENCODER
    except Exception:  # noqa: BLE001 - 双双失败，保留 None（返回 WAV）
        pass
    _ENCODER = None
    return "none"


def encode_mp3(data: np.ndarray, sr: int) -> tuple[bytes, str]:
    """输入 (N,) float32 → (音频 bytes, 实际格式)。无可用 MP3 编码器时回退 WAV。"""
    if _ENCODER == "soundfile":
        buf = io.BytesIO()
        sf.write(buf, data, sr, format="MP3", subtype="MPEG_LAYER_III")
        return buf.getvalue(), "mp3"
    if _ENCODER == "lameenc":
        import lameenc

        enc = lameenc.Encoder()
        enc.set_bit_rate(128)
        enc.set_in_sample_rate(sr)
        enc.set_channels(1)
        enc.set_quality(2)
        pcm = (np.clip(data, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
        out = enc.encode(pcm) + enc.flush()
        return bytes(out), "mp3"
    # 双编码器均不可用：返回 WAV，由客户端按 X-Audio-Format 报错指引
    buf = io.BytesIO()
    sf.write(buf, data, sr, format="WAV", subtype="PCM_16")
    return buf.getvalue(), "wav"


# ---------------- FastAPI 应用 ----------------

EMO_LABELS = "happy,angry,sad,afraid,disgusted,melancholic,surprised,calm"

# 上游自回归采样参数的默认值 —— SSOT 在 vibe_tts_engine_indextts（SAMPLING_PASSTHROUGH_DEFAULTS，
# 锚点 indextts/infer_v2_5.py:731-739（HEAD 4f8792f），与 infer_v2.py:536-544 完全一致），
# 本文件经文件头 import 引用（别名 SAMPLING_DEFAULTS 供下方请求模型取默认值）。
# 此前这里持有一份手抄副本、靠注释约束「逐字一致」——两份 7/9 键字典已经漂移过一次
# （服务端缺 text_normalization/seed 两键），现改为运行时导入，改一处两端同步。
# 注意 SSOT 只覆盖 7 个**透传 generation_kwargs** 的键；text_normalization（v2.5 独立形参）
# 与 seed（本服务自行 set_seed）不在此列，由下方请求模型单独定义。
#
# 三条口径提醒（写进注释而非文档，因为它们直接决定该不该动这些值）：
#   length_penalty=0.0 **不是中性**——束打分 score = sum_logprobs / len**0 = sum_logprobs，
#     对数概率恒负故越长越吃亏，即系统性偏好更短假设，是 num_beams>1 时吞尾/漏字的机制来源；
#     仅在束搜索打分时生效，num_beams=1 下改它无效。
#   repetition_penalty=10.0 作用在 8194 类语义码头上、是对 logit 的符号相关缩放（非概率硬禁），
#     故能取到远超文本 LM 常用 1.0-1.2 的值；但其有效强度依赖 logit 绝对尺度，因而与音色/情感
#     向量耦合——跨音色迁移调参结论必须重新验证。
#   max_mel_tokens=1500 ≈ 30 s 音频（语义码率 50 Hz × 1.72 mel 帧/token，hop 256 @ 22050）；
#     溢出后果不是音频被裁短，而是文本尾部根本没被念出（infer_v2_5.py:792-813）。


class BlockSpec(BaseModel):
    """段落演绎（story 档）块切分参数：服务端把一次块合成的 PCM 按句界切回 N 段。

    weights：各句「发音量」权重（客户端按字符口径算好），用于把 N−1 个句界匹配到
    静音段（期望位置按累计权重内插）；discard_sec：每个句界静音正中丢弃的秒数——
    时间轴随后加回同值，听感＝自然停顿原值；tail_pad_sec：末句尾垫静音（块间对齐）。
    """

    weights: list[float]
    discard_sec: float = 0.32
    tail_pad_sec: float = 0.0

    @field_validator("weights")
    @classmethod
    def _weights_ok(cls, v: list[float]) -> list[float]:
        if len(v) < 1:
            raise ValueError("block.weights 不能为空")
        if not all(math.isfinite(x) and x > 0 for x in v):
            raise ValueError("block.weights 各分量必须为正有限数值")
        return v

    @field_validator("discard_sec", "tail_pad_sec")
    @classmethod
    def _sec_ok(cls, v: float) -> float:
        if not 0.0 <= v <= 2.0:
            raise ValueError("block 时间参数必须在 [0, 2] 秒")
        return v


class SynthesizeRequest(BaseModel):
    text: str
    ref_path: str
    emo_vector: list[float] | None = None
    # 情感参考音频：音色取自 ref_path，语调/情绪取自本字段（另一段录音），无合成味的风格迁移。
    # 本服务拒绝它与 emo_vector 同传，理由有二（第 1 条为 2026-09-25 核源码勘误：
    # infer_v2_5.py:582-585 只要给了 emo_vector 就把 emo_audio_prompt 置 None——音频被
    # 整个丢弃，同传实测与纯向量输出逐字节一致；**并非**旧注释所说「音频仍会以 (1−Σw)
    # 权重混进最终 emovec」，该表述错误，见 INDEXTTS-2.5-ADVANCED §3.1 勘误）：
    #   1) 向量在场即音频失效，同传没有意义；
    #   2) emo_alpha 被消费两次：先在 :605-608 缩放向量（clamp [0,1]），又在 merge_emovec
    #      用作音频插值系数，语义混乱且不可预测。
    emo_ref_path: str | None = None
    # 自然语言情感描述（如「轻快爽朗、自信阳光」）：服务端先用 QwenEmotion 转成 8 维向量，
    # 再按 ≤0.8 有效和规则缩放后当作 emo_vector 使用，并在 X-Emo-Vector 响应头回显供固化复用。
    emo_text: str | None = None
    emo_alpha: float = 1.0
    duration_factor: float = 1.0
    lang: str = "ZH"
    num_beams: int = 1
    # 块切分（story 档专用）：给出即走 JSON 响应（clips 数组），不再返回单个 mp3。
    block: BlockSpec | None = None
    # ---- 采样参数族：缺省即上游默认，取值域对齐 webui.py:901-910 的滑杆区间 ----
    temperature: float = SAMPLING_DEFAULTS["temperature"]
    top_p: float = SAMPLING_DEFAULTS["top_p"]
    top_k: int = SAMPLING_DEFAULTS["top_k"]
    length_penalty: float = SAMPLING_DEFAULTS["length_penalty"]
    repetition_penalty: float = SAMPLING_DEFAULTS["repetition_penalty"]
    max_mel_tokens: int = SAMPLING_DEFAULTS["max_mel_tokens"]
    # 段间静音（毫秒）：仅作用于**单请求内**因超 max_text_tokens_per_segment 而被上游切开的分段
    # 之间。本管线逐句合成、单句远低于分段预算，故默认路径下不生效；句间停顿由时间轴常数
    # video/src/timing.json 的 sentenceGapSec 提供，二者不是同一件事。
    interval_silence: int = SAMPLING_DEFAULTS["interval_silence"]
    # 中文文本归一化（数字/百分号/量词 → 口语读法）。保持 True：实测 % / 小数 / 量词 / 月日 /
    # 章节的读法都正确，关掉会把全部读法责任推给逐字稿。发音标注 <字|读音> 由上游占位符机制
    # 保护、天然免疫归一化，故**不需要**为保标记而关它。v2.5 专属参数。
    text_normalization: bool = True
    # 随机种子：上游 do_sample 恒 True 且全链路无种子，同句每次合成都是不同的 take。
    # 给定即可复现（transformers.set_seed 覆盖 random/numpy/torch）。None = 保持上游随机行为。
    seed: int | None = None

    @field_validator("emo_vector")
    @classmethod
    def _vec_ok(cls, v: list[float] | None) -> list[float] | None:
        if v is None:
            return v
        if len(v) != 8:
            raise ValueError(f"emo_vector 必须为 8 维（{EMO_LABELS}）")
        if not all(
            math.isfinite(x) and x >= 0 for x in v
        ):  # isfinite 拦 NaN/Inf（比较恒 False 漏网）
            raise ValueError("emo_vector 各分量必须为非负有限数值")
        return v  # 有效和（×emo_alpha）校验在 handler 内跨字段联合进行

    @field_validator("emo_alpha")
    @classmethod
    def _alpha_ok(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:  # NaN 会因比较恒 False 被拦截
            raise ValueError("emo_alpha 必须在 [0, 1]")
        return v

    @field_validator("duration_factor")
    @classmethod
    def _df_ok(cls, v: float) -> float:
        if not 0.5 <= v <= 2.0:  # NaN 会因比较恒 False 被拦截
            raise ValueError("duration_factor 必须在 [0.5, 2.0]")
        return v

    @field_validator("num_beams")
    @classmethod
    def _beams_ok(cls, v: int) -> int:
        if not 1 <= v <= 5:
            raise ValueError("num_beams 必须在 [1, 5]")
        return v

    @field_validator("temperature")
    @classmethod
    def _temp_ok(cls, v: float) -> float:
        if not 0.1 <= v <= 2.0:  # NaN 比较恒 False，一并被拦
            raise ValueError("temperature 必须在 [0.1, 2.0]")
        return v

    @field_validator("top_p")
    @classmethod
    def _top_p_ok(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("top_p 必须在 [0.0, 1.0]")
        return v

    @field_validator("top_k")
    @classmethod
    def _top_k_ok(cls, v: int) -> int:
        # 0 = 关闭 TopK warper（上游门控为 top_k != 0）。1 在 num_beams>1 下会踩到
        # multinomial 的非零元素数下界（min_tokens_to_keep = n_eos+1 = 2），故禁用 1。
        if v == 1:
            raise ValueError(
                "top_k=1 在束搜索下不安全（每束需保底 2 个候选）：用 0 关闭或 ≥2"
            )
        if not 0 <= v <= 100:
            raise ValueError("top_k 必须在 [0, 100]")
        return v

    @field_validator("length_penalty")
    @classmethod
    def _len_pen_ok(cls, v: float) -> float:
        if not -2.0 <= v <= 2.0:
            raise ValueError("length_penalty 必须在 [-2.0, 2.0]")
        return v

    @field_validator("repetition_penalty")
    @classmethod
    def _rep_pen_ok(cls, v: float) -> float:
        if not 0.1 <= v <= 20.0:
            raise ValueError("repetition_penalty 必须在 [0.1, 20.0]")
        return v

    @field_validator("max_mel_tokens")
    @classmethod
    def _max_mel_ok(cls, v: int) -> int:
        # 上限 1815 = config.yaml 的 gpt.max_mel_tokens（mel 位置嵌入容量，≈36.2 s），越界即报错。
        if not 50 <= v <= 1815:
            raise ValueError(
                "max_mel_tokens 必须在 [50, 1815]（1815 为架构上限 ≈36.2 s）"
            )
        return v

    @field_validator("interval_silence")
    @classmethod
    def _interval_ok(cls, v: int) -> int:
        if not 0 <= v <= 2000:
            raise ValueError("interval_silence 必须在 [0, 2000] 毫秒")
        return v


STATE: dict = {}


def _qwen_vector_sync(tts, emo_text: str, alpha: float) -> list[float]:
    """自然语言情感描述 → 8 维向量（QwenEmotion），并按 ≤0.8 有效和规则整体缩放。

    Qwen 每维 clamp 在 [0, 1.2] 但**不做和归一**，Σ 可能 >1；而上游混合式为
    `emovec = Σ(w·基向量) + (1 - Σw)·参考音频情感`，Σw>1 会让参考音频项变负权重（发音劣化）。
    故此处等比缩放（保留 Qwen 选定的「方向」，只压「强度」），使残留的本人语调 ≥0.2。

    注意上游 emo_text 路径**完全没有**这层保护：webui 只对「自定义向量」模式调 normalize_emo_vec，
    emo_text 模式把 Qwen 输出直接交给 infer()。这个 0.8 是本管线补的，不是上游行为。
    """
    vec = list(tts.qwen_emo.inference(emo_text).values())
    total = sum(vec) * alpha
    if total > 0.8:
        scale = 0.8 / total
        vec = [round(x * scale, 4) for x in vec]
    return vec


def _infer_sync(tts, ref: Path, req: SynthesizeRequest, tmpdir: Path) -> Path:
    wav_path = tmpdir / "out.wav"
    if req.seed is not None:
        # 必须在 infer 之前、且在同一线程内设置：generate 的多项式采样读的是全局 RNG。
        from transformers import set_seed

        set_seed(req.seed)
    kwargs = dict(
        spk_audio_prompt=str(ref),
        text=req.text,
        output_path=str(wav_path),
        emo_vector=req.emo_vector,
        emo_audio_prompt=req.emo_ref_path,
        emo_alpha=req.emo_alpha,
        # use_random=True 会为每个情感维度从 73 行原型里均匀乱抽，放弃「按你的 CAMPPlus 风格
        # 挑最像你的那行」这一步 —— 等于让陌生人来演这个情绪，直接掉克隆保真度。恒 False。
        use_random=False,
        verbose=False,
        # 束搜索宽度：上游默认 3，管线长跑默认 1。束宽只作用于 T2S（S2M 入口 codes 形状与
        # 束宽无关）；代价高度依赖硬件——CUDA 上近线性放大，MPS 上近乎免费（整集实测 1→3
        # 仅 +4%，因 beam 扩张只把 batch 1→3 而 kernel 发射与逐步同步点不变）。
        num_beams=req.num_beams,
        interval_silence=req.interval_silence,
        # 以下六项经 **generation_kwargs 透传到 HF generate，全部生效（唯一失效的 do_sample
        # 被上游 infer_v2_5.py:780 用字面量 True 覆盖，故不暴露）。
        temperature=req.temperature,
        top_p=req.top_p,
        top_k=req.top_k,
        length_penalty=req.length_penalty,
        repetition_penalty=req.repetition_penalty,
        max_mel_tokens=req.max_mel_tokens,
    )
    if STATE["supports_duration_factor"]:
        kwargs["duration_factor"] = req.duration_factor
        kwargs["lang"] = req.lang
    if STATE["supports_text_normalization"]:
        kwargs["text_normalization"] = req.text_normalization
    tts.infer(**kwargs)
    return wav_path


def _read_audio(path: Path) -> tuple[np.ndarray, int]:
    data, sr = sf.read(str(path), dtype="float32")
    if not np.isfinite(data).all():
        raise HTTPException(
            500,
            "生成音频含 NaN/Inf（MPS 数值问题）：请重试；仍失败则服务加 --device cpu 重启",
        )
    return data, sr


@asynccontextmanager
async def lifespan(app: FastAPI):
    args = app.state.args
    ensure_indextts_import(args.index_tts_root)
    # 上限必须先于 load_model：模型加载本身就是最大分配波（fp32 权重常驻 ~10 GiB），且设置
    # 失败要在服务就绪前暴露；uvicorn 单进程下 lifespan 与 infer 同进程，水位线必然作用于推理。
    mps_limit_gib = _apply_mps_limit(
        args.mps_mem_limit_gib, args.device, args.use_qwen_emo
    )
    print(">> 加载 IndexTTS 模型（首次运行会自动下载 w2v-bert 等辅助模型）…")
    tts, meta = load_model(
        args.version, args.model_dir, args.dtype, args.device, args.use_qwen_emo
    )
    encoder = _probe_encoders()
    STATE.update(
        tts=tts,
        version=meta["version"],
        device=str(getattr(tts, "device", "unknown")),
        dtype=meta["dtype_flag"],
        encoder=encoder,
        supports_duration_factor=meta["supports_duration_factor"],
        supports_text_normalization=meta["supports_text_normalization"],
        supports_blocks=meta.get("supports_blocks", False),
        low_vram=bool(getattr(tts, "low_vram", False)),
        supports_emo_text=getattr(tts, "qwen_emo", None) is not None,
        mps_mem_limit_gib=mps_limit_gib,
        infer_lock=asyncio.Lock(),
    )
    print(
        f">> 就绪：IndexTTS-{STATE['version']} device={STATE['device']} dtype={STATE['dtype']} "
        f"encoder={encoder} emo_text={'on' if STATE['supports_emo_text'] else 'off'} "
        f"blocks={'on' if STATE['supports_blocks'] else 'off'} "
        f"sampling=on seed=on"
        + (f" mps_limit={STATE['mps_mem_limit_gib']}GiB" if mps_limit_gib else "")
    )
    yield
    STATE.clear()


app = FastAPI(title="IndexTTS Pipeline Server", version="1.0", lifespan=lifespan)


@app.get("/health")
async def health():
    return {
        "ok": True,
        "version": STATE.get("version"),
        "device": STATE.get("device"),
        # MPS 进程显存上限（GiB）：null=未设（非 MPS / --device cpu / --mps-mem-limit-gib 0 /
        # 设置失败 / 缺省额度不足保底线）。仅提示性字段（无 must-act 语义），旧客户端不读
        # 无影响，新客户端可用于排障展示。
        "mps_mem_limit_gib": STATE.get("mps_mem_limit_gib"),
        "synthesizing": STATE["infer_lock"].locked()
        if STATE.get("infer_lock")
        else False,
        "dtype": STATE.get("dtype"),
        "encoder": STATE.get("encoder"),
        "supports_duration_factor": STATE.get("supports_duration_factor"),
        "supports_emo_text": STATE.get("supports_emo_text", False),
        "supports_text_normalization": STATE.get("supports_text_normalization", False),
        # 采样参数族与 seed 由本服务自身实现（不依赖模型版本，v2/v2.5 的 generation_kwargs
        # 默认值完全一致），故恒为 True。客户端据此在旧服务上对非默认取值硬失败。
        "supports_sampling_params": True,
        "supports_seed": True,
        # 块切分（story 档）：low_vram 路径 >40 字即分段（段间 200ms 定长静音会污染句界），
        # 不具备条件；v2 未验证亦不开放。客户端据此在旧服务上对块模式硬失败。
        "supports_blocks": STATE.get("supports_blocks", False),
        # 上游按显存自动开启（CUDA <10 GB），重启不会变：客户端据此把「不支持块合成」
        # 诊断为换档而非「代码过旧请重启」
        "low_vram": STATE.get("low_vram", False),
    }


def _mps_empty_cache() -> None:
    """MPS 缓存归还（非 MPS 平台静默跳过——torch.cuda/empty_cache 语义对偶）。"""
    try:
        import torch

        if hasattr(torch, "mps") and torch.backends.mps.is_available():
            torch.mps.empty_cache()
    except Exception:  # noqa: BLE001 - 清缓存失败不影响合成结果
        pass


def _is_mps_oom(exc: BaseException) -> bool:
    """只把实际运行在 MPS 上的 OOM 交给本服务的 MPS 诊断分流。"""
    if not str(STATE.get("device", "")).lower().startswith("mps"):
        return False
    # OutOfMemoryError 也可能来自 CPU/CUDA，故类型兜底只能在 MPS device 下启用。
    return (
        "MPS backend out of memory" in str(exc)
        or type(exc).__name__ == "OutOfMemoryError"
    )


_MPS_SIZE_RE = re.compile(
    r"(?P<label>MPS allocated|other allocations|max allowed|Tried to allocate):?\s*"
    r"(?P<value>[0-9]+(?:\.[0-9]+)?)\s*(?P<unit>[KMGT](?:i?B)|B|bytes?)",
    re.IGNORECASE,
)


def _mps_limit_exceeded(detail: str) -> bool:
    """仅将明确超过 high watermark 的 MPS OOM 视为确定性上限失败。

    同一 PyTorch 异常也可能表示系统内存不足或碎片化；解析失败时保守地返回 False，
    让客户端保留重试路径。
    """
    values: dict[str, float] = {}
    factors = {
        "b": 1.0,
        "byte": 1.0,
        "bytes": 1.0,
        "kb": 1000.0,
        "kib": 1024**1,
        "mb": 1000.0**2,
        "mib": 1024**2,
        "gb": 1000.0**3,
        "gib": 1024**3,
        "tb": 1000.0**4,
        "tib": 1024**4,
    }
    for match in _MPS_SIZE_RE.finditer(detail):
        unit = match.group("unit").lower()
        label = match.group("label").lower()
        values[label] = float(match.group("value")) * factors[unit]
    required = {
        "mps allocated",
        "other allocations",
        "max allowed",
        "tried to allocate",
    }
    if not required.issubset(values):
        return False
    return (
        values["mps allocated"]
        + values["other allocations"]
        + values["tried to allocate"]
        > values["max allowed"]
    )


def _apply_mps_limit(
    limit_gib: float | None, device: str, use_qwen_emo: bool = False
) -> float | None:
    """设置 MPS 进程级显存水位线，返回生效上限（GiB）；cpu/非 MPS/未设/失败/缺省不足保底 → None。

    torch MPS 分配器默认高水位 = 1.7 × recommendedMaxWorkingSetSize（本机 17.76 GiB →
    ≈30.2 GiB，超 24 GB 物理统一内存）：长跑累积与单次峰值都能把系统内存拉爆成换页卡顿，
    且 torch 在系统级内存压力前不报 OOM（RSI-017，机制循证见 INDEXTTS-2.5-ADVANCED §6.8）。
    上限经 set_per_process_memory_fraction（fraction = limit / recommended）设置；超限路径：
    分配器先自动归还缓存，仍不足抛 RuntimeError "MPS backend out of memory (...)"
    （torch 2.8.0 实测签名，消息含 other/max allowed 与 PYTORCH_MPS_HIGH_WATERMARK_RATIO
    提示；超限计数取 MTLDevice currentAllocatedSize——**只计本进程**分配（torch 注释
    "allocated in the process"，含 MPS/MPSGraph 隐式分配，不含其它进程，双进程实测：
    外部持 1.5 GiB 不影响本进程 1 GiB 水位判定），未设上限时的 OOM 为本进程缓存累积
    或单次峰值触默认水位，finally 归还缓存后重试可自愈，客户端只对「上限在场」分支
    短路重试）。
    torch 语义 fraction=0 是 unlimited 而非恢复默认；显式 limit=0 需同时覆盖继承的
    PYTORCH_MPS_HIGH_WATERMARK_RATIO，并调用 setter 使 allocator 采用 unlimited。
    """
    if device == "cpu":
        # 显式 cpu 部署全程不经 MPS 分配器：不设不上报（/health 如实回 null）
        return None
    try:
        import torch

        if not (hasattr(torch, "mps") and torch.backends.mps.is_available()):
            return None
        if limit_gib is not None and limit_gib <= 0:
            # 在首次访问 recommended memory 前覆盖继承的 HIGH env，避免 allocator 初始化
            # 时已锁定旧水位；setter 再次显式设 0，保证 Python API 与环境口径一致。
            os.environ["PYTORCH_MPS_HIGH_WATERMARK_RATIO"] = "0.0"
        rec_gib = torch.mps.recommended_max_memory() / (1024**3)
        if rec_gib <= 0:
            print(
                ">> ⚠️ MPS recommendedMaxWorkingSetSize 读取异常（0），跳过显存上限设置"
            )
            return None
        # 模型常驻口径：v2.5 MPS 强制 fp32 权重 ~10 GiB（INDEXTTS-2.5-ADVANCED §6.5 实测
        # 稳态 driver_allocated 10.00 GB；v2 在 MPS 上同样 fp32 常驻——infer_v2.py MPS
        # 分支强制 use_fp16=False，显式 --device mps 也仅 GPT .half() ~1.9 GB，两版本
        # 常驻同量级）+ QwenEmotion 1.5
        residency_gib = 10.0 + (1.5 if use_qwen_emo else 0.0)
        if limit_gib is None:
            # 策略默认：90% 推荐值与 16 GiB 取小——防 16 GB 小机型上固定值直接越界超发。
            # 下限保底（常驻 + ~1 GiB 峰值余量）：缺省额度跌破底线（16 GB 级机型 0.9×rec
            # ≈9.6 < 常驻）时设限只会开箱即 OOM——不设限回退 torch 默认水位（该机型无从
            # 「既跑得动又有护栏」两全，保可用性）。
            limit_gib = min(0.90 * rec_gib, 16.0)
            if limit_gib < residency_gib + 1.0:
                print(
                    f">> ⚠️ MPS 缺省上限 {limit_gib:.1f} GiB 低于模型可运行底线"
                    f"（~{residency_gib + 1.0:.1f} GiB = 常驻 ~{residency_gib:.1f}"
                    " + 峰值余量），跳过设置、沿用 torch 默认水位（1.7×recommended）"
                    "——小机型请靠句间缓存归还与短句控制内存，分工见 references/ENGINES.md"
                )
                return None
        if limit_gib <= 0:
            torch.mps.set_per_process_memory_fraction(0.0)
            print(
                ">> MPS 显存上限未设置（--mps-mem-limit-gib 0），已关闭 high watermark"
            )
            return None
        if limit_gib < residency_gib:
            print(
                f">> ⚠️ MPS 显存上限 {limit_gib} GiB 低于模型常驻（~{residency_gib:.1f} GiB），"
                "加载即可能 OOM（加载路径在 lifespan，无 /synthesize 的可操作 500 转换）"
            )
        fraction = limit_gib / rec_gib
        if (
            fraction > 2.0
        ):  # torch API 硬边界（>2 抛 ValueError），截断并告警保留用户可感知性
            fraction = 2.0
            print(
                f">> ⚠️ MPS 显存上限 {limit_gib} GiB 超出 API 边界，已截断为 2.0×recommended"
            )
        torch.mps.set_per_process_memory_fraction(float(fraction))
    except Exception as exc:  # noqa: BLE001 - fail-open：上限失败不阻断服务
        print(f">> ⚠️ MPS 显存上限设置失败（{exc}），继续以 torch 默认水位运行")
        return None
    effective = rec_gib * fraction
    print(
        f">> MPS 内存上限: {effective:.1f} GiB"
        f"（fraction {fraction:.3f} × recommended {rec_gib:.2f} GiB）"
    )
    return round(effective, 2)


def _block_reply(data, sr: int, req: SynthesizeRequest) -> tuple[dict, str]:
    """块 PCM → {split, clips, cuts, seams}（切分本体是 vibe_tts_blocks 的纯函数，numpy 在本进程可用）。

    切分失败不抛错：返回 split="failed"+原因，客户端按逐句兜底（保持 HTTP 200——
    失败是可降级的业务结果，不是服务错误）。
    """
    from vibe_tts_blocks import (  # noqa: E402 - 与文件头同方向，运行时导入省冷启动
        frame_db,
        plan_block_cuts,
        silence_runs,
        split_block_pcm,
    )

    n = len(req.block.weights)
    if n == 1:  # 单句块：无句界，仅尾垫
        clips_pcm = split_block_pcm(
            data, sr, [], req.block.discard_sec, req.block.tail_pad_sec
        )
        return _clips_json(clips_pcm, sr, [], []), "json"
    db = frame_db(data, sr)
    runs, on, off = silence_runs(db, sr)
    picked = plan_block_cuts(runs, req.block.weights, len(data) / sr, on, off)
    if picked is None:
        return {
            "split": "failed",
            "reason": f"静音候选与句数不匹配（{len(runs)} 个候选 / {n - 1} 个句界）",
            "clips": None,
        }, "json"
    # 分段缝检测：上游对 >118 token 的块按标点分段并以 interval_silence 定长静音拼接，
    # 该静音是精确零值且不随内容变化——检出即告警（客户端 WARN 提示缩短块）。
    # ⚠️ 只看**语音之间**的零值段：块首/块尾的数字静音（BigVGAN 输出的天然零垫）不是缝
    # （2026-09-25 实测：不排除首尾时 4/4 块全部误报）。
    import numpy as np

    flat = data.flatten()
    zero = np.flatnonzero(np.diff((flat == 0).astype(int)) != 0) + 1
    seams = []
    if len(zero):
        edges = np.concatenate(([0], zero, [len(flat)]))
        for k in range(len(edges) - 1):
            if flat[edges[k]] == 0:
                t0, dur = edges[k] / sr, (edges[k + 1] - edges[k]) / sr
                if 0.15 <= dur <= 0.25 and on < t0 and t0 + dur < off:
                    seams.append(round(t0, 2))
    clips_pcm = split_block_pcm(
        data, sr, picked, req.block.discard_sec, req.block.tail_pad_sec
    )
    return (
        _clips_json(
            clips_pcm, sr, [(round(s, 2), round(e, 2)) for s, e in picked], seams
        ),
        "json",
    )


def _clips_json(clips_pcm, sr: int, cuts, seams) -> dict:
    clips = []
    for pcm, _nat in clips_pcm:
        audio, fmt = encode_mp3(pcm, sr)
        clips.append(
            {
                "audio": base64.b64encode(audio).decode(),
                "durationSec": round(len(pcm) / sr, 3),
                # 实际编码格式（同逐句路径的 X-Audio-Format）：无 MP3 编码器时回退 wav，客户端据此硬拒
                "format": fmt,
            }
        )
    return {"split": "ok", "clips": clips, "cuts": cuts, "seams": seams}


@app.post("/synthesize")
async def synthesize(req: SynthesizeRequest):
    if not STATE:
        raise HTTPException(503, "模型尚未加载完成，请稍候")
    ref = Path(req.ref_path).expanduser()
    if not ref.is_file():
        raise HTTPException(400, f"参考音频不存在: {ref}")
    # 三种情感来源互斥：向量 / 参考音频 / 自然语言描述。上游会把向量与音频共同混合，
    # 但 emo_alpha 会被消费两次；静默降级比报错更难排查，故此处显式拒绝。
    sources = [
        name
        for name, val in (
            ("emo_vector", req.emo_vector),
            ("emo_ref_path", req.emo_ref_path),
            ("emo_text", req.emo_text),
        )
        if val
    ]
    if len(sources) > 1:
        raise HTTPException(400, f"情感来源互斥，只能给一个：{' / '.join(sources)}")
    if req.emo_ref_path:
        emo_ref = Path(req.emo_ref_path).expanduser()
        if not emo_ref.is_file():
            raise HTTPException(400, f"情感参考音频不存在: {emo_ref}")
        req.emo_ref_path = str(emo_ref)
    if req.emo_text and not STATE.get("supports_emo_text"):
        raise HTTPException(
            400,
            "emo_text 需要 QwenEmotion：服务启动时加 --use-qwen-emo（约 +1.5 GB 内存）",
        )
    # 有效和护栏 Σvec×alpha ≤ 0.8 是**本管线自定的口径**，不是上游行为：上游 infer() 从不做
    # 归一（normalize_emo_vec 只被 webui.py:665 的自定义向量分支调用），且 webui 那条的 0.8
    # 作用在「已乘 emo_bias 的和」上、且在 alpha 之前。因此从社区/WebUI 抄来的 (vec, alpha)
    # 在本服务上实际比原意强 16%–33%（含 calm 越重偏差越大）——跨来源参数迁移需重新试听定档。
    # 详见 references/INDEXTTS-2.5-ADVANCED.md §3.2。
    effective_sum = (sum(req.emo_vector) if req.emo_vector else 0.0) * req.emo_alpha
    if effective_sum > 0.8:  # infer 内部以 alpha 缩放向量，有效和超界会产生负混合权重
        raise HTTPException(
            400,
            f"情感向量有效和 {effective_sum:.3f}（Σvec×alpha）超过 0.8 上限，请降低权重或 --emo-alpha",
        )
    if req.duration_factor != 1.0 and not STATE["supports_duration_factor"]:
        raise HTTPException(
            400,
            "IndexTTS-2 不支持 duration_factor（v2.5 专属），请改用 v2.5 服务或去掉 --duration-factor",
        )
    if not req.text_normalization and not STATE["supports_text_normalization"]:
        raise HTTPException(
            400,
            "IndexTTS-2 的 infer() 没有 text_normalization 形参（v2.5 专属）："
            "请改用 v2.5 服务，或去掉 --no-text-normalization",
        )
    if req.block and not STATE.get("supports_blocks"):
        raise HTTPException(
            400,
            "块切分不可用（IndexTTS-2 或 low_vram 路径不支持）——客户端应以 supports_blocks 预检",
        )

    derived: list[float] | None = None
    async with STATE["infer_lock"]:
        try:
            if (
                req.emo_text
            ):  # 先算向量（占 GPU，须在锁内），再走与显式向量完全相同的合成路径
                derived = await asyncio.to_thread(
                    _qwen_vector_sync, STATE["tts"], req.emo_text, req.emo_alpha
                )
                req.emo_vector = derived
            with tempfile.TemporaryDirectory(prefix="indextts_") as td:
                wav_path = await asyncio.to_thread(
                    _infer_sync, STATE["tts"], ref, req, Path(td)
                )
                data, sr = await asyncio.to_thread(_read_audio, wav_path)
                if req.block:
                    # 块模式：PCM 在此（float32、未过 mp3 编码），切分走 vibe_tts_blocks 的纯函数
                    # （服务端持有 numpy；客户端 mutagen-only 且不得 import 兄弟模块）
                    audio, fmt = await asyncio.to_thread(_block_reply, data, sr, req)
                else:
                    audio, fmt = await asyncio.to_thread(encode_mp3, data, sr)
        except Exception as exc:
            # 水位线 OOM 转可操作 500：未捕获异常只会得到无信息的 "Internal Server Error"。
            # 仅在错误详情确认 allocated + other + tried 超过 max allowed 时认定为确定性
            # high watermark OOM，客户端按「上限不足」分支签名转 NonRetryableError 跳过重试；
            # 其余 MPS OOM 可能来自系统内存压力或碎片化，客户端保持重试。detail 带上限值与出路，
            # 经客户端
            # _http_error_detail 透传到最终报错。签名匹配为主判据
            # （MPS OOM 是 RuntimeError 文本，torch 2.8.0 实测 "MPS backend out of memory"）；
            # 类型名兜底 torch.OutOfMemoryError 子类（不 import torch）。其余异常原样放行
            # （NaN 已在 _read_audio 单独 500）。
            if _is_mps_oom(exc):
                limit = STATE.get("mps_mem_limit_gib")
                if limit is not None and _mps_limit_exceeded(str(exc)):
                    remedy = (
                        f"MPS 显存上限不足（当前上限 {limit} GiB）："
                        f"上调 --mps-mem-limit-gib 重启、拆短该句/块，"
                        f"或 --mps-mem-limit-gib 0 禁用 high watermark（unlimited，承担系统内存风险）。"
                    )
                elif limit is not None:
                    remedy = (
                        f"MPS 显存耗尽（已配置上限 {limit} GiB，但本次未确认触顶，"
                        "可能是系统内存压力或碎片化）：拆短该句/块，或重启服务端后重试。"
                    )
                else:  # 未设上限的三种成因：--mps-mem-limit-gib 0 / setter 失败 / 小机型缺省保底
                    # 劝「显式设上限」不攻自破：设更低的进程水位不会凭空多出内存，反让本进程
                    # 更早触顶；此处 OOM 为本进程缓存累积或单次峰值触默认水位（超限只计
                    # 本进程分配，不含其它进程），归还缓存/重启才对症，客户端对该分支保持重试。
                    remedy = (
                        "MPS 显存耗尽（未设上限，torch 默认水位 ≈1.7×recommended，"
                        "超限只计本进程分配，常为缓存累积或单次峰值）："
                        "拆短该句/块，或重启服务端复位分配器状态。"
                    )
                traceback.print_exc()  # HTTPException 不再经 uvicorn 落栈，控制台补记全量
                raise HTTPException(500, f"{remedy} 原始错误: {exc}") from exc
            raise
        finally:
            # MPS 长跑泄漏对冲：每次合成后归还分配器缓存。实测（2026-09-23 本机）连续
            # 合成约 40 分钟后 MPS 缓存累积击穿 30 GiB 上限，之后所有请求 500 且 health
            # 假绿；empty_cache 每句 <100ms，换整集长跑稳定。放 finally：失败路径（NaN
            # 500 / infer 抛错，含 OOM 本身）同样归还，否则一次击穿后缓存再无释放时机。
            await asyncio.to_thread(_mps_empty_cache)
    if req.block:
        return JSONResponse(audio)  # _block_reply 已产出 JSON dict（split ok/failed）
    headers = {"X-Audio-Format": fmt, "X-Duration-Sec": f"{len(data) / sr:.3f}"}
    if derived is not None:  # 回显 Qwen 推出的向量，便于事后用 --emo-vector 固化复现
        headers["X-Emo-Vector"] = ",".join(f"{x:g}" for x in derived)
    return Response(
        audio,
        media_type="audio/mpeg" if fmt == "mp3" else "audio/wav",
        headers=headers,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="IndexTTS 声音克隆推理服务")
    parser.add_argument(
        "--model-dir", default="checkpoints", help="模型目录（绝对或相对当前目录）"
    )
    parser.add_argument(
        "--index-tts-root",
        default=str(Path.cwd()),
        help="index-tts checkout 根目录（sys.path 兜底用）",
    )
    parser.add_argument(
        "--indextts-version", choices=["2", "2.5"], default="2.5", dest="version"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument(
        "--dtype",
        choices=["auto", "bf16", "fp16", "fp32"],
        default="auto",
        help="auto：v2.5→bf16（MPS 强制 fp32）/ v2→fp16；显式 fp32 两个版本均安全",
    )
    parser.add_argument("--device", choices=["auto", "mps", "cpu"], default="auto")
    parser.add_argument(
        "--use-qwen-emo",
        action="store_true",
        help="加载 QwenEmotion（0.6B，约 +1.5 GB 内存），开启后请求可用 emo_text 自然语言描述情感",
    )
    parser.add_argument(
        "--mps-mem-limit-gib",
        type=float,
        default=None,
        help="MPS 进程显存上限（GiB）：缺省 min(0.90×recommended, 16)（本机 ≈16；"
        "缺省额度不足 ~11 GiB 的小机型自动不设限，--use-qwen-emo 时随常驻上移至 ~12.5）；0=禁用 high watermark（unlimited，可能导致系统内存耗尽）；默认水位 1.7×recommended，"
        "24GB 机型 ≈30 GiB 超物理内存，长跑易拉爆系统内存）",
    )
    args = parser.parse_args()

    # argparse type=float 会放过 nan/inf（比较恒 False 的老陷阱），在此整体拦截
    if args.mps_mem_limit_gib is not None and not (
        math.isfinite(args.mps_mem_limit_gib) and args.mps_mem_limit_gib >= 0
    ):
        sys.exit(
            f"--mps-mem-limit-gib 必须为 ≥0 的有限数值（0=禁用 high watermark），收到: {args.mps_mem_limit_gib}"
        )

    args.index_tts_root = Path(args.index_tts_root).resolve()
    args.model_dir = Path(args.model_dir).resolve()
    if not args.model_dir.is_dir():
        sys.exit(
            f"模型目录不存在: {args.model_dir} —— 先按 references/ENGINES.md 部署 index-tts 并下载 checkpoints"
        )
    if not (args.model_dir / "config.yaml").is_file():
        sys.exit(
            f"模型目录缺少 config.yaml: {args.model_dir} —— checkpoints 下载不完整"
        )

    app.state.args = args
    print(
        f">> IndexTTS 服务器启动: {args.host}:{args.port} version={args.version} model_dir={args.model_dir}"
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
