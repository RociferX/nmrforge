"""中间谱内存盘自适应放置(0.2.199-补29ey,用户方案 0.2.199-补29ez)。

把中间产物统一收进工作目录(process/)下的 _intermediate 子目录,内存盘只
接管这一个子目录;工作目录其它内容(fid/、nuslist、phase.json、脚本、终谱
归属)一律保持原逻辑、始终在磁盘。

- process/_intermediate 落盘(默认)或符号链接到内存盘(Linux /dev/shm,
  显式配置 processing.memory_disk_path 可覆盖;Windows 无标准 tmpfs,
  自建 RAM 盘后指定路径);
- 判据:中间谱峰值 ×2 ≤ 系统可用内存,且 峰值 ×1.2 ≤ 内存盘剩余,且峰值
  ≥32MB;任一不满足回退磁盘(_intermediate 为真实目录);
- 结束由调用方 teardown:删除 _intermediate(符号链接则只删链接)与内存目录。
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from backend.config import load_config

DEFAULT_POLICY = "auto"
MIN_PEAK_BYTES = 32 * 1024 * 1024  # 小于 32MB 不上内存盘(无收益)
SYSTEM_SAFETY_FACTOR = 2.0  # 系统可用内存 ≥ 峰值 × 2
DISK_SAFETY_FACTOR = 1.2  # 内存盘剩余 ≥ 峰值 × 1.2
BYTES_PER_POINT = 8  # 复 float32
NUS_PEAK_FACTOR = 3  # SMILE 重构平面 + 终谱 + 预览
UNIFORM_PEAK_FACTOR = 2

# 中间产物统一子目录(位于工作目录下;可符号链接到内存盘)
INTERMEDIATE_SUBDIR = "_intermediate"


def intermediate_memory_policy(config: dict[str, Any] | None = None) -> str:
    """中间谱内存盘策略(auto/off,无效值回退 auto)。"""
    cfg = load_config(config)
    processing = cfg.get("processing") or {}
    policy = str(processing.get("intermediate_memory", DEFAULT_POLICY)).strip().lower()
    return policy if policy in ("auto", "off") else DEFAULT_POLICY


def memory_disk_path(config: dict[str, Any] | None = None) -> Path | None:
    """内存盘根目录:显式配置优先;Linux 默认 /dev/shm(tmpfs);Windows 无。"""
    cfg = load_config(config)
    processing = cfg.get("processing") or {}
    explicit = str(processing.get("memory_disk_path", "")).strip()
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.is_dir() else None
    if os.name == "nt":
        return None  # Windows 无标准 tmpfs
    shm = Path("/dev/shm")
    if shm.is_dir() and shm.is_mount():
        return shm
    return None


def system_available_bytes() -> int | None:
    """系统当前可用内存(Linux /proc/meminfo;Windows GlobalMemoryStatusEx)。"""
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
        except Exception:  # noqa: BLE001 - 探测失败视为未知
            return None
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except Exception:  # noqa: BLE001
        pass
    return None


def estimate_intermediate_peak(experiment: Any) -> int | None:
    """估算一次生成谱图的中间谱峰值字节数(终谱复点数 × 安全系数)。

    以 auto 填零后的 SI 为每轴点数;NUS 按 3 份(重构平面+终谱+预览)计,
    uniform 按 2 份计;任一维度未知返回 None(调用方回退磁盘)。
    """
    try:
        from backend.script_generator import effective_td, zero_fill_plan

        plan = zero_fill_plan(experiment)
        axes = [dim.logical_axis for dim in experiment.dimensions]
        if not axes:
            return None
        dims = {dim.logical_axis: dim for dim in experiment.dimensions}
        tds = list(effective_td(experiment))
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
            total *= size
        sampling = getattr(getattr(experiment, "sampling", None), "mode", None)
        factor = NUS_PEAK_FACTOR if str(sampling) == "nus" else UNIFORM_PEAK_FACTOR
        return total * BYTES_PER_POINT * factor
    except Exception:  # noqa: BLE001 - 估算失败回退磁盘
        return None


def select_memory_dir(
    experiment: Any, config: dict[str, Any] | None = None
) -> Path | None:
    """内存充足时在内存盘建一个临时目录;任一条件不满足返回 None。"""
    if intermediate_memory_policy(config) == "off":
        return None
    root = memory_disk_path(config)
    if root is None:
        return None
    peak = estimate_intermediate_peak(experiment)
    if peak is None or peak < MIN_PEAK_BYTES:
        return None
    try:
        free = shutil.disk_usage(root).free
    except OSError:
        return None
    avail = system_available_bytes()
    if avail is not None and avail < int(peak * SYSTEM_SAFETY_FACTOR):
        return None
    if free < int(peak * DISK_SAFETY_FACTOR):
        return None
    try:
        return Path(tempfile.mkdtemp(prefix="nmrforge-mem-", dir=str(root)))
    except OSError:
        return None


def _remove_link_or_dir(path: Path) -> None:
    """删除符号链接(仅链接本身)或真实目录(整树)。"""
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
) -> tuple[Path, Path | None]:
    """把 work/_intermediate 建好;内存充足时改为指向内存盘的符号链接。

    返回 (intermediate_root, memory_dir 或 None);调用方结束须
    teardown_intermediate(work, memory_dir)。符号链接失败自动回退真实目录。
    """
    root = work / INTERMEDIATE_SUBDIR
    _remove_link_or_dir(root)
    mem = select_memory_dir(experiment, config)
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
    """运行结束:删除 _intermediate(符号链接只删链接)与内存目录。"""
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
    "system_available_bytes",
    "teardown_intermediate",
]
