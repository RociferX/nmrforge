"""101:用 sampleJ 根目录的 proj3D 投影文件抽直接维迹线 + HT 搜索。"""

from __future__ import annotations

import sys

import numpy as np

from core.optimization.phase_consensus import search_direct_phase_real_ht


def main() -> int:
    import nmrglue as ng

    base = "/home/<lab-user>/Desktop/sampleJ/"
    rows: list[np.ndarray] = []
    for name in ("1H.13C.dat", "1H.15N.dat"):
        _dic, data = ng.pipe.read(base + name)
        data = np.asarray(data)
        if np.iscomplexobj(data):
            data = data.real
        print(name, data.shape)
        rows.append(data.reshape(-1, data.shape[-1]))
    traces = np.concatenate(rows, axis=0)
    print("直接维投影迹线:", traces.shape)
    for mode in ("uniform", "mixed"):
        est = search_direct_phase_real_ht(traces, axis=-1, sign_mode=mode)
        print(f"  sign_mode={mode}: {est}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
