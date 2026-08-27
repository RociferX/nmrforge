"""VM 验证:相位优化维度解析(0.2.199,不跑 SMILE)。

对 sampleB(HNCACB,切片输入风格)的 nus3d_rc 跑 direct/F2/F1 复型预览 +
 对应轴搜索,确认每个轴的解析正确。
"""

from __future__ import annotations

import sys
from pathlib import Path

from backend.nmrpipe_backend import NMRPipeBackend
from core.data.bruker_reader import read_dataset
from workflow.phase_routes import (
    _axis_index,
    _read_complex_ft3,
    _read_complex_preview,
)


def main() -> int:
    from core.optimization.phase_search import search_direct_phase_on_spectrum
    from workflow.memory_phase_search import search_axis_memory

    raw = Path("/home/<lab-user>/Desktop/data/sampleB")
    work = Path("/home/<lab-user>/Desktop/data/sampleB.nmrpipe")
    exp = read_dataset(raw)
    backend = NMRPipeBackend(work_dir=str(work))
    axes = [d.logical_axis for d in exp.dimensions]
    print("axes:", axes)
    zf_none = {
        "zero_fill": {a: {"mode": "none"} for a in axes}
    }
    # 1) 直接维:复型频域终谱,直接维 = 最后一轴
    resp = backend.finalize_nus(
        exp, phases={}, work_dir=str(work),
        params={**zf_none, "keep_complex": True},
        out_file="28_axischeck_direct.ft3",
        script_name="28_axischeck_direct_finalize.com",
    )
    print("direct preview ok:", resp.get("success"))
    cplx = _read_complex_ft3(str(resp["spectrum_path"]))
    print("direct complex shape:", cplx.shape)
    est = search_direct_phase_on_spectrum(
        cplx, axis=-1, metric="symmetry", sign_mode="mixed"
    )
    print("direct search axis=-1:", est)
    # 2) 间接维预览:unpack_axis = _axis_index
    for axis in ("F2", "F1"):
        ax = _axis_index(axis, exp.ndim)
        resp = backend.finalize_nus(
            exp, phases={}, work_dir=str(work),
            params={**zf_none, "preview_axis": axis},
            out_file=f"28_axischeck_{axis}.ft3",
            script_name=f"28_axischeck_{axis}_finalize.com",
        )
        arr = _read_complex_preview(
            str(resp["spectrum_path"]), unpack_axis=ax
        )
        print(f"{axis}: unpack_axis={ax} shape={arr.shape}")
        est = search_axis_memory(arr, ax, sign_mode="mixed")
        print(f"{axis}: search_axis_memory(ax={ax}):",
              (est.phase, round(est.score, 2)) if est else None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
