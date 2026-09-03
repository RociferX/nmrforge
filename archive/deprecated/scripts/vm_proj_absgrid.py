"""VM 调试:投影迹线逐条吸收度网格 (p0,p1),看 p1 分布是否居中。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from backend.nmrpipe_backend import NMRPipeBackend
from core.data.bruker_reader import read_dataset
from core.optimization.phase_consensus import _lock_trace_peaks
from core.optimization.phase_search import _row_absorption
from core.optimization.projection_phase import projected_traces
from workflow.phase_routes import _read_complex_ft3


def _rotate(arr, axis, p0, p1):
    n = arr.shape[axis]
    k = np.arange(n, dtype=float)
    ramp = np.exp(1j * np.deg2rad(p0 + p1 * k / max(n - 1, 1)))
    shape = [1] * arr.ndim
    shape[axis] = n
    return arr * ramp.reshape(shape)


def _trace_best(row, pos, heights):
    best = ((0.0, 0.0), -1.0)
    for p1 in np.arange(-90.0, 91.0, 10.0):
        for p0 in np.arange(0.0, 360.0, 30.0):
            a, _s = _row_absorption(row, pos, heights, p0, p1, radius=1)
            if a > best[1]:
                best = ((float(p0), float(p1)), a)
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
        out_file="28_projabs.ft3", script_name="28_projabs_finalize.com",
    )
    arr = _read_complex_ft3(str(resp["spectrum_path"]))
    arr = _rotate(arr, 0, 86.0, -27.5)
    arr = _rotate(arr, 1, 355.0, 0.0)
    tr = projected_traces(arr, 2)
    gm = float(np.max(np.abs(tr)))
    p0s, p1s = [], []
    for row in tr:
        pk = _lock_trace_peaks(row, margin=8, max_peaks=8, global_max=gm)
        if pk is None:
            continue
        (p0, p1), _a = _trace_best(row, pk[0], pk[1])
        p0s.append(p0)
        p1s.append(p1)
    p1a = np.array(p1s)
    p0a = np.array(p0s)
    print("traces:", len(p1s))
    print("p1 quantiles:", np.percentile(p1a, [10, 25, 50, 75, 90]))
    hist, edges = np.histogram(p1a, bins=9, range=(-90, 90))
    print("p1 hist:", list(zip(np.round(edges[:-1]).astype(int), hist)))
    # p0 圆均值
    rad = np.deg2rad(p0a)
    print("p0 圆均值:", round(np.rad2deg(np.arctan2(np.mean(np.sin(rad)), np.mean(np.cos(rad)))) % 360, 1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
