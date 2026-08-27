"""检查直接迹线是否为真复型(-di 是否删掉了直接维虚部)(0.2.199)。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from core.data.pipe_io import read_pipe_complex


def main() -> int:
    work = Path("/home/<lab-user>/Desktop/sampleK.nmrpipe")
    for name in ("nus3d_1", "nus3d_rc"):
        planes = sorted((work / name).glob("test*.ft1"))
        arrays = [read_pipe_complex(p) for p in planes[:168]]
        stack = np.stack(arrays, axis=-1)
        # 最强迹线(幅度最大)
        mag = np.abs(stack)
        t = int(np.argmax(mag))
        i0, i1, _k = np.unravel_index(t, mag.shape)
        trace = stack[i0, i1, :]
        ph = np.rad2deg(np.angle(trace))
        # 如果虚部被删(实投影),相位应只在 0/180 附近
        frac_0_180 = float(
            np.mean(
                np.minimum(np.abs(ph), np.abs(180.0 - np.abs(ph))) < 5.0
            )
        )
        print(
            f"{name}: 最强迹线(i0={i0},i1={i1}) 峰相位={float(ph[np.argmax(np.abs(trace))]):.1f}° "
            f"相位∈{{0,180}}±5°比例={frac_0_180:.2f} "
            f"imag/real 能量比={float(np.abs(trace.imag).sum() / max(np.abs(trace.real).sum(), 1e-9)):.2f}"
        )
        print(
            f"  相位序列(每8点)={[round(float(ph[q]), 1) for q in range(0, trace.size, 8)]}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
