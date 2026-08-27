"""VM 对比:实部/虚部两条 SMILE 通道的重构平面间接峰位。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import nmrglue as ng


def _peaks(mag: np.ndarray, n_top: int = 10) -> list[tuple[int, float]]:
    interior = np.zeros(mag.size, dtype=bool)
    interior[1:-1] = (mag[1:-1] >= mag[:-2]) & (mag[1:-1] > mag[2:])
    idx = np.where(interior)[0]
    if not idx.size:
        return []
    order = np.argsort(mag[idx])[::-1][:n_top]
    return [(int(idx[i]), float(mag[idx[i]])) for i in order]


def main() -> int:
    work = Path("/home/<lab-user>/Desktop/data/sampleB.nmrpipe")
    out = []
    for sub in ("nus3d_rc", "nus3d_rc_imag"):
        f = work / sub / "test0001.ft1"
        if not f.is_file():
            print(sub, "MISSING")
            continue
        dic, raw = ng.pipe.read(str(f))
        arr = np.asarray(raw)
        c = arr[0::2] + 1j * arr[1::2]  # 平面 (F1 时, F2 时)
        # 沿两轴 FT,取投影峰位
        mag0 = np.abs(np.fft.fft(c, axis=0)).sum(axis=1)
        mag1 = np.abs(np.fft.fft(c, axis=1)).sum(axis=0)
        pk0 = _peaks(mag0)
        pk1 = _peaks(mag1)
        out.append((sub, c.shape, pk0, pk1))
        print(f"{sub}: shape={c.shape}")
        print(f"  F1(13C) FT 峰位: {pk0[:6]}")
        print(f"  F2(15N) FT 峰位: {pk1[:6]}")
    if len(out) == 2:
        (_, _s, a0, a1), (_, _s2, b0, b1) = out
        pos_a = sorted(p[0] for p in a0[:5])
        pos_b = sorted(p[0] for p in b0[:5])
        print("F1 峰位(实 vs 虚):", pos_a, "vs", pos_b)
        pos_a1 = sorted(p[0] for p in a1[:5])
        pos_b1 = sorted(p[0] for p in b1[:5])
        print("F2 峰位(实 vs 虚):", pos_a1, "vs", pos_b1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
