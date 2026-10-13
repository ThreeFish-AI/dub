#!/usr/bin/env python3
"""dub —— 文本 + 风格 → .mp3 配音。双引擎：edge（预置音色，联网）/ index-tts（本地声音克隆）。

用法（脚本当黑盒，先 --help）：
  edge：      uv run --no-project --with edge-tts --with mutagen $T/scripts/dub.py say \
                  --text "今天我们拆解的是：Agent Skills"
  index-tts： uv run --no-project --with mutagen --with soundfile --with numpy $T/scripts/dub.py say \
                  --engine index-tts --final-voice --style story --ref <参考样本.wav> \
                  --expect-ref-sha1 <12 位> --text "今天我们拆解的是：Agent Skills"
  风格列表：  python3 $T/scripts/dub.py styles
  服务：      python3 $T/scripts/dub.py server start|stop|status|doctor

输出：<DUB_OUTPUT_DIR 或 --out-dir>/<名>.mp3；stdout **最后一行**是 JSON：
  {"path","durationSec","sampleRate","engine","style","cached","chunks"}
契约见 references/CONTRACT.md。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import dub_audio  # noqa: E402
import dub_engine_edge  # noqa: E402
import dub_engine_indextts as ix  # noqa: E402
import dub_paths  # noqa: E402
import dub_profiles  # noqa: E402
import dub_server_ctl as ctl  # noqa: E402
from dub_textprep import split_chunks, tts_text  # noqa: E402

ENGINES = ("edge", "index-tts")
LANGS = {"zh": "ZH", "en": "EN"}
#: index-tts 块间补的静音：服务端 interval_silence 的上游默认 200 ms，与块内句间停顿同口径。
CHUNK_GAP_SEC = 0.2
SLUG_MAX = 24


class UsageError(Exception):
    """可操作的用法错误：stderr 打印后以退出码 2 结束。"""


# ---------------- 纯函数 ----------------


def slugify(text: str, limit: int = SLUG_MAX) -> str:
    """文本 → 文件名友好的 slug：保留字母数字与汉字，其余折成 `-`；空则 `dub`。"""
    out: list[str] = []
    for ch in unicodedata.normalize("NFKC", text):
        if ch.isalnum():
            out.append(ch.lower())
        elif out and out[-1] != "-":
            out.append("-")
    slug = "".join(out).strip("-")[:limit].strip("-")
    return slug or "dub"


def sha1_12(path: Path) -> str:
    """参考样本指纹（sha1 前 12 位）——与 vibe-video refs.toml 同口径。"""
    return hashlib.sha1(path.read_bytes()).hexdigest()[:12]


def say_digest(engine: str, parts: list[str]) -> str:
    """say 产物的缓存键：任一影响音频的参数变化即换键。"""
    return hashlib.sha1("|".join(["dub", engine, *parts]).encode()).hexdigest()


# ---------------- say ----------------


def _read_text(args: argparse.Namespace) -> str:
    if args.text_file:
        text = Path(args.text_file).expanduser().read_text(encoding="utf-8")
    else:
        text = args.text or ""
    text = text.strip()
    if not text:
        raise UsageError("文本为空：用 --text 或 --text-file 提供要配音的内容")
    return text


def _final_voice_gate(args: argparse.Namespace) -> None:
    """RSI-040：index-tts 是本人声音克隆（生物特征），默认不启用，须具名授权。

    `--engine index-tts` 由用户显式给出仍不够——还要 `--final-voice`，二者合起来才是
    「本人明确要求」的证据；Agent 不得自行为用户补上这个 flag。
    """
    if not args.final_voice:
        raise UsageError(
            "index-tts 使用本人声音克隆，须具名授权：由本人显式要求后加 --final-voice。\n"
            "  （默认引擎 edge 无需授权；Agent 不得自行提议或补加 index-tts / --final-voice）"
        )


def _say_edge(args: argparse.Namespace, text: str, out_dir: Path) -> dict:
    lang = LANGS[args.lang]
    voice = dub_engine_edge.resolve_voice(args.voice, args.lang)
    rate = args.rate or dub_profiles.DEFAULT_RATE
    digest = say_digest("edge", [voice, rate, lang, tts_text(text, lang)])
    name = args.out_name or f"{slugify(text)}.{digest[:12]}"
    dst = out_dir / f"{name}.mp3"
    cached = dst.is_file() and dst.stat().st_size > 0 and not args.force
    if not cached:
        asyncio.run(dub_engine_edge.synth_text(text, voice, rate, dst, lang))
    return {"dst": dst, "cached": cached, "style": voice, "chunks": 1}


def _say_indextts(args: argparse.Namespace, text: str, out_dir: Path) -> dict:
    _final_voice_gate(args)
    if not args.ref:
        raise UsageError(
            "index-tts 需要 --ref <参考样本.wav>（本人声音样本，10–14 s、单人、不入库）"
        )
    ref = Path(args.ref).expanduser().resolve()
    if not ref.is_file():
        raise UsageError(f"参考样本不存在：{ref}")
    ref_sha = sha1_12(ref)
    if args.expect_ref_sha1 and args.expect_ref_sha1 != ref_sha:
        raise UsageError(
            f"参考样本指纹不符：期望 {args.expect_ref_sha1}，实际 {ref_sha}——"
            "样本被换过会让音色整体漂移，先确认是不是原件"
        )
    style = args.style or "story"
    preset = dub_profiles.STYLE_PRESETS.get(style)
    if preset is None:
        raise UsageError(
            f"未知风格 {style!r}（可用：{', '.join(dub_profiles.STYLE_PRESETS)}）"
        )
    vec = preset["vec"]
    alpha, df, beams = preset["alpha"], preset["df"], preset.get("beams", 1)
    sampling: dict = {}
    if preset.get("seed") is not None:
        sampling["seed"] = int(preset["seed"])
    block_cfg = preset.get("block") or {}
    lang = LANGS[args.lang]
    perform = bool(block_cfg.get("perform_punct"))

    chunks = split_chunks(
        tts_text(text, lang, perform=perform),
        max_sentences=int(block_cfg.get("max_sentences", 3)),
        max_chars=int(block_cfg.get("max_chars", 90)),
    )
    if not chunks:
        raise UsageError("文本经预处理后为空")
    server = args.server or ix.default_server()
    digests = [
        ix.digest_indextts(
            ref_sha,
            style,
            vec,
            alpha,
            df,
            lang,
            "indextts",
            c,
            beams,
            None,
            None,
            sampling,
        )
        for c in chunks
    ]
    whole = say_digest("index-tts", [ref_sha, style, lang, *digests])
    name = args.out_name or f"{slugify(text)}.{whole[:12]}"
    dst = out_dir / f"{name}.mp3"
    if dst.is_file() and dst.stat().st_size > 0 and not args.force:
        return {"dst": dst, "cached": True, "style": style, "chunks": len(chunks)}

    store = ix.store_root(args.no_store)
    slug = "say"
    audio: list[bytes | None] = [None] * len(chunks)
    todo: list[int] = []
    for i, d in enumerate(digests):
        entry = ix.store_entry(store, slug, f"c{i}", d)
        if (
            not args.force
            and entry is not None
            and ix.store_has(store, slug, f"c{i}", d)
        ):
            audio[i] = entry.read_bytes()
        else:
            todo.append(i)

    started: dict | None = None
    if todo:
        try:
            started = ctl.start_server(server)
        except ctl.ServerError as e:
            raise UsageError(str(e)) from e
        _print_err(
            f"index-tts 服务：{'复用' if started['reused'] else '已拉起'}（port {started['port']}），待合成 {len(todo)}/{len(chunks)} 块"
        )
    try:
        for i in todo:
            _print_err(f"  合成块 {i + 1}/{len(chunks)}（{len(chunks[i])} 字）…")
            audio[i] = _synth_chunk(
                server, chunks[i], str(ref), vec, alpha, df, lang, beams, sampling
            )
            if store is not None:
                tmp = out_dir / f".{name}.c{i}.tmp.mp3"
                dub_audio.write_atomic(audio[i], tmp)  # type: ignore[arg-type]
                ix.store_deposit(tmp, f"c{i}", digests[i], store, slug)
                tmp.unlink(missing_ok=True)
        dub_audio.concat_mp3([a for a in audio if a is not None], CHUNK_GAP_SEC, dst)
    finally:
        if started and started["started"] and not args.keep_server:
            r = ctl.stop_or_watch(server)
            _print_err(
                f"服务收尾：{r['action']}"
                + (f"（watchdog 日志 {r['log']}）" if r["action"] == "watchdog" else "")
            )
    return {"dst": dst, "cached": False, "style": style, "chunks": len(chunks)}


def _synth_chunk(server, text, ref, vec, alpha, df, lang, beams, sampling) -> bytes:
    """单块合成，带重试：4xx / 确定性 MPS OOM 不重试，其余退避重试 4 次。"""
    import time

    last: Exception | None = None
    for attempt in range(4):
        try:
            data, fmt = ix.http_synthesize(
                server,
                text,
                ref,
                vec,
                alpha,
                df,
                lang,
                beams,
                sampling=sampling or None,
            )
        except ix.NonRetryableError:
            raise
        except Exception as e:  # noqa: BLE001 - 本地 HTTP 服务需整体重试
            last = e
            time.sleep(1.5 * (attempt + 1))
            continue
        if fmt != "mp3":
            raise RuntimeError(
                f"服务端返回 {fmt!r} 而非 mp3（缺编码器？先 `dub.py server doctor`）"
            )
        return data
    raise RuntimeError(f"合成失败（已重试 4 次）：{last}")


def _print_err(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def cmd_say(args: argparse.Namespace) -> int:
    text = _read_text(args)
    out_dir = dub_paths.output_dir(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    res = (_say_edge if args.engine == "edge" else _say_indextts)(args, text, out_dir)
    dst: Path = res["dst"]
    dur, sr = dub_audio.mp3_info(dst)
    print(
        json.dumps(
            {
                "path": str(dst),
                "durationSec": round(dur, 3),
                "sampleRate": sr,
                "engine": args.engine,
                "style": res["style"],
                "cached": res["cached"],
                "chunks": res["chunks"],
            },
            ensure_ascii=False,
        )
    )
    return 0


# ---------------- styles / server ----------------


def cmd_styles(_: argparse.Namespace) -> int:
    print(
        f"{'风格':<16}{'向量和':>7}{'alpha':>7}{'df':>6}{'beams':>7}{'seed':>7}  说明"
    )
    for name, p in dub_profiles.STYLE_PRESETS.items():
        vec = p["vec"]
        s = f"{sum(vec):.2f}" if vec else "—"
        print(
            f"{name:<16}{s:>7}{p['alpha']:>7}{p['df']:>6}{p.get('beams', 1):>7}{str(p.get('seed', '')):>7}  {p['label']}"
        )
    print(
        f"\nedge 缺省音色：{dict(dub_profiles.EDGE_VOICE_BY_LANG)}  缺省语速 {dub_profiles.DEFAULT_RATE}"
    )
    return 0


def cmd_server(args: argparse.Namespace) -> int:
    server = args.server or ix.default_server()
    try:
        if args.action == "start":
            print(json.dumps(ctl.start_server(server), ensure_ascii=False))
        elif args.action == "stop":
            print(json.dumps(ctl.stop_or_watch(server), ensure_ascii=False))
        elif args.action == "status":
            h = ctl.health(server)
            port = ctl.port_from_server(server)
            print(json.dumps({"server": server, "healthy": bool(h and h.get("ok")), "health": h,
                              "listeners": ctl.port_listeners(port)}, ensure_ascii=False))  # fmt: skip
        else:
            return _doctor(server)
    except ctl.ServerError as e:
        _print_err(str(e))
        return 1
    return 0


def _doctor(server: str) -> int:
    root = dub_paths.index_tts_root()
    checks = [
        (
            "DUB_OUTPUT_DIR",
            bool(__import__("os").environ.get("DUB_OUTPUT_DIR")),
            "未设（say 需 --out-dir 或此变量）",
        ),
        (
            "index-tts checkout",
            (root / "checkpoints").is_dir(),
            f"{root}/checkpoints 缺失（DUB_INDEX_TTS_ROOT）",
        ),
        ("uv", shutil.which("uv") is not None, "uv 不在 PATH"),
        ("lsof", shutil.which("lsof") is not None, "lsof 不在 PATH"),
        (
            "服务健康",
            ctl.server_healthy(server),
            f"{server}/health 不通（`dub.py server start` 可拉起）",
        ),
    ]
    for mod, pip in (
        ("edge_tts", "edge-tts"),
        ("mutagen", "mutagen"),
        ("soundfile", "soundfile"),
        ("numpy", "numpy"),
    ):
        try:
            __import__(mod)
            ok = True
        except ImportError:
            ok = False
        checks.append(
            (f"python:{mod}", ok, f"缺 {pip}（`uv run --with {pip}` 按需带上）")
        )
    bad = 0
    for name, ok, hint in checks:
        print(f"{'✅' if ok else '⚠️ '} {name}" + ("" if ok else f" —— {hint}"))
        bad += 0 if ok else 1
    print(f"\n数据目录：{dub_paths.data_dir()}")
    return 0  # doctor 只报告，不以未就绪为失败（edge 路径本就不需要 index-tts）


# ---------------- argparse ----------------


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="dub", description="文本 + 风格 → .mp3 配音（edge / index-tts）"
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("say", help="合成一段配音并写出 mp3")
    src = s.add_mutually_exclusive_group()
    src.add_argument("--text", help="要配音的文本")
    src.add_argument("--text-file", help="从文件读取文本（UTF-8）")
    s.add_argument(
        "--engine", choices=ENGINES, default="edge", help="引擎（默认 edge）"
    )
    s.add_argument(
        "--style", help="[index-tts] 风格预设（默认 story；见 styles 子命令）"
    )
    s.add_argument(
        "--lang", choices=sorted(LANGS), default="zh", help="文本语言（默认 zh）"
    )
    s.add_argument(
        "--voice", help="[edge] 音色（默认 zh-CN-YunxiNeural / en-US-AndrewNeural）"
    )
    s.add_argument("--rate", help="[edge] 语速，如 +4%% / -10%%（默认 +4%%）")
    s.add_argument("--ref", help="[index-tts] 参考音色样本 wav（本人声音，不入库）")
    s.add_argument(
        "--expect-ref-sha1",
        help="[index-tts] 样本 sha1 前 12 位，不符即拒绝（防换样本致音色漂移）",
    )
    s.add_argument(
        "--final-voice",
        action="store_true",
        help="[index-tts] 本人显式要求的具名授权（RSI-040）",
    )
    s.add_argument(
        "--server",
        help="[index-tts] 服务地址（默认 DUB_INDEXTTS_SERVER 或 http://127.0.0.1:8766）",
    )
    s.add_argument(
        "--keep-server",
        action="store_true",
        help="[index-tts] 合成后不停服务（默认停我们拉起的实例）",
    )
    s.add_argument("--no-store", action="store_true", help="[index-tts] 不读写版本库")
    s.add_argument("--out-dir", help="输出目录（默认 DUB_OUTPUT_DIR）")
    s.add_argument("--out-name", help="输出文件名（不含 .mp3；默认 文本slug.摘要12位）")
    s.add_argument("--force", action="store_true", help="忽略缓存强制重合成")
    s.set_defaults(func=cmd_say)

    t = sub.add_parser("styles", help="列出风格预设")
    t.set_defaults(func=cmd_styles)

    v = sub.add_parser("server", help="index-tts 服务起停与体检")
    v.add_argument("action", choices=["start", "stop", "status", "doctor"])
    v.add_argument("--server", help="服务地址")
    v.set_defaults(func=cmd_server)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except UsageError as e:
        _print_err(f"❌ {e}")
        return 2
    except (RuntimeError, ValueError, OSError, ix.NonRetryableError) as e:
        _print_err(f"❌ {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
