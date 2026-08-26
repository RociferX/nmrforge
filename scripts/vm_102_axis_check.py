"""sampleK 相位搜索轴对比:axis=0(旧)vs axis=-1(补17 修正)(0.2.199)。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from core.data.pipe_io import read_pipe_complex
from core.optimization.phase_search import search_direct_phase_on_spectrum


def main() -> int:
    work = Path("/home/<lab-user>/Desktop/sampleK.nmrpipe")
    planes = sorted((work / "nus3d_rc").glob("test*.ft1"))
    if len(planes) > 8:
        index = np.linspace(0, len(planes) - 1, 8).astype(int)
        planes = [planes[i] for i in index]
    arrays = [read_pipe_complex(p) for p in planes]
    arr = np.stack(arrays, axis=-1)  # (i0, i1, k=直接维)
    print("stack shape:", arr.shape)
    for axis in (0, -1):
        est = search_direct_phase_on_spectrum(
            arr, axis=axis, metric="symmetry"
        )
        print(f"axis={axis}: {est}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
