"""VM 调试:逐条吸收度网格 p1(多峰迹线),对照记录 F2 p1=-27.5。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from backend.nmrpipe_backend import NMRPipeBackend
from core.data.bruker_reader import read_dataset
from core.optimization.phase_consensus import _lock_trace_peaks
from core.optimization.phase_search import _row_absorption
from workflow.phase_routes import _read_complex_ft3


def _trace_best_p1(row, pos, heights):
    """吸收度网格:固定峰窗,扫 p1,每 p1 取最优 p0 的中位吸收,返回最佳 p1。"""
    best = (0.0, -1.0)
    n = row.shape[-1]
    for p1 in np.arange(-90.0, 91.0, 10.0):
        best_abs = -1.0
        for p0 in np.arange(0.0, 360.0, 30.0):
            a, _s = _row_absorption(row, pos, heights, p0, p1, radius=1)
            best_abs = max(best_abs, a)
        if best_abs > best[1]:
            best = (float(p1), best_abs)
    return best


def main() -> int:
    raw = Path("/home/<lab-user>/Desktop/data/sampleB")
    work = Path("/home/<lab-user>/Desktop/data/sampleB.nmrpipe")
    exp = read_dataset(raw)
    backend = NMRPipeBackend(work_dir=str(work))
    zf = {"zero_fill": {"F2": {"mode": "size", "size": 128},
                        "F1": {"mode": "size", "size": 256},
                        "F3": {"mode": "none"}}}
    resp = backend.finalize_nus(
        exp, phases={}, work_dir=str(work),
        params={**zf, "keep_complex": True},
        out_file="28_p1grid.ft3", script_name="28_p1grid_finalize.com",
    )
    arr = _read_complex_ft3(str(resp["spectrum_path"]))
    for ax, name in ((0, "F2"), (1, "F1"), (2, "F3")):
        moved = np.moveaxis(arr, ax, -1)
        flat = moved.reshape(-1, moved.shape[-1])
        gm = float(np.max(np.abs(flat)))
        p1s: list[float] = []
        for row in flat:
            pk = _lock_trace_peaks(row, margin=8, max_peaks=8, global_max=gm)
            if pk is None or pk[0].size < 2:
                continue
            p1, _a = _trace_best_p1(row, pk[0], pk[1])
            p1s.append(p1)
        print(f"{name}: multi-peak traces={len(p1s)}")
        if p1s:
            p1a = np.array(p1s)
            print("  p1 quantiles:", np.percentile(p1a, [25, 50, 75]))
            hist, edges = np.histogram(p1a, bins=9, range=(-90, 90))
            print("  hist:", list(zip(np.round(edges[:-1]).astype(int), hist)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
