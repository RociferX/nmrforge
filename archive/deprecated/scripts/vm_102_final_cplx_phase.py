"""sampleK 复型终谱直接维相位搜索(0.2.199,用户方案验证)。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from core.optimization.phase_search import search_direct_phase_on_spectrum


def main() -> int:
    import nmrglue as ng

    work = Path("/tmp/cplx_run/102_cplx.ft3")
    _dic, data = ng.pipe.read(str(work))
    arr = np.asarray(data)
    print("complex final shape:", arr.shape, "dtype:", arr.dtype)
    for ax in (2, 0, 1):
        est = search_direct_phase_on_spectrum(
            arr, axis=ax, metric="symmetry"
        )
        print(f"  axis={ax}: {est}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
