"""sampleK 复型终谱直接维 p1±180 全范围扫描(0.2.199)。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np


def main() -> int:
    import nmrglue as ng

    _dic, data = ng.pipe.read(str(Path("/tmp/cplx_run/102_cplx.ft3")))
    raw = np.asarray(data)
    cplx = raw[0::2] + 1j * raw[1::2]
    cplx = cplx[:, 0::2] + 1j * cplx[:, 1::2]
    axis = 2
    n = cplx.shape[axis]
    moved = np.moveaxis(cplx, axis, -1)
    traces = moved.reshape(-1, n)
    peak_mag = np.max(np.abs(traces), axis=-1)
    # 最强 500 条迹线
    order = np.argsort(peak_mag)[::-1][:500]
    sig = traces[order]
    positions = np.argmax(np.abs(sig), axis=-1)
    weights = np.max(np.abs(sig), axis=-1) + 1e-12
    k = np.arange(n, dtype=float)
    offset = np.arange(-5, 6)
    index = np.clip(positions[:, None] + offset[None, :], 0, n - 1)
    rows = np.arange(len(positions))[:, None]

    def _score(p0, p1):
        ramp = np.exp(1j * np.deg2rad(p0 + p1 * k / max(n - 1, 1)))
        rot = sig * ramp
        prof = rot[rows, index]
        re = np.real(prof)
        re_abs = np.abs(re)
        im_abs = np.abs(np.imag(prof))
        denom = re_abs + im_abs + 1e-12
        absorption = np.sum(re_abs, axis=-1) / np.sum(denom, axis=-1)
        return float(np.average(absorption, weights=weights))

    best = (-1.0, 0.0, 0.0)
    for p0 in np.arange(0.0, 360.0, 15.0):
        for p1 in np.arange(-180.0, 181.0, 15.0):
            s = _score(float(p0), float(p1))
            if s > best[0]:
                best = (s, float(p0), float(p1))
    print(f"p1±180 扫描: 最优 p0={best[1]:.0f}° p1={best[2]:.0f}° absorption={best[0]:.3f}")
    # p1 固定为最优附近,扫 p0 细化
    best2 = best
    for p1 in np.arange(best[2] - 15, best[2] + 16, 5.0):
        for p0 in np.arange(0.0, 360.0, 5.0):
            s = _score(float(p0), float(p1))
            if s > best2[0]:
                best2 = (s, float(p0), float(p1))
    print(f"细化: p0={best2[1]:.0f}° p1={best2[2]:.0f}° absorption={best2[0]:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
