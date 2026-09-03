"""sampleK nus3d_1(SMILE 前)直接迹线检查(0.2.199)。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from core.data.pipe_io import read_pipe_complex


def main() -> int:
    work = Path("/home/<lab-user>/Desktop/sampleK.nmrpipe")
    planes = sorted((work / "nus3d_1").glob("test*.ft1"))
    print("nus3d_1 plane count:", len(planes))
    arrays = [read_pipe_complex(p) for p in planes]
    stack = np.stack(arrays, axis=-1)
    print("shape:", stack.shape)
    mag = np.abs(stack)
    top = np.argsort(mag.ravel())[-3:][::-1]
    for t in top:
        i0, i1, _k = np.unravel_index(t, mag.shape)
        trace = stack[i0, i1, :]
        n = trace.size
        ph = np.rad2deg(np.angle(trace))
        j = int(np.argmax(np.abs(trace)))
        print(
            f"峰(i0={i0},i1={i1}) 峰@k={j} 峰相位={float(ph[j]):.1f}° "
            f"相位序列(每8点)={[round(float(ph[q]), 1) for q in range(0, n, 8)]}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
