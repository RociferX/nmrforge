"""Probe the NMRPipe / SMILE versions and register them with ``core.version`` (PROV-009,
2026-09-12).

Why this lives in the backend layer: the NMRPipe version can only be obtained by running its
executable, and ``core`` may not depend on the backend. The backend calls
``register_nmrpipe_versions`` once it has really parsed an NMRPipe installation directory, and
``ProjectManager.finish_run`` then merges the registered versions into WorkflowRun.tool_versions.

Constraints:

- cached per installation directory, each directory is probed at most once;
- any failure (a non-executable file, a timeout, no version string) returns silently and never
  affects the processing flow;
- only the version string the tool reports itself is recorded; nothing is guessed or fabricated.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from core.version import register_tool_version

_TIMEOUT_SECONDS = 5.0
  # probed directory -> {tool name: version}; failures are cached too (an empty table) to avoid
# respawning
_CACHE: dict[str, dict[str, str]] = {}

_EXECUTABLES = {"nmrpipe": "nmrPipe", "smile": "smile"}
_VERSION_RE = re.compile(r"\b(\d+\.\d+(?:[.\-]\w+)*)\b")


def _probe(executable: Path) -> str:
    """Run ``<tool> -version`` to obtain the version string; an empty string on failure."""
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
    """Probe and register the NMRPipe / SMILE versions under ``bin_dir``; returns this run
    result."""
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
    """Clear the probe cache (for tests)."""
    _CACHE.clear()


__all__ = ["clear_cache", "register_nmrpipe_versions"]
