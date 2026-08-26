"""sampleK uniform 模式:反号窗排除(×0)vs 降权(×0.2)(0.2.199-补20)。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from core.optimization.phase_search import _signal_peak_windows
from workflow.phase_routes import _read_complex_ft3


def _sym(f):
    n = f.size
    if n < 2:
        return 0.0
    half = n // 2
    left = f[:half]
    right = f[n - half:][::-1]
    denom = 2.0 * (left**2 + right**2) + 1e-12
    return float(np.mean((left + right) ** 2 / denom))


def main() -> int:
    cplx = _read_complex_ft3(
        Path("/home/<lab-user>/Desktop/sampleK.nmrpipe/102_direct_preview.ft3")
    )
    n = cplx.shape[-1]
    real = np.real(cplx)
    windows = _signal_peak_windows(real, axis=-1)
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

    def _score(p0, p1, factor):
        ramp = np.exp(1j * np.deg2rad(p0 + p1 * k / max(n - 1, 1)))
        rr = np.real(selected * ramp)
        vals = []
        for j, lo, hi in slices:
            win = rr[j, lo:hi]
            s = _sym(win)
            if float(np.sum(win)) >= 0.0:
                vals.append(s)
            else:
                vals.append(s * factor)
        return 100.0 * float(np.mean(vals))

    for factor in (0.2, 0.0):
        best = (-1.0, 0.0, 0.0)
        for p0 in np.arange(0.0, 360.0, 15.0):
            for p1 in (-60, -30, 0, 30, 60):
                s = _score(float(p0), float(p1), factor)
                if s > best[0]:
                    best = (s, float(p0), float(p1))
        print(
            f"反号窗 factor={factor}: 最优 p0={best[1]:.0f}° p1={best[2]:.0f}° "
            f"score={best[0]:.2f}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
