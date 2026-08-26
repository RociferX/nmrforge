"""sampleK 复型频域终谱:去掉正峰惩罚后的对称性搜索(0.2.199)。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from core.optimization.phase_search import (
    _signal_peak_windows,
    search_direct_phase_on_spectrum,
)


def _sym_no_sign(profile: np.ndarray) -> float:
    """纯对称性(不惩罚净 Re 为负)——负峰左右对称同样给高分。"""
    f = np.asarray(profile, dtype=float)
    n = f.size
    if n < 2:
        return 0.0
    half = n // 2
    left = f[:half]
    right = f[n - half:][::-1]
    denom = 2.0 * (left**2 + right**2) + 1e-12
    sym = float(np.mean((left + right) ** 2 / denom))
    if n % 2 == 1:
        c = f[half]
        sym = float(
            np.mean(
                np.concatenate(
                    [np.asarray((left + right) ** 2 / denom), [1.0]]
                )
            )
        )
    return sym


def main() -> int:
    from workflow.phase_routes import _read_complex_ft3

    cplx = _read_complex_ft3(
        Path("/home/<lab-user>/Desktop/sampleK.nmrpipe/102_direct_preview.ft3")
    )
    n = cplx.shape[-1]
    real = np.real(cplx)
    windows = _signal_peak_windows(real, axis=-1)
    print("窗数:", len(windows))
    if len(windows) > 200:
        index = np.linspace(0, len(windows) - 1, 200).astype(int)
        windows = [windows[i] for i in index]
    rows = np.moveaxis(cplx, -1, -1).reshape(-1, n)
    sel = np.asarray([i for i, _p in windows], dtype=np.intp)
    selected = rows[sel]
    slices = [
        (j, max(0, peak - 12), min(n, peak + 13))
        for j, (_i, peak) in enumerate(windows)
    ]
    k = np.arange(n, dtype=float)

    def _score(p0, p1):
        ramp = np.exp(1j * np.deg2rad(p0 + p1 * k / max(n - 1, 1)))
        rr = np.real(selected * ramp)
        vals = [_sym_no_sign(rr[j, lo:hi]) for j, lo, hi in slices]
        return 100.0 * float(np.mean(vals))

    best = (-1.0, 0.0, 0.0)
    for p0 in np.arange(0.0, 360.0, 15.0):
        for p1 in (-60, -30, 0, 30, 60):
            s = _score(float(p0), float(p1))
            if s > best[0]:
                best = (s, float(p0), float(p1))
    print(f"纯对称性扫描: 最优 p0={best[1]:.0f}° p1={best[2]:.0f}° score={best[0]:.2f}")
    # 细化
    best2 = best
    for p0 in np.arange(best[1] - 30, best[1] + 31, 5.0):
        for p1 in np.arange(best[2] - 30, best[2] + 31, 5.0):
            s = _score(float(p0) % 360.0, float(p1))
            if s > best2[0]:
                best2 = (s, float(p0) % 360.0, float(p1))
    print(f"细化: p0={best2[1]:.0f}° p1={best2[2]:.0f}° score={best2[0]:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
