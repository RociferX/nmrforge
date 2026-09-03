"""sampleK 最强峰直接迹线:以线中心为窗心扫描相位(0.2.199)。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from core.optimization.phase_search import _symmetry_sign_metric


def main() -> int:
    import nmrglue as ng

    _dic, data = ng.pipe.read(str(Path("/tmp/cplx_run/102_cplx.ft3")))
    raw = np.asarray(data)
    cplx = raw[0::2] + 1j * raw[1::2]
    cplx = cplx[:, 0::2] + 1j * cplx[:, 1::2]
    mag = np.abs(cplx)
    t = int(np.argmax(mag))
    i0, i1, _k = np.unravel_index(t, mag.shape)
    trace = cplx[i0, i1, :]
    n = trace.size
    k = np.arange(n, dtype=float)
    j = int(np.argmax(np.abs(trace)))  # 线中心
    half = 12
    print(f"最强峰(i0={i0},i1={i1}) 直接迹线峰@k={j}")
    best = (-1.0, 0.0, 0.0)
    for p0 in np.arange(0.0, 360.0, 5.0):
        for p1 in np.arange(-180.0, 181.0, 15.0):
            rot = trace * np.exp(1j * np.deg2rad(p0 + p1 * k / max(n - 1, 1)))
            re = np.real(rot)
            win = re[max(0, j - half) : j + half + 1]
            s = _symmetry_sign_metric(win)
            if s > best[0]:
                best = (s, p0, p1)
    print(f"线中心窗心: 最优 p0={best[1]:.0f}° p1={best[2]:.0f}° 对称={best[0]:.3f}")
    # 用旧窗心(实部瓣最大)对比
    jr = int(np.argmax(np.abs(np.real(trace))))
    best2 = (-1.0, 0.0, 0.0)
    for p0 in np.arange(0.0, 360.0, 5.0):
        for p1 in np.arange(-180.0, 181.0, 15.0):
            rot = trace * np.exp(1j * np.deg2rad(p0 + p1 * k / max(n - 1, 1)))
            re = np.real(rot)
            win = re[max(0, jr - half) : jr + half + 1]
            s = _symmetry_sign_metric(win)
            if s > best2[0]:
                best2 = (s, p0, p1)
    print(f"实部瓣窗心: 最优 p0={best2[1]:.0f}° p1={best2[2]:.0f}° 对称={best2[0]:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
