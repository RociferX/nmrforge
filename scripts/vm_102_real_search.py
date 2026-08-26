"""修复后真实搜索函数在 102 复型频域终谱上的结果(0.2.199-补19)。"""

from __future__ import annotations

import sys
from pathlib import Path

from core.optimization.phase_search import search_direct_phase_on_spectrum
from workflow.phase_routes import _read_complex_ft3


def main() -> int:
    cplx = _read_complex_ft3(
        Path("/home/<lab-user>/Desktop/sampleK.nmrpipe/102_direct_preview.ft3")
    )
    print("complex:", cplx.shape)
    est = search_direct_phase_on_spectrum(
        cplx, axis=-1, metric="symmetry"
    )
    print("直接维搜索:", est)
    return 0


if __name__ == "__main__":
    sys.exit(main())
