"""sampleK 幅度窗评分地形:用最强幅度窗而非逐行 MAD(0.2.199)。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from core.data.pipe_io import read_pipe_complex
from core.optimization.phase_search import _symmetry_sign_metric


def main() -> int:
    work = Path("/home/<lab-user>/Desktop/sampleK.nmrpipe")
    planes = sorted((work / "nus3d_rc").glob("test*.ft1"))
    arrays = [read_pipe_complex(p) for p in planes]
    arr = np.stack(arrays, axis=-1)
    n = arr.shape[-1]
    mag = np.abs(arr)
    rows = np.moveaxis(arr, -1, -1).reshape(-1, n)
    rowmag = np.max(mag.reshape(-1, n), axis=-1)
    top = np.argsort(rowmag)[::-1][:120]  # 最强 120 个直接迹线
    selected = rows[top]
    peaks = np.argmax(mag.reshape(-1, n)[top], axis=-1)
    slices = [
        (j, max(0, int(pk) - 12), min(n, int(pk) + 13))
        for j, pk in enumerate(peaks)
    ]
    k = np.arange(n, dtype=float)
    scores = []
    for p0 in np.arange(0.0, 360.0, 10.0):
        for p1 in (-60, -30, 0, 30, 60):
            ramp = np.exp(1j * np.deg2rad(p0 + p1 * k / max(n - 1, 1)))
            rr = np.real(selected * ramp)
            vals = [_symmetry_sign_metric(rr[j, lo:hi]) for j, lo, hi in slices]
            scores.append((100.0 * float(np.mean(vals)), p0, p1))
    scores.sort(reverse=True)
    print("幅度窗评分地形 top10 (score, p0, p1):")
    for s in scores[:10]:
        print(f"  {s[0]:7.2f}  p0={s[1]:6.1f}  p1={s[2]:6.1f}")
    # 各窗在 (0,0) 下的对称性分布
    ramp0 = np.exp(1j * np.deg2rad(0.0 + 0.0 * k / max(n - 1, 1)))
    rr0 = np.real(selected * ramp0)
    per = [_symmetry_sign_metric(rr0[j, lo:hi]) for j, lo, hi in slices]
    print(f"窗数={len(per)} (0,0)下均值={np.mean(per):.3f} "
          f"高分窗(>0.8)={sum(1 for x in per if x > 0.8)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
