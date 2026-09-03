"""VM:虚通道平面配对 + 实/虚重构平面相关性。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import nmrglue as ng


def _plane(sub: str, idx: int) -> np.ndarray:
    _d, raw = ng.pipe.read(
        str(Path("/home/<lab-user>/Desktop/data/sampleB.nmrpipe") / sub / f"test{idx:04d}.ft1")
    )
    arr = np.asarray(raw)
    if np.iscomplexobj(arr):
        return arr
    return arr[0::2] + 1j * arr[1::2]


def main() -> int:
    c2 = _plane("nus3d_1_cplx", 2)
    imag = _plane("nus3d_1_imag", 1)
    print("cplx2 == imag(test1)? max diff:", float(np.max(np.abs(c2 - imag))))
    # 实/虚重构平面:FT 沿两间接轴,比较幅度谱相关
    real_r = _plane("nus3d_rc_r300", 1)
    imag_r = _plane("nus3d_rc_imag300", 1)
    fr = np.fft.fft2(real_r)
    fi = np.fft.fft2(imag_r)
    print("recon real vs imag |FT| 相关:", round(float(np.corrcoef(
        np.abs(fr).ravel(), np.abs(fi).ravel())[0, 1]), 4))
    # 相位对齐后相关
    ratio = fi / (fr + 1e-12)
    # 取最强点的相位比
    flat_r = np.abs(fr).ravel()
    top = np.argsort(flat_r)[-20:]
    ph = np.angle(ratio.ravel()[top])
    print("最强20点相位比(rad):", np.round(ph, 2))
    return 0


if __name__ == "__main__":
    main()
