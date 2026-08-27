"""VM 验证:先间接后直接——间接维按记录相位固定(-di),直接维复型预览
上搜索直接维相位(sampleB,不跑 SMILE)。"""

from __future__ import annotations

import sys
from pathlib import Path

from backend.nmrpipe_backend import NMRPipeBackend
from core.data.bruker_reader import read_dataset
from core.optimization.phase_search import search_direct_phase_on_spectrum
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
    # 间接维固定到记录相位,F2/F1 加 -di(实型),直接维 F3 复型
    phases = {"F2": (86.0, -27.5), "F1": (355.0, 0.0)}
    resp = backend.finalize_nus(
        exp, phases=phases, work_dir=str(work),
        params={**zf, "preview_axis": "F3"},
        out_file="28_direct_clean.ft3",
        script_name="28_direct_clean_finalize.com",
    )
    print("finalize ok:", resp.get("success"))
    arr = _read_complex_ft3(str(resp["spectrum_path"]))
    print("shape:", arr.shape)
    est = search_direct_phase_on_spectrum(
        arr, axis=-1, metric="symmetry", sign_mode="uniform"
    )
    print("直接维(间接固定)搜索:", est)
    return 0


if __name__ == "__main__":
    sys.exit(main())
