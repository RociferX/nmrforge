"""VM 检查单文件 aq2D fid 布局(0.2.199-补25,不跑 SMILE)。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np


def main() -> int:
    import nmrglue as ng

    path = Path("/home/<lab-user>/Desktop/sampleK.nmrpipe/102.fid")
    dic, data = ng.pipe.read(str(path))
    arr = np.asarray(data)
    print("shape:", arr.shape, "dtype:", arr.dtype)
    print("FDSIZE:", dic.get("FDSIZE"), "FDSPECNUM:", dic.get("FDSPECNUM"))
    print("FDREALSIZE:", dic.get("FDREALSIZE"), "FDDIMORDER:", dic.get("FDDIMORDER"))
    print("FDF1TDSIZE:", dic.get("FDF1TDSIZE"), "FDF2TDSIZE:", dic.get("FDF2TDSIZE"),
          "FDF3TDSIZE:", dic.get("FDF3TDSIZE"))
    print("FDFILECOUNT:", dic.get("FDFILECOUNT"))
    # 检查某行的实/虚交错位置:直接维 F3 应沿最后一轴,实虚交错?
    print("arr[0,:6]:", arr[0, :6])
    print("arr[0,-6:]:", arr[0, -6:])
    return 0


if __name__ == "__main__":
    sys.exit(main())
