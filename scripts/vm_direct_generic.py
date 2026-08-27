"""通用:任意 .ft3 终谱跑直接维投影+HT 搜索。用法: <ft3路径>"""

from __future__ import annotations

import sys

import numpy as np

from core.optimization.phase_consensus import search_direct_phase_real_ht


def main() -> int:
    import nmrglue as ng

    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    path = sys.argv[1]
    _dic, arr = ng.pipe.read(path)
    arr = np.asarray(arr)
    if np.iscomplexobj(arr):
        arr = arr.real
    print(f"{path} shape: {arr.shape}")
    for mode in ("uniform", "mixed"):
        est = search_direct_phase_real_ht(arr, axis=-1, sign_mode=mode)
        print(f"  sign_mode={mode}: {est}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
