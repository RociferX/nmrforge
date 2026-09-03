"""sampleK 布局定案:终谱直接维迹线 ↔ 重构堆叠逐点相关(0.2.199)。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from core.data.pipe_io import read_pipe_complex


def main() -> int:
    import nmrglue as ng

    work = Path("/home/<lab-user>/Desktop/sampleK.nmrpipe")
    planes = sorted((work / "nus3d_rc").glob("test*.ft1"))
    arrays = [read_pipe_complex(p) for p in planes]
    stack = np.stack(arrays, axis=-1)  # (i0, i1, 直接维平面)
    _dic, data = ng.pipe.read(str(work / "102.ft3"))
    fin = np.asarray(data, dtype=float)
    mag = np.abs(fin)
    t = int(np.argmax(mag))
    i2, i1f, i3 = np.unravel_index(t, mag.shape)
    trace = fin[i2, i1f, :].astype(float)
    print(
        f"终谱最强峰 (F2={i2},F1={i1f},F3={i3}),直接维迹线 168 点,"
        f"峰在 F3={int(np.argmax(np.abs(trace)))}"
    )
    # 在重构堆叠里找与 trace 最相关的 (i0, i1)
    tr = trace - trace.mean()
    trn = np.linalg.norm(tr)
    best = []
    for i0 in range(0, stack.shape[0], 4):
        for i1 in range(0, stack.shape[1], 3):
            v = np.real(stack[i0, i1, :])
            v = v - v.mean()
            nv = np.linalg.norm(v)
            if nv < 1e-9:
                continue
            c = float(np.dot(tr, v) / (trn * nv))
            best.append((c, i0, i1))
    best.sort(reverse=True)
    print("与终谱直接迹线最相关的重构点 top5 (corr, i0, i1):")
    for c, i0, i1 in best[:5]:
        v = np.real(stack[i0, i1, :])
        print(
            f"  corr={c:.3f} i0={i0} i1={i1} "
            f"重构峰在 k={int(np.argmax(np.abs(v)))}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
