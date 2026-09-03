"""复现 0.2.164 删除前的旧 _search_axis,在 102 复型终谱上验证(0.2.199)。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np


def _trace_profiles(traces, positions, radius=5):
    n = traces.shape[-1]
    offset = np.arange(-radius, radius + 1)
    index = np.clip(positions[:, None] + offset[None, :], 0, n - 1)
    rows = np.arange(len(positions))[:, None]
    return traces[rows, index]


def _window_metrics(profiles):
    real = np.real(profiles)
    re_abs = np.abs(real)
    im_abs = np.abs(np.imag(profiles))
    denom = re_abs + im_abs + 1e-12
    absorption = np.sum(re_abs, axis=-1) / np.sum(denom, axis=-1)
    positive = np.clip(real, 0.0, None).sum(axis=-1)
    negative = np.clip(real, None, 0.0).sum(axis=-1)
    sign = (positive + negative) / (np.sum(re_abs, axis=-1) + 1e-12)
    return absorption, sign


def _search_axis(data, axis, max_traces=2000, coarse_step=30.0):
    n = data.shape[axis]
    if n < 8:
        return None
    moved = np.moveaxis(data, axis, -1)
    traces = moved.reshape(-1, n)
    real = np.real(data)
    corner = tuple(slice(0, min(16, s)) for s in data.shape)
    noise = float(np.std(real[corner])) if real.size else 0.0
    threshold = max(float(np.percentile(real, 99.5)), noise * 5.0)
    peak_mag = np.max(np.abs(traces), axis=-1)
    signal = peak_mag > threshold
    if not np.any(signal):
        return None
    sig_traces = traces[signal]
    if len(sig_traces) > max_traces:
        order = np.argsort(peak_mag[signal])[::-1][:max_traces]
        sig_traces = sig_traces[order]
    positions = np.argmax(np.abs(sig_traces), axis=-1)
    peak_weights = np.max(np.abs(sig_traces), axis=-1) + 1e-12
    k = np.arange(n, dtype=float)

    def _ramp(p0, p1):
        return np.exp(1j * np.deg2rad(p0 + p1 * k / max(n - 1, 1)))

    def _evaluate(p0, p1):
        rotated = sig_traces * _ramp(p0, p1)
        profiles = _trace_profiles(rotated, positions)
        absorption, sign = _window_metrics(profiles)
        return (
            float(np.average(absorption, weights=peak_weights)),
            float(np.average(sign, weights=peak_weights)),
        )

    best_p0 = 0.0
    best_score = -1.0
    for p0 in np.arange(0.0, 360.0, coarse_step):
        a, _ = _evaluate(float(p0), 0.0)
        if a > best_score:
            best_score, best_p0 = a, float(p0)
    for span, step in ((30.0, 10.0), (10.0, 2.5)):
        for offset in np.arange(-span, span + 1e-9, step):
            p0 = (best_p0 + offset) % 360.0
            a, _ = _evaluate(p0, 0.0)
            if a > best_score:
                best_score, best_p0 = a, p0
    _, sign_here = _evaluate(best_p0, 0.0)
    _, sign_opposite = _evaluate((best_p0 + 180.0) % 360.0, 0.0)
    if sign_opposite > sign_here:
        best_p0 = (best_p0 + 180.0) % 360.0
    best_p1 = 0.0
    best_score = _evaluate(best_p0, 0.0)[0]
    for p1 in np.arange(-90.0, 91.0, 30.0):
        a, _ = _evaluate(best_p0, float(p1))
        if a > best_score:
            best_score, best_p1 = a, float(p1)
    for span, step in ((30.0, 10.0), (10.0, 5.0)):
        for offset in np.arange(-span, span + 1e-9, step):
            p1 = best_p1 + offset
            a, _ = _evaluate(best_p0, p1)
            if a > best_score:
                best_score, best_p1 = a, p1
    return float(best_p0), float(best_p1), float(best_score)


def main() -> int:
    import nmrglue as ng

    _dic, data = ng.pipe.read(str(Path("/tmp/cplx_run/102_cplx.ft3")))
    raw = np.asarray(data)
    cplx = raw[0::2] + 1j * raw[1::2]
    cplx = cplx[:, 0::2] + 1j * cplx[:, 1::2]
    print("complex final:", cplx.shape)
    for ax, name in ((2, "F3/direct"), (0, "F1"), (1, "F2")):
        est = _search_axis(cplx, ax)
        print(f"  axis={ax} ({name}): {est}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
