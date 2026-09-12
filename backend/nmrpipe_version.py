"""探测 NMRPipe / SMILE 版本并登记到 ``core.version``(PROV-009,2026-09-12)。

为什么单独放 backend 层:NMRPipe 版本只能通过运行其可执行文件得到,而
``core`` 不允许依赖 backend。backend 在真正解析到 NMRPipe 安装目录时调用
``register_nmrpipe_versions``,``ProjectManager.finish_run`` 再把已登记的版本
并入 WorkflowRun.tool_versions。

约束:

- 按安装目录缓存,每个目录最多探测一次;
- 任何失败(文件不可执行/超时/无版本串)都静默返回,绝不影响处理流程;
- 只记录工具自报的版本串,不猜测、不伪造。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from core.version import register_tool_version

_TIMEOUT_SECONDS = 5.0
# 已探测目录 → {工具名: 版本};失败也缓存(空表),避免反复起子进程
_CACHE: dict[str, dict[str, str]] = {}

_EXECUTABLES = {"nmrpipe": "nmrPipe", "smile": "smile"}
_VERSION_RE = re.compile(r"\b(\d+\.\d+(?:[.\-]\w+)*)\b")


def _probe(executable: Path) -> str:
    """运行 ``<tool> -version`` 取版本串;失败返回空串。"""
    try:
        proc = subprocess.run(
            [str(executable), "-version"],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    match = _VERSION_RE.search(f"{proc.stdout or ''}\n{proc.stderr or ''}")
    return match.group(1) if match else ""


def register_nmrpipe_versions(bin_dir: Path | None) -> dict[str, str]:
    """探测并登记 ``bin_dir`` 下 NMRPipe / SMILE 版本,返回本次结果。"""
    if bin_dir is None:
        return {}
    key = str(bin_dir)
    cached = _CACHE.get(key)
    if cached is not None:
        return dict(cached)
    found: dict[str, str] = {}
    for name, executable_name in _EXECUTABLES.items():
        executable = Path(bin_dir) / executable_name
        if not executable.is_file():
            continue
        version = _probe(executable)
        if version:
            found[name] = version
            register_tool_version(name, version)
    _CACHE[key] = found
    return dict(found)


def clear_cache() -> None:
    """清空探测缓存(测试用)。"""
    _CACHE.clear()


__all__ = ["clear_cache", "register_nmrpipe_versions"]
