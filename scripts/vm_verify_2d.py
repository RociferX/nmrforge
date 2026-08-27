"""VM 验证:用 sampleF uniform 制造的 2D NUS recon 验证 2D 路径(不跑 SMILE)。

覆盖:2D recon.ft1 读取(_load_recon_planes 两处)、2D finalize 输出布局、
F1 复型预览 + 内存搜索、直接维搜索(recon 轴 0)。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from backend.nmrpipe_backend import NMRPipeBackend
from core.data.bruker_reader import read_dataset
from core.data.internal_data_model import SamplingMode
from workflow.phase_routes import (
    _axis_index,
    _load_recon_planes as routes_load_planes,
    _read_complex_preview,
)
from workflow.window_optimize import _load_recon_planes as win_load_planes


def main() -> int:
    raw = Path("/home/<lab-user>/Desktop/sampleF")
    work = Path("/home/<lab-user>/Desktop/sampleF.nmrpipe")
    # sampleF 是 uniform,强制按 2D NUS 语义构造(用于验证 2D NUS 布局路径)
    exp = read_dataset(raw)
    exp.sampling.mode = SamplingMode.NUS  # type: ignore[attr-defined]
    print("axes:", [d.logical_axis for d in exp.dimensions])
    backend = NMRPipeBackend(work_dir=str(work))

    # 1) 两个 _load_recon_planes 的 2D 读取
    planes_w, dic_w = win_load_planes(work, exp)
    print("window_optimize planes:", planes_w.shape, np.iscomplexobj(planes_w))
    planes_r = routes_load_planes(exp, work)
    print("phase_routes planes:", planes_r.shape, np.iscomplexobj(planes_r))
    assert planes_w.shape == planes_r.shape

    # 2) 直接维搜索:recon 轴 0 = F2 频
    from core.optimization.phase_search import search_direct_phase_on_spectrum

    est = search_direct_phase_on_spectrum(
        planes_r, axis=0, metric="symmetry"
    )
    print("2D 直接维搜索(recon axis=0):", est)

    # 3) F1 复型预览 + 内存搜索(2D 布局 F1 在轴 0)
    zf_none = {"zero_fill": {d.logical_axis: {"mode": "none"} for d in exp.dimensions}}
    resp = backend.finalize_nus(
        exp,
        phases={},
        work_dir=work,
        params={**zf_none, "preview_axis": "F1"},
        out_file="3_2d_preview_F1.ft2",
        script_name="3_2d_preview_F1_finalize.com",
    )
    print("F1 预览 ok:", resp.get("success"))
    ax = _axis_index("F1", exp.ndim)
    arr = _read_complex_preview(str(resp["spectrum_path"]), unpack_axis=ax)
    print("F1 预览 unpack_axis=", ax, "shape=", arr.shape)
    from workflow.memory_phase_search import search_axis_memory

    est1 = search_axis_memory(arr, ax, sign_mode="uniform")
    print("F1 内存搜索:", (est1.phase, round(est1.score, 2)) if est1 else None)

    # 4) 2D finalize 非预览输出布局
    resp2 = backend.finalize_nus(
        exp,
        phases={"F1": est1.phase} if est1 else {},
        work_dir=work,
        params=zf_none,
        out_file="3_2d_final.ft2",
        script_name="3_2d_final_finalize.com",
    )
    import nmrglue as ng

    dic2, data2 = ng.pipe.read(str(resp2["spectrum_path"]))
    print(
        "2D finalize 输出:", np.asarray(data2).shape,
        "complex" if np.iscomplexobj(data2) else "real",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
