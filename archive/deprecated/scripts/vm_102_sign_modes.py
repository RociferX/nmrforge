"""sampleK 复型频域终谱两种 sign_mode 搜索对比(0.2.199-补20)。"""

from __future__ import annotations

import sys
from pathlib import Path

from core.optimization.phase_search import search_direct_phase_on_spectrum
from workflow.phase_routes import _read_complex_ft3


def main() -> int:
    cplx = _read_complex_ft3(
        Path("/home/<lab-user>/Desktop/sampleK.nmrpipe/102_direct_preview.ft3")
    )
    for mode in ("uniform", "mixed"):
        est = search_direct_phase_on_spectrum(
            cplx, axis=-1, metric="symmetry", sign_mode=mode
        )
        print(f"sign_mode={mode}: {est}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
