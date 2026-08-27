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


real1 = _read("nus3d_1_r300", 1)
c1 = _read("nus3d_1_cplx", 1)
c2 = _read("nus3d_1_cplx", 2)
print("real1[0,:3]:", real1[0, :3])
print("cplx1[0,:3]:", c1[0, :3])
print("cplx2[0,:3]:", c2[0, :3])
print("real1-cplx1 max:", np.max(np.abs(real1 - c1)))
print("real1.real==cplx1.real?", np.allclose(real1.real, c1.real))
print("real1.imag==cplx1.imag?", np.allclose(real1.imag, c1.imag))
print("real1 vs cplx1 real 平均相对:", np.mean(np.abs(real1.real - c1.real)) / (np.mean(np.abs(real1.real)) + 1e-9))
