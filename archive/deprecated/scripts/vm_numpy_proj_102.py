"""102.ft3 numpy 投影路径(等价 proj3D -sum):直接维 = 最后一轴。"""

from __future__ import annotations

import sys

import numpy as np

from core.optimization.phase_consensus import search_direct_phase_real_ht


def main() -> int:
    import nmrglue as ng

    src = "/home/<lab-user>/Desktop/sampleK.nmrpipe/102.ft3"
    _dic, arr = ng.pipe.read(src)
    arr = np.asarray(arr)
    if np.iscomplexobj(arr):
        arr = arr.real
    print("102.ft3 shape:", arr.shape)
    for mode in ("uniform", "mixed"):
        est = search_direct_phase_real_ht(arr, axis=-1, sign_mode=mode)
        print(f"sign_mode={mode}:", est)
    print("参考(phase.json): (40, 0)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
