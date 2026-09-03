"""sampleK 全平面(168)axis=-1 相位搜索验证(0.2.199-补17)。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from core.data.pipe_io import read_pipe_complex
from core.optimization.phase_search import search_direct_phase_on_spectrum


def main() -> int:
    work = Path("/home/<lab-user>/Desktop/sampleK.nmrpipe")
    planes = sorted((work / "nus3d_rc").glob("test*.ft1"))
    print("plane count:", len(planes))
    arrays = [read_pipe_complex(p) for p in planes]
    arr = np.stack(arrays, axis=-1)  # (i0, i1, k=直接维)
    print("stack shape:", arr.shape)
    est = search_direct_phase_on_spectrum(
        arr, axis=-1, metric="symmetry"
    )
    print("axis=-1 (全平面):", est)
    return 0


if __name__ == "__main__":
    sys.exit(main())
