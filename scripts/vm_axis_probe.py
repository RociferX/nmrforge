"""VM 轻量检查:sampleB nus3d_rc 平面轴含义实证(拆包轴 + FT 峰位 vs SMILE 预测)。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import nmrglue as ng


def _peaks(mag: np.ndarray, n_top: int = 8) -> list[tuple[int, float]]:
    n = mag.size
    if n < 8:
        return []
    interior = np.zeros(n, dtype=bool)
    interior[1:-1] = (mag[1:-1] >= mag[:-2]) & (mag[1:-1] > mag[2:])
    idx = np.where(interior)[0]
    if not idx.size:
        return []
    order = np.argsort(mag[idx])[::-1][:n_top]
    return [(int(idx[i]), float(mag[idx[i]])) for i in order]


def main() -> int:
    work = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    f = work / "nus3d_rc" / "test0001.ft1"
    dic, raw = ng.pipe.read(str(f))
    arr = np.asarray(raw)
    print("raw shape", arr.shape)
    # 拆包轴 0:(150, 30) 复型
    c0 = arr[0::2] + 1j * arr[1::2]
    print("unpack axis0 ->", c0.shape)
    # 拆包轴 1:(300, 15) 复型
    c1 = arr[:, 0::2] + 1j * arr[:, 1::2]
    print("unpack axis1 ->", c1.shape)
    for label, c in (("axis0", c0), ("axis1", c1)):
        if c.shape[0] < 8:
            continue
        f0 = np.abs(np.fft.fft(c, axis=0))
        f1 = np.abs(np.fft.fft(c, axis=1)) if c.shape[1] >= 8 else None
        mag0 = f0.sum(axis=1) if c.ndim == 2 else f0
        mag1 = f1.sum(axis=0) if f1 is not None else None
        print(f"[{label}] FT axis0 peaks (n={c.shape[0]}):", _peaks(mag0))
        if mag1 is not None:
            print(f"[{label}] FT axis1 peaks (n={c.shape[1]}):", _peaks(mag1))

    return 0


if __name__ == "__main__":
    sys.exit(main())
