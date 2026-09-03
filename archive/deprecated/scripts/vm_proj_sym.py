"""VM:投影迹线集合上跑对称性搜索(sampleB 直接维)。"""

from __future__ import annotations

import sys
from pathlib import Path

from backend.nmrpipe_backend import NMRPipeBackend
from core.data.bruker_reader import read_dataset
from core.optimization.phase_search import search_direct_phase_on_spectrum
from core.optimization.projection_phase import projected_traces
from workflow.phase_routes import _read_complex_ft3


def _rotate(arr, axis, p0, p1):
    import numpy as np

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
    zf = {"zero_fill": {"F2": {"mode": "size", "size": 128},
                        "F1": {"mode": "size", "size": 256},
                        "F3": {"mode": "none"}}}
    resp = backend.finalize_nus(
        exp, phases={}, work_dir=str(work),
        params={**zf, "keep_complex": True},
        out_file="28_projsym.ft3", script_name="28_projsym_finalize.com",
    )
    arr = _read_complex_ft3(str(resp["spectrum_path"]))
    for label, rotate in (
        ("未校正间接维", False),
        ("间接维按记录相位校正", True),
    ):
        a = arr.copy()
        if rotate:
            a = _rotate(a, 0, 86.0, -27.5)
            a = _rotate(a, 1, 355.0, 0.0)
        tr = projected_traces(a, 2)
        est = search_direct_phase_on_spectrum(tr, axis=-1, metric="symmetry")
        print(f"直接维投影迹线({label}):", est)
    return 0


if __name__ == "__main__":
    sys.exit(main())
