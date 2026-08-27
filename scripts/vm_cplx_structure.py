"""VM 检查:无 -di 1200 平面结构(交错/分块)+ 与实通道相似度。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import nmrglue as ng


def _read(sub: str, idx: int) -> np.ndarray:
    _d, raw = ng.pipe.read(
        str(Path("/home/<lab-user>/Desktop/data/sampleB.nmrpipe") / sub / f"test{idx:04d}.ft1")
    )
    arr = np.asarray(raw)
    if np.iscomplexobj(arr):
        return arr
    return arr[0::2] + 1j * arr[1::2]


def _corr(a: np.ndarray, b: np.ndarray) -> float:
    aa = np.abs(a).ravel()
    bb = np.abs(b).ravel()
    return float(np.corrcoef(aa, bb)[0, 1])


def main() -> int:
    real1 = _read("nus3d_1_r300", 1)
    c1 = _read("nus3d_1_cplx", 1)
    c2 = _read("nus3d_1_cplx", 2)
    c601 = _read("nus3d_1_cplx", 601)
    c602 = _read("nus3d_1_cplx", 602)
    print("cplx planes shapes:", c1.shape, c2.shape, c601.shape, c602.shape)
    print("real1 vs cplx1  |.| 相关:", round(_corr(real1, c1), 4))
    print("real1 vs cplx2  |.| 相关:", round(_corr(real1, c2), 4))
    print("real1 vs cplx601 |.| 相关:", round(_corr(real1, c601), 4))
    print("real1 vs cplx602 |.| 相关:", round(_corr(real1, c602), 4))
    print("cplx1 vs cplx2   |.| 相关:", round(_corr(c1, c2), 4))
    print("cplx601 vs cplx602 |.| 相关:", round(_corr(c601, c602), 4))
    return 0


if __name__ == "__main__":
    main()
