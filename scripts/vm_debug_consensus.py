"""VM 调试:sampleB 共识搜索中间量。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from backend.nmrpipe_backend import NMRPipeBackend
from core.data.bruker_reader import read_dataset
from core.optimization.phase_consensus import _lock_trace_peaks, _row_p0_raw
from core.optimization.phase_search import _row_p1_fit
from workflow.phase_routes import _read_complex_ft3


def main() -> int:
    raw = Path("/home/<lab-user>/Desktop/data/sampleB")
    work = Path("/home/<lab-user>/Desktop/data/sampleB.nmrpipe")
    exp = read_dataset(raw)
    backend = NMRPipeBackend(work_dir=str(work))
    axes = [d.logical_axis for d in exp.dimensions]
    zf_none = {"zero_fill": {a: {"mode": "none"} for a in axes}}
    resp = backend.finalize_nus(
        exp,
        phases={},
        work_dir=str(work),
        params={**zf_none, "keep_complex": True},
        out_file="28_dbg.ft3",
        script_name="28_dbg_finalize.com",
    )
    arr = _read_complex_ft3(str(resp["spectrum_path"]))
    print("shape:", arr.shape)
    for ax, name in ((0, "F2"), (1, "F1"), (2, "F3")):
        moved = np.moveaxis(arr, ax, -1)
        flat = moved.reshape(-1, moved.shape[-1])
        gm = float(np.max(np.abs(flat)))
        infos = []
        for row in flat:
            pk = _lock_trace_peaks(row, margin=8, max_peaks=8, global_max=gm)
            if pk is not None:
                infos.append((row, pk[0], pk[1]))
        p1s = []
        p0s = []
        for r, p, h in infos:
            fit = _row_p1_fit(r, p, h)
            if fit is not None:
                p1s.append(fit[0])
            p0s.append(_row_p0_raw(r, p, h, fit[0] if fit else 0.0))
        print(
            f"{name} axis={ax}: infos={len(infos)} "
            f"p1s={[round(x,1) for x in p1s][:8]} "
            f"p0s={[round(x,1) for x in p0s][:8]}"
        )
        if p1s:
            print("  p1_signal median:", round(float(np.median(p1s)), 1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
