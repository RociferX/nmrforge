"""VM 验证(真实谱):keep_complex 全复谱逐轴对称性搜索 + 迭代内存旋转。
用法:python vm_iter_sym.py sampleB 或 sampleC(不跑 SMILE)。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from backend.nmrpipe_backend import NMRPipeBackend
from core.data.bruker_reader import read_dataset
from core.optimization.phase_search import search_direct_phase_on_spectrum
from workflow.phase_routes import _axis_index, _read_complex_ft3


def _rotate(arr: np.ndarray, axis: int, p0: float, p1: float) -> np.ndarray:
    n = arr.shape[axis]
    k = np.arange(n, dtype=float)
    ramp = np.exp(1j * np.deg2rad(p0 + p1 * k / max(n - 1, 1)))
    shape = [1] * arr.ndim
    shape[axis] = n
    return arr * ramp.reshape(shape)


def main() -> int:
    tag = sys.argv[1] if len(sys.argv) > 1 else "sampleB"
    raw = Path(f"/home/<lab-user>/Desktop/data/{tag}")
    work = Path(f"/home/<lab-user>/Desktop/data/{tag}.nmrpipe")
    exp = read_dataset(raw)
    backend = NMRPipeBackend(work_dir=str(work))
    axes = [d.logical_axis for d in exp.dimensions]
    zf = {"zero_fill": {"F2": {"mode": "size", "size": 128},
                        "F1": {"mode": "size", "size": 256},
                        "F3": {"mode": "none"}}}
    resp = backend.finalize_nus(
        exp, phases={}, work_dir=str(work),
        params={**zf, "keep_complex": True},
        out_file=f"{exp.dataset_id}_sym_iter.ft3",
        script_name=f"{exp.dataset_id}_sym_iter_finalize.com",
    )
    arr0 = _read_complex_ft3(str(resp["spectrum_path"]))
    print(f"{tag}: complex shape", arr0.shape)
    order = [("F2", _axis_index("F2", exp.ndim)),
             ("F1", _axis_index("F1", exp.ndim)),
             ("F3", _axis_index("F3", exp.ndim))]
    found: dict[str, tuple[float, float]] = {}
    for iteration in range(3):
        arr = arr0.copy()
        for logical, ax in order:
            if logical in found:
                arr = _rotate(arr, ax, found[logical][0], found[logical][1])
        for logical, ax in order:
            est = search_direct_phase_on_spectrum(
                arr, axis=ax, metric="symmetry", sign_mode="uniform"
            )
            if est is None:
                print(f"iter{iteration} {logical}: 无干净信号")
                continue
            prev = found.get(logical, (0.0, 0.0))
            p0 = (prev[0] + est[0]) % 360.0
            p1 = prev[1] + est[1]
            found[logical] = (p0, p1)
            print(
                f"iter{iteration} {logical} axis={ax}: "
                f"本次=({round(est[0],1)},{round(est[1],1)},{round(est[2],1)}) "
                f"累计=({round(p0,1)},{round(p1,1)})"
            )
    print("最终相位:", {k: tuple(round(v, 1) for v in ph) for k, ph in found.items()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
