"""watchdog 的安全逻辑：决策表与 lsof 解析。

误杀别人的服务是事故、多占一会儿内存只是浪费——决策表的方向性全部偏向「不动手」。
"""

from __future__ import annotations

import pytest
from dub_server_ctl import decide, parse_lsof_clients

PORT = 8766


def _d(**kw):
    base = dict(
        listener_pids=[100],
        expected_pid=100,
        cmdline_ok=True,
        busy=False,
        idle_streak=0,
        idle_needed=3,
        elapsed=0.0,
        max_wait=7200.0,
    )
    return decide(**{**base, **kw})


# ---------------- decide ----------------


def test_gone_when_no_listener():
    assert _d(listener_pids=[]) == "gone"


def test_wait_when_busy():
    assert _d(busy=True, idle_streak=0) == "wait"


def test_idle_needs_consecutive_streak_before_stopping():
    assert _d(idle_streak=1) == "wait"
    assert _d(idle_streak=2) == "wait"
    assert _d(idle_streak=3) == "stop"
    assert _d(idle_streak=9) == "stop"


def test_timeout_only_when_still_busy():
    assert _d(busy=True, elapsed=7200.0) == "timeout"
    assert _d(busy=True, elapsed=7199.0) == "wait"
    # 到点那一刻已空闲且满足连续条件：停掉优于放弃
    assert _d(busy=False, idle_streak=3, elapsed=9999.0) == "stop"


def test_foreign_when_cmdline_not_ours():
    """PID 复用 / 他人接管端口：绝不动手。"""
    assert _d(cmdline_ok=False) == "foreign"


def test_foreign_when_expected_pid_changed():
    """端口还在服务，但已不是我们当初盯着的那个进程（被别人重启）。"""
    assert _d(listener_pids=[200], expected_pid=100) == "foreign"


def test_unknown_expected_pid_does_not_force_foreign():
    assert _d(expected_pid=None, idle_streak=3) == "stop"


@pytest.mark.parametrize("busy", [True, False])
@pytest.mark.parametrize("streak", [0, 5])
@pytest.mark.parametrize("elapsed", [0.0, 99999.0])
def test_foreign_beats_everything_else(busy, streak, elapsed):
    assert (
        _d(cmdline_ok=False, busy=busy, idle_streak=streak, elapsed=elapsed)
        == "foreign"
    )


def test_decide_never_stops_while_busy():
    for streak in range(0, 10):
        for elapsed in (0.0, 10.0, 99999.0):
            assert _d(busy=True, idle_streak=streak, elapsed=elapsed) != "stop"


# ---------------- parse_lsof_clients ----------------

# `lsof -F pn` 实测形态（本机探测所得）：连接两端各出一行，服务端一侧
# `本地:PORT->远端:临时口`，客户端一侧 `本地:临时口->远端:PORT`。
_LSOF = """p94719
n127.0.0.1:18766->127.0.0.1:55598
p94721
n127.0.0.1:55598->127.0.0.1:18766
"""


def test_parse_lsof_counts_client_not_server():
    assert parse_lsof_clients(_LSOF, 18766) == {94721}


def test_parse_lsof_server_alone_is_not_a_client():
    only_server = "p94719\nn127.0.0.1:18766->127.0.0.1:55598\n"
    assert parse_lsof_clients(only_server, 18766) == set()


def test_parse_lsof_multiple_clients():
    out = _LSOF + "p555\nn127.0.0.1:60001->127.0.0.1:18766\n"
    assert parse_lsof_clients(out, 18766) == {94721, 555}


def test_parse_lsof_ignores_other_ports_and_junk():
    out = "p1\nn127.0.0.1:50000->127.0.0.1:9999\n\nzzz\np2\nn*:18766\n"
    assert parse_lsof_clients(out, 18766) == set()


def test_parse_lsof_empty():
    assert parse_lsof_clients("", PORT) == set()


def test_parse_lsof_ipv6():
    out = "p77\nn[::1]:50001->[::1]:18766\n"
    assert parse_lsof_clients(out, 18766) == {77}
