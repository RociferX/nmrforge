"""VM 验证:逐维共识相位搜索(sampleB,不跑 SMILE)。"""

from __future__ import annotations

import sys
from pathlib import Path

from backend.nmrpipe_backend import NMRPipeBackend
from core.data.bruker_reader import read_dataset
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
        out_file="28_consensus.ft3",
        script_name="28_consensus_finalize.com",
    )
    print("finalize ok:", resp.get("success"))
    arr = _read_complex_ft3(str(resp["spectrum_path"]))
    print("complex shape:", arr.shape)
    from core.optimization.phase_consensus import search_axis_phase_consensus

    from core.experiments.registry import REGISTRY
    import core.experiments  # noqa: F401

    tpl = REGISTRY.get(exp.experiment_type.name)
    sign_mode = "mixed" if tpl is not None and tpl.peak_sign == "mixed" else "uniform"
    for ax, name in ((0, "F2(15N)"), (1, "F1(13C)"), (2, "F3(1H 直接)")):
        est = search_axis_phase_consensus(arr, ax, sign_mode=sign_mode)
        print(
            f"{name} axis={ax}:",
            (round(est[0], 1), round(est[1], 1), round(est[2], 1)) if est else None,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
