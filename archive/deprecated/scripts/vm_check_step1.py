"""VM 检查:step1 实部/虚部通道平面幅度一致性(90° 旋转应保模)。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import nmrglue as ng


def main() -> int:
    work = Path("/home/<lab-user>/Desktop/data/sampleB.nmrpipe")
    vals = {}
    for sub in ("nus3d_1", "nus3d_1_imag"):
        f = work / sub / "test0001.ft1"
        _d, raw = ng.pipe.read(str(f))
        arr = np.asarray(raw)
        c = arr[0::2] + 1j * arr[1::2]  # (F1 时, F2 时) 复型(近似)
        vals[sub] = (arr.shape, float(np.mean(np.abs(c))), float(np.max(np.abs(c))))
        print(sub, "shape", arr.shape, "mean|.|", round(vals[sub][1], 2),
              "max|.|", round(vals[sub][2], 2))
    (sa, ma, mxa), (sb, mb, mxb) = vals["nus3d_1"], vals["nus3d_1_imag"]
    print("幅度比 imag/real:", round(mb / ma, 4), round(mxb / mxa, 4))
    return 0


if __name__ == "__main__":
    main()
