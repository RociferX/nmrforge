"""诊断:28.ft3 直接维强迹线峰的 HT 相位分布(校正 = -相位)。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from core.optimization.phase_consensus import _hilbert, _lock_trace_peaks
from core.optimization.phase_search import _row_absorption


def main() -> int:
    import nmrglue as ng

    src = Path("/home/<lab-user>/Desktop/data/sampleB.nmrpipe/28.ft3")
    _dic, arr = ng.pipe.read(str(src))
    arr = np.asarray(arr)
    if np.iscomplexobj(arr):
        arr = arr.real
    print("shape:", arr.shape)
    flat = arr.reshape(-1, arr.shape[-1])
    gmax = float(np.max(np.abs(flat)))
    phases: list[float] = []
    scores: dict[tuple[float, float], list[float]] = {}
    for row in flat:
        z = _hilbert(row)
        peaks = _lock_trace_peaks(
            np.abs(z), margin=8, max_peaks=8, global_max=gmax
        )
        if peaks is None:
            continue
        pos, heights = peaks
        vals = z[pos]
        for v in vals:
            phases.append((np.rad2deg(np.angle(v)) % 360.0))
        for cand in ((0.0, 0.0), (309.47, 90.0), (309.47, 0.0), (50.0, 0.0)):
            a, s = _row_absorption(z, pos, heights, cand[0], cand[1], radius=1)
            scores.setdefault(cand, []).append(a)
    ph = np.array(phases)
    print("n peaks:", len(ph))
    print("峰相位(校正=-相位) 中位数:", np.median(ph), "圆均值:",
          np.rad2deg(np.arctan2(np.mean(np.sin(np.deg2rad(ph))),
                                np.mean(np.cos(np.deg2rad(ph))))) % 360)
    print("峰相位直方图(30° bin):",
          np.histogram(ph, bins=12, range=(0, 360))[0].tolist())
    for cand, vals in scores.items():
        print(f"吸收度 @ {cand}: 中位数 {np.median(vals):.3f} "
              f"均值 {np.mean(vals):.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
