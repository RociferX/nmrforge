"""sampleK 复型终谱(正确拆包)直接维相位搜索(0.2.199)。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from core.optimization.phase_search import search_direct_phase_on_spectrum


def main() -> int:
    import nmrglue as ng

    _dic, data = ng.pipe.read(str(Path("/tmp/cplx_run/102_cplx.ft3")))
    raw = np.asarray(data)
    print("raw shape:", raw.shape, "dtype:", raw.dtype)
    # 轴 0/1 实虚交错:复型 (512, 512, 168)
    cplx = raw[0::2] + 1j * raw[1::2]
    cplx = cplx[:, 0::2] + 1j * cplx[:, 1::2]
    print("complex shape:", cplx.shape, "dtype:", cplx.dtype)
    for ax in (2, 0, 1):
        est = search_direct_phase_on_spectrum(
            cplx, axis=ax, metric="symmetry"
        )
        print(f"  axis={ax}: {est}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
