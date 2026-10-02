"""Locate NMRPipe on this machine (Linux).

Search order (user requirement: trust the nmrpipe the csh environment really answers with):
1. an explicitly configured path (a directory or the executable itself);
2. ``which nmrPipe`` after ``source ~/.cshrc`` in csh/tcsh (the NMRPipe environment lives in
   ~/.cshrc);
3. shutil.which as a last resort.

Note: the NMRPipe executable is the case-sensitive ``nmrPipe``.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def _csh_which(name: str) -> str | None:
    """Find an executable path inside the csh login environment (source ~/.cshrc)."""
    shell = shutil.which("tcsh") or shutil.which("csh")
    if shell is None:
        return None
    script = f"if (-e ~/.cshrc) source ~/.cshrc; which {name}"
    try:
        proc = subprocess.run(
            [shell, "-c", script],
            capture_output=True,
            text=True,
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
    """Return the bin directory holding the nmrPipe executable (None when it cannot be found)."""
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


def csh_run(command: str, timeout: float = 15.0) -> tuple[int, str]:
    """Run a command in the csh login environment and return ``(return_code, combined_output)``.

    This must use csh because SMILE is an NMRPipe plugin registered through the
    ``NMR_PLUGIN_EXE`` and ``NMR_PLUGIN_FN`` environment variables in the user's shell setup.
    A plain shell reports ``unknown function SMILE`` for ``nmrPipe -fn SMILE -help``; the
    configured csh environment reports the plugin version. Therefore SMILE availability must
    be checked through csh rather than by running ``nmrPipe`` directly with
    ``subprocess.run``.

    Parameters
    ----------
    command : str
        Command to run after sourcing the user's csh startup file.
    timeout : float
        Timeout in seconds.

    Returns
    -------
    tuple[int, str]
        ``(return_code, stdout+stderr)``; returns ``(-1, "")`` if csh is unavailable or times
        out. Standard input is connected to ``/dev/null`` because ``nmrPipe`` may otherwise wait
        for terminal input.

    Side effects
    ------------
    Read-only: starts a csh subprocess.
    """
    shell = shutil.which("tcsh") or shutil.which("csh")
    if shell is None:
        return -1, ""
    script = f"if (-e ~/.cshrc) source ~/.cshrc; {command}"
    try:
        proc = subprocess.run(
            [shell, "-c", script],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return -1, ""
    return proc.returncode, f"{proc.stdout or ''}\n{proc.stderr or ''}"


def find_tool(name: str, nmrpipe_bin: Path | None = None) -> Path | None:
    """Find an NMRPipe companion tool (bruker and similar live in com/).

    The csh answer wins; otherwise the bin directory and its parent levels are searched,
    including
    their com/ subdirectories.
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
