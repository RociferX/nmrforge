"""VM 检查:uniform 全复型 2D 谱两轴复型 + 逐维搜索。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import nmrglue as ng

from core.optimization.phase_search import search_direct_phase_on_spectrum
from workflow.memory_phase_search import search_axis_memory


def main() -> int:
    p = Path("/home/<lab-user>/Desktop/sampleF.nmrpipe/3_full_cplx.ft2")
    _d, raw = ng.pipe.read(str(p))
    arr = np.asarray(raw)
    print("read shape:", arr.shape, "complex:", np.iscomplexobj(raw))
    if np.iscomplexobj(arr):
        cplx = arr.astype(np.complex128)
    else:
        cplx = arr[0::2] + 1j * arr[1::2]
    print("cplx shape:", cplx.shape)
    # 两轴复部占比
    for ax in range(cplx.ndim):
        moved = np.moveaxis(cplx, ax, -1).reshape(-1, cplx.shape[ax])
        frac = float(np.mean(np.abs(moved.imag)) / (np.mean(np.abs(moved)) + 1e-9))
        print(f"轴{ax} 虚部占比: {round(frac, 4)}")
    # 逐维搜索:F2(直接,轴1)/F1(间接,轴0)
    est_f2 = search_direct_phase_on_spectrum(cplx, axis=1, metric="symmetry")
    print("F2(直接)对称搜索:", est_f2)
    est_f1 = search_axis_memory(cplx, 0, sign_mode="uniform")
    print("F1(间接)内存搜索:", (round(est_f1.phase[0], 1), round(est_f1.phase[1], 1),
                              round(est_f1.score, 1)) if est_f1 else None)
    return 0


if __name__ == "__main__":
    main()
