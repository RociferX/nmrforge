"""对照用户手调 p0=150:102.ft3 真实峰(排除最强伪峰)的相位分布。

显示层(scipy 解析信号)与脚本(nmrPipe HT 约定)两种约定分别给出
“看起来好”的 p0;同时测 p0=150 在两种约定下的吸收度。
"""

from __future__ import annotations

import sys

import numpy as np

from core.optimization.phase_consensus import _hilbert, _lock_trace_peaks
from core.optimization.phase_search import _row_absorption


def _scipy_analytic(row: np.ndarray) -> np.ndarray:
    n = row.size
    X = np.fft.fft(np.asarray(row, dtype=float))
    h = np.zeros(n)
    if n % 2 == 0:
        h[0] = h[n // 2] = 1.0
        h[1:n // 2] = 2.0
    else:
        h[0] = 1.0
        h[1:(n + 1) // 2] = 2.0
    return np.fft.ifft(X * h)


def main() -> int:
    import nmrglue as ng

    _dic, arr = ng.pipe.read("/home/<lab-user>/Desktop/sampleK.nmrpipe/102.ft3")
    arr = np.asarray(arr)
    if np.iscomplexobj(arr):
        arr = arr.real
    flat = arr.reshape(-1, arr.shape[-1])
    peak_mag = np.max(np.abs(flat), axis=-1)
    print("trace max 分位: p50/p90/p99/max =",
          [f"{float(np.percentile(peak_mag, q)):.3g}" for q in (50, 90, 99, 100)])
    # 排除最强伪峰:取 max 在 [p50, p98] 的迹线(避开噪声与超大峰)
    lo, hi = np.percentile(peak_mag, 50), np.percentile(peak_mag, 98)
    sel = np.where((peak_mag >= lo) & (peak_mag <= hi))[0]
    print("选中迹线:", len(sel), "分位区间:",
          [f"{float(v):.3g}" for v in (lo, hi)])
    gmax = float(np.max(np.abs(flat)))
    phases_std: list[float] = []
    phases_pipe: list[float] = []
    absorb: dict[tuple[str, float], list[float]] = {}
    for idx in sel:
        row = flat[idx]
        z_std = _scipy_analytic(row)
        z_pipe = _hilbert(row)
        peaks = _lock_trace_peaks(
            np.abs(z_pipe), margin=8, max_peaks=8, global_max=gmax
        )
        if peaks is None:
            continue
        pos, heights = peaks
        for p in pos:
            phases_std.append((np.rad2deg(np.angle(z_std[p])) % 360.0))
            phases_pipe.append((np.rad2deg(np.angle(z_pipe[p])) % 360.0))
        for conv, z in (("std", z_std), ("pipe", z_pipe)):
            for cand in (150.0, 197.6, 17.6, 330.0, 30.0):
                a, _s = _row_absorption(z, pos, heights, cand, 0.0, radius=1)
                absorb.setdefault((conv, cand), []).append(a)
    ph_s = np.array(phases_std)
    ph_p = np.array(phases_pipe)

    def _circ(ph: np.ndarray) -> float:
        return float(
            np.rad2deg(
                np.arctan2(np.mean(np.sin(np.deg2rad(ph))),
                           np.mean(np.cos(np.deg2rad(ph))))
            )
            % 360.0
        )

    print("峰相位(display 用 std): 圆均值", f"{_circ(ph_s):.1f}",
          "中位数", f"{np.median(ph_s):.1f}")
    print("峰相位(script 用 pipe): 圆均值", f"{_circ(ph_p):.1f}",
          "中位数", f"{np.median(ph_p):.1f}")
    print("=> display p0 ≈ -phase_std:", f"{(-_circ(ph_s)) % 360:.1f}")
    print("=> script p0 ≈ -phase_pipe:", f"{(-_circ(ph_p)) % 360:.1f}")
    for conv in ("std", "pipe"):
        for cand in (150.0, 197.6, 17.6, 330.0, 30.0):
            vals = absorb.get((conv, cand), [0.0])
            print(f"吸收度[{conv}] @ p0={cand}: 中位数 {np.median(vals):.3f} "
                  f"均值 {np.mean(vals):.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
