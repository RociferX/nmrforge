"""sampleK 复型频域谱直接维相位搜索验证(0.2.199,用户方案)。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from core.data.pipe_io import read_pipe_complex
from core.optimization.phase_search import search_direct_phase_on_spectrum


def main() -> int:
    work = Path("/home/<lab-user>/Desktop/sampleK.nmrpipe")
    planes = sorted((work / "nus3d_rc").glob("test*.ft1"))
    arrays = [read_pipe_complex(p) for p in planes]
    stack = np.stack(arrays, axis=-1)  # (i0, i1, k=直接维)
    print("recon stack:", stack.shape)
    # 间接维 FT(沿平面内两轴,即 finalize 的 F2/F1 定稿),复型保持
    freq = np.fft.fftshift(np.fft.fft(np.fft.fft(stack, axis=0), axis=1), axes=(0, 1))
    print("freq spectrum:", freq.shape, "dtype:", freq.dtype)
    for ax in (2, 0, 1):
        est = search_direct_phase_on_spectrum(
            freq, axis=ax, metric="symmetry"
        )
        print(f"  axis={ax}: {est}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
