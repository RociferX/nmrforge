"""sampleK (80°,55°) 相位验证:最强峰线型(0.2.199)。"""

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
    for p0, p1 in ((0.0, 0.0), (80.0, 55.0)):
        print(f"=== (p0={p0:.0f}°, p1={p1:.0f}°) ===")
        for t in top:
            i0, i1, _ = np.unravel_index(t, mag.shape)
            trace = cplx[i0, i1, :]
            j = int(np.argmax(np.abs(trace)))
            half = 12
            rot = trace * np.exp(1j * np.deg2rad(p0 + p1 * k / max(n - 1, 1)))
            re = np.real(rot)
            win = re[max(0, j - half) : j + half + 1]
            print(
                f"  峰(i0={i0},i1={i1}) k={j}: 对称={_sym(win):.3f} "
                f"峰幅Re={re[j]:.3g} 净Re={win.sum():.3g}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
