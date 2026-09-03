"""VM:uniform 2D 全复谱逐维迭代相位(sampleF,收敛到人工参考 F1 p0=100)。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import nmrglue as ng

from backend.nmrpipe_backend import NMRPipeBackend
from core.data.bruker_reader import read_dataset
from core.optimization.phase_search import search_direct_phase_on_spectrum
from workflow.memory_phase_search import search_axis_memory


def _rotate(arr, axis, p0, p1):
    n = arr.shape[axis]
    k = np.arange(n, dtype=float)
    ramp = np.exp(1j * np.deg2rad(p0 + p1 * k / max(n - 1, 1)))
    shape = [1] * arr.ndim
    shape[axis] = n
    return arr * ramp.reshape(shape)


def main() -> int:
    raw = Path("/home/<lab-user>/Desktop/sampleF")
    work = Path("/home/<lab-user>/Desktop/sampleF.nmrpipe")
    exp = read_dataset(raw)
    backend = NMRPipeBackend(work_dir=str(work))
    from core.planning.method_selector import select_method

    plan = select_method(exp)
    resp = backend.process(
        exp, plan,
        params={"keep_complex_all": True, "zero_fill": 1},
        out_file="3_iter.ft2", script_name="3_iter.com",
    )
    _d, rawd = ng.pipe.read(str(resp["spectrum_path"]))
    arr = np.asarray(rawd)
    c0 = arr[:, 0::2] + 1j * arr[:, 1::2]
    c0 = c0[0::2, :] + 1j * c0[1::2, :]
    print("fully complex:", c0.shape)
    found = {"F2": (0.0, 0.0), "F1": (0.0, 0.0)}
    for iteration in range(3):
        c = c0.copy()
        c = _rotate(c, 1, *found["F2"])
        c = _rotate(c, 0, *found["F1"])
        for name, ax, fn in (
            ("F2", 1, lambda a: search_direct_phase_on_spectrum(a, axis=1, metric="symmetry")),
            ("F1", 0, lambda a: (lambda e: (e.phase[0], e.phase[1], e.score))(search_axis_memory(a, 0, sign_mode="uniform"))),
        ):
            est = fn(c)
            if est is None:
                print(f"iter{iteration} {name}: 无结果")
                continue
            p0 = (found[name][0] + est[0]) % 360.0
            p1 = found[name][1] + est[1]
            found[name] = (p0, p1)
            print(f"iter{iteration} {name}: 本次=({round(est[0],1)},{round(est[1],1)}) "
                  f"累计=({round(p0,1)},{round(p1,1)})")
    print("最终:", {k: tuple(round(v, 1) for v in ph) for k, ph in found.items()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
