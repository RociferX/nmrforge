"""VM 检查:28_ht.ft3 复型结构 + 直接维相位搜索。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import nmrglue as ng

from core.optimization.phase_search import search_direct_phase_on_spectrum


def main() -> int:
    p = Path("/home/<lab-user>/Desktop/data/sampleB.nmrpipe/28_ht.ft3")
    dic, raw = ng.pipe.read(str(p))
    arr = np.asarray(raw)
    print("shape:", arr.shape, "complex:", np.iscomplexobj(raw))
    if np.iscomplexobj(arr):
        cplx = arr.astype(np.complex128)
    else:
        cplx = arr[0::2] + 1j * arr[1::2]
        if cplx.ndim >= 2:
            cplx = cplx[:, 0::2] + 1j * cplx[:, 1::2]
    print("cplx shape:", cplx.shape)
    print("直接维复部占比(轴2):", round(float(
        np.mean(np.abs(cplx.imag)) / (np.mean(np.abs(cplx)) + 1e-9)), 4))
    est = search_direct_phase_on_spectrum(
        cplx, axis=-1, metric="symmetry", sign_mode="uniform"
    )
    print("直接维(HT复型)搜索:", est)
    return 0


if __name__ == "__main__":
    sys.exit(main())
