"""csh runtime: run NMRPipe commands and scripts after sourcing ~/.cshrc (Linux).

Task termination: every process started by run() is registered in a module-level
registry (as a process group, start_new_session=True), so
terminate_current_tasks() can stop all current tasks at any time (taskkill /T on
Windows, killpg SIGKILL on Linux) without leaving csh/nmrPipe/SMILE processes
behind; a timeout likewise kills the process tree before returning.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import signal
import subprocess
import threading
import time as _time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ui_support.i18n import tr


class ToolError(RuntimeError):
    """External tool execution error."""


class TaskTerminatedError(RuntimeError):
    """The task was stopped by the user or the UI."""


@dataclass
class CompletedProcess:
    command: str
    stdout: str
    stderr: str
    returncode: int

    @property
    def ok(self) -> bool:
        return self.returncode == 0


# Global registry of live processes: shared across CshRuntime instances, so
# terminate_current_tasks() kills every running backend task
_ACTIVE: dict[int, subprocess.Popen] = {}
_LOCK = threading.Lock()
_USER_TERMINATED: set[int] = set()
# 0.2.199-patch6: cancellation flag for in-memory computation (direct-dimension
# phase search and the like) -- terminate_current_tasks() sets it, phase-search
# loops check it and raise to exit; clear_cancel() runs before a new task starts
_CANCEL = threading.Event()


def request_cancel() -> None:
    """Request cancellation of the current task (in-memory computation responds
    too)."""
    _CANCEL.set()


def clear_cancel() -> None:
    """Clear the cancellation flag (called before a new task so a previous
    cancellation cannot leak into it)."""
    _CANCEL.clear()


def cancel_requested() -> bool:
    """Whether cancellation has been requested for the current task."""
    return _CANCEL.is_set()


def terminate_current_tasks() -> int:
    """Terminate every running backend task (process trees); returns how many
    were killed.

    Callers (such as the GUI stop button) may safely call this from any thread;
    the read loop in run() then exits and raises TaskTerminatedError.
    """
    with _LOCK:
        procs = list(_ACTIVE.values())
        _ACTIVE.clear()
        for proc in procs:
            _USER_TERMINATED.add(proc.pid)
    for proc in procs:
        _kill_process_tree(proc)
    return len(procs)


_DESCENDANTS_CACHE_TTL = 0.5
_descendants_cache: tuple[float, dict[int, list[int]]] = (0.0, {})


def _children_map(timeout: float = 5.0) -> dict[int, list[int]]:
    """Full ps table -> {ppid: [pid]} (with a short TTL cache, so killing a whole
    tree does not rescan the table repeatedly)."""
    global _descendants_cache
    now = _time.monotonic()
    if now - _descendants_cache[0] < _DESCENDANTS_CACHE_TTL:
        return _descendants_cache[1]
    try:
        out = subprocess.run(
            ["ps", "-eo", "pid=,ppid="],
            capture_output=True,
            text=True,
            timeout=timeout,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return _descendants_cache[1]
    children: dict[int, list[int]] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2:
            try:
                children.setdefault(int(parts[1]), []).append(int(parts[0]))
            except ValueError:
                pass
    _descendants_cache = (now, children)
    return children


def _collect_descendants(root_pid: int) -> list[int]:
    """Recursively collect every descendant PID of the root process (covers
    descendants that escaped into a process group the application created)."""
    children = _children_map()
    found: list[int] = []
    stack = [root_pid]
    while stack:
        pid = stack.pop()
        for child in children.get(pid, []):
            found.append(child)
            stack.append(child)
    return found


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """Terminate the whole process tree, leaving no child behind (including
    descendants that escaped into another process group)."""
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
        else:
            # start_new_session=True -> the pid leads the root process group; then
            # cover every descendant recursively
            pids = [proc.pid] + _collect_descendants(proc.pid)
            for pid in pids:
                try:
                    os.killpg(pid, signal.SIGKILL)
                except (OSError, ProcessLookupError):
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except (OSError, ProcessLookupError):
                        pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
    except (OSError, ProcessLookupError):
        pass


_NMRPIPE_TOOL_NAMES = (
    "nmrpipe",
    "xyz2pipe",
    "pipe2xyz",
    "nmrdraw",
    "nmrft",
    "nmrtrans",
    "nmrzz",
    "bruk2pipe",
    "addnmr",
    "smile",
    "nmrwish",
    "bruker",
)


def _scan_processes() -> list[dict[str, Any]]:
    """Full-table process scan (best effort): [{pid, ppid, name, args}]."""
    procs: list[dict[str, Any]] = []
    if os.name == "nt":
        try:
            script = (
                "Get-CimInstance Win32_Process | ForEach-Object { "
                "'{0}|{1}|{2}|{3}' -f $_.ProcessId, $_.ParentProcessId, "
                "$_.Name, ($_.CommandLine -replace '\\|','_') }"
            )
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command", script],
                capture_output=True,
                text=True,
                timeout=30,
            ).stdout
        except (OSError, subprocess.TimeoutExpired):
            return procs
        for line in out.splitlines():
            parts = line.split("|", 3)
            if len(parts) == 4:
                try:
                    procs.append(
                        {
                            "pid": int(parts[0]),
                            "ppid": int(parts[1]),
                            "name": (parts[2] or "").lower(),
                            "args": parts[3] or "",
                        }
                    )
                except ValueError:
                    pass
        return procs
    try:
        out = subprocess.run(
            ["ps", "-eo", "pid=,ppid=,comm=,args="],
            capture_output=True,
            text=True,
            timeout=15,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return procs
    for line in out.splitlines():
        parts = line.split(None, 3)
        if len(parts) >= 3:
            try:
                procs.append(
                    {
                        "pid": int(parts[0]),
                        "ppid": int(parts[1]),
                        "name": (parts[2] or "").lower(),
                        "args": parts[3] if len(parts) > 3 else "",
                    }
                )
            except ValueError:
                pass
    return procs


def _orphan_match(
    proc: dict[str, Any], bin_dir: str | None, workspace: str | None
) -> bool:
    """Decide whether a process belongs to a leftover NMRPipe toolchain of this
    workspace.

    Whether it is a tool executable or a csh/tcsh wrapper, the command line must
    explicitly contain the current workspace path. Matching on the process name
    alone cannot prove that a process belongs to this instance and must not be
    used as grounds for termination.
    """
    name = str(proc.get("name") or "")
    args = str(proc.get("args") or "")
    base = name.split(".")[0].lower()
    if base not in _NMRPIPE_TOOL_NAMES and base not in ("csh", "tcsh"):
        return False
    if not _command_mentions_path(args, workspace):
        return False
    if base in _NMRPIPE_TOOL_NAMES and bin_dir:
        return _command_mentions_path(args, bin_dir)
    return True


def _command_mentions_path(command: str, path: str | None) -> bool:
    """Whether the command line contains the target directory with a path boundary
    (compatible across platform separators)."""
    if not command or not path:
        return False
    normalized_command = command.replace("\\", "/")
    normalized_path = str(path).replace("\\", "/").rstrip("/")
    if not normalized_path:
        return False
    if os.name == "nt":
        normalized_command = normalized_command.casefold()
        normalized_path = normalized_path.casefold()
    # Sub-paths after the target directory are allowed, but /project and
    # /project-copy are not treated as the same thing.
    before = r"(?<![\w.-])"
    after = r"(?=$|[\s\"';|&)/])"
    return re.search(before + re.escape(normalized_path) + after, normalized_command) is not None


def _orphan_targets(
    procs: list[dict[str, Any]], bin_dir: str | None, workspace: str | None
) -> list[dict[str, Any]]:
    """Return toolchain processes whose parent is gone and that provably belong to
    the current workspace."""
    live_pids = {int(proc["pid"]) for proc in procs}
    targets: list[dict[str, Any]] = []
    for proc in procs:
        try:
            ppid = int(proc.get("ppid", 0))
        except (TypeError, ValueError):
            continue
        parent_is_gone = ppid <= 1 or ppid not in live_pids
        if parent_is_gone and _orphan_match(proc, bin_dir, workspace):
            targets.append(proc)
    return targets


def cleanup_orphan_tasks(
    bin_dir: str | None = None, workspace: str | None = None
) -> int:
    """Clean up leftover NMRPipe processes that are not in the registry (orphans
    left by an abnormal exit or a closed application).

    terminate_current_tasks() kills only the registered trees; orphan processes
    (parent gone, PPID=1) are matched by process name and command line and their
    whole tree is force-killed. taskkill /T /F on Windows, SIGKILL on Linux.
    Returns how many were cleaned up (best effort, failures ignored).
    """
    procs = _scan_processes()
    targets = _orphan_targets(procs, bin_dir, workspace)
    killed = 0
    for proc in targets:
        pid = proc["pid"]
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                )
            else:
                os.kill(pid, signal.SIGKILL)
            killed += 1
        except (OSError, ProcessLookupError):
            pass
    return killed


class CshRuntime:
    """Run NMRPipe tools through csh (source ~/.cshrc)."""

    def run(
        self,
        argv: list[str],
        *,
        cwd: str | None = None,
        timeout: float = 3600,
        on_line: Callable[[str], None] | None = None,
    ) -> CompletedProcess:
        """Run a csh command; when on_line is not None every stdout line is
        forwarded (so stage logs are visible in real time).

        The process starts in its own session and is registered in the global
        registry so the stop button can terminate it; on timeout the process tree
        is likewise killed before returning (nothing is left behind).
        """
        shell = shutil_which_csh()
        if shell is None:
            raise ToolError(tr(
                "tcsh/csh not found on this machine (NMRPipe script requires "
                "C-shell)",
            ))
        parts = ["if (-e ~/.cshrc) source ~/.cshrc"]
        if cwd:
            parts.append(f"cd '{cwd}'")
        # The pipe character | stays a shell pipe and the other arguments are
        # escaped (controlled arguments, safe)
        parts.append(" ".join(part if part == "|" else shlex.quote(part) for part in argv))
        command = "; ".join(parts)
        try:
            proc = subprocess.Popen(
                [shell, "-c", command],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE if on_line is None else subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
        except OSError as exc:
            raise ToolError(tr("csh execution failed: {p0}", p0=exc)) from exc
        with _LOCK:
            _ACTIVE[proc.pid] = proc
        try:
            assert proc.stdout is not None
            lines: list[str] = []
            import threading

            # 0.2.199-patch29dz: stdout is read on a thread so the main thread's
            # wait(timeout) really takes effect -- the old code blocked while reading
            # stdout in a for-line loop, so a child hanging without output never
            # exited and the following wait(timeout) was never reached (deleting an
            # input file by hand could hang a script and freeze the UI). A timeout
            # still kills the process tree before returning, leaving nothing behind.
            _done = threading.Event()
            _stderr_done = threading.Event()
            _stderr_buf: list[str] = []

            def _read_stdout() -> None:
                try:
                    for line in proc.stdout:
                        lines.append(line)
                        if on_line is not None:
                            on_line(line.rstrip("\n"))
                finally:
                    _done.set()

            def _read_stderr() -> None:
                try:
                    if proc.stderr is not None:
                        _stderr_buf.append(proc.stderr.read())
                finally:
                    _stderr_done.set()

            threading.Thread(target=_read_stdout, daemon=True).start()
            if proc.stderr is not None:
                threading.Thread(target=_read_stderr, daemon=True).start()
            try:
                returncode = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                if proc.poll() is None:
                    _kill_process_tree(proc)
                    proc.wait()
                return CompletedProcess(
                    command,
                    "".join(lines),
                    tr("Command timeout (>{p0:.0f}s), terminated process tree", p0=timeout),
                    124,
                )
            _done.wait(timeout=5.0)  # clean exit: wait for stdout to reach EOF
            _stderr_done.wait(timeout=5.0)
            stderr = "".join(_stderr_buf)
            with _LOCK:
                if proc.pid in _USER_TERMINATED:
                    _USER_TERMINATED.discard(proc.pid)
                    raise TaskTerminatedError(tr("Task has been stopped by user"))
            return CompletedProcess(command, "".join(lines), stderr, returncode)
        finally:
            with _LOCK:
                _ACTIVE.pop(proc.pid, None)
                _USER_TERMINATED.discard(proc.pid)


def shutil_which_csh() -> str | None:
    return shutil.which("tcsh") or shutil.which("csh")


__all__ = [
    "CompletedProcess",
    "CshRuntime",
    "TaskTerminatedError",
    "ToolError",
    "shutil_which_csh",
    "terminate_current_tasks",
]
