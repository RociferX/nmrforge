"""VM 验证:投影平面逐维相位(sampleB,间接维按记录相位校正后搜直接维)。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from backend.nmrpipe_backend import NMRPipeBackend
from core.data.bruker_reader import read_dataset
from core.optimization.projection_phase import projected_traces, search_projected_axis
from workflow.phase_routes import _read_complex_ft3


def _rotate(arr, axis, p0, p1):
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
        out_file="28_proj.ft3", script_name="28_proj_finalize.com",
    )
    arr = _read_complex_ft3(str(resp["spectrum_path"]))
    print("complex shape:", arr.shape)
    # 间接维按记录相位校正
    arr = _rotate(arr, 0, 86.0, -27.5)   # F2
    arr = _rotate(arr, 1, 355.0, 0.0)    # F1
    tr = projected_traces(arr, 2)
    print("直接维投影迹线:", tr.shape)
    est = search_projected_axis(arr, 2, sign_mode="uniform")
    print("直接维(投影迹线)共识:", est)
    return 0


if __name__ == "__main__":
    sys.exit(main())
