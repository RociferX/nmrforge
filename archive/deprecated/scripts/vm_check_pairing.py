"""VM 校验:无 -di 的 1200 平面(Re/Im 交错)与实/虚通道的配对。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import nmrglue as ng


def _complex(sub: str, idx: int) -> np.ndarray:
    _d, raw = ng.pipe.read(
        str(Path("/home/<lab-user>/Desktop/data/sampleB.nmrpipe") / sub / f"test{idx:04d}.ft1")
    )
    arr = np.asarray(raw)
    if np.iscomplexobj(arr):
        return arr
    return arr[0::2] + 1j * arr[1::2]


def _rel(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.mean(np.abs(a - b)) / (np.mean(np.abs(b)) + 1e-9))


def main() -> int:
    c1 = _complex("nus3d_1_cplx", 1)
    c2 = _complex("nus3d_1_cplx", 2)
    real = _complex("nus3d_1_r300", 1)
    imag = _complex("nus3d_1_imag", 1)
    print("cplx1.real vs real:", round(_rel(c1.real, real), 4))
    print("cplx1.imag vs -imag:", round(_rel(c1.imag, -imag), 4))
    print("cplx2.real vs -imag:", round(_rel(c2.real, -imag), 4))
    print("cplx2.imag vs real:", round(_rel(c2.imag, real), 4))
    return 0


if __name__ == "__main__":
    main()
