"""sampleK 候选相位对比:(40,105) vs (40,0) vs (80,55)(0.2.199)。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from workflow.phase_routes import _read_complex_ft3


def _sym(win):
    win = np.asarray(win, dtype=float)
    left = win[: len(win) // 2]
    right = win[len(win) - len(left):][::-1]
    return float(np.mean((left + right) ** 2 / (2 * (left**2 + right**2) + 1e-12)))


def main() -> int:
    cplx = _read_complex_ft3(
        Path("/home/<lab-user>/Desktop/sampleK.nmrpipe/102_direct_preview.ft3")
    )
    n = cplx.shape[-1]
    k = np.arange(n, dtype=float)
    mag = np.abs(cplx)
    top = np.argsort(mag.ravel())[-6:][::-1]
    pts = []
    for t in top:
        i0, i1, _ = np.unravel_index(t, mag.shape)
        pts.append((i0, i1, int(np.argmax(np.abs(cplx[i0, i1, :])))))
    for p0, p1 in ((0.0, 0.0), (40.0, 105.0), (40.0, 0.0), (80.0, 55.0)):
        syms = []
        for i0, i1, j in pts:
            trace = cplx[i0, i1, :]
            rot = trace * np.exp(1j * np.deg2rad(p0 + p1 * k / max(n - 1, 1)))
            re = np.real(rot)
            win = re[max(0, j - 12) : j + 12 + 1]
            syms.append(_sym(win))
        print(f"({p0:5.0f}°, {p1:5.0f}°): 6 峰对称均值={np.mean(syms):.3f} "
              f"每峰={[round(s, 2) for s in syms]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
