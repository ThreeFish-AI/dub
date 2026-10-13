#!/usr/bin/env python3
"""IndexTTS 服务生命周期：按需起、按端口停、占用时交给有界 watchdog 巡检后再停。

铁律（均源自 vibe-video 的长跑教训）：
- 只按**端口**定位进程，绝不 `pkill -f`（会误伤同名进程）；
- 只停 cmdline 含 `tts_server.py` 的监听者；端口被别的进程占着一律拒绝，不杀；
- 「忙」= /health.synthesizing 或存在其他进程的 ESTABLISHED 客户端连接；
- 停服前要求**连续**空闲（去抖）：逐请求短连接的客户端在句间有毫秒级空窗，
  单次采样会把别人长跑中途的服务杀掉；
- watchdog 有界（超时留着服务不管）、每个 tick 复核监听 PID 与 cmdline（防 PID 复用）。

本模块纯 stdlib——watchdog 作为分离子进程用基础解释器直接跑，不依赖 uv 临时环境。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dub_paths import data_dir, index_tts_root, logs_dir  # noqa: E402

SERVER_SCRIPT = Path(__file__).resolve().parent / "dub_tts_server.py"
#: 监听者 cmdline 必含的标记；同时放行 vibe-video 旧名 tts_server.py 的存量实例。
SERVER_MARK = "tts_server.py"

POLL_SEC = 3.0
KILL_GRACE_SEC = 10.0
STARTUP_TIMEOUT_SEC = 300.0  # 模型冷加载 ~1–2 分钟

WATCHDOG_MAX_WAIT_SEC = 7200.0  # 有界：2 小时后放弃（服务留着，记日志）
WATCHDOG_POLL_SEC = 30.0
WATCHDOG_IDLE_GRACE_SEC = 90.0  # 连续空闲这么久才停

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}


class ServerError(RuntimeError):
    """服务起停的可操作错误（调用方转成退出信息）。"""


# ---------------- 纯函数（单测靶点） ----------------


def port_from_server(server: str) -> int:
    parts = urllib.parse.urlsplit(server)
    if parts.port:
        return parts.port
    if parts.scheme == "https":
        return 443
    if parts.scheme == "http":
        return 80
    raise ServerError(
        f"无法从 server {server!r} 解析端口（需 http(s)://host:port 形态）"
    )


def is_local_server(server: str) -> bool:
    return (urllib.parse.urlsplit(server).hostname or "") in _LOCAL_HOSTS


def server_argv(
    port: int, use_qwen_emo: bool = False, mps_mem_limit_gib: float | None = None
) -> list[str]:
    """服务启动命令（须在 index-tts checkout 的 uv 环境内运行，cwd=index_tts_root）。"""
    argv = [
        "uv", "run", "--frozen",
        "--with", "fastapi", "--with", "uvicorn", "--with", "soundfile",
        "--with", "numpy", "--with", "lameenc",
        "python", str(SERVER_SCRIPT),
        "--model-dir", "checkpoints", "--indextts-version", "2.5",
        "--host", "127.0.0.1", "--port", str(port),
    ]  # fmt: skip
    if use_qwen_emo:
        argv.append("--use-qwen-emo")
    if mps_mem_limit_gib is not None:
        argv += ["--mps-mem-limit-gib", f"{mps_mem_limit_gib:g}"]
    return argv


def parse_lsof_clients(lsof_f_output: str, port: int) -> set[int]:
    """从 `lsof -F pn` 输出里取出「连向 port 的客户端」PID。

    ESTABLISHED 会同时列出连接两端：服务端一侧 `本地:port->远端:临时口`，客户端一侧
    `本地:临时口->远端:port`。只有后者（**远端端口 == port**）才是客户端——直接数行会把
    服务端自己算进去，导致永远判忙。
    """
    clients: set[int] = set()
    pid: int | None = None
    for line in lsof_f_output.splitlines():
        if not line:
            continue
        tag, val = line[0], line[1:]
        if tag == "p":
            try:
                pid = int(val)
            except ValueError:
                pid = None
        elif tag == "n" and pid is not None and "->" in val:
            remote = val.split("->", 1)[1]
            if remote.rsplit(":", 1)[-1] == str(port):
                clients.add(pid)
    return clients


def decide(
    *,
    listener_pids: list[int],
    expected_pid: int | None,
    cmdline_ok: bool,
    busy: bool,
    idle_streak: int,
    idle_needed: int,
    elapsed: float,
    max_wait: float,
) -> str:
    """watchdog 单个 tick 的决策：gone | foreign | stop | wait | timeout。

    foreign 优先于一切——端口已不是我们起的那个服务（PID 变了或 cmdline 不符，
    典型是 PID 复用 / 他人重启）：放弃，**绝不动手**。误放过只是多占内存，误杀是事故。
    """
    if not listener_pids:
        return "gone"
    if not cmdline_ok or (
        expected_pid is not None and expected_pid not in listener_pids
    ):
        return "foreign"
    if not busy:
        return "stop" if idle_streak >= idle_needed else "wait"
    return "timeout" if elapsed >= max_wait else "wait"


# ---------------- 进程 / 网络探测 ----------------


def process_cmdline(pid: int) -> str:
    try:
        r = subprocess.run(
            ["/bin/ps", "-p", str(pid), "-o", "command="],
            capture_output=True, text=True, timeout=5,
        )  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return r.stdout.strip()


def port_listeners(port: int) -> list[int]:
    """按端口找 LISTEN 进程 pid（只查这一个端口——判据面=作用面）。"""
    try:
        r = subprocess.run(
            ["lsof", "-tnP", f"-iTCP:{port}", "-sTCP:LISTEN"],
            capture_output=True, text=True, timeout=10,
        )  # fmt: skip
    except FileNotFoundError:
        raise ServerError(
            "lsof 不可用——服务起停依赖按端口定位进程（macOS 自带）"
        ) from None
    return [int(x) for x in r.stdout.split()]


def established_clients(port: int, exclude: set[int]) -> set[int]:
    try:
        r = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:ESTABLISHED", "-F", "pn"],
            capture_output=True, text=True, timeout=10,
        )  # fmt: skip
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return set()
    return parse_lsof_clients(r.stdout, port) - exclude


def health(server: str, timeout: float = 5.0) -> dict | None:
    """GET /health → dict（HTTP 200 且为 JSON）；任何失败 → None。"""
    url = server.rstrip("/") + "/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            if getattr(resp, "status", 200) != 200:
                return None
            data = json.loads(resp.read())
            return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001 - 连接拒绝/超时/坏 JSON/非 HTTP 回包一律按不健康
        return None


def server_healthy(server: str, timeout: float = 5.0) -> bool:
    h = health(server, timeout)
    return bool(h and h.get("ok"))


def wait_healthy(server: str, timeout_sec: float, poll_sec: float = POLL_SEC) -> bool:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if server_healthy(server):
            return True
        time.sleep(poll_sec)
    return False


def is_busy(server: str, port: int, samples: int = 1, interval: float = 0.5) -> bool:
    """服务是否被占用：任一采样点出现 synthesizing 或他人客户端连接即为忙。"""
    me = {os.getpid()}
    for i in range(samples):
        h = health(server, timeout=3.0)
        if h and h.get("synthesizing"):
            return True
        if established_clients(port, me | set(port_listeners(port))):
            return True
        if i < samples - 1:
            time.sleep(interval)
    return False


# ---------------- 起 / 停 ----------------


def _state_path(port: int) -> Path:
    return data_dir() / f"server-{port}.json"


def _tail(path: Path, n: int = 15) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "(无日志)"
    return "\n".join(lines[-n:])


def _reclaim(proc: subprocess.Popen, grace_sec: float = KILL_GRACE_SEC) -> None:
    """回收**自己刚拉起、未能就绪**的子进程：SIGTERM → 逾期 SIGKILL，按进程组发。

    `start_new_session=True` 使 pgid == pid，一并带走 uv 与其子 python。只动持有句柄且未
    reap 的子进程（PID 不会被复用）；已退出则不发任何信号。不按名字、不按端口找人。
    """
    if proc.poll() is not None:
        return
    for sig, wait_sec in ((signal.SIGTERM, grace_sec), (signal.SIGKILL, 5.0)):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=wait_sec)
            return
        except subprocess.TimeoutExpired:
            continue


def start_server(
    server: str,
    *,
    root_override: str | None = None,
    startup_timeout: float = STARTUP_TIMEOUT_SEC,
    use_qwen_emo: bool = False,
    mps_mem_limit_gib: float | None = None,
) -> dict:
    """确保服务可用。返回 {started, reused, port, pid?}：started=True 才表示是**我们**拉起的。

    - 已健康 → 复用（started=False，之后不归我们停）；
    - 端口上是别的进程 → 拒绝；是 tts 服务但还在加载 → 等它；
    - 非本机地址 → 不代为启动（ref_path 也是服务端本地路径，远端无意义）。
    """
    port = port_from_server(server)
    if server_healthy(server):
        return {"started": False, "reused": True, "port": port}
    if not is_local_server(server):
        raise ServerError(f"{server} 不可达，且不是本机地址——dub 只代为启动本机服务")
    pids = port_listeners(port)
    if pids:
        foreign = [p for p in pids if SERVER_MARK not in process_cmdline(p)]
        if foreign:
            raise ServerError(
                f"端口 {port} 被非 tts 服务的进程占用（pid {foreign}），拒绝启动或终止"
            )
        if wait_healthy(server, startup_timeout):  # 我方服务还在加载模型
            return {"started": False, "reused": True, "loading": True, "port": port}
        raise ServerError(
            f"端口 {port} 上的 tts 服务 {startup_timeout:.0f}s 内未就绪，见 {logs_dir()}"
        )

    root = index_tts_root(root_override)
    if not (root / "checkpoints").is_dir():
        raise ServerError(
            f"index-tts 环境不可用：{root}/checkpoints 不存在"
            "（设 DUB_INDEX_TTS_ROOT 指向 checkout，部署见 references/ENGINES.md）"
        )
    if shutil.which("uv") is None:
        raise ServerError(
            "uv 不在 PATH——服务须经 `uv run --frozen` 在 index-tts 环境内启动"
        )

    log_path = logs_dir() / f"server-{port}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    argv = server_argv(port, use_qwen_emo, mps_mem_limit_gib)
    with log_path.open("ab") as log:
        proc = subprocess.Popen(
            argv, cwd=str(root), stdout=log, stderr=subprocess.STDOUT, start_new_session=True
        )  # fmt: skip
    _state_path(port).parent.mkdir(parents=True, exist_ok=True)
    _state_path(port).write_text(
        json.dumps(
            {
                "pid": proc.pid,
                "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "by": "dub",
            }
        ),
        encoding="utf-8",
    )
    ready = False
    try:
        ready = wait_healthy(server, startup_timeout)
    finally:  # 超时或 Ctrl-C：别把半加载的 ~10 GiB 服务留成无人认领的孤儿
        if not ready:
            _reclaim(proc)
            _state_path(port).unlink(missing_ok=True)
    if not ready:
        raise ServerError(
            f"服务 {startup_timeout:.0f}s 内未就绪，本次拉起的进程已清理。"
            f"日志尾部（{log_path}）：\n{_tail(log_path)}"
        )
    return {
        "started": True,
        "reused": False,
        "port": port,
        "pid": proc.pid,
        "log": str(log_path),
    }


def stop_server(port: int, grace_sec: float = KILL_GRACE_SEC) -> None:
    """按端口停服：SIGTERM → 等端口释放 → 逾期 SIGKILL。调用前须已确认监听者是 tts 服务。"""

    def _signal(sig: int) -> None:
        for pid in port_listeners(port):
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass

    def _wait_free(sec: float) -> None:
        deadline = time.monotonic() + sec
        while time.monotonic() < deadline and port_listeners(port):
            time.sleep(0.2)

    if not port_listeners(port):
        return
    _signal(signal.SIGTERM)
    _wait_free(grace_sec)
    _signal(signal.SIGKILL)
    _wait_free(grace_sec / 2)
    _state_path(port).unlink(missing_ok=True)


def stop_or_watch(
    server: str,
    *,
    max_wait: float = WATCHDOG_MAX_WAIT_SEC,
    poll: float = WATCHDOG_POLL_SEC,
    idle_grace: float = WATCHDOG_IDLE_GRACE_SEC,
) -> dict:
    """空闲 → 立即停；被占用 → 起有界 watchdog 后立即返回（不阻塞调用方）。

    返回 {action: not-running|foreign|stopped|watchdog, ...}。
    """
    port = port_from_server(server)
    pids = port_listeners(port)
    if not pids:
        return {"action": "not-running", "port": port}
    if any(SERVER_MARK not in process_cmdline(p) for p in pids):
        return {"action": "foreign", "port": port, "pids": pids}
    if not is_busy(server, port, samples=3, interval=0.5):
        stop_server(port)
        return {"action": "stopped", "port": port}
    log_path = logs_dir() / f"watchdog-{port}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    py = (
        getattr(sys, "_base_executable", None) or sys.executable
    )  # 基础解释器：纯 stdlib，不依赖 uv 临时环境
    with log_path.open("ab") as log:
        proc = subprocess.Popen(
            [py, str(Path(__file__).resolve()), "watchdog", "--server", server,
             "--expected-pid", str(pids[0]), "--max-wait", str(max_wait),
             "--poll", str(poll), "--idle-grace", str(idle_grace)],
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )  # fmt: skip
    return {
        "action": "watchdog",
        "port": port,
        "watchdogPid": proc.pid,
        "log": str(log_path),
    }


# ---------------- watchdog ----------------


def _log(msg: str) -> None:
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {msg}", flush=True)


def watchdog_main(
    server: str,
    expected_pid: int | None,
    max_wait: float,
    poll: float,
    idle_grace: float,
) -> int:
    """巡检至服务空闲后停掉；退出码 0=已停/已自行退出，3=端口非我们的服务（放弃），4=超时放弃。"""
    port = port_from_server(server)
    needed = max(1, math.ceil(idle_grace / poll))
    start = time.monotonic()
    idle_streak = 0
    _log(f"watchdog 启动 port={port} expected_pid={expected_pid} max_wait={max_wait:g}s "
         f"poll={poll:g}s idle_grace={idle_grace:g}s（需连续空闲 {needed} 次）")  # fmt: skip
    while True:
        pids = port_listeners(port)
        cmdline_ok = bool(pids) and all(SERVER_MARK in process_cmdline(p) for p in pids)
        busy = is_busy(server, port) if pids and cmdline_ok else False
        idle_streak = 0 if busy else idle_streak + 1
        act = decide(
            listener_pids=pids, expected_pid=expected_pid, cmdline_ok=cmdline_ok, busy=busy,
            idle_streak=idle_streak, idle_needed=needed,
            elapsed=time.monotonic() - start, max_wait=max_wait,
        )  # fmt: skip
        if act == "gone":
            _log("服务已自行退出，watchdog 结束")
            return 0
        if act == "foreign":
            _log(f"端口 {port} 的监听者已不是我们起的服务（pids={pids}），放弃——不动手")
            return 3
        if act == "timeout":
            _log(
                f"等待超过 {max_wait:g}s 服务仍被占用，放弃——服务保留运行，请手动 `dub.py server stop`"
            )
            return 4
        if act == "stop":
            _log(f"已连续空闲 {idle_streak} 次，停止服务（SIGTERM→SIGKILL）")
            stop_server(port)
            return 0
        _log(f"wait busy={busy} idle_streak={idle_streak}/{needed}")
        time.sleep(poll)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="dub 服务控制（内部入口：watchdog 分离子进程用）"
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("watchdog", help="巡检至空闲后停服")
    w.add_argument("--server", required=True)
    w.add_argument("--expected-pid", type=int, default=None)
    w.add_argument("--max-wait", type=float, default=WATCHDOG_MAX_WAIT_SEC)
    w.add_argument("--poll", type=float, default=WATCHDOG_POLL_SEC)
    w.add_argument("--idle-grace", type=float, default=WATCHDOG_IDLE_GRACE_SEC)
    a = ap.parse_args(argv)
    if a.cmd == "watchdog":
        return watchdog_main(a.server, a.expected_pid, a.max_wait, a.poll, a.idle_grace)
    return 2


if __name__ == "__main__":
    sys.exit(main())
