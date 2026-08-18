"""SMILE 峰值内存估计与护栏(0.2.112)。

标定来源(2026-08-18 VM 实测,3D HNCACB 网格 4000,线程 2):
  - 峰值内存 ∝ 直接维点数(EXT 窗口内的点数),≈1.15 MB/点:
    直接维 150/600/2048 点 → 峰值 179/665/2284 MB;
  - 与采样点数无关(SMILE 按全网格分配,100 与 250 采样峰值相同);
  - 2D 峰值 ≈3MB,不构成瓶颈(护栏用保守下限 128MB);
  - 网格(间接维 TD 积)是另一个线性因子(未直接实测,按 4000 基准外推)。

因此「小内存处理大数据」的可行手段:切片流(直接维先 FT 成平面流,
峰值 ∝ 单平面点数而非整谱)+ 直接维填零 1×TD + 收紧 EXT 窗口(线性降)。
"""

from __future__ import annotations

import math
from typing import Any

# VM 实测标定常量(网格 4000、nThread=2)
MB_PER_DIRECT_POINT_3D = 1.15
REF_INDIRECT_GRID = 4000.0
MB_FLOOR_2D = 128.0  # 2D 实测≈3MB,保守下限(含管道缓冲)
MEM_SAFETY = 0.85  # 峰值不超过可用内存的 85%,留系统余量


def direct_points_after_ext(
    experiment: Any, zf_size: int, ext_lo: Any, ext_hi: Any
) -> int:
    """直接维 EXT 窗口内的采样点数(≈SMILE 每平面尺寸)。"""
    dims = getattr(experiment, "dimensions", None) or []
    zf = max(int(zf_size or 1), 1)
    if not dims:
        return zf
    d0 = dims[0]
    sw = float(getattr(d0, "sw", 0.0) or 0.0)
    sf = float(getattr(d0, "sf", 0.0) or 0.0)
    sw_ppm = sw / sf if sf > 0 else 8.0
    if sw_ppm <= 0:
        sw_ppm = 8.0
    try:
        lo, hi = float(ext_lo), float(ext_hi)
        window = abs(hi - lo)
    except (TypeError, ValueError):
        window = sw_ppm
    frac = min(max(window / sw_ppm, 0.02), 1.0)
    return max(int(round(zf * frac)), 1)


def estimate_smile_peak_mb(
    ndim: int, direct_points: int, indirect_grid: int, nthread: int = 2
) -> float:
    """SMILE 峰值内存估计(MB)。"""
    if int(ndim) < 3:
        return MB_FLOOR_2D
    scale = max(int(indirect_grid), 1) / REF_INDIRECT_GRID
    return MB_PER_DIRECT_POINT_3D * max(int(direct_points), 1) * scale


def available_memory_mb() -> int:
    """系统可用内存(Linux /proc/meminfo MemAvailable;兜底 4096MB)。"""
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except OSError:
        pass
    try:
        import psutil  # type: ignore

        return int(psutil.virtual_memory().available / (1024 * 1024))
    except Exception:  # noqa: BLE001 - 兜底
        return 4096


def memory_guard(
    ndim: int,
    direct_points: int,
    indirect_grid: int,
    available_mb: int | None = None,
    nthread: int = 2,
) -> dict[str, Any]:
    """SMILE 内存护栏判定。

    返回 {"ok", "peak_mb", "available_mb", "needed_gb", "message"}。
    """
    peak = estimate_smile_peak_mb(ndim, direct_points, indirect_grid, nthread)
    avail = int(available_mb or available_memory_mb())
    budget = max(int(avail * MEM_SAFETY), 1)
    if peak <= budget:
        return {
            "ok": True,
            "peak_mb": peak,
            "available_mb": avail,
            "needed_gb": 0,
            "message": "",
        }
    needed_gb = math.ceil(peak / 1024.0)
    return {
        "ok": False,
        "peak_mb": peak,
        "available_mb": avail,
        "needed_gb": needed_gb,
        "message": (
            f"当前可用内存约 {avail} MB,处理该谱 SMILE 峰值约 {peak:.0f} MB,"
            f"无法在当前内存下处理,请至少提供 {needed_gb} GB 内存"
        ),
    }
