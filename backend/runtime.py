"""csh 运行时:source ~/.cshrc 后执行 NMRPipe 命令/脚本(Linux)。

任务终止:所有 run() 开启的进程统一注册到模块级注册表(进程组,
start_new_session=True),terminate_current_tasks() 可随时终止全部
当前任务(Windows 用 taskkill /T,Linux 用 killpg SIGKILL),不留下
csh/nmrPipe/SMILE 残留进程;超时同样先杀进程树再返回。
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


class ToolError(RuntimeError):
    """外部工具执行错误。"""


class TaskTerminatedError(RuntimeError):
    """任务被用户/界面主动终止。"""


@dataclass
class CompletedProcess:
    command: str
    stdout: str
    stderr: str
    returncode: int

    @property
    def ok(self) -> bool:
        return self.returncode == 0


# 全局活动进程注册表:跨 CshRuntime 实例共享,
# terminate_current_tasks() 杀掉所有正在运行的后端任务
_ACTIVE: dict[int, subprocess.Popen] = {}
_LOCK = threading.Lock()
_USER_TERMINATED: set[int] = set()
# 0.2.199-补6:内存计算(直接维相位搜索等)取消标志——terminate_current_tasks()
# 置位,相位搜索循环检查后抛异常退出;新任务开始前 clear_cancel()
_CANCEL = threading.Event()


def request_cancel() -> None:
    """请求取消当前任务(内存计算阶段同样响应)。"""
    _CANCEL.set()


def clear_cancel() -> None:
    """清除取消标志(新任务开始前调用,避免上一次取消污染新任务)。"""
    _CANCEL.clear()


def cancel_requested() -> bool:
    """当前任务是否已被请求取消。"""
    return _CANCEL.is_set()


def terminate_current_tasks() -> int:
    """终止所有正在运行的后端任务(进程树),返回被杀数量。

    调用方(如 GUI 停止按钮)可安全地在任意线程调用;
    run() 中的读取循环会随之退出并抛 TaskTerminatedError。
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
    """ps 全表 → {ppid: [pid]}(带短 TTL 缓存,终止整树时避免反复扫表)。"""
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
    """递归收集根进程的全部后代 PID(处理应用自建新进程组的逃逸)。"""
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
    """终止整个进程树,不残留子进程(含逃逸到其它进程组的后代)。"""
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
            # start_new_session=True → pid 即根进程组组长;再递归覆盖所有后代
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
    """全表进程扫描(尽力而为):[{pid, ppid, name, args}]。"""
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
    """判定进程是否属于本工作区遗留的 NMRPipe 工具链。

    无论工具可执行文件还是 csh/tcsh 包装进程,命令行都必须明确包含当前
    工作区路径。只按进程名匹配无法证明进程归本实例所有,不得作为终止依据。
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
    """命令行是否包含带路径边界的目标目录(跨平台分隔符兼容)。"""
    if not command or not path:
        return False
    normalized_command = command.replace("\\", "/")
    normalized_path = str(path).replace("\\", "/").rstrip("/")
    if not normalized_path:
        return False
    if os.name == "nt":
        normalized_command = normalized_command.casefold()
        normalized_path = normalized_path.casefold()
    # 允许目标目录后的子路径,但不把 /project 与 /project-copy 混为一谈。
    before = r"(?<![\w.-])"
    after = r"(?=$|[\s\"';|&)/])"
    return re.search(before + re.escape(normalized_path) + after, normalized_command) is not None


def _orphan_targets(
    procs: list[dict[str, Any]], bin_dir: str | None, workspace: str | None
) -> list[dict[str, Any]]:
    """返回父进程已不存在且可证明属于当前工作区的工具链进程。"""
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
    """清理不在注册表里的遗留 NMRPipe 进程(异常退出/关闭应用留下的孤儿)。

    terminate_current_tasks() 只杀注册树;孤儿进程(父进程已退出、PPID=1)
    按进程名/命令行匹配后整树强制结束。Windows taskkill /T /F,
    Linux SIGKILL。返回清理数量(尽力而为,失败忽略)。
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
    """通过 csh(source ~/.cshrc)执行 NMRPipe 工具。"""

    def run(
        self,
        argv: list[str],
        *,
        cwd: str | None = None,
        timeout: float = 3600,
        on_line: Callable[[str], None] | None = None,
    ) -> CompletedProcess:
        """执行 csh 命令;on_line 非 None 时逐行转发 stdout(阶段日志实时可见)。

        进程以独立会话启动并注册到全局注册表,供停止按钮终止;
        超时同样先杀进程树再返回(不残留)。
        """
        shell = shutil_which_csh()
        if shell is None:
            raise ToolError("本机未找到 tcsh/csh（NMRPipe 脚本需要 C-shell）")
        parts = ["if (-e ~/.cshrc) source ~/.cshrc"]
        if cwd:
            parts.append(f"cd '{cwd}'")
        # 管道符 | 保留为 shell 管道,其余参数转义（受控参数,安全）
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
            raise ToolError(f"csh 执行失败: {exc}") from exc
        with _LOCK:
            _ACTIVE[proc.pid] = proc
        try:
            assert proc.stdout is not None
            lines: list[str] = []
            import threading

            # 0.2.199-补29dz:stdout 读取放线程,主线程 wait(timeout) 真正生效——
            # 旧代码 for line 阻塞读 stdout,子进程挂起不输出/不退出时永久卡住,
            # 后面的 wait(timeout) 永远执行不到(手动删输入文件等导致脚本挂起时
            # 界面卡死)。超时仍先杀进程树再返回,不残留。
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
                    f"命令超时（>{timeout:.0f}s）,已终止进程树",
                    124,
                )
            _done.wait(timeout=5.0)  # 正常退出:等 stdout 读完(EOF)
            _stderr_done.wait(timeout=5.0)
            stderr = "".join(_stderr_buf)
            with _LOCK:
                if proc.pid in _USER_TERMINATED:
                    _USER_TERMINATED.discard(proc.pid)
                    raise TaskTerminatedError("任务已被用户停止")
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
