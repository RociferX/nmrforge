"""sampleK 强峰相位一致性拟合(±180 折叠)(0.2.199)。"""

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
    mag = np.abs(cplx)
    flat = mag.ravel()
    top = np.argsort(flat)[-60:][::-1]
    n = cplx.shape[2]
    ks: list[int] = []
    phs: list[float] = []
    for t in top:
        i0, i1, _ = np.unravel_index(t, mag.shape)
        trace = cplx[i0, i1, :]
        j = int(np.argmax(np.abs(trace)))
        if j < 8 or j > n - 8:
            continue
        ks.append(j)
        phs.append(float(np.rad2deg(np.angle(trace[j])) % 180.0))  # 折叠 ±180
    ks = np.array(ks, dtype=float)
    phs = np.array(phs, dtype=float)
    best = (1e18, 0.0, 0.0)
    for p0 in np.arange(0.0, 180.0, 3.0):
        for p1 in np.arange(-360.0, 361.0, 6.0):
            model = (p0 + p1 * ks / max(n - 1, 1)) % 180.0
            diff = np.abs(((phs - model + 90.0) % 180.0) - 90.0)
            err = float(np.sum(diff**2))
            if err < best[0]:
                best = (err, p0, p1)
    _, p0b, p1b = best
    model = (p0b + p1b * ks / max(n - 1, 1)) % 180.0
    diff = np.abs(((phs - model + 90.0) % 180.0) - 90.0)
    print(f"峰数={len(ks)} (±180折叠) p0={p0b:.0f}° p1={p1b:.0f}° RMS={np.sqrt(np.mean(diff**2)):.1f}°")
    return 0


if __name__ == "__main__":
    sys.exit(main())
