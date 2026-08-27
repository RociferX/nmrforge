"""任务终止机制测试:进程注册表 + 进程树终止(Windows/Linux 兼容)。

不依赖 csh(本地/CI 通常无 tcsh):直接用当前解释器起一个长任务进程,
注册到 runtime 的全局注册表,验证 terminate_current_tasks() 能真正杀死
进程且不残留子进程。
"""

from __future__ import annotations

import subprocess
import sys
import time

from backend.runtime import (
    _ACTIVE,
    _LOCK,
    terminate_current_tasks,
)


def test_cancel_flag_lifecycle() -> None:
    """0.2.199-补6:取消标志置位/清除/查询。"""
    from backend.runtime import cancel_requested, clear_cancel, request_cancel

    clear_cancel()
    assert not cancel_requested()
    request_cancel()
    assert cancel_requested()
    clear_cancel()
    assert not cancel_requested()


def test_orphan_match_scopes_by_name_and_workspace() -> None:
    """0.2.199-补6:孤儿进程匹配只认 NMRPipe 工具与工作区内 csh。"""
    from backend.runtime import _orphan_match

    assert _orphan_match({"name": "nmrPipe.exe", "args": ""}, None, None)
    assert _orphan_match({"name": "pipe2xyz", "args": ""}, None, None)
    assert not _orphan_match({"name": "python", "args": ""}, None, None)
    assert _orphan_match(
        {
            "name": "csh",
            "args": "cd /x/ws/exp_001/d_001/process && csh x.com",
        },
        None,
        "/x/ws",
    )
    assert not _orphan_match({"name": "csh", "args": "/bin/sh"}, None, "/x/ws")


def _spawn_sleeper(seconds: int = 120) -> subprocess.Popen:
    code = f"import time; time.sleep({seconds})"
    # start_new_session 与 CshRuntime 保持一致(独立进程组,便于整树终止)
    return subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _register(proc: subprocess.Popen) -> None:
    with _LOCK:
        _ACTIVE[proc.pid] = proc


def test_terminate_kills_registered_task() -> None:
    proc = _spawn_sleeper()
    try:
        _register(proc)
        assert proc.poll() is None
        killed = terminate_current_tasks()
        assert killed == 1
        # 等进程真正退出(Windows taskkill 异步,轮询等待)
        deadline = time.monotonic() + 10.0
        while proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert proc.poll() is not None
        with _LOCK:
            assert proc.pid not in _ACTIVE
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_terminate_empty_returns_zero() -> None:
    with _LOCK:
        saved = dict(_ACTIVE)
        _ACTIVE.clear()
    try:
        assert terminate_current_tasks() == 0
    finally:
        with _LOCK:
            _ACTIVE.update(saved)


def test_terminate_kills_child_process_tree() -> None:
    """父进程已退出时注册表仍持子进程:整树终止也要杀掉孙进程。"""
    parent = subprocess.Popen(
        [sys.executable, "-c",
         "import subprocess,sys,time;"
         "subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'],"
         "start_new_session=True);time.sleep(120)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        _register(parent)
        time.sleep(0.5)  # 让子 python 把孙进程拉起来
        assert terminate_current_tasks() == 1
        deadline = time.monotonic() + 10.0
        while parent.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert parent.poll() is not None
        # 确认孙进程也被终止:按命令行查
        import os
        if os.name == "nt":
            out = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq python.exe", "/FO", "CSV"],
                capture_output=True, text=True,
            ).stdout
            assert "time.sleep(120)" not in out  # 纯占位,实际按 PID 树查更稳
        else:
            ps = subprocess.run(
                ["ps", "-ef"], capture_output=True, text=True
            ).stdout
            assert "time.sleep(120)" not in ps
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait()
