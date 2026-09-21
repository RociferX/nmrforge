"""SMILE peak memory estimate and guard (0.2.112; aligned with what SMILE itself
reports as of 0.2.199-patch15).

Model (SMILE startup banner "Memory Used", verified on sampleK 2026-08-26):
  - peak ~ direct-dimension points x product of the indirect-dimension iterative
    FT sizes x 16 B (a complex double plane): sampleK with zero fill 1024
    (EXT 9-7ppm): Z=168, input complex grid 146x145 (NusTD/2), SMILE
    reconstruction grid 219x217 (x1.5), iterative FT 1024x1024 -> it reported
    2.8GB; the formula 168x1024x1024x16B ~ 2.82GB matches (2.85GB with the x1.06
    overhead);
  - iterative FT size = next_pow2(3 x NusTD): 292 -> 1024 and 290 -> 1024
    (banner-verified);
  - older VM measurements (2026-08-18, HNCACB, NusTD product 4000 -> FT
    256x256): 150/600/2048 points -> 179/665/2284 MB while the formula gives
    157/629/2147 MB (+6-14% overhead), which matches;
  - independent of the sampling-point count and thread count (SMILE allocates
    over the whole grid and parallel workers share one working set).

So the practical ways to process large data in little memory are: the slice
stream (the direct dimension is FT'd into a plane stream first, so the peak
scales with one plane's point count rather than the whole spectrum) plus a
default direct-dimension zero fill of 2x TD that the guard reduces to 1x TD
when memory is short (0.2.199-patch29dq) plus a tighter EXT window (linear
scaling).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from ui_support.i18n import tr

# 0.2.199-patch15: SMILE model constants (see the module docstring)
MB_PER_FT_PLANE = 16.0 / (1024.0 * 1024.0)  # complex double (8B x 2) per plane,
# MB/point
FT_OVERHEAD = 1.06  # array overhead for input/reconstruction/simulation (+6-14%)
FT_SIM_FACTOR = 3.0  # iterative FT = next_pow2(3 x NusTD) (sampleK banner verified)
FT_MIN_3D = 256  # lower bound for the 3D iterative FT size
MB_FLOOR_2D = 128.0  # 2D measures ~3MB; conservative floor incl. pipeline buffers
MEM_SAFETY = 0.85  # peak below 85% of available memory, leaving system slack


def smile_iteration_ft_size(td: int) -> int:
    """SMILE indirect-dimension iterative FT size: next_pow2(3 x NusTD), floor 256.

    Banner-verified on sampleK: NusTD 292/290 -> FT 1024/1024; HNCACB (NusTD
    product 4000, i.e. about 64 per dimension -> FT 256x256) agrees with the
    older measurements of 179/665/2284 MB.
    """
    v = max(int(td or 1), 1)
    return max(1 << ((int(FT_SIM_FACTOR * v) - 1).bit_length()), FT_MIN_3D)


def direct_points_after_ext(
    experiment: Any, zf_size: int, ext_lo: Any, ext_hi: Any
) -> int:
    """Number of sampling points inside the direct-dimension EXT window
    (approximately SMILE's per-plane size)."""
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
    """SMILE peak memory estimate (MB), calibrated to SMILE's startup "Memory
    Used".

    indirect_td: for 3D pass the per-dimension NusTD list [td1, td2]; an int (the
    old grid product) is split approximately by sqrt, for compatibility with
    older tests and callers.
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
    """Available system memory (Linux /proc/meminfo MemAvailable; 4096MB as a
    fallback)."""
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
    except Exception:  # noqa: BLE001 - fallback
        return 4096


def memory_guard(
    ndim: int,
    direct_points: int,
    indirect_td: int | Sequence[int],
    available_mb: int | None = None,
) -> dict[str, Any]:
    """SMILE memory guard decision (indirect_td follows estimate_smile_peak_mb).

    Returns {"ok", "peak_mb", "available_mb", "needed_gb", "message"}.
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
            tr(
                "The currently available memory is approx. {p0} MB; processing this spectrum "
                "(SMILE) peaks at about {p1:.0f} MB, which cannot be handled at the current memory "
                "level. Please provide at least {p2} GB of "
                "memory",
                p0=avail,
                p1=peak,
                p2=needed_gb,
            )
        ),
    }
