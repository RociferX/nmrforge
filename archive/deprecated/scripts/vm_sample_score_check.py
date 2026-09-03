"""sampleC 与 sampleK 直接维评分对比(axis=0 vs 平面轴)(0.2.199)。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from core.data.pipe_io import read_pipe_complex
from core.optimization.phase_search import search_direct_phase_on_spectrum


def main() -> int:
    for name, work, axes in (
        ("sampleC", Path("/home/<lab-user>/Desktop/data/sampleC.nmrpipe"), (0, -1)),
        ("sampleK", Path("/home/<lab-user>/Desktop/sampleK.nmrpipe"), (0, -1)),
    ):
        ps = sorted((work / "nus3d_rc").glob("test*.ft1"))
        if len(ps) > 8:
            index = np.linspace(0, len(ps) - 1, 8).astype(int)
            ps = [ps[i] for i in index]
        arr = np.stack([read_pipe_complex(p) for p in ps], axis=-1)
        print(f"=== {name} planes={len(ps)} shape={arr.shape}")
        for ax in axes:
            est = search_direct_phase_on_spectrum(
                arr, axis=ax, metric="symmetry"
            )
            print(f"  axis={ax}: {est}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
