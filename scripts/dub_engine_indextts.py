"""IndexTTS-2.5 引擎原语：采样参数表、HTTP 客户端、缓存摘要、内容寻址版本库。

摘要格式（digest_indextts）是缓存稳定性契约：新增字段一律追加在末尾且默认省略，
tests/test_digest.py 以黄金哈希钉死——改动前先读测试。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from dub_paths import data_dir

CONCURRENCY_INDEXTTS = 1  # 服务端串行锁推理；>1 会在锁后排队，排队时长计入客户端超时
HTTP_TIMEOUT = 600  # MPS fp32 长句可达数分钟；须覆盖队列等待
DEFAULT_SERVER = "http://127.0.0.1:8766"


def default_server() -> str:
    """服务地址：DUB_INDEXTTS_SERVER 环境变量 → 本机缺省。"""
    return os.environ.get("DUB_INDEXTTS_SERVER") or DEFAULT_SERVER


# 上游自回归采样参数的默认值 —— 已知副本，锚点 indextts/infer_v2_5.py:731-739（HEAD 4f8792f）；
# 与 infer_v2.py:536-544 完全一致，两版共享同一组默认。
#
# **分层 SSOT**：`SAMPLING_PASSTHROUGH_DEFAULTS`（7 个经 **generation_kwargs 透传给 HF
# generate 的参数）是唯一的数据副本，服务端 tts_server.py 运行时从本模块导入它（那个进程
# 不受本 skill 版本控制之外的依赖影响——导入是纯常量读取）；本字典在其上追加两个
# **非透传**键：text_normalization（v2.5 infer() 的独立形参）与 seed（本服务自己 set_seed），
# 它们不进 SAMPLING_RANGES/SAMPLING_CLI 的透传校验路径。
#
# 本副本只有一个用途：判定「这一项是否被显式改过」，从而决定**要不要进缓存摘要**。
# 摘要沿用「未使用即省略」规则（同 |beams=N），故全部取默认时摘要与历史逐字节相同 ——
# 这是已上线三集近 600 句缓存零失效的前提，由 tests/test_digest.py 黄金哈希钉死。
SAMPLING_PASSTHROUGH_DEFAULTS: dict[str, float | int] = {
    "temperature": 0.8,
    "top_p": 0.8,
    "top_k": 30,
    "length_penalty": 0.0,
    "repetition_penalty": 10.0,
    "max_mel_tokens": 1500,
    "interval_silence": 200,
}
SAMPLING_DEFAULTS: dict[str, float | int | bool | None] = {
    **SAMPLING_PASSTHROUGH_DEFAULTS,
    "text_normalization": True,
    "seed": None,
}

#: 采样参数的合法区间（对齐上游 webui.py:901-910 的滑杆），用于长跑前提前失败。
#: max_mel_tokens 上限 1815 = config.yaml 的 gpt.max_mel_tokens（mel 位置嵌入容量 ≈36.2 s）。
SAMPLING_RANGES: dict[str, tuple[float, float]] = {
    "temperature": (0.1, 2.0),
    "top_p": (0.0, 1.0),
    "top_k": (0, 100),
    "length_penalty": (-2.0, 2.0),
    "repetition_penalty": (0.1, 20.0),
    "max_mel_tokens": (50, 1815),
    "interval_silence": (0, 2000),
}


def sampling_suffix(sampling: dict | None) -> str:
    """采样参数 → 缓存摘要后缀。空 dict 返回空串（存量缓存零失效的关键）。

    键按字母序，值用 repr()（防 0.7 → 0.70 之类的表示漂移，与 alpha/df 同口径）。
    """
    if not sampling:
        return ""
    return "".join(f"|{k}={sampling[k]!r}" for k in sorted(sampling))


class NonRetryableError(Exception):
    """重试无意义的错误（4xx 类 / 确定性 MPS OOM 签名）：直接失败并携带服务端错误详情。"""


def _http_error_detail(e: urllib.error.HTTPError) -> str:
    try:
        parsed = json.loads(e.read())
        if isinstance(parsed, dict):
            return str(parsed.get("detail", parsed))
        if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
            return str(parsed[0].get("msg", parsed[0]))  # FastAPI 422 校验数组
        return str(parsed)
    except Exception:  # noqa: BLE001 - 详情解析失败退化为字符串
        return str(e)


def _deterministic_mps_oom(detail: str) -> bool:
    """上限在场时的确定性 MPS 水位线 OOM（服务端 tts_server remedy 的「上限不足」分支
    以「MPS 显存上限不足」开头）：该句/块内在需求超上限，重试只是分钟级空跑（手册 §七）
    ——转 NonRetryableError 短路。**只匹配该分支**：未设上限时默认水位 1.7×recommended
    极高，OOM 为本进程缓存累积或极端峰值（超限只计本进程分配，服务端 finally 已归还
    缓存），重试仍可能自愈，须留在 5xx 重试桶；文案失配时自然回退可重试（fail-safe 方向）。"""
    return detail.startswith("MPS 显存上限不足")


def http_json(
    method: str, url: str, payload: dict | None = None, timeout: int = HTTP_TIMEOUT
) -> dict:
    """同步 urllib 调用（调用方需置于 asyncio.to_thread）；4xx → NonRetryableError。"""
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            ctype = resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        detail = _http_error_detail(e)
        if 400 <= e.code < 500:
            raise NonRetryableError(f"HTTP {e.code}: {detail}") from e
        raise RuntimeError(f"HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"连接失败: {e.reason}") from e
    if "application/json" not in ctype:
        raise NonRetryableError(f"响应 Content-Type 异常: {ctype}")
    return json.loads(body)


def http_synthesize(
    server: str,
    text: str,
    ref: str,
    vec: list[float] | None,
    alpha: float,
    df: float,
    lang: str,
    num_beams: int = 1,
    emo_ref: str | None = None,
    emo_text: str | None = None,
    headers_out: dict | None = None,
    sampling: dict | None = None,
) -> tuple[bytes, str]:
    """POST /synthesize → (mp3 bytes, X-Audio-Format)。4xx 与确定性 MPS OOM 不可重试。

    headers_out：可选出参，传入 dict 时回填全部响应头（如 emo_text 模式的 X-Emo-Vector），
    供试听工具回显；管线主路径不需要，故保持返回值签名不变。
    **键统一小写**——urllib 的 HTTPMessage 查找不分大小写，但拷进普通 dict 后会变成
    大小写敏感，而 Starlette 下发的响应头名是小写的，故此处归一避免调用方取不到值。
    """
    payload: dict = {
        "text": text,
        "ref_path": ref,
        "emo_alpha": alpha,
        "duration_factor": df,
        "lang": lang,
        "num_beams": num_beams,
    }
    if vec is not None:
        payload["emo_vector"] = vec
    if emo_ref:  # 情感参考音频：音色仍取 ref，语调迁移自 emo_ref（服务端与向量互斥）
        payload["emo_ref_path"] = emo_ref
    if emo_text:  # 自然语言情感描述（服务端 QwenEmotion 转向量）
        payload["emo_text"] = emo_text
    if sampling:  # 只发被显式改过的采样参数，其余由服务端取上游默认
        payload.update(sampling)
    req = urllib.request.Request(
        f"{server}/synthesize",
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            if headers_out is not None:
                headers_out.update({k.lower(): v for k, v in resp.headers.items()})
            return resp.read(), resp.headers.get("X-Audio-Format", "unknown")
    except urllib.error.HTTPError as e:
        detail = _http_error_detail(e)
        if 400 <= e.code < 500 or _deterministic_mps_oom(detail):
            raise NonRetryableError(f"HTTP {e.code}: {detail}") from e
        raise RuntimeError(f"HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"连接失败: {e.reason}") from e


def digest_indextts(
    ref_sha1: str,
    style: str,
    vec: list[float] | None,
    alpha: float,
    df: float,
    lang: str,
    engine_tag: str,
    text: str,
    num_beams: int = 1,
    emo_ref_sha1: str | None = None,
    emo_text: str | None = None,
    sampling: dict | None = None,
    block: str | None = None,
    pos: str | None = None,
) -> str:
    vec_str = ",".join(repr(x) for x in vec) if vec else "none"
    # 束宽/情感来源/采样参数改变合成结果，须入键；未使用时省略字段——沿用历史摘要格式，
    # 存量缓存不失效。新增字段一律追加在**末尾**且默认省略，故摘要格式可持续扩展。
    beams_part = "" if num_beams == 1 else f"|beams={num_beams}"
    emo_part = f"|emoref={emo_ref_sha1}" if emo_ref_sha1 else ""
    emo_part += f"|emotext={emo_text}" if emo_text else ""
    # 块模式（story 档）：块=缓存单位——block 后缀由成员文本+切分参数派生，改任一句
    # 即整块换摘要；pos 区分块内句位。仅块模式追加，逐句口径逐字节不变。
    block_part = f"|block={block}|{pos}" if block else ""
    return hashlib.sha1(
        f"indextts|{engine_tag}|{ref_sha1}|{lang}|{style}|{vec_str}|{alpha!r}|{df!r}|{text}"
        f"{beams_part}{emo_part}{sampling_suffix(sampling)}{block_part}".encode()
    ).hexdigest()


# ---------------- 音频版本库（内容寻址持久库） ----------------
#
# 按「句 id + digest 前 12 位」命名放在机器级持久目录：同一 (文本, 引擎参数) 不重复合成。
#   - 合成成功 → store_deposit 入库（同 digest 刷新；不同 digest 并存=保留历史版本）；
#   - 本地未命中 → store_restore 按 digest 回收；
# 条目路径 <slug>/<sid>.<digest12>.mp3 + 同名 .sha 邻档存全量 digest（防 12 位前缀巧合）。
# 库根整体搬迁零失效（mv 即迁移）。库是加速器不是门：失败只 WARN。

STORE_ENV = "DUB_STORE"


def store_root(disabled: bool = False) -> Path | None:
    """版本库根目录；disabled 或 DUB_STORE 置空串时返回 None（全程直通不落盘）。

    解析顺序：DUB_STORE → <DUB_DATA_DIR>/tts-store。"""
    if disabled:
        return None
    env = os.environ.get(STORE_ENV)
    if env == "":
        return None
    if env:
        return Path(env).expanduser()
    return data_dir() / "tts-store"


def store_entry(store: Path | None, slug: str, sid: str, digest: str) -> Path | None:
    """库内条目路径：<root>/<集 slug>/<sid>.<digest12>.mp3（同名 .sha 邻档存全量 digest）。"""
    if store is None:
        return None
    return store / slug / f"{sid}.{digest[:12]}.mp3"


def store_has(store: Path | None, slug: str, sid: str, digest: str) -> bool:
    """--plan 用：库内是否存在该 digest 的句子（校验 .sha 邻档，防 12 位前缀巧合）。"""
    if store is None:
        return False
    mp3 = store_entry(store, slug, sid, digest)
    assert mp3 is not None
    sha = mp3.with_suffix(".sha")
    try:
        return (
            mp3.is_file()
            and mp3.stat().st_size > 0
            and sha.is_file()
            and sha.read_text(encoding="utf-8") == digest
        )
    except (OSError, UnicodeError) as e:
        print(f"WARN 版本库读取失败（{sid}）：{e}", file=sys.stderr)
        return False


def store_deposit(
    mp3: Path, sid: str, digest: str, store: Path | None, slug: str
) -> None:
    """合成成功后入库。失败只 WARN 不断长跑——库是加速器，不是门。"""
    if store is None:
        return
    dst = store_entry(store, slug, sid, digest)
    assert dst is not None
    sha = dst.with_suffix(".sha")
    tmp_mp3: Path | None = None
    tmp_sha: Path | None = None
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=dst.parent, prefix=f".{dst.name}.", suffix=".tmp", delete=False
        ) as f:
            tmp_mp3 = Path(f.name)
        shutil.copyfile(mp3, tmp_mp3)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=dst.parent,
            prefix=f".{sha.name}.",
            suffix=".tmp",
            delete=False,
        ) as f:
            tmp_sha = Path(f.name)
            f.write(digest)
        os.replace(tmp_mp3, dst)
        tmp_mp3 = None
        os.replace(tmp_sha, sha)
        tmp_sha = None
    except (OSError, UnicodeError) as e:
        print(f"WARN 版本库入库失败（{sid}）：{e}", file=sys.stderr)
    finally:
        for tmp in (tmp_mp3, tmp_sha):
            if tmp is not None:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass


def store_restore(
    sid: str, digest: str, out_dir: Path, store: Path | None, slug: str
) -> bool:
    """集内缓存 miss 时先向库回收；命中则 mp3 + .sha 落位，等价集内缓存命中。"""
    if not store_has(store, slug, sid, digest):
        return False
    src = store_entry(store, slug, sid, digest)
    assert src is not None
    try:
        shutil.copyfile(src, out_dir / f"{sid}.mp3")
        (out_dir / f"{sid}.sha").write_text(digest, encoding="utf-8")
        return True
    except (OSError, UnicodeError) as e:
        print(f"WARN 版本库回收失败（{sid}）：{e}", file=sys.stderr)
        return False


def http_synthesize_block(
    server: str,
    text: str,
    ref: str,
    vec: list[float],
    alpha: float,
    df: float,
    lang: str,
    num_beams: int,
    weights: list[float],
    discard_sec: float,
    tail_pad_sec: float,
    sampling: dict | None = None,
) -> dict:
    """块模式 POST /synthesize → JSON {clips:[{audio,durationSec,format}], cuts, seams, split}。

    块音频在服务端按句界切回 N 个 mp3（b64）。超时 1800 s：块 ≈3 句音频 12–18 s，
    忙时热节流可把单块推理推过逐句口径的 600 s。
    """
    payload: dict = {
        "text": text,
        "ref_path": ref,
        "emo_vector": vec,
        "emo_alpha": alpha,
        "duration_factor": df,
        "lang": lang,
        "num_beams": num_beams,
        "block": {
            "weights": weights,
            "discard_sec": discard_sec,
            "tail_pad_sec": tail_pad_sec,
        },
    }
    if sampling:
        payload.update(sampling)
    req = urllib.request.Request(
        f"{server}/synthesize",
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=1800) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = _http_error_detail(e)
        if 400 <= e.code < 500 or _deterministic_mps_oom(detail):
            raise NonRetryableError(f"HTTP {e.code}: {detail}") from e
        raise RuntimeError(f"HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"连接失败: {e.reason}") from e


def block_digest_suffix(
    member_texts: list[str], discard: float, tail_pad: float
) -> str:
    """块缓存后缀（仅块模式追加）：改任一句/切分参数 ⇒ 整块换 digest 重录。

    `split=vN` 版本化块文本拼接与切分算法（v2：软停顿结尾不再补 `。`；v3：收引号前
    的标点计入末字、逐句兜底仅末句补尾垫）——算法改变合成结果而成员文本不变时，靠它换键。
    换键口径：改了送合成文本或切段时长（⇒ 时间轴）才递增；静音内的样本级淡变（如末段
    补 5ms 淡入）时长不变、听感不可辨，不换键——否则存量音频会为不可听差异整块重录。
    """
    payload = "\x1f".join(member_texts) + f"\x1f{discard!r}\x1f{tail_pad!r}\x1fsplit=v3"
    return hashlib.sha1(payload.encode()).hexdigest()[:12]
