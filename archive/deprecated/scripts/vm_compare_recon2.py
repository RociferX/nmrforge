"""VM 对比:同迭代(50)实部 vs 虚部 SMILE 重构平面峰位。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import nmrglue as ng


def _peaks(mag: np.ndarray, n_top: int = 8) -> list[tuple[int, float]]:
    interior = np.zeros(mag.size, dtype=bool)
    interior[1:-1] = (mag[1:-1] >= mag[:-2]) & (mag[1:-1] > mag[2:])
    idx = np.where(interior)[0]
    if not idx.size:
        return []
    order = np.argsort(mag[idx])[::-1][:n_top]
    return [(int(idx[i]), float(mag[idx[i]])) for i in order]


def main() -> int:
    work = Path("/home/<lab-user>/Desktop/data/sampleB.nmrpipe")
    data = {}
    for sub in ("nus3d_rc_r300", "nus3d_rc_imag300"):
        f = work / sub / "test0001.ft1"
        _d, raw = ng.pipe.read(str(f))
        c = np.asarray(raw)[0::2] + 1j * np.asarray(raw)[1::2]
        mag0 = np.abs(np.fft.fft(c, axis=0)).sum(axis=1)
        mag1 = np.abs(np.fft.fft(c, axis=1)).sum(axis=0)
        data[sub] = (_peaks(mag0), _peaks(mag1))
        print(f"{sub}: F1 峰位 {data[sub][0][:5]}  F2 峰位 {data[sub][1][:5]}")
    a = sorted(p[0] for p in data["nus3d_rc_r50"][0][:5])
    b = sorted(p[0] for p in data["nus3d_rc_imag"][0][:5])
    a1 = sorted(p[0] for p in data["nus3d_rc_r50"][1][:5])
    b1 = sorted(p[0] for p in data["nus3d_rc_imag"][1][:5])
    print("F1 实vs虚:", a, "vs", b)
    print("F2 实vs虚:", a1, "vs", b1)
    return 0


if __name__ == "__main__":
    main()
