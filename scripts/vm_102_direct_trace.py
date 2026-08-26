"""sampleK 直接维迹线核验:沿平面序号取复相位,拟合真实 p0/p1(0.2.199)。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from core.data.pipe_io import read_pipe_complex


def _fit(p0s: np.ndarray, n: int) -> tuple[float, float, float]:
    """加权圆拟合 p0+p1*k/(n-1),返回 (p0, p1, rms)。"""
    k = np.arange(len(p0s), dtype=float)
    best = (1e18, 0.0, 0.0)
    for p0 in np.arange(0.0, 360.0, 5.0):
        for p1 in np.arange(-180.0, 181.0, 5.0):
            model = (p0 + p1 * k / max(n - 1, 1)) % 360.0
            diff = np.abs(((p0s - model + 180.0) % 360.0) - 180.0)
            err = float(np.sum(diff**2))
            if err < best[0]:
                best = (err, p0, p1)
    _, p0b, p1b = best
    best2 = (best[0], p0b, p1b)
    for dp0 in np.arange(-5.0, 5.1, 1.0):
        for dp1 in np.arange(-5.0, 5.1, 1.0):
            pc = (p0b + dp0) % 360.0
            qc = p1b + dp1
            model = (pc + qc * k / max(n - 1, 1)) % 360.0
            diff = np.abs(((p0s - model + 180.0) % 360.0) - 180.0)
            err = float(np.sum(diff**2))
            if err < best2[0]:
                best2 = (err, pc, qc)
    model = (best2[1] + best2[2] * k / max(n - 1, 1)) % 360.0
    diff = np.abs(((p0s - model + 180.0) % 360.0) - 180.0)
    rms = float(np.sqrt(np.mean(diff**2)))
    return best2[1], best2[2], rms


def _sym(win: np.ndarray) -> float:
    win = np.asarray(win, dtype=float)
    if win.size < 2:
        return 0.0
    left = win[: len(win) // 2]
    right = win[len(win) - len(left):][::-1]
    return float(np.mean((left + right) ** 2 / (2 * (left**2 + right**2) + 1e-12)))


def main() -> int:
    work = Path("/home/<lab-user>/Desktop/sampleK.nmrpipe")
    planes = sorted((work / "nus3d_rc").glob("test*.ft1"))
    arrays = [read_pipe_complex(p) for p in planes]
    stack = np.stack(arrays, axis=-1)  # (i0=F1相关, i1=F2, k=直接维平面)
    print("stack shape:", stack.shape, "(平面内轴0, 平面内轴1, 直接维平面)")
    mag = np.abs(stack)
    flat = mag.ravel()
    top = np.argsort(flat)[-8:][::-1]
    for t in top:
        i0, i1, kk = np.unravel_index(t, mag.shape)
        trace = stack[i0, i1, :]
        ph = np.rad2deg(np.angle(trace))
        p0, p1, rms = _fit(ph, len(planes))
        # 施加拟合相位后窗口对称性
        n = len(planes)
        k = np.arange(n, dtype=float)
        rot = trace * np.exp(1j * np.deg2rad(p0 + p1 * k / max(n - 1, 1)))
        re = np.real(rot)
        j = int(np.argmax(np.abs(re)))
        half = min(max(j, 1), 20)
        sym = _sym(re[max(0, j - half) : j + half + 1])
        print(
            f"峰(i0={i0},i1={i1},k={kk}): 拟合 p0={p0:6.1f}° p1={p1:6.1f}° "
            f"RMS={rms:.1f}° 校正后对称={sym:.3f} 峰幅Re={re[j]:.3g}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
