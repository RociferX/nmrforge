"""SMILE 峰值内存估计与护栏(0.2.112,口径 0.2.199-补15 对齐 SMILE 自报)。

模型(SMILE 启动横幅 Memory Used 口径,2026-08-26 sampleK 验证):
  - 峰值 ≈ 直接维点数 × 间接维迭代 FT 尺寸乘积 × 16 B(复 double 平面):
    sampleK 填零1024(EXT 9-7ppm):Z=168,输入复网格 146×145(NusTD/2),
    SMILE 重建网格 219×217(×1.5),迭代 FT 1024×1024 → 自报 2.8GB;
    公式 168×1024×1024×16B≈2.82GB ✓(×1.06 开销后 2.85GB);
  - 迭代 FT 尺寸 = next_pow2(3×NusTD):292→1024、290→1024(横幅验证);
  - 旧 VM 实测(2026-08-18,HNCACB,NusTD 积 4000 → FT 256×256):
    150/600/2048 点 → 179/665/2284 MB;公式 157/629/2147 MB
    (+6~14% 开销)✓;
  - 与采样点数/线程数无关(SMILE 按全网格分配,并行共享同一工作集)。

因此「小内存处理大数据」的可行手段:切片流(直接维先 FT 成平面流,
峰值 ∝ 单平面点数而非整谱)+ 直接维填零默认 2×TD、内存不足由护栏
降 1×TD(0.2.199-补29dq)+ 收紧 EXT 窗口(线性降)。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

# 0.2.199-补15:SMILE 口径常量(见模块文档)
MB_PER_FT_PLANE = 16.0 / (1024.0 * 1024.0)  # 复 double(8B×2)单平面 MB/点
FT_OVERHEAD = 1.06  # 输入/重建/模拟数组开销(旧实测 +6~14%)
FT_SIM_FACTOR = 3.0  # 迭代 FT = next_pow2(3×NusTD)(sampleK 横幅验证)
FT_MIN_3D = 256  # 3D 迭代 FT 尺寸下限
MB_FLOOR_2D = 128.0  # 2D 实测≈3MB,保守下限(含管道缓冲)
MEM_SAFETY = 0.85  # 峰值不超过可用内存的 85%,留系统余量


def smile_iteration_ft_size(td: int) -> int:
    """SMILE 间接维迭代 FT 尺寸:next_pow2(3×NusTD),下限 256。

    sampleK 横幅验证:NusTD 292/290 → FT 1024/1024;HNCACB(NusTD 积
    4000,反推每维 ~64 → FT 256×256)与旧实测 179/665/2284 MB 吻合。
    """
    v = max(int(td or 1), 1)
    return max(1 << ((int(FT_SIM_FACTOR * v) - 1).bit_length()), FT_MIN_3D)


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
    ndim: int, direct_points: int, indirect_td: int | Sequence[int]
) -> float:
    """SMILE 峰值内存估计(MB),口径 = SMILE 启动自报 Memory Used。

    indirect_td:3D 传逐维 NusTD 列表 [td1, td2];传 int(旧网格积)时按
    sqrt 拆分近似(兼容旧测试/调用)。
    """
    if int(ndim) < 3:
        return MB_FLOOR_2D
    if isinstance(indirect_td, int):
        half = math.sqrt(max(int(indirect_td), 1))
        td_list = [half, half]
    else:
        vals = [max(int(v or 1), 1) for v in indirect_td]
        td_list = (vals + [1, 1])[:2]
    ft_x = smile_iteration_ft_size(int(td_list[0]))
    ft_y = smile_iteration_ft_size(int(td_list[1]))
    plane_mb = ft_x * ft_y * MB_PER_FT_PLANE
    return plane_mb * max(int(direct_points), 1) * FT_OVERHEAD


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
    indirect_td: int | Sequence[int],
    available_mb: int | None = None,
) -> dict[str, Any]:
    """SMILE 内存护栏判定(indirect_td 同 estimate_smile_peak_mb 口径)。

    返回 {"ok", "peak_mb", "available_mb", "needed_gb", "message"}。
    """
    peak = estimate_smile_peak_mb(ndim, direct_points, indirect_td)
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
