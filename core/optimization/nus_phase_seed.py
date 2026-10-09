"""Conservative zero-order bootstrap for a sampled 2D zero-increment pair.

At zero indirect evolution, coherent quadrature rows share the same direct
complex lineshape. A real two-channel Gram matrix estimates their mixing angle
without Fourier transforming the sparse indirect dimension. This is a seed,
not a final phase measurement: no first-order phase is inferred from one pair.
"""

from __future__ import annotations

from typing import Any

import numpy as np


def zero_increment_phase_seed(
    real_row: np.ndarray,
    imaginary_row: np.ndarray,
    *,
    indirect_neg: bool = False,
    sign_mode: str = "uniform",
) -> dict[str, Any]:
    """Return a gated joint P0 seed, or an explicit rejection reason.

    Rows must be the converted, unphased zero-increment quadrature pair with the
    direct dimension already Fourier transformed. The indirect angle is known
    only modulo 180 degrees; direct peak phases choose a consistent joint sign.
    Mixed-sign spectra do not receive a positive-peak preference.
    """
    rejected: dict[str, Any] = {
        "accepted": False, "method": "zero_increment_quadrature", "reason": "",
    }
    r = np.asarray(real_row, dtype=np.complex128)
    i = np.asarray(imaginary_row, dtype=np.complex128)
    if r.ndim != 1 or i.shape != r.shape or r.size < 32:
        return {**rejected, "reason": "invalid_quadrature_shape"}
    if not np.isfinite(r).all() or not np.isfinite(i).all():
        return {**rejected, "reason": "nonfinite_quadrature"}
    scale = max(float(np.max(abs(r))), float(np.max(abs(i))))
    if scale <= 0:
        return {**rejected, "reason": "empty_zero_increment"}
    r, i = r / scale, i / scale
    power = abs(r) ** 2 + abs(i) ** 2
    selected = (power >= np.percentile(power, 90)) & (power > 0)
    if np.count_nonzero(selected) < 8:
        return {**rejected, "reason": "insufficient_zero_increment_signal"}
    gram = np.array([
        [np.vdot(r[selected], r[selected]).real, np.vdot(r[selected], i[selected]).real],
        [np.vdot(i[selected], r[selected]).real, np.vdot(i[selected], i[selected]).real],
    ])
    values, vectors = np.linalg.eigh(gram)
    # A strongly rank-one pair is required. Delayed evolution, antiphase
    # structure or incoherent noise must not be assigned a confident seed.
    fraction = float(values[-1] / max(float(values.sum()), np.finfo(float).tiny))
    if fraction < 0.95:
        return {**rejected, "reason": "incoherent_zero_increment", "rank_fraction": fraction}
    v = vectors[:, -1]
    indirect_p0 = float(-np.rad2deg(np.arctan2(v[1], v[0])) % 180.0)
    angle = np.deg2rad(indirect_p0)
    trace = r * np.cos(angle) - i * np.sin(angle)
    amplitude = abs(trace)
    peaks = np.flatnonzero(
        (amplitude[1:-1] > amplitude[:-2]) & (amplitude[1:-1] > amplitude[2:])
    ) + 1
    peaks = peaks[(peaks >= 8) & (peaks < r.size - 8)]
    peaks = peaks[amplitude[peaks] >= 0.1 * np.max(amplitude)]
    peaks = peaks[np.argsort(amplitude[peaks])[-16:]]
    if peaks.size < 2:
        return {**rejected, "reason": "insufficient_direct_peaks", "rank_fraction": fraction}
    weights = amplitude[peaks]
    resultant = np.sum(weights * np.exp(2j * np.angle(trace[peaks])))
    concentration = float(abs(resultant) / np.sum(weights))
    if concentration < 0.5:
        return {**rejected, "reason": "incoherent_direct_phase", "rank_fraction": fraction,
                "phase_concentration": concentration}
    direct_p0 = float(-np.angle(resultant, deg=True) / 2 % 180.0)
    if sign_mode == "mixed":
        direct_p0 = (direct_p0 + 90.0) % 180.0 - 90.0
    elif float(np.sum(weights * (trace[peaks] * np.exp(1j*np.deg2rad(direct_p0))).real)) < 0:
        direct_p0 += 180.0
    # FT -neg conjugates the indirect quadrature before transforming it.
    if indirect_neg:
        indirect_p0 = -indirect_p0
    return {
        "accepted": True, "method": "zero_increment_quadrature", "reason": "",
        "phases": {"F1": [indirect_p0 % 360.0, 0.0]},
        "direct_phase": [direct_p0 % 360.0, 0.0],
        "rank_fraction": fraction, "phase_concentration": concentration,
        "direct_peak_count": int(peaks.size), "first_order_inferred": False,
    }
