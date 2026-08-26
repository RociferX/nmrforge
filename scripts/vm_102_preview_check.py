"""真实复型频域预览文件的直接维检查(0.2.199-补18)。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from workflow.phase_routes import _read_complex_ft3


def _sym(win):
    win = np.asarray(win, dtype=float)
    if win.size < 2:
        return 0.0
    left = win[: len(win) // 2]
    right = win[len(win) - len(left):][::-1]
    return float(np.mean((left + right) ** 2 / (2 * (left**2 + right**2) + 1e-12)))


def main() -> int:
    cplx = _read_complex_ft3(
        Path("/home/<lab-user>/Desktop/sampleK.nmrpipe/102_direct_preview.ft3")
    )
    print("complex preview:", cplx.shape)
    mag = np.abs(cplx)
    flat = mag.ravel()
    top = np.argsort(flat)[-6:][::-1]
    n = cplx.shape[-1]
    k = np.arange(n, dtype=float)
    print("最强 6 峰直接迹线(线中心窗心扫描):")
    for t in top:
        i0, i1, _ = np.unravel_index(t, mag.shape)
        trace = cplx[i0, i1, :]
        j = int(np.argmax(np.abs(trace)))
        half = 12
        best = (-1.0, 0.0, 0.0)
        for p0 in np.arange(0.0, 360.0, 10.0):
            for p1 in np.arange(-180.0, 181.0, 30.0):
                rot = trace * np.exp(1j * np.deg2rad(p0 + p1 * k / max(n - 1, 1)))
                re = np.real(rot)
                win = re[max(0, j - half) : j + half + 1]
                s = _sym(win)
                if s > best[0]:
                    best = (s, float(p0), float(p1))
        print(
            f"  峰(i0={i0},i1={i1}) k={j} | 最优 p0={best[1]:.0f}° p1={best[2]:.0f}° "
            f"对称={best[0]:.3f}"
        )
    # 40 强峰一致性
    top40 = np.argsort(flat)[-40:][::-1]
    ks, phs = [], []
    for t in top40:
        i0, i1, _ = np.unravel_index(t, mag.shape)
        trace = cplx[i0, i1, :]
        j = int(np.argmax(np.abs(trace)))
        if j < 8 or j > n - 8:
            continue
        ks.append(j)
        phs.append(float(np.rad2deg(np.angle(trace[j])) % 180.0))
    ks = np.array(ks, dtype=float)
    phs = np.array(phs, dtype=float)
    best = (1e18, 0.0, 0.0)
    for p0 in np.arange(0.0, 180.0, 3.0):
        for p1 in np.arange(-360.0, 361.0, 6.0):
            model = (p0 + p1 * ks / max(n - 1, 1)) % 180.0
            diff = np.abs(((phs - model + 90.0) % 180.0) - 90.0)
            err = float(np.sum(diff**2))
            if err < best[0]:
                best = (err, p0, p1)
    model = (best[1] + best[2] * ks / max(n - 1, 1)) % 180.0
    diff = np.abs(((phs - model + 90.0) % 180.0) - 90.0)
    print(
        f"40 强峰一致性(±180折叠): p0={best[1]:.0f}° p1={best[2]:.0f}° "
        f"RMS={np.sqrt(np.mean(diff**2)):.1f}°"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
