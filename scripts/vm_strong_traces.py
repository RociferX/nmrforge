"""诊断:28.ft3 最强直接维迹线的峰相位与吸收度(判定真实直接维相位)。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from core.optimization.phase_consensus import _hilbert
from core.optimization.phase_search import _row_absorption


def main() -> int:
    import nmrglue as ng

    src = Path("/home/<lab-user>/Desktop/data/sampleB.nmrpipe/28.ft3")
    _dic, arr = ng.pipe.read(str(src))
    arr = np.asarray(arr)
    if np.iscomplexobj(arr):
        arr = arr.real
    flat = arr.reshape(-1, arr.shape[-1])
    peak_mag = np.max(np.abs(flat), axis=-1)
    order = np.argsort(peak_mag)[::-1][:12]
    print("最强 12 条迹线:")
    for row_idx in order:
        row = flat[row_idx]
        z = _hilbert(row)
        mag = np.abs(z)
        pk = int(np.argmax(mag))
        phase = (np.rad2deg(np.angle(z[pk])) % 360.0)
        # 该迹线内前 5 峰的相位(判断正负峰混合)
        top = np.argsort(mag)[::-1][:5]
        phases = [f"{np.rad2deg(np.angle(z[i])) % 360:.0f}" for i in top]
        print(
            f"trace {row_idx} max={peak_mag[row_idx]:.3g} "
            f"pk={pk} phase={phase:.1f} top5 phases={' '.join(phases)}"
        )
    # 最强迹线在不同候选相位下的吸收度
    row0 = flat[order[0]]
    z0 = _hilbert(row0)
    mag0 = np.abs(z0)
    local = np.zeros(mag0.size, dtype=bool)
    local[8:-8] = (mag0[8:-8] >= mag0[7:-9]) & (mag0[8:-8] > mag0[9:-7])
    thr = max(10.0 * np.median(np.abs(mag0 - np.median(mag0))) * 1.4826,
              0.005 * float(np.max(mag0)))
    pos = np.where(local & (mag0 > thr))[0][:8]
    hts = mag0[pos]
    print("最强迹线锁定峰:", pos, "相位:",
          [f"{np.rad2deg(np.angle(z0[i])) % 360:.0f}" for i in pos])
    for cand in ((0.0, 0.0), (309.47, 90.0), (309.47, 0.0), (50.0, 0.0),
                 (230.0, 0.0)):
        a, s = _row_absorption(z0, pos, hts, cand[0], cand[1], radius=1)
        print(f"  最强迹线吸收度 @ {cand}: {a:.3f} sign={s:+.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
