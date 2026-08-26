"""sampleK 终谱直接维迹线最优相位扫描(0.2.199 基准核验)。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np


def _sym_abs(win: np.ndarray) -> float:
    win = np.asarray(win, dtype=float)
    if win.size < 2:
        return 0.0
    left = win[: len(win) // 2]
    right = win[len(win) - len(left):][::-1]
    s = float(np.mean((left + right) ** 2 / (2 * (left**2 + right**2) + 1e-12)))
    return s if float(win.sum()) >= 0.0 else s * 0.05


def _best_phase(prof: np.ndarray) -> tuple[float, float, float, float]:
    """扫描 p0/p1 使迹线实部吸收对称性最大,返回 (p0,p1,score,raw_sym_at_0)。"""
    prof = np.asarray(prof, dtype=np.complex128)
    n = prof.size
    k = np.arange(n, dtype=float)
    j = int(np.argmax(np.abs(prof)))
    half = min(max(j, 1), 20)
    best = (-1.0, 0.0, 0.0)
    raw = 0.0
    for p0 in np.arange(0.0, 360.0, 15.0):
        for p1 in np.arange(-180.0, 181.0, 15.0):
            rot = prof * np.exp(1j * np.deg2rad(p0 + p1 * k / max(n - 1, 1)))
            re = np.real(rot)
            win = re[max(0, j - half) : j + half + 1]
            s = _sym_abs(win)
            if p0 == 0.0 and p1 == 0.0:
                raw = s
            if s > best[0]:
                best = (s, p0, p1)
    return best[1], best[2], best[0], raw


def main() -> int:
    import nmrglue as ng

    work = Path("/home/<lab-user>/Desktop/sampleK.nmrpipe")
    _dic, data = ng.pipe.read(str(work / "102.ft3"))
    arr = np.asarray(data, dtype=np.complex128)
    mag = np.abs(arr)
    top = np.argsort(mag.ravel())[-6:][::-1]
    print("终谱最强峰直接维迹线最优相位(基准):")
    for t in top:
        i2, i1, i3 = np.unravel_index(t, mag.shape)
        prof = arr[i2, i1, :]
        p0, p1, s, raw = _best_phase(prof)
        print(
            f"  峰(F2={i2},F1={i1}): 最优 p0={p0:6.1f}° p1={p1:6.1f}° "
            f"score={s:.3f} | p0=0时的score={raw:.3f}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
