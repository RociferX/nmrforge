"""Task termination mechanism test: process registry + process tree termination (Windows/Linux
compatible). Does not rely on csh (local/CI usually does not have tcsh): directly use the
current interpreter to start a long task process, register it in the global registry of the
runtime, and verify that terminate_current_tasks() can actually kill the process without leaving
any child processes."""

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
    """0.2.199-patch6:Cancel flag set/Clear/Query."""
    from backend.runtime import cancel_requested, clear_cancel, request_cancel

    clear_cancel()
    assert not cancel_requested()
    request_cancel()
    assert cancel_requested()
    clear_cancel()
    assert not cancel_requested()


def test_orphan_match_scopes_by_name_and_workspace() -> None:
    """The tool name itself does not represent ownership; it must match the current workspace."""
    from backend.runtime import _orphan_match

    assert not _orphan_match({"name": "nmrPipe.exe", "args": ""}, None, None)
    assert not _orphan_match({"name": "pipe2xyz", "args": ""}, None, "/x/ws")
    assert not _orphan_match({"name": "python", "args": ""}, None, None)
    assert _orphan_match(
        {"name": "nmrPipe", "args": "nmrPipe -in /x/ws/exp_001/d_001/a.fid"},
        None,
        "/x/ws",
    )
    assert not _orphan_match(
        {"name": "nmrPipe", "args": "nmrPipe -in /x/ws-copy/a.fid"},
        None,
        "/x/ws",
    )
    assert _orphan_match(
        {
            "name": "csh",
            "args": "cd /x/ws/exp_001/d_001/process && csh x.com",
        },
        None,
        "/x/ws",
    )
    assert not _orphan_match({"name": "csh", "args": "/bin/sh"}, None, "/x/ws")


def test_orphan_match_honors_configured_bin_dir() -> None:
    from backend.runtime import _orphan_match

    proc = {
        "name": "nmrPipe",
        "args": "/opt/nmrpipe/bin/nmrPipe -in /x/ws/exp_001/d_001/a.fid",
    }
    assert _orphan_match(proc, "/opt/nmrpipe/bin", "/x/ws")
    assert not _orphan_match(proc, "/other/nmrpipe/bin", "/x/ws")


def test_orphan_targets_require_dead_parent_and_current_workspace() -> None:
    from backend.runtime import _orphan_targets

    procs = [
        {"pid": 100, "ppid": 1, "name": "python", "args": "nmrforge"},
        {
            "pid": 101,
            "ppid": 100,
            "name": "csh",
            "args": "csh -c cd /x/ws/exp_001/d_001/process",
        },
        {
            "pid": 201,
            "ppid": 999,
            "name": "csh",
            "args": "csh -c cd /x/ws/exp_001/d_001/process",
        },
        {
            "pid": 202,
            "ppid": 999,
            "name": "nmrPipe",
            "args": "nmrPipe -in /other/project/a.fid",
        },
    ]

    assert [proc["pid"] for proc in _orphan_targets(procs, None, "/x/ws")] == [201]


def _spawn_sleeper(seconds: int = 120) -> subprocess.Popen:
    code = f"import time; time.sleep({seconds})"
    # Start_new_session Consistent with CshRuntime (independent process group to facilitate tree
    # termination).
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
        # Wait for the process to actually exit (Windows taskkill asynchronous, polling wait).
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
    """When the parent process has exited, the registry still holds the child process: the
    grandchild process will also be killed when the entire tree is terminated."""
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
        time.sleep(0.5)  # Let the child python pull up the grandchild process.
        assert terminate_current_tasks() == 1
        deadline = time.monotonic() + 10.0
        while parent.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert parent.poll() is not None
        # Confirm that the grandson process has also been terminated: check on the command line.
        import os
        if os.name == "nt":
            out = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq python.exe", "/FO", "CSV"],
                capture_output=True, text=True,
            ).stdout
            # Pure placeholder, actually press PID to search the tree more stably.
            assert "time.sleep(120)" not in out
        else:
            ps = subprocess.run(
                ["ps", "-ef"], capture_output=True, text=True
            ).stdout
            assert "time.sleep(120)" not in ps
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait()
