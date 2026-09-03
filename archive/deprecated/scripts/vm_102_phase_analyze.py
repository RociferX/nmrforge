"""sampleK 相位核验:拟合真实直接维相位 + 对称性评分地形(0.2.199)。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from core.data.pipe_io import read_pipe_complex
from core.optimization.phase_search import (
    _signal_peak_windows,
    _symmetry_sign_metric,
)


def _fit_true_phase(planes: list[Path]) -> tuple[float, float, int]:
    """从复型重构平面最强峰的峰相位拟合 (p0, p1)。"""
    peaks: list[tuple[int, float, float]] = []  # (k, phase_deg, weight)
    n = None
    for path in planes:
        arr = read_pipe_complex(path)
        n = arr.shape[0]
        mag = np.abs(arr)
        flat = mag.ravel()
        top = np.argsort(flat)[-25:]
        for t in top:
            i3, i2 = np.unravel_index(t, mag.shape)
            ph = float(np.rad2deg(np.angle(arr[i3, i2])))
            peaks.append((i3, ph, float(mag[i3, i2])))
    k = np.array([p[0] for p in peaks], dtype=float)
    y = np.array([p[1] for p in peaks], dtype=float)
    w = np.array([p[2] for p in peaks], dtype=float)
    best = (1e18, 0.0, 0.0)
    for p0 in np.arange(0.0, 360.0, 5.0):
        for p1 in np.arange(-180.0, 181.0, 5.0):
            model = (p0 + p1 * k / max(n - 1, 1)) % 360.0
            diff = np.abs(((y - model + 180.0) % 360.0) - 180.0)
            err = float(np.sum(w * diff**2))
            if err < best[0]:
                best = (err, p0, p1)
    # 细化
    _, p0b, p1b = best
    best2 = (best[0], p0b, p1b)
    for dp0 in np.arange(-5.0, 5.1, 1.0):
        for dp1 in np.arange(-5.0, 5.1, 1.0):
            pc = (p0b + dp0) % 360.0
            qc = p1b + dp1
            model = (pc + qc * k / max(n - 1, 1)) % 360.0
            diff = np.abs(((y - model + 180.0) % 360.0) - 180.0)
            err = float(np.sum(w * diff**2))
            if err < best2[0]:
                best2 = (err, pc, qc)
    return best2[1], best2[2], len(peaks)


def _score_landscape(planes: list[Path]) -> None:
    """复现显示层搜索的对称性评分地形(8 平面均布,直接维 axis 0)。"""
    arrays = [read_pipe_complex(p) for p in planes]
    arr = np.stack(arrays, axis=-1) if len(arrays) > 1 else arrays[0]
    n = arr.shape[0]
    real = np.real(arr)
    windows = _signal_peak_windows(real, axis=0)
    print(f"评分窗数: {len(windows)}")
    if len(windows) > 200:
        index = np.linspace(0, len(windows) - 1, 200).astype(int)
        windows = [windows[i] for i in index]
    rows = np.moveaxis(arr, 0, -1).reshape(-1, n)
    sel = np.asarray([i for i, _p in windows], dtype=np.intp)
    selected = rows[sel]
    slices = [
        (j, max(0, peak - 12), min(n, peak + 13))
        for j, (_i, peak) in enumerate(windows)
    ]
    k = np.arange(n, dtype=float)
    scores: list[tuple[float, float, float]] = []
    for p0 in np.arange(0.0, 360.0, 15.0):
        for p1 in (-25, 0, 25):
            ramp = np.exp(1j * np.deg2rad(p0 + p1 * k / max(n - 1, 1)))
            rot = selected * ramp
            rr = np.real(rot)
            vals = [
                _symmetry_sign_metric(rr[j, lo:hi]) for j, lo, hi in slices
            ]
            scores.append((100.0 * float(np.mean(vals)), p0, p1))
    scores.sort(reverse=True)
    print("评分地形 top12 (score, p0, p1):")
    for s in scores[:12]:
        print(f"  {s[0]:7.2f}  p0={s[1]:6.1f}  p1={s[2]:6.1f}")


def main() -> int:
    work = Path("/home/<lab-user>/Desktop/sampleK.nmrpipe")
    planes = sorted((work / "nus3d_rc").glob("test*.ft1"))
    if not planes:
        print("未找到 nus3d_rc 平面,先跑 vm_102_phase_debug.py")
        return 1
    if len(planes) > 8:
        index = np.linspace(0, len(planes) - 1, 8).astype(int)
        planes = [planes[i] for i in index]
    print(f"平面数: {len(planes)},首平面 shape 待读")
    p0, p1, npeak = _fit_true_phase(planes)
    print(f"真实相位拟合(峰相位加权): p0={p0:.1f}° p1={p1:.1f}° "
          f"(峰值 {npeak})")
    _score_landscape(planes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
