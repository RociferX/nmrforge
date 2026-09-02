"""中间谱内存盘自适应放置(0.2.199-补29ey,补29ey-修 持久安全版)。

处理流程的工作目录同时承载两类内容:
- 持久产物:fid/、merged/、seg_001/、nuslist、phase.json、*.com 脚本、
  smile.log——跨步骤/跨运行需要保留(人工重跑/相位缓存/重新优化),必须
  始终在磁盘(数据 process 目录);
- 中间谱:preview/joint/候选评分谱、SMILE 重构平面、终谱(写后即移动到
  spectra/)——用完即删,可放内存盘。

本实现:工作目录主体仍在磁盘;内存余量充足时,把「运行期渲染工作区」放到
内存盘——每次运行:从磁盘持久目录预置持久产物 → 渲染(中间谱在内存) →
把持久产物同步回磁盘 → 删除内存盘目录。重启/崩溃最多丢失当次未同步的
脚本/相位缓存(重跑即可),fid/ 等原始派生物始终在磁盘,不会出现
「找不到脚本/fid」的奇怪问题。

- 配置 processing.intermediate_memory:auto(默认)=内存充足时启用;off=关闭;
- 配置 processing.memory_disk_path:可选显式内存盘路径(Windows 无标准
  tmpfs,自建 RAM 盘后在此指定;默认 Linux 用 /dev/shm);
- 判据:中间谱峰值 ×2 + 持久产物 ≤ 系统可用内存,且 峰值 ×1.2 + 持久产物
  ≤ 内存盘剩余,且峰值 ≥32MB;任一不满足回退磁盘。
"""

from __future__ import annotations

import hashlib
import os
import shutil
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

# 工作目录中需要跨运行保留的产物(其余为可再生成中间谱)
PERSISTENT_DIRS = ("fid", "merged", "seg_001")
PERSISTENT_FILES = ("nuslist", "phase.json", "smile.log")
PERSISTENT_GLOBS = ("*.com",)


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


def _persistent_paths(directory: Path) -> list[Path]:
    """工作目录中需要跨运行保留的路径(目录 + 文件 glob)。"""
    paths: list[Path] = []
    for name in PERSISTENT_DIRS:
        p = directory / name
        if p.is_dir():
            paths.append(p)
    for name in PERSISTENT_FILES:
        p = directory / name
        if p.is_file():
            paths.append(p)
    for pattern in PERSISTENT_GLOBS:
        paths.extend(sorted(directory.glob(pattern)))
    return paths


def persistent_usage(directory: Path) -> int:
    """工作目录中持久产物总字节数(用于内存盘容量/内存余量判断)。"""
    total = 0
    for path in _persistent_paths(directory):
        if path.is_dir():
            for p in path.rglob("*"):
                if p.is_file():
                    try:
                        total += p.stat().st_size
                    except OSError:
                        pass
        elif path.is_file():
            try:
                total += path.stat().st_size
            except OSError:
                pass
    return total


def _copy_persistent(src: Path, dst: Path) -> None:
    """把 src 的持久产物复制到 dst(覆盖;目录合并)。"""
    for name in PERSISTENT_DIRS:
        p = src / name
        if p.is_dir():
            shutil.copytree(p, dst / name, dirs_exist_ok=True)
    for name in PERSISTENT_FILES:
        p = src / name
        if p.is_file():
            shutil.copy2(p, dst / name)
    for pattern in PERSISTENT_GLOBS:
        for p in sorted(src.glob(pattern)):
            shutil.copy2(p, dst / p.name)


def seed_persistent(persistent_dir: Path, mem_dir: Path) -> None:
    """运行前:把磁盘持久产物预置到内存盘工作目录。"""
    _copy_persistent(persistent_dir, mem_dir)


def sync_persistent(mem_dir: Path, persistent_dir: Path) -> None:
    """运行后:把内存盘工作目录中的持久产物同步回磁盘。"""
    _copy_persistent(mem_dir, persistent_dir)


def memory_work_dir(
    persistent_dir: Path,
    experiment: Any,
    config: dict[str, Any] | None = None,
) -> Path | None:
    """自适应选择内存盘工作目录;任一条件不满足返回 None(用磁盘)。

    返回的目录不存在(调用方负责 mkdir 并预置/同步/清理);目录名按持久
    目录路径哈希稳定,重启后残留由下次运行清理。
    """
    if intermediate_memory_policy(config) == "off":
        return None
    root = memory_disk_path(config)
    if root is None:
        return None
    peak = estimate_intermediate_peak(experiment)
    if peak is None or peak < MIN_PEAK_BYTES:
        return None
    persistent_bytes = persistent_usage(persistent_dir)
    required_disk = int(peak * DISK_SAFETY_FACTOR) + persistent_bytes
    required_sys = int(peak * SYSTEM_SAFETY_FACTOR) + persistent_bytes
    try:
        free = shutil.disk_usage(root).free
    except OSError:
        return None
    avail = system_available_bytes()
    if avail is not None and avail < required_sys:
        return None
    if free < required_disk:
        return None
    digest = hashlib.sha1(
        str(persistent_dir.resolve()).encode("utf-8")
    ).hexdigest()[:12]
    return root / f"nmrforge-{digest}"


__all__ = [
    "estimate_intermediate_peak",
    "intermediate_memory_policy",
    "memory_disk_path",
    "memory_work_dir",
    "persistent_usage",
    "seed_persistent",
    "sync_persistent",
    "system_available_bytes",
]
