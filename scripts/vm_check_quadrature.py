"""VM 校验:step1 无 -di 的复型平面,其实部=实通道、虚部=虚通道(±)。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import nmrglue as ng


def main() -> int:
    work = Path("/home/<lab-user>/Desktop/data/sampleB.nmrpipe")

    def _read(sub: str) -> np.ndarray:
        _d, raw = ng.pipe.read(str(work / sub / "test0001.ft1"))
        return np.asarray(raw)

    cplx_raw = _read("nus3d_1_cplx")
    real_raw = _read("nus3d_1")
    imag_raw = _read("nus3d_1_imag")
    print("shapes:", cplx_raw.shape, real_raw.shape, imag_raw.shape)
    # 复型平面:axis0 交错(实/虚)
    if np.iscomplexobj(cplx_raw):
        cplx = cplx_raw
    else:
        cplx = cplx_raw[0::2] + 1j * cplx_raw[1::2]
    # 实/虚通道各为交错实型
    re = real_raw[0::2] + 1j * real_raw[1::2]
    im = imag_raw[0::2] + 1j * imag_raw[1::2]
    # 校验:re ≈ Re(cplx)?im ≈ -Im(cplx)?
    print("re vs Re(cplx) 相对误差:", round(
        float(np.mean(np.abs(re - cplx.real)) / (np.mean(np.abs(cplx.real)) + 1e-9)), 4
    ))
    print("im vs -Im(cplx) 相对误差:", round(
        float(np.mean(np.abs(im - (-cplx.imag))) / (np.mean(np.abs(cplx.imag)) + 1e-9)), 4
    ))
    print("im vs Im(cplx) 相对误差:", round(
        float(np.mean(np.abs(im - cplx.imag)) / (np.mean(np.abs(cplx.imag)) + 1e-9)), 4
    ))
    return 0


if __name__ == "__main__":
    main()
