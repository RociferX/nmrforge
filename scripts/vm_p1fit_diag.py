"""诊断:102.ft3 逐条 p1 集中度拟合分布。"""

from __future__ import annotations

import sys

import numpy as np

from core.optimization.phase_consensus import _hilbert, _lock_trace_peaks
from core.optimization.phase_search import _row_p1_fit


def main() -> int:
    import nmrglue as ng

    _dic, arr = ng.pipe.read("/home/<lab-user>/Desktop/sampleK.nmrpipe/102.ft3")
    arr = np.asarray(arr)
    if np.iscomplexobj(arr):
        arr = arr.real
    flat = arr.reshape(-1, arr.shape[-1])
    gmax = float(np.max(np.abs(flat)))
    p1s: list[float] = []
    concs: list[float] = []
    npeaks: list[int] = []
    for row in flat:
        z = _hilbert(row)
        peaks = _lock_trace_peaks(np.abs(z), margin=8, max_peaks=8, global_max=gmax)
        if peaks is None or peaks[0].size < 2:
            continue
        fit = _row_p1_fit(z, peaks[0], peaks[1])
        if fit is None:
            continue
        p1s.append(fit[0])
        concs.append(fit[1])
        npeaks.append(peaks[0].size)
    p1s = np.array(p1s)
    concs = np.array(concs)
    print("n fits:", len(p1s))
    print("p1_sig median:", np.median(p1s), "hist:",
          np.histogram(p1s, bins=9, range=(-90, 90))[0].tolist())
    print("concentration median:", np.median(concs),
          "mean:", np.mean(concs))
    # 各 p1 桶的平均浓度
    edges = np.arange(-90, 91, 20)
    idx = np.digitize(p1s, edges)
    for i in range(1, len(edges)):
        mask = idx == i
        if mask.any():
            print(f"p1 [{edges[i-1]},{edges[i]}): n={mask.sum()} "
                  f"conc_med={np.median(concs[mask]):.4f}")
    # 采样 20 条多峰迹线的浓度曲线
    print("--- 浓度曲线样例(p1_sig: conc) ---")
    shown = 0
    for row in flat:
        if shown >= 5:
            break
        z = _hilbert(row)
        peaks = _lock_trace_peaks(np.abs(z), margin=8, max_peaks=8, global_max=gmax)
        if peaks is None or peaks[0].size < 3:
            continue
        vals = z[peaks[0]]
        weights = peaks[1] + 1e-12
        denom = float(np.sum(weights))
        n = z.size
        curve = []
        for p1 in np.arange(-90.0, 91.0, 10.0):
            ramp = np.exp(-1j * np.deg2rad(p1 * peaks[0] / max(n - 1, 1)))
            unit = np.exp(1j * np.angle(vals * ramp))
            vec = np.sum(weights * unit) / denom
            curve.append(f"{p1:+.0f}:{abs(vec):.3f}")
        print("pos:", peaks[0].tolist(), "phases:",
              [f"{np.rad2deg(np.angle(v)) % 360:.0f}" for v in vals])
        print("  " + " ".join(curve))
        shown += 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
