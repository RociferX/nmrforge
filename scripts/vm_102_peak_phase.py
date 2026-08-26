"""sampleK 终谱最强峰各轴迹线相位核验(0.2.199)。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np


def _profile_stats(prof: np.ndarray) -> dict[str, float]:
    prof = np.asarray(prof)
    real = np.real(prof)
    i = int(np.argmax(np.abs(prof)))
    peak = prof[i]
    ph = float(np.rad2deg(np.angle(peak)))
    n = real.size
    half = max(n // 2, 1)
    lo, hi = max(0, i - half), min(n, i + half + 1)
    win = real[lo:hi]
    if len(win) >= 2:
        left = win[: len(win) // 2]
        right = win[len(win) - len(left):][::-1]
        sym = float(np.mean((left + right) ** 2 / (2 * (left**2 + right**2) + 1e-12)))
    else:
        sym = 0.0
    return {"idx": i, "phase_deg": ph, "sym": sym, "net": float(win.sum())}


def main() -> int:
    import nmrglue as ng

    work = Path("/home/<lab-user>/Desktop/sampleK.nmrpipe")
    ft3 = work / "102.ft3"
    dic, data = ng.pipe.read(str(ft3))
    arr = np.asarray(data)
    print("终谱 shape:", arr.shape, "dtype:", arr.dtype)
    mag = np.abs(arr)
    flat = mag.ravel()
    top = np.argsort(flat)[-6:][::-1]
    print("最强 6 峰各轴迹线(直接维应表现为高对称吸收峰):")
    for t in top:
        idx = np.unravel_index(t, mag.shape)
        parts = []
        for axis in range(3):
            s = _profile_stats(np.take(arr, idx[axis], axis=axis))
            parts.append(
                f"轴{axis}:相位={s['phase_deg']:6.1f}° "
                f"对称={s['sym']:.3f} 净Re={s['net']:.2g}"
            )
        print(f"  峰{idx} | " + " | ".join(parts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
