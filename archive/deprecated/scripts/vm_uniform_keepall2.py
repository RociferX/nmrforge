"""VM:uniform keep_complex_all 双轴拆包 + 逐维搜索(sampleF)。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import nmrglue as ng

from backend.nmrpipe_backend import NMRPipeBackend
from core.data.bruker_reader import read_dataset
from core.optimization.phase_search import search_direct_phase_on_spectrum
from workflow.memory_phase_search import search_axis_memory


def main() -> int:
    raw = Path("/home/<lab-user>/Desktop/sampleF")
    work = Path("/home/<lab-user>/Desktop/sampleF.nmrpipe")
    exp = read_dataset(raw)
    backend = NMRPipeBackend(work_dir=str(work))
    from core.planning.method_selector import select_method

    plan = select_method(exp)
    resp = backend.process(
        exp, plan,
        params={"keep_complex_all": True, "zero_fill": 1},
        out_file="3_keepall2.ft2", script_name="3_keepall2.com",
    )
    p = resp["spectrum_path"]
    _d, rawd = ng.pipe.read(str(p))
    arr = np.asarray(rawd)
    # 双轴交错拆包
    c = arr[:, 0::2] + 1j * arr[:, 1::2]      # 轴1(F2 直接)
    c = c[0::2, :] + 1j * c[1::2, :]          # 轴0(F1 间接)
    print("fully complex:", c.shape)
    for ax, name in ((1, "F2(直接)"), (0, "F1(间接)")):
        moved = np.moveaxis(c, ax, -1).reshape(-1, c.shape[ax])
        print(f"{name} 轴{ax} 虚部占比:",
              round(float(np.mean(np.abs(moved.imag)) / (np.mean(np.abs(moved)) + 1e-9)), 4))
    est2 = search_direct_phase_on_spectrum(c, axis=1, metric="symmetry")
    print("F2 对称搜索:", est2)
    est1 = search_axis_memory(c, 0, sign_mode="uniform")
    print("F1 内存搜索:", (round(est1.phase[0], 1), round(est1.phase[1], 1),
                         round(est1.score, 1)) if est1 else None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
