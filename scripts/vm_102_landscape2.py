"""sampleK 正确轴(axis=-1,全平面)评分地形扫描(0.2.199-补17)。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from core.data.pipe_io import read_pipe_complex
from core.optimization.phase_search import (
    _signal_peak_windows,
    _symmetry_sign_metric,
)


def main() -> int:
    work = Path("/home/<lab-user>/Desktop/sampleK.nmrpipe")
    planes = sorted((work / "nus3d_rc").glob("test*.ft1"))
    arrays = [read_pipe_complex(p) for p in planes]
    arr = np.stack(arrays, axis=-1)  # (i0, i1, k=直接维)
    n = arr.shape[-1]
    real = np.real(arr)
    windows = _signal_peak_windows(real, axis=-1)
    print("窗数:", len(windows))
    if len(windows) > 200:
        index = np.linspace(0, len(windows) - 1, 200).astype(int)
        windows = [windows[i] for i in index]
    rows = np.moveaxis(arr, -1, -1).reshape(-1, n)
    sel = np.asarray([i for i, _p in windows], dtype=np.intp)
    selected = rows[sel]
    slices = [
        (j, max(0, peak - 12), min(n, peak + 13))
        for j, (_i, peak) in enumerate(windows)
    ]
    k = np.arange(n, dtype=float)
    scores = []
    for p0 in np.arange(0.0, 360.0, 15.0):
        for p1 in (-45, -25, 0, 25, 45):
            ramp = np.exp(1j * np.deg2rad(p0 + p1 * k / max(n - 1, 1)))
            rr = np.real(selected * ramp)
            vals = [_symmetry_sign_metric(rr[j, lo:hi]) for j, lo, hi in slices]
            scores.append((100.0 * float(np.mean(vals)), p0, p1))
    scores.sort(reverse=True)
    print("评分地形 top10 (score, p0, p1):")
    for s in scores[:10]:
        print(f"  {s[0]:7.2f}  p0={s[1]:6.1f}  p1={s[2]:6.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
