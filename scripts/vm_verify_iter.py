"""VM 验证:keep_complex 全复谱逐维迭代相位(每轮内存旋转应用,其它维
校正后该维搜索更干净——人工投影调相思路的迭代实现)。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from backend.nmrpipe_backend import NMRPipeBackend
from core.data.bruker_reader import read_dataset
from workflow.memory_phase_search import search_axis_memory
from workflow.phase_routes import _axis_index, _read_complex_ft3


def _rotate_complex(arr: np.ndarray, axis: int, p0: float, p1: float) -> np.ndarray:
    n = arr.shape[axis]
    k = np.arange(n, dtype=float)
    ramp = np.exp(1j * np.deg2rad(p0 + p1 * k / max(n - 1, 1)))
    shape = [1] * arr.ndim
    shape[axis] = n
    return arr * ramp.reshape(shape)


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
        out_file="28_iter.ft3", script_name="28_iter_finalize.com",
    )
    arr0 = _read_complex_ft3(str(resp["spectrum_path"]))
    order = [("F2", 0), ("F1", 1), ("F3", 2)]
    found: dict[str, tuple[float, float]] = {}
    for iteration in range(3):
        arr = arr0.copy()
        # 每轮开始前把已累计相位旋转应用(其它维校正 → 本维搜索干净)
        for logical, ax in order:
            if logical in found:
                arr = _rotate_complex(arr, ax, found[logical][0], found[logical][1])
        for logical, ax in order:
            est = search_axis_memory(arr, ax, sign_mode="uniform")
            if est is None:
                print(f"iter{iteration} {logical}: 无干净迹线")
                continue
            # 该轴新相位:先撤掉已应用相位,再加本次估计
            prev = found.get(logical, (0.0, 0.0))
            p0 = (prev[0] + est.phase[0]) % 360.0
            p1 = prev[1] + est.phase[1]
            found[logical] = (p0, p1)
            print(
                f"iter{iteration} {logical} axis={ax}: "
                f"本次=({round(est.phase[0],1)},{round(est.phase[1],1)},"
                f"{round(est.score,1)}) 累计=({round(p0,1)},{round(p1,1)})"
            )
    print("最终相位:", {k: tuple(round(v, 1) for v in ph) for k, ph in found.items()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
