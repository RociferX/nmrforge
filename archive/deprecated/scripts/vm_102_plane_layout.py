"""sampleK nus3d_rc 平面布局核验:哪个轴是直接维(0.2.199)。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from core.data.pipe_io import read_pipe_complex


def main() -> int:
    import nmrglue as ng

    work = Path("/home/<lab-user>/Desktop/sampleK.nmrpipe")
    planes = sorted((work / "nus3d_rc").glob("test*.ft1"))
    print("plane count:", len(planes))
    a = read_pipe_complex(planes[0])
    print("complex shape:", a.shape)
    print("axis0 size:", a.shape[0], "| axis1 size:", a.shape[1])
    _dic, raw = ng.pipe.read(str(planes[0]))
    print("raw real shape:", raw.shape)
    # 终谱直接维点数 = 168(EXT 9-7ppm);哪个轴是 168 就是直接维
    for axis, size in enumerate(a.shape):
        print(f"axis {axis}: {size}  {'← 直接维(168)' if size == 168 else ''}")
    # 直接维沿 axis1 时,取最强峰在该轴的线型相位
    mag = np.abs(a)
    t = int(np.argmax(mag))
    i0, i1 = np.unravel_index(t, mag.shape)
    for axis in (0, 1):
        prof = np.take(a, (i1 if axis == 0 else i0), axis=axis)
        j = int(np.argmax(np.abs(prof)))
        ph = float(np.rad2deg(np.angle(prof[j])))
        print(
            f"峰(k0={i0},k1={i1}) 沿轴{axis} 峰相位={ph:7.1f}° "
            f"k={j}/{a.shape[axis] - 1}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
