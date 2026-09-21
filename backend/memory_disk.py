"""Adaptive ramdisk placement for intermediate spectra (0.2.199-patch29ey; the
user's plan, 0.2.199-patch29ez).

Intermediate products are collected in the _intermediate subdirectory of the
working directory (process/), and the ramdisk takes over only that one
subdirectory; everything else in the working directory (fid/, nuslist,
phase.json, scripts, final-spectrum ownership) keeps its original logic and
always stays on disk.

- process/_intermediate goes to disk (default) or becomes a symbolic link to the
  ramdisk (Linux /dev/shm; the setting processing.memory_disk_path overrides it;
  Windows has no standard tmpfs, so a self-made RAM disk path can be given);
- criteria (0.2.199-patch29fk-fix): intermediate peak x1.6 <= available system
  memory and peak x1.1 <= free ramdisk space and peak >= 32MB; failing any of
  them falls back to disk (_intermediate becomes a real directory);
- peak estimate (0.2.199-patch29fk): the indirect dimension uses the auto
  zero-fill SI and the direct dimension uses the point count after the EXT
  window (10.5-6.5 ppm by default; a user final_ext/ext overrides it) --
  estimating from the full direct-dimension SI inflated large 3D NUS peaks
  (e.g. sampleJ at 25.8GB) and machines with 15G of memory always fell back to
  disk;
- the caller tears down at the end: delete _intermediate (only the link when it
  is a symbolic link) and the memory directory.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from backend.config import load_config, resolve_ext_hi, resolve_ext_lo
from ui_support.i18n import tr

DEFAULT_POLICY = "auto"
MIN_PEAK_BYTES = 32 * 1024 * 1024  # below 32MB the ramdisk brings no benefit
SYSTEM_SAFETY_FACTOR = 1.6  # available memory >= peak x 1.6 (leaves room for SMILE)
DISK_SAFETY_FACTOR = 1.1  # free ramdisk >= peak x 1.1 (keeps tmpfs from filling)
BYTES_PER_POINT = 8  # complex float32
NUS_PEAK_FACTOR = 2  # intermediates are deleted at once, so ~one spectrum x2 slack
UNIFORM_PEAK_FACTOR = 2

# Shared subdirectory for intermediate products (under the working directory;
# it may be a symbolic link to the ramdisk)
INTERMEDIATE_SUBDIR = "_intermediate"


def intermediate_memory_policy(config: dict[str, Any] | None = None) -> str:
    """Ramdisk policy for intermediate spectra (auto/off; an invalid value falls
    back to auto)."""
    cfg = load_config(config)
    processing = cfg.get("processing") or {}
    policy = str(processing.get("intermediate_memory", DEFAULT_POLICY)).strip().lower()
    return policy if policy in ("auto", "off") else DEFAULT_POLICY


def memory_disk_path(config: dict[str, Any] | None = None) -> Path | None:
    """Ramdisk root: an explicit setting wins; Linux defaults to /dev/shm (tmpfs)
    and Windows has none."""
    cfg = load_config(config)
    processing = cfg.get("processing") or {}
    explicit = str(processing.get("memory_disk_path", "")).strip()
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.is_dir() else None
    if os.name == "nt":
        return None  # Windows has no standard tmpfs
    shm = Path("/dev/shm")
    if shm.is_dir() and shm.is_mount():
        return shm
    return None


def system_available_bytes() -> int | None:
    """Currently available system memory (Linux /proc/meminfo; Windows
    GlobalMemoryStatusEx)."""
    if os.name == "nt":
        try:
            import ctypes

            class _MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            state = _MEMORYSTATUSEX()
            state.dwLength = ctypes.sizeof(state)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(state))
            return int(state.ullAvailPhys)
        except Exception:  # noqa: BLE001 - a failed probe counts as unknown
            return None
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except Exception:  # noqa: BLE001
        pass
    return None


def _ext_window(params: dict[str, Any] | None) -> tuple[str, str]:
    """Direct-dimension EXT window for this run: an explicit ext_lo/hi wins;
    otherwise, when "apply this range to the optimisation" is on (the default),
    final_ext_lo/final_ext_hi are used; failing that, the configured or built-in
    defaults (see resolve_ext_* in backend.config)."""
    p = dict(params or {})
    ext_lo = p.get("ext_lo")
    ext_hi = p.get("ext_hi")
    apply_opt = str(p.get("apply_ext_to_opt", "1")).strip().lower() in (
        "1", "true", "yes", "on"
    )
    if ext_lo is None and apply_opt:
        ext_lo = p.get("final_ext_lo")
    if ext_hi is None and apply_opt:
        ext_hi = p.get("final_ext_hi")
    return resolve_ext_lo(ext_lo), resolve_ext_hi(ext_hi)


def estimate_intermediate_peak(
    experiment: Any, params: dict[str, Any] | None = None
) -> int | None:
    """Estimate the intermediate-spectrum peak in bytes for one spectrum
    generation (final-spectrum complex point count x safety factor).

    The auto zero-filled SI gives the point count per axis; NUS counts 3 copies
    (reconstruction planes + final spectrum + preview) and uniform counts 2; an
    unknown dimension returns None (the caller falls back to disk).
    """
    try:
        from backend.memory_guard import direct_points_after_ext
        from backend.script_generator import effective_td, zero_fill_plan
        from core.data.internal_data_model import AxisRole

        plan = zero_fill_plan(experiment)
        axes = [dim.logical_axis for dim in experiment.dimensions]
        if not axes:
            return None
        dims = {dim.logical_axis: dim for dim in experiment.dimensions}
        tds = list(effective_td(experiment))
        direct_axis = next(
            (
                dim.logical_axis
                for dim in experiment.dimensions
                if getattr(dim, "role", None) is AxisRole.DIRECT
            ),
            None,
        )
        if direct_axis is None and experiment.dimensions:
            direct_axis = experiment.dimensions[0].logical_axis
        ext_lo, ext_hi = _ext_window(params)
        total = 1
        for axis in axes:
            size = int((plan.get(axis) or {}).get("size") or 0)
            if not size:
                size = int(getattr(dims.get(axis), "ft_size", 0) or 0)
            if not size:
                idx = axes.index(axis)
                size = int(tds[idx]) if idx < len(tds) else 0
            if not size:
                return None
            if axis == direct_axis:
                size = direct_points_after_ext(
                    experiment, size, ext_lo, ext_hi
                )
            total *= size
        sampling = getattr(getattr(experiment, "sampling", None), "mode", None)
        factor = NUS_PEAK_FACTOR if str(sampling) == "nus" else UNIFORM_PEAK_FACTOR
        return total * BYTES_PER_POINT * factor
    except Exception:  # noqa: BLE001 - a failed estimate falls back to disk
        return None


def _memory_conditions(
    experiment: Any,
    config: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """Return (whether the ramdisk is usable, reason). No directory is created."""
    if intermediate_memory_policy(config) == "off":
        return False, tr("policyintermediate_memory=off")
    root = memory_disk_path(config)
    if root is None:
        return False, tr("No ramdisk path (Windows None /dev/shm or memory_disk_path is invalid)")
    peak = estimate_intermediate_peak(experiment, params)
    if peak is None:
        return False, tr("Intermediate spectrum peak estimation is not available")
    if peak < MIN_PEAK_BYTES:
        return False, (
            tr(
            "The middle spectrum peak is too small ({p0:.0f}MB < 32MB, not worth "
            "it)",
            p0=peak / 1e6,
        )
        )
    try:
        free = shutil.disk_usage(root).free
    except OSError as exc:
        return False, tr("ramdisk is not readable: {p0}", p0=exc)
    avail = system_available_bytes()
    need_avail = int(peak * SYSTEM_SAFETY_FACTOR)
    need_free = int(peak * DISK_SAFETY_FACTOR)
    if avail is not None and avail < need_avail:
        return False, (
            tr(
                "Not enough free memory: peak {p0:.2f}GB, requires >={p1:.1f}GB,currently "
                "{p2:.1f}GB",
                p0=peak / 1e9,
                p1=need_avail / 1e9,
                p2=avail / 1e9,
            )
        )
    if free < need_free:
        return False, (
            tr(
                "ramdisk remaining insufficient: peak {p0:.2f}GB, requires >={p1:.1f}GB,currently "
                "{p2:.1f}GB",
                p0=peak / 1e9,
                p1=need_free / 1e9,
                p2=free / 1e9,
            )
        )
    return True, ""


def selection_reason(
    experiment: Any,
    config: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> str:
    """Fallback-reason text (an empty string when the conditions hold), for log
    messages."""
    ok, reason = _memory_conditions(experiment, config, params)
    return "" if ok else reason


def select_memory_dir(
    experiment: Any,
    config: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> Path | None:
    """Create a temporary directory on the ramdisk when memory allows; returns
    None when any condition fails."""
    ok, _reason = _memory_conditions(experiment, config, params)
    if not ok:
        return None
    root = memory_disk_path(config)
    if root is None:
        return None
    try:
        return Path(tempfile.mkdtemp(prefix="nmrforge-mem-", dir=str(root)))
    except OSError:
        return None


def _remove_link_or_dir(path: Path) -> None:
    """Delete a symbolic link (the link alone) or a real directory (the whole
    tree)."""
    if path.is_symlink():
        try:
            path.unlink()
        except OSError:
            pass
    elif path.is_dir():
        shutil.rmtree(path, ignore_errors=True)


def prepare_intermediate(
    work: Path,
    experiment: Any,
    config: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> tuple[Path, Path | None]:
    """Set up work/_intermediate; with enough memory it becomes a symbolic link
    to the ramdisk.

    Returns (intermediate_root, memory_dir or None); the caller must call
    teardown_intermediate(work, memory_dir) when done. A failed symbolic link
    falls back to a real directory automatically.
    """
    root = work / INTERMEDIATE_SUBDIR
    _remove_link_or_dir(root)
    mem = select_memory_dir(experiment, config, params)
    if mem is not None:
        try:
            root.symlink_to(mem, target_is_directory=True)
            return root, mem
        except OSError:
            _remove_link_or_dir(root)
            mem = None
    root.mkdir(parents=True, exist_ok=True)
    return root, None


def teardown_intermediate(work: Path, memory_dir: Path | None) -> None:
    """End of a run: delete _intermediate (only the link when it is one) and the
    memory directory."""
    root = work / INTERMEDIATE_SUBDIR
    _remove_link_or_dir(root)
    if memory_dir is not None:
        shutil.rmtree(memory_dir, ignore_errors=True)


__all__ = [
    "INTERMEDIATE_SUBDIR",
    "estimate_intermediate_peak",
    "intermediate_memory_policy",
    "memory_disk_path",
    "prepare_intermediate",
    "select_memory_dir",
    "selection_reason",
    "system_available_bytes",
    "teardown_intermediate",
]
