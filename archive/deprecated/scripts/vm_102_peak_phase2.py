"""sampleK 直接维相位核验 v2:终谱实数线型对称性 + 重构平面复相位。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from core.data.pipe_io import read_pipe_complex


def _sym(win: np.ndarray) -> float:
    win = np.asarray(win, dtype=float)
    if win.size < 2:
        return 0.0
    left = win[: len(win) // 2]
    right = win[len(win) - len(left):][::-1]
    return float(np.mean((left + right) ** 2 / (2 * (left**2 + right**2) + 1e-12)))


def main() -> int:
    import nmrglue as ng

    work = Path("/home/<lab-user>/Desktop/sampleK.nmrpipe")
    # 1) 终谱(实数):直接维轴 2,最强峰线型对称性
    ft3 = work / "102.ft3"
    _dic, data = ng.pipe.read(str(ft3))
    arr = np.asarray(data, dtype=float)
    print("终谱 shape:", arr.shape, "(F2,F1,F3=直接维)")
    mag = np.abs(arr)
    flat = mag.ravel()
    top = np.argsort(flat)[-6:][::-1]
    print("终谱最强 6 峰:直接维(F3)线型对称性(1=吸收,0=色散):")
    for t in top:
        i2, i1, i3 = np.unravel_index(t, mag.shape)
        prof = arr[i2, i1, :]
        i = int(np.argmax(np.abs(prof)))
        half = min(max(i, 1), 24)
        win = prof[max(0, i - half) : i + half + 1]
        print(
            f"  峰(F2={i2},F1={i1},F3={i3}) 峰幅={prof[i]:.3g} "
            f"对称={_sym(win):.3f}"
        )
    # 2) 重构平面(复型):最强峰直接维复相位 → 真实 (p0+p1·k/(n-1))
    planes = sorted((work / "nus3d_rc").glob("test*.ft1"))
    if len(planes) > 8:
        index = np.linspace(0, len(planes) - 1, 8).astype(int)
        planes = [planes[i] for i in index]
    print("\n重构平面最强峰:直接维(轴0)峰相位:")
    for path in planes[:6]:
        a = read_pipe_complex(path)
        mag2 = np.abs(a)
        t = int(np.argmax(mag2))
        i0, i1 = np.unravel_index(t, mag2.shape)
        ph = float(np.rad2deg(np.angle(a[i0, i1])))
        prof = np.real(a[:, i1])
        half = min(max(i0, 1), 24)
        win = prof[max(0, i0 - half) : i0 + half + 1]
        print(
            f"  {path.name}: 峰(k={i0}) 相位={ph:7.1f}° "
            f"对称={_sym(win):.3f} (k/(n-1)={i0 / max(a.shape[0] - 1, 1):.3f})"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
