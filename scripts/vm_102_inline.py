"""内联共识(预锁峰,一致 gmax):验证 102≈150、28≈0。"""

from __future__ import annotations

import sys

import numpy as np

from core.optimization.phase_consensus import (
    _direct_projected_traces,
    _hilbert,
    _lock_trace_peaks,
    _row_p0_raw,
)
from core.optimization.phase_search import _row_p1_fit, _row_absorption


def search_inline(real: np.ndarray, *, max_traces: int = 512,
                  sign_mode: str = "uniform"):
    flat = _direct_projected_traces(real, -1)
    if flat is None:
        return None
    if flat.shape[0] > max_traces:
        idx = np.linspace(0, flat.shape[0] - 1, max_traces).astype(int)
        flat = flat[idx]
    gmax = float(np.max(np.abs(flat)))
    infos = []
    for row in flat:
        cplx = _hilbert(row)
        peaks = _lock_trace_peaks(np.abs(cplx), margin=8, max_peaks=8,
                                  global_max=gmax)
        if peaks is not None and peaks[0].size >= 1:
            infos.append((cplx, peaks[0], peaks[1]))
    if not infos:
        return None
    if len(infos) > 16:
        row_max = np.max(np.abs(np.asarray([i[0] for i in infos])), axis=-1)
        cutoff = float(np.percentile(row_max, 98))
        infos = [i for i, m in zip(infos, row_max) if m <= cutoff]
    p1_rows = []
    for cplx, pos, hts in infos:
        fit = _row_p1_fit(cplx, pos, hts)
        if fit is not None:
            p1_rows.append(fit[0])
    p1_signal = float(np.median(p1_rows)) if p1_rows else 0.0
    p1 = -p1_signal
    raw = np.array([_row_p0_raw(c, p, h, p1_signal) for c, p, h in infos])
    folded = raw % 180.0
    p0 = (
        0.5
        * np.rad2deg(
            np.arctan2(np.mean(np.sin(2 * np.deg2rad(folded))),
                       np.mean(np.cos(2 * np.deg2rad(folded))))
        )
    ) % 180.0

    def _abs_sign(phase):
        av, sv = [], []
        for c, p, h in infos:
            a, s = _row_absorption(c, p, h, phase, p1, radius=1)
            av.append(a)
            sv.append(s)
        return float(np.median(av)), float(np.median(sv))

    if sign_mode != "mixed":
        a0, s0 = _abs_sign(p0)
        _a1, s1 = _abs_sign((p0 + 180.0) % 360.0)
        if s1 > s0:
            p0 = (p0 + 180.0) % 360.0
            a0 = _a1
    else:
        a0, _s = _abs_sign(p0)
    return p0, p1, 100.0 * a0


def main() -> int:
    import nmrglue as ng

    for path, ref, name in (
        ("/home/<lab-user>/Desktop/sampleK.nmrpipe/102.ft3", 150.0, "102"),
        ("/home/<lab-user>/Desktop/data/sampleB.nmrpipe/28.ft3", 0.0, "28"),
    ):
        _dic, arr = ng.pipe.read(path)
        arr = np.asarray(arr)
        if np.iscomplexobj(arr):
            arr = arr.real
        for mode in ("uniform", "mixed"):
            est = search_inline(arr, sign_mode=mode)
            print(f"{name} {mode}: {est}")
        print(f"  (参考 {ref})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
