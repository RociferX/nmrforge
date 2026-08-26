"""sampleK 终谱最强峰映射回重构堆叠,验证读取与信号形态(0.2.199)。"""

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
    stack = np.stack(arrays, axis=-1)  # (i0, i1, k)
    _dic, data = ng.pipe.read(str(work / "102.ft3"))
    fin = np.asarray(data, dtype=float)
    mag = np.abs(fin)
    t = int(np.argmax(mag))
    i2, i1f, i3 = np.unravel_index(t, mag.shape)
    print(f"终谱最强峰: (F2={i2}, F1={i1f}, F3={i3})")
    # 映射到重构网格(按尺寸比例)
    i0 = int(round(i1f / fin.shape[1] * stack.shape[0]))
    i1 = int(round(i2 / fin.shape[0] * stack.shape[1]))
    print(f"映射重构点: (i0={i0}, i1={i1}, k={i3})")
    # 该点幅度 vs 全局
    v = np.abs(stack[i0, i1, i3])
    print(f"该点|幅|={v:.3g} 全局max={np.abs(stack).max():.3g} "
          f"全局中位={np.median(np.abs(stack)):.3g}")
    # 该点直接迹线(沿 k)
    trace = stack[i0, i1, :]
    j = int(np.argmax(np.abs(trace)))
    ph = np.rad2deg(np.angle(trace))
    print(f"直接迹线峰@k={j} 峰相位={float(ph[j]):.1f}°")
    print(f"迹线峰相位序列(每8点)={[round(float(ph[q]), 1) for q in range(0, trace.size, 8)]}")
    # 该点在平面内 (i0,i1) 邻域是否尖锐
    win = np.abs(stack[max(0, i0 - 3) : i0 + 4, max(0, i1 - 3) : i1 + 4, i3])
    print(f"k={i3} 平面 7×7 邻域 max={win.max():.3g} 中心={win[3, 3]:.3g}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
