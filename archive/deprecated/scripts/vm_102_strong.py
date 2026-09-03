"""诊断:102.ft3 最强直接维迹线的峰相位与吸收度。"""

from __future__ import annotations

import sys

import numpy as np

from core.optimization.phase_consensus import _hilbert
from core.optimization.phase_search import _row_absorption


def main() -> int:
    import nmrglue as ng

    _dic, arr = ng.pipe.read("/home/<lab-user>/Desktop/sampleK.nmrpipe/102.ft3")
    arr = np.asarray(arr)
    if np.iscomplexobj(arr):
        arr = arr.real
    flat = arr.reshape(-1, arr.shape[-1])
    peak_mag = np.max(np.abs(flat), axis=-1)
    order = np.argsort(peak_mag)[::-1][:10]
    phases = []
    for row_idx in order:
        row = flat[row_idx]
        z = _hilbert(row)
        mag = np.abs(z)
        pk = int(np.argmax(mag))
        phase = (np.rad2deg(np.angle(z[pk])) % 360.0)
        phases.append(phase)
        print(f"trace {row_idx} max={peak_mag[row_idx]:.3g} pk={pk} "
              f"phase={phase:.1f}")
    print("最强 10 条峰相位 圆均值:",
          np.rad2deg(np.arctan2(np.mean(np.sin(np.deg2rad(phases))),
                                np.mean(np.cos(np.deg2rad(phases))))) % 360)
    # 最强迹线吸收度对照
    z0 = _hilbert(flat[order[0]])
    mag0 = np.abs(z0)
    local = np.zeros(mag0.size, dtype=bool)
    local[8:-8] = (mag0[8:-8] >= mag0[7:-9]) & (mag0[8:-8] > mag0[9:-7])
    thr = max(10.0 * np.median(np.abs(mag0 - np.median(mag0))) * 1.4826,
              0.005 * float(np.max(mag0)))
    pos = np.where(local & (mag0 > thr))[0][:8]
    hts = mag0[pos]
    print("最强迹线锁定峰:", pos.tolist(), "相位:",
          [f"{np.rad2deg(np.angle(z0[i])) % 360:.0f}" for i in pos])
    for cand in ((0.0, 0.0), (40.0, 0.0), (17.6, 0.0), (197.6, 0.0),
                 (340.0, 0.0)):
        a, s = _row_absorption(z0, pos, hts, cand[0], cand[1], radius=1)
        print(f"  最强迹线吸收度 @ {cand}: {a:.3f} sign={s:+.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
