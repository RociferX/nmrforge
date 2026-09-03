"""对比 sampleB 与 sampleK 的 nus3d_rc 布局(0.2.199)。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from core.data.pipe_io import read_pipe_complex


def main() -> int:
    import nmrglue as ng

    for name, work in (
        ("sampleB", Path("/home/<lab-user>/Desktop/data/sampleB.nmrpipe")),
        ("sampleC", Path("/home/<lab-user>/Desktop/data/sampleC.nmrpipe")),
        ("sampleK", Path("/home/<lab-user>/Desktop/sampleK.nmrpipe")),
    ):
        plane_dir = work / "nus3d_rc"
        ps = sorted(plane_dir.glob("test*.ft1"))
        a = read_pipe_complex(ps[0])
        dic, raw = ng.pipe.read(str(ps[0]))
        ft3s = sorted((work / ".." / "spectra").glob("*.ft3"))
        print(f"=== {name} ===")
        print(f"  planes={len(ps)} plane_shape={a.shape} raw={raw.shape}")
        for k in ("FDDIMORDER", "FDF1LABEL", "FDF2LABEL", "FDF3LABEL", "FDSIZE", "FDSPECNUM"):
            if k in dic:
                print(f"  {k}={dic[k]}")
        # 终谱形状
        for cand in (work / "spectra", work.parent / "spectra"):
            for ft3 in sorted(cand.glob("*.ft3")):
                _d, data = ng.pipe.read(str(ft3))
                print(f"  ft3 {ft3.name}: {np.asarray(data).shape}")
                break
            else:
                continue
            break
    return 0


if __name__ == "__main__":
    sys.exit(main())
