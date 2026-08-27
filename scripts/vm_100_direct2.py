"""100:首遍 SMILE(窄直接维窗)→ 实型 finalize → 直接维投影+HT 搜索。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from backend.nmrpipe_backend import NMRPipeBackend
from core.data.bruker_reader import read_dataset
from core.optimization.phase_consensus import search_direct_phase_real_ht
from workflow.phase_routes import (
    _apply_final_ext,
    _read_complex_ft3,
    _split_final_ext,
)


def main() -> int:
    raw = Path("/home/<lab-user>/Desktop/data/100")
    work = Path("/home/<lab-user>/Desktop/data/100.nmrpipe")
    exp = read_dataset(raw)
    backend = NMRPipeBackend(work_dir=str(work))
    base = {
        "direct_phase_search": False,
        "display_phase_search": False,
        "final_ext_lo": "6.5",
        "final_ext_hi": "10.5",
        "apply_ext_to_opt": "1",
    }
    params_first, lo, hi, apply_opt = _split_final_ext(base)
    if apply_opt:
        params_first = _apply_final_ext(params_first, lo, hi)
    print("SMILE 首遍重构中(直接维窗口 6.5-10.5 ppm)...")
    first = backend.reconstruct_nus(exp, params_first)
    if not first.get("success") or not first.get("spectrum_path"):
        print("SMILE 首遍失败:", first.get("message"))
        return 1
    print("SMILE 首遍完成:", first.get("spectrum_path"))
    resp = backend.finalize_nus(
        exp,
        phases={},
        work_dir=str(work),
        params=dict(params_first),
        out_file="100_direct_final.ft3",
        script_name="100_direct_final_finalize.com",
    )
    if not resp.get("success") or not resp.get("spectrum_path"):
        print("finalize 失败:", resp.get("message"))
        return 1
    arr = np.real(_read_complex_ft3(str(resp["spectrum_path"])))
    print("100 实型终谱 shape:", arr.shape)
    for mode in ("uniform", "mixed"):
        est = search_direct_phase_real_ht(arr, axis=-1, sign_mode=mode)
        print(f"  直接维 sign_mode={mode}: {est}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
