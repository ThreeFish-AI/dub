"""服务控制的纯函数与「绝不 pkill」的 argv 面约束。"""

from __future__ import annotations

import subprocess
import sys
import urllib.error

import dub_server_ctl as ctl
import pytest


def test_port_from_server():
    assert ctl.port_from_server("http://127.0.0.1:8766") == 8766
    assert ctl.port_from_server("http://localhost") == 80
    assert ctl.port_from_server("https://host") == 443


def test_port_from_server_rejects_unparseable():
    with pytest.raises(ctl.ServerError):
        ctl.port_from_server("not-a-url")


@pytest.mark.parametrize(
    ("server", "local"),
    [
        ("http://127.0.0.1:8766", True),
        ("http://localhost:8766", True),
        ("http://[::1]:8766", True),
        ("http://192.168.1.5:8766", False),
        ("http://example.com:8766", False),
    ],
)
def test_is_local_server(server, local):
    assert ctl.is_local_server(server) is local


def test_server_argv_shape():
    argv = ctl.server_argv(8766)
    assert argv[:3] == ["uv", "run", "--frozen"]
    for dep in ("fastapi", "uvicorn", "soundfile", "numpy", "lameenc"):
        assert argv[argv.index("--with", argv.index(dep) - 1) + 1] == dep
    assert argv[argv.index("python") + 1].endswith("dub_tts_server.py")
    assert argv[argv.index("--port") + 1] == "8766"
    assert argv[argv.index("--indextts-version") + 1] == "2.5"
    assert argv[argv.index("--host") + 1] == "127.0.0.1"
    assert "--use-qwen-emo" not in argv


def test_server_argv_optional_flags():
    argv = ctl.server_argv(9000, use_qwen_emo=True, mps_mem_limit_gib=12.5)
    assert "--use-qwen-emo" in argv
    assert argv[argv.index("--mps-mem-limit-gib") + 1] == "12.5"
    assert argv[argv.index("--port") + 1] == "9000"


def test_server_script_exists_and_is_dub_prefixed():
    assert ctl.SERVER_SCRIPT.is_file()
    assert ctl.SERVER_SCRIPT.name == "dub_tts_server.py"
    assert ctl.SERVER_MARK in ctl.SERVER_SCRIPT.name  # cmdline 复核标记必须能匹配本服务


def test_stop_server_signals_by_port_never_pkill(monkeypatch):
    """停服只经 lsof 按端口定位再 kill——所有 subprocess argv 里不得出现 pkill。"""
    argv_seen: list[list[str]] = []
    alive = {"pids": [4242]}

    def fake_run(argv, **kw):
        argv_seen.append(list(argv))
        out = " ".join(map(str, alive["pids"]))
        return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")

    killed: list[tuple[int, int]] = []

    def fake_kill(pid, sig):
        killed.append((pid, sig))
        alive["pids"] = []  # SIGTERM 后端口即释放

    monkeypatch.setattr(ctl.subprocess, "run", fake_run)
    monkeypatch.setattr(ctl.os, "kill", fake_kill)
    ctl.stop_server(8766, grace_sec=1.0)
    assert killed and killed[0][0] == 4242
    assert any("lsof" in a[0] for a in argv_seen)
    assert not any("pkill" in " ".join(a) for a in argv_seen)


def test_stop_server_without_listeners_is_noop(monkeypatch):
    monkeypatch.setattr(
        ctl.subprocess,
        "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 0, stdout="", stderr=""),
    )
    called = []
    monkeypatch.setattr(ctl.os, "kill", lambda *a: called.append(a))
    ctl.stop_server(8766)
    assert called == []


# ---------------- health ----------------


def test_health_returns_none_on_connection_refused(monkeypatch):
    def boom(*a, **k):
        raise urllib.error.URLError("refused")

    monkeypatch.setattr(ctl.urllib.request, "urlopen", boom)
    assert ctl.health("http://127.0.0.1:1") is None
    assert ctl.server_healthy("http://127.0.0.1:1") is False


def test_health_returns_none_on_garbage_body(monkeypatch):
    class Resp:
        status = 200

        def read(self):
            return b"<html>not json"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(ctl.urllib.request, "urlopen", lambda *a, **k: Resp())
    assert ctl.health("http://x") is None


def test_server_healthy_requires_ok_true(monkeypatch):
    monkeypatch.setattr(ctl, "health", lambda s, timeout=5.0: {"ok": False})
    assert ctl.server_healthy("http://x") is False
    monkeypatch.setattr(ctl, "health", lambda s, timeout=5.0: {"ok": True})
    assert ctl.server_healthy("http://x") is True


# ---------------- stop_or_watch 分支 ----------------


def test_stop_or_watch_not_running(monkeypatch):
    monkeypatch.setattr(ctl, "port_listeners", lambda p: [])
    assert ctl.stop_or_watch("http://127.0.0.1:8766")["action"] == "not-running"


def test_stop_or_watch_refuses_foreign_listener(monkeypatch):
    monkeypatch.setattr(ctl, "port_listeners", lambda p: [9])
    monkeypatch.setattr(
        ctl, "process_cmdline", lambda pid: "/usr/bin/some-other-daemon --port 8766"
    )
    killed = []
    monkeypatch.setattr(ctl, "stop_server", lambda p, *a, **k: killed.append(p))
    r = ctl.stop_or_watch("http://127.0.0.1:8766")
    assert r["action"] == "foreign" and killed == []


def test_stop_or_watch_idle_stops_immediately(monkeypatch):
    monkeypatch.setattr(ctl, "port_listeners", lambda p: [9])
    monkeypatch.setattr(
        ctl, "process_cmdline", lambda pid: "python dub_tts_server.py --port 8766"
    )
    monkeypatch.setattr(ctl, "is_busy", lambda *a, **k: False)
    stopped = []
    monkeypatch.setattr(ctl, "stop_server", lambda p, *a, **k: stopped.append(p))
    assert ctl.stop_or_watch("http://127.0.0.1:8766")["action"] == "stopped"
    assert stopped == [8766]


def test_stop_or_watch_busy_spawns_watchdog_and_does_not_kill(monkeypatch):
    monkeypatch.setattr(ctl, "port_listeners", lambda p: [9])
    monkeypatch.setattr(
        ctl, "process_cmdline", lambda pid: "python dub_tts_server.py --port 8766"
    )
    monkeypatch.setattr(ctl, "is_busy", lambda *a, **k: True)
    stopped, spawned = [], []
    monkeypatch.setattr(ctl, "stop_server", lambda p, *a, **k: stopped.append(p))

    class FakePopen:
        pid = 31337

        def __init__(self, argv, **kw):
            spawned.append((argv, kw))

    monkeypatch.setattr(ctl.subprocess, "Popen", FakePopen)
    r = ctl.stop_or_watch("http://127.0.0.1:8766", max_wait=60, poll=1, idle_grace=2)
    assert r["action"] == "watchdog" and r["watchdogPid"] == 31337
    assert stopped == [], "占用中不得杀服务"
    argv, kw = spawned[0]
    assert kw.get("start_new_session") is True, "watchdog 必须脱离调用方会话"
    assert "watchdog" in argv and argv[argv.index("--expected-pid") + 1] == "9"
    assert argv[0] == (getattr(sys, "_base_executable", None) or sys.executable)


# ---------------- start_server 的拒绝路径 ----------------


def test_start_server_reuses_healthy_instance(monkeypatch):
    monkeypatch.setattr(ctl, "server_healthy", lambda s, timeout=5.0: True)
    r = ctl.start_server("http://127.0.0.1:8766")
    assert r["started"] is False and r["reused"] is True


def test_start_server_refuses_remote_address(monkeypatch):
    monkeypatch.setattr(ctl, "server_healthy", lambda s, timeout=5.0: False)
    with pytest.raises(ctl.ServerError, match="本机"):
        ctl.start_server("http://10.0.0.5:8766")


def test_start_server_refuses_foreign_port_occupant(monkeypatch):
    monkeypatch.setattr(ctl, "server_healthy", lambda s, timeout=5.0: False)
    monkeypatch.setattr(ctl, "port_listeners", lambda p: [777])
    monkeypatch.setattr(ctl, "process_cmdline", lambda pid: "nginx: master process")
    with pytest.raises(ctl.ServerError, match="占用"):
        ctl.start_server("http://127.0.0.1:8766")


def test_start_server_missing_checkpoints_is_actionable(monkeypatch, tmp_path):
    monkeypatch.setattr(ctl, "server_healthy", lambda s, timeout=5.0: False)
    monkeypatch.setattr(ctl, "port_listeners", lambda p: [])
    with pytest.raises(ctl.ServerError, match="checkpoints"):
        ctl.start_server("http://127.0.0.1:8766", root_override=str(tmp_path))
