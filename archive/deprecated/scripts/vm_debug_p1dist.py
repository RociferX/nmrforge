"""VM 调试:sampleB 零填复型终谱逐轴 p1 拟合分布。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from backend.nmrpipe_backend import NMRPipeBackend
from core.data.bruker_reader import read_dataset
from core.optimization.phase_consensus import _lock_trace_peaks
from core.optimization.phase_search import _row_p1_fit, _row_peak_positions
from workflow.phase_routes import _read_complex_ft3


def main() -> int:
    raw = Path("/home/<lab-user>/Desktop/data/sampleB")
    work = Path("/home/<lab-user>/Desktop/data/sampleB.nmrpipe")
    exp = read_dataset(raw)
    backend = NMRPipeBackend(work_dir=str(work))
    axes = [d.logical_axis for d in exp.dimensions]
    zf = {"zero_fill": {"F2": {"mode": "size", "size": 128},
                        "F1": {"mode": "size", "size": 256},
                        "F3": {"mode": "none"}}}
    resp = backend.finalize_nus(
        exp, phases={}, work_dir=str(work),
        params={**zf, "keep_complex": True},
        out_file="28_p1dist.ft3", script_name="28_p1dist_finalize.com",
    )
    arr = _read_complex_ft3(str(resp["spectrum_path"]))
    for ax, name in ((0, "F2"), (1, "F1"), (2, "F3")):
        moved = np.moveaxis(arr, ax, -1)
        flat = moved.reshape(-1, moved.shape[-1])
        gm = float(np.max(np.abs(flat)))
        p1s: list[float] = []
        n_infos = 0
        for row in flat:
            pk = _lock_trace_peaks(row, margin=8, max_peaks=8, global_max=gm)
            if pk is None:
                continue
            n_infos += 1
            fit = _row_p1_fit(row, pk[0], pk[1])
            if fit is not None:
                p1s.append(fit[0])
        print(f"{name} axis={ax}: infos={n_infos} p1_fits={len(p1s)}")
        if p1s:
            p1a = np.array(p1s)
            print("  p1 quantiles:", np.percentile(p1a, [10, 25, 50, 75, 90]))
            hist, edges = np.histogram(p1a, bins=9, range=(-90, 90))
            print("  hist:", list(zip(np.round(edges[:-1]).astype(int), hist)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
