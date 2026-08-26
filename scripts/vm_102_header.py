"""sampleK NMRPipe 头核验(0.2.199)。"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    import nmrglue as ng

    work = Path("/home/<lab-user>/Desktop/sampleK.nmrpipe")
    plane = work / "nus3d_rc" / "test0001.ft1"
    ft3 = work / "102.ft3"
    for name, path in (("plane", plane), ("ft3", ft3)):
        dic, data = ng.pipe.read(str(path))
        keys = (
            "FDDIMORDER",
            "FDF1LABEL",
            "FDF2LABEL",
            "FDF3LABEL",
            "FDSLICECOUNT",
            "FDPIPECOUNT",
            "FDDIMCOUNT",
            "FDSIZE",
        )
        print(name, "shape:", data.shape)
        for k in keys:
            if k in dic:
                print(f"  {k}: {dic[k]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
