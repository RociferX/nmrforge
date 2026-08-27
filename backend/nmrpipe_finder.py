"""本机 NMRPipe 查找（Linux）。

查找顺序（用户要求：以 csh 环境实际响应的 nmrpipe 为准）：
1. 显式配置路径（nmrpipe_bin 目录或可执行文件）；
2. csh/tcsh ``source ~/.cshrc`` 后 ``which nmrPipe``（NMRPipe 环境写在 ~/.cshrc）；
3. shutil.which 兜底。

注意：NMRPipe 可执行文件名为大小写敏感的 ``nmrPipe``。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def _csh_which(name: str) -> str | None:
    """在 csh 登录环境（source ~/.cshrc）中查找可执行文件路径。"""
    shell = shutil.which("tcsh") or shutil.which("csh")
    if shell is None:
        return None
    script = f"if (-e ~/.cshrc) source ~/.cshrc; which {name}"
    try:
        proc = subprocess.run(
            [shell, "-c", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in proc.stdout.splitlines():
        line = line.strip()
        if "/" in line and "not found" not in line and "no " not in line.lower():
            return line
    return None


def find_nmrpipe_bin(explicit: str = "") -> Path | None:
    """返回含 nmrPipe 可执行文件的 bin 目录（找不到返回 None）。"""
    candidates: list[Path] = []
    if explicit:
        path = Path(explicit).expanduser()
        candidates.append(path if path.is_dir() else path.parent)
    else:
        csh = _csh_which("nmrPipe")
        if csh:
            path = Path(csh)
            candidates.append(path if path.is_dir() else path.parent)
        which = shutil.which("nmrPipe")
        if which:
            candidates.append(Path(which).parent)
    for candidate in candidates:
        if (candidate / "nmrPipe").is_file():
            return candidate.resolve()
    return None


def find_tool(name: str, nmrpipe_bin: Path | None = None) -> Path | None:
    """查找 NMRPipe 配套工具（bruker 等在 com/ 目录）。

    优先 csh 响应；其次在 bin 目录及其父级 com/ 中查找。
    """
    csh = _csh_which(name)
    if csh:
        path = Path(csh)
        if path.is_file():
            return path.resolve()
    if nmrpipe_bin is not None:
        for root in (nmrpipe_bin, nmrpipe_bin.parent, nmrpipe_bin.parent.parent):
            for sub in ("", "com"):
                candidate = root / sub / name
                if candidate.is_file():
                    return candidate.resolve()
    return None
