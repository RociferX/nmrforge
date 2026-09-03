"""VM 验证:keep_complex 全复谱上逐轴对称性搜索(sampleB,不跑 SMILE)。"""

from __future__ import annotations

import sys
from pathlib import Path

from backend.nmrpipe_backend import NMRPipeBackend
from core.data.bruker_reader import read_dataset
from core.optimization.phase_search import search_direct_phase_on_spectrum
from workflow.phase_routes import _axis_index, _read_complex_ft3


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
        out_file="28_sym.ft3", script_name="28_sym_finalize.com",
    )
    arr = _read_complex_ft3(str(resp["spectrum_path"]))
    print("complex shape:", arr.shape)
    for logical in ("F2", "F1", "F3"):
        ax = _axis_index(logical, exp.ndim)
        est = search_direct_phase_on_spectrum(
            arr, axis=ax, metric="symmetry", sign_mode="uniform"
        )
        print(
            f"{logical} axis={ax}:",
            (round(est[0], 1), round(est[1], 1), round(est[2], 1)) if est else None,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
