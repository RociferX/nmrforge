"""sampleK 最强峰直接维线型剖面(0.2.199):终谱实部 + 重构复部。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from core.data.pipe_io import read_pipe_complex


def main() -> int:
    import nmrglue as ng

    work = Path("/home/<lab-user>/Desktop/sampleK.nmrpipe")
    _dic, data = ng.pipe.read(str(work / "102.ft3"))
    fin = np.asarray(data, dtype=float)
    mag = np.abs(fin)
    t = int(np.argmax(mag))
    i2, i1f, i3 = np.unravel_index(t, mag.shape)
    prof = fin[i2, i1f, :]
    lo, hi = max(0, i3 - 15), min(168, i3 + 16)
    print(f"终谱最强峰(F2={i2},F1={i1f}) 直接维实部剖面 k={lo}..{hi-1}:")
    print("  k:", list(range(lo, hi)))
    print("  Re:", [round(float(prof[q] / 1e9), 2) for q in range(lo, hi)])
    # 重构堆叠映射点复剖面
    planes = sorted((work / "nus3d_rc").glob("test*.ft1"))
    arrays = [read_pipe_complex(p) for p in planes]
    stack = np.stack(arrays, axis=-1)
    i0 = int(round(i1f / fin.shape[1] * stack.shape[0]))
    i1 = int(round(i2 / fin.shape[0] * stack.shape[1]))
    tr = stack[i0, i1, :]
    print(f"重构映射点(i0={i0},i1={i1}) 复剖面 k={lo}..{hi-1}:")
    print("  Re:", [round(float(tr[q].real / 1e6), 2) for q in range(lo, hi)])
    print("  Im:", [round(float(tr[q].imag / 1e6), 2) for q in range(lo, hi)])
    return 0


if __name__ == "__main__":
    sys.exit(main())
