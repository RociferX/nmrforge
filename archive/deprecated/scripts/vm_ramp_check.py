"""诊断:最强迹线 top5 峰的位置 vs 相位——直接维 p1 斜坡是否可辨。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from core.optimization.phase_consensus import _hilbert


def main() -> int:
    import nmrglue as ng

    src = Path("/home/<lab-user>/Desktop/data/sampleB.nmrpipe/28.ft3")
    _dic, arr = ng.pipe.read(str(src))
    arr = np.asarray(arr)
    if np.iscomplexobj(arr):
        arr = arr.real
    flat = arr.reshape(-1, arr.shape[-1])
    peak_mag = np.max(np.abs(flat), axis=-1)
    order = np.argsort(peak_mag)[::-1][:6]
    for row_idx in order:
        row = flat[row_idx]
        z = _hilbert(row)
        mag = np.abs(z)
        top = np.argsort(mag)[::-1][:8]
        info = []
        for i in top:
            ph = (np.rad2deg(np.angle(z[i])) % 360.0)
            info.append(f"k={i} ph={ph:.0f}")
        print(f"trace {row_idx} max={peak_mag[row_idx]:.3g}: " + " | ".join(info))
    return 0


if __name__ == "__main__":
    sys.exit(main())
