"""2D Gaussian peak localisation (**2D only**): sub-grid refinement of candidate
peaks that have already been detected.

Parallel to, and never a replacement for, parabolic localisation
(``core.qc.peak_detection``): detection stays with ``peak_detection.detect``; this
module only answers "where are the true centre/width/amplitude of this candidate".
The shared entry point is ``core.peaks.localize``.

Model (no rotation, axis-separable, no xy covariance, no polynomial baseline):

    I(x, y) = B + A * exp( -(x - x0)^2 / (2 sx^2) - (y - y0)^2 / (2 sy^2) )

where **x = data axis 1 (direct dimension F2) and y = data axis 0 (indirect
dimension F1)**; ``scipy.optimize.least_squares`` solves for
``(A, x0, y0, sx, sy, B)`` inside bounds.

Design notes (2026-09-13, user requirement):

- **the caller converts the physical width (ppm) into points and passes the ROI**:
  this module only knows points, which keeps it unit-testable and reusable and stops a
  "fixed point count" from creeping into the core (the conversion lives in
  ``core.peaks.localize``/``core.peaks.axis_units``);
- **the initial centre is the existing parabolic result** (the caller passes a seed
  within a 1x point error), so both methods start from the same candidate and can be
  compared independently;
- **negative peaks**: ``sign`` flips the ROI before fitting (which constrains
  ``A > 0``) and the amplitude/baseline come back in the original units
  (``sign * A``), so mixed-sign experiments keep working;
- **failures are explicit**: any abnormal case returns ``success=False`` plus
  ``reason`` and the caller records it and falls back to the parabola (never silent,
  see core/peaks/localize.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

# sigma lower bound (points): a Gaussian below 0.5 points is a spike and the fit is
# necessarily ill-conditioned
SIGMA_MIN_POINTS = 0.5
# minimum points per ROI axis and minimum finite points (below that we do not fit)
MIN_ROI_POINTS_PER_AXIS = 3
MIN_FINITE_POINTS = 8
# fit failure reasons (stable strings, written to records/logs)
REASON_OK = ""
REASON_NOT_2D = "not_2d"
REASON_ROI_TOO_SMALL = "roi_too_small"
REASON_INSUFFICIENT_DATA = "insufficient_data"
REASON_NON_FINITE = "non_finite"
REASON_FLAT_REGION = "flat_region"
REASON_NO_PEAK = "no_peak"
REASON_OPTIMIZER_ERROR = "optimizer_error"
REASON_NOT_CONVERGED = "not_converged"
REASON_CENTER_AT_BOUNDARY = "center_at_boundary"
REASON_SIGMA_AT_BOUND = "sigma_at_bound"
REASON_POOR_FIT = "poor_fit"
REASON_NON_FINITE_PARAMS = "non_finite_params"


@dataclass
class GaussianFitResult:
    """Result of one 2D Gaussian fit (all coordinates use **data axis order**:
    axis 0 = F1, axis 1 = F2)."""

    success: bool = False
    reason: str = ""
    center: tuple[float, float] = (0.0, 0.0)     # fractional index (absolute, not ROI-local)
    amplitude: float = 0.0                        # original units (sign * A)
    sigma: tuple[float, float] = (0.0, 0.0)       # unit: data points
    baseline: float = 0.0                         # original units (sign * B)
    rmse: float = 0.0                             # residual RMS (original units)
    boundary_hit: bool = False
    n_points: int = 0                             # finite points inside the ROI
    sign: int = 1
    seed: tuple[float, float] = (0.0, 0.0)        # initial centre (parabolic result)
    n_iter: int = 0
    # ROI actually used (data points, inclusive; kept so the fit can be recomputed)
    roi: tuple[int, int, int, int] = (0, 0, 0, 0)  # (lo0, hi0, lo1, hi1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fit_success": bool(self.success),
            "fit_failure_reason": str(self.reason),
            "center_point_f1": float(self.center[0]),
            "center_point_f2": float(self.center[1]),
            "amplitude": float(self.amplitude),
            "sigma_points_f1": float(self.sigma[0]),
            "sigma_points_f2": float(self.sigma[1]),
            "baseline": float(self.baseline),
            "fit_rmse": float(self.rmse),
            "boundary_hit": bool(self.boundary_hit),
            "n_points": int(self.n_points),
            "sign": int(self.sign),
            "seed_point_f1": float(self.seed[0]),
            "seed_point_f2": float(self.seed[1]),
            "n_iter": int(self.n_iter),
            "roi_points": [int(v) for v in self.roi],
        }


def _roi_bounds(seed: float, half: float, size: int) -> tuple[int, int]:
    """Closed interval centred on seed with half-width half (points), clipped to [0, size-1]."""
    lo = int(np.ceil(float(seed) - float(half)))
    hi = int(np.floor(float(seed) + float(half)))
    lo = max(0, min(lo, int(size) - 1))
    hi = max(0, min(hi, int(size) - 1))
    return lo, hi


def _border_median(block: np.ndarray) -> float:
    """Median of the ROI border (first/last row and column): a robust initial value for
    the local constant baseline."""
    edge = np.concatenate(
        [block[0, :], block[-1, :], block[:, 0], block[:, -1]]
    )
    return float(np.median(edge))


def _profile_sigma(
    profile: np.ndarray, center: int, base: float, amplitude: float, fallback: float
) -> float:
    """Half-width at half maximum along the profile through the centre -> sigma (points);
    falls back to ``fallback`` when it cannot be estimated."""
    if amplitude <= 0 or not np.isfinite(amplitude):
        return float(fallback)
    level = base + 0.5 * amplitude
    size = int(profile.size)
    center = int(min(max(center, 0), size - 1))
    if profile[center] < level:
        return float(fallback)
    left = center
    while left - 1 >= 0 and profile[left - 1] >= level:
        left -= 1
    right = center
    while right + 1 < size and profile[right + 1] >= level:
        right += 1
    fwhm = float(right - left) + 1.0
    if fwhm <= 1.0:
        return float(fallback)
    return float(fwhm / 2.3548200450309493)  # 2*sqrt(2 ln 2)


def _model(params: np.ndarray, row: np.ndarray, col: np.ndarray) -> np.ndarray:
    """row/col are index grids for data axes 0/1 (axis 0 = F1 indirect, axis 1 = F2 direct)."""
    amp, c0, c1, s0, s1, base = params
    return base + amp * np.exp(
        -((row - c0) ** 2) / (2.0 * s0 * s0)
        - ((col - c1) ** 2) / (2.0 * s1 * s1)
    )


def fit_gaussian_2d(
    data: Any,
    *,
    seed: tuple[float, float],
    roi: tuple[float, float],
    sign: int = 1,
    max_rmse_ratio: float = 0.0,
    max_nfev: int = 200,
) -> GaussianFitResult:
    """Fit a 2D Gaussian around ``seed`` (fractional index in data axis order).

    seed: initial centre (should be the existing parabolic result); roi: per-axis ROI
    **half-width in points**; sign: ``+1`` positive peak / ``-1`` negative peak (as
    determined from the sign of the ROI intensity); max_rmse_ratio: when > 0,
    ``rmse > ratio * |amplitude|`` reports ``poor_fit``; max_nfev: maximum number of
    function evaluations for ``least_squares``.

    Returns a ``GaussianFitResult``; every failure carries a ``reason`` and nothing is
    raised (except for invalid arguments), so the caller can record it and fall back.
    """
    arr = np.asarray(data)
    if arr.ndim != 2:
        return GaussianFitResult(
            success=False, reason=REASON_NOT_2D, sign=int(sign),
            seed=(float(seed[0]), float(seed[1])),
        )
    real = np.real(arr).astype(float, copy=False)
    size0, size1 = int(real.shape[0]), int(real.shape[1])
    if not (np.isfinite(seed[0]) and np.isfinite(seed[1])):
        # Non-finite initial value (e.g. NaN in the spectrum): fail rather than guess,
        # the caller falls back.
        return GaussianFitResult(
            success=False, reason=REASON_NON_FINITE, sign=int(sign),
            seed=(float(seed[0]), float(seed[1])),
        )
    lo0, hi0 = _roi_bounds(seed[0], roi[0], size0)
    lo1, hi1 = _roi_bounds(seed[1], roi[1], size1)
    result = GaussianFitResult(
        success=False,
        sign=int(sign),
        seed=(float(seed[0]), float(seed[1])),
        roi=(lo0, hi0, lo1, hi1),
    )
    if (
        hi0 - lo0 + 1 < MIN_ROI_POINTS_PER_AXIS
        or hi1 - lo1 + 1 < MIN_ROI_POINTS_PER_AXIS
    ):
        result.reason = REASON_ROI_TOO_SMALL
        return result

    block = real[lo0 : hi0 + 1, lo1 : hi1 + 1]
    finite = np.isfinite(block)
    n_finite = int(finite.sum())
    result.n_points = n_finite
    if not finite.all():
        result.reason = REASON_NON_FINITE
        return result
    if n_finite < MIN_FINITE_POINTS:
        result.reason = REASON_INSUFFICIENT_DATA
        return result

    value = float(sign) * block
    if float(np.ptp(value)) <= 1e-12:
        result.reason = REASON_FLAT_REGION
        return result

    # local constant baseline = ROI border median; amplitude = peak value - baseline
    base_init = _border_median(value)
    peak_value = float(np.max(value))
    amp_init = peak_value - base_init
    if not np.isfinite(amp_init) or amp_init <= 0.0:
        result.reason = REASON_NO_PEAK
        return result

    # fractional centre in local (ROI) coordinates = seed relative to the ROI origin
    c0_init = float(seed[0]) - lo0
    c1_init = float(seed[1]) - lo1
    row = int(round(c0_init))
    col = int(round(c1_init))
    row = min(max(row, 0), value.shape[0] - 1)
    col = min(max(col, 0), value.shape[1] - 1)
    fallback_sigma = max(SIGMA_MIN_POINTS * 1.5, 0.5 * float(roi[0]))
    s0_init = _profile_sigma(value[:, col], row, base_init, amp_init, fallback_sigma)
    fallback_sigma1 = max(SIGMA_MIN_POINTS * 1.5, 0.5 * float(roi[1]))
    s1_init = _profile_sigma(value[row, :], col, base_init, amp_init, fallback_sigma1)

    # bounds: the centre stays inside the ROI; sigma in [0.5, ROI half-width]; the
    # baseline stays inside the ROI value range
    c0_lo, c0_hi = 0.0, float(value.shape[0] - 1)
    c1_lo, c1_hi = 0.0, float(value.shape[1] - 1)
    s0_hi = max(SIGMA_MIN_POINTS * 2.0, float(roi[0]))
    s1_hi = max(SIGMA_MIN_POINTS * 2.0, float(roi[1]))
    v_lo, v_hi = float(np.min(value)), float(np.max(value))
    if v_hi <= v_lo:
        result.reason = REASON_FLAT_REGION
        return result
    # Baseline bounds: the true baseline is <= the ROI minimum (the peak top has height,
    # so the corners still sit above the baseline), hence the lower bound must allow one
    # ROI dynamic range downwards or the solution is excluded from the start (measured:
    # it shrinks the amplitude by 3%+). The upper bound does not exceed the ROI maximum.
    span = float(v_hi - v_lo)
    b_lo, b_hi = float(v_lo - span), float(v_hi)
    p0 = np.array(
        [
            min(max(amp_init, 0.0), max(v_hi - v_lo, 1e-12)),
            min(max(c0_init, c0_lo), c0_hi),
            min(max(c1_init, c1_lo), c1_hi),
            min(max(s0_init, SIGMA_MIN_POINTS), s0_hi),
            min(max(s1_init, SIGMA_MIN_POINTS), s1_hi),
            min(max(base_init, b_lo), b_hi),
        ],
        dtype=float,
    )
    lower = np.array([0.0, c0_lo, c1_lo, SIGMA_MIN_POINTS, SIGMA_MIN_POINTS, b_lo])
    upper = np.array([np.inf, c0_hi, c1_hi, s0_hi, s1_hi, b_hi])
    # least_squares requires a strict lo < hi
    for idx in (1, 2, 3, 4, 5):
        if not (lower[idx] < upper[idx]):
            result.reason = REASON_ROI_TOO_SMALL
            return result

    row_grid, col_grid = np.mgrid[0 : value.shape[0], 0 : value.shape[1]]
    row_grid = row_grid.astype(float)
    col_grid = col_grid.astype(float)

    def residual(params: np.ndarray) -> np.ndarray:
        return (_model(params, row_grid, col_grid) - value).ravel()

    def jacobian(params: np.ndarray) -> np.ndarray:
        """Analytic Jacobian (2026-09-14): numeric differencing costs about 7 residual
        evaluations per iteration, this costs 1.

        I = B + A*exp(-d0^2/(2 s0^2) - d1^2/(2 s1^2)), d0 = r-c0, d1 = c-c1;
        partial derivatives with respect to (A, c0, c1, s0, s1, B), shape (m, 6).
        """
        amp, c0, c1, s0, s1, _base = (float(v) for v in params)
        d0 = row_grid - c0
        d1 = col_grid - c1
        e = np.exp(
            -(d0 * d0) / (2.0 * s0 * s0) - (d1 * d1) / (2.0 * s1 * s1)
        )
        return np.column_stack(
            [
                e.ravel(),
                (amp * e * d0 / (s0 * s0)).ravel(),
                (amp * e * d1 / (s1 * s1)).ravel(),
                (amp * e * d0 * d0 / (s0**3)).ravel(),
                (amp * e * d1 * d1 / (s1**3)).ravel(),
                np.ones(e.size),
            ]
        )

    try:
        from scipy.optimize import least_squares

        fit = least_squares(
            residual,
            p0,
            bounds=(lower, upper),
            max_nfev=int(max_nfev),
            jac=jacobian,
        )
    except Exception as exc:  # noqa: BLE001 - record the reason for any optimiser failure
        result.reason = f"{REASON_OPTIMIZER_ERROR}: {type(exc).__name__}: {exc}"
        return result

    amp, c0, c1, s0, s1, base = (float(v) for v in fit.x)
    result.n_iter = int(getattr(fit, "nfev", 0) or 0)
    sv = np.asarray(fit.x, dtype=float)
    if not np.all(np.isfinite(sv)):
        result.reason = REASON_NON_FINITE_PARAMS
        return result
    if not bool(getattr(fit, "success", False)):
        result.reason = REASON_NOT_CONVERGED
        return result

    # centre/width hit the bounds -> explicit failure (the caller falls back to the parabola)
    tol = 1e-3
    center_hit = (
        c0 <= c0_lo + tol
        or c0 >= c0_hi - tol
        or c1 <= c1_lo + tol
        or c1 >= c1_hi - tol
    )
    sigma_hit = (
        s0 <= SIGMA_MIN_POINTS * (1.0 + 1e-3)
        or s1 <= SIGMA_MIN_POINTS * (1.0 + 1e-3)
        or s0 >= s0_hi * (1.0 - 1e-3)
        or s1 >= s1_hi * (1.0 - 1e-3)
    )
    rmse = float(np.sqrt(np.mean(residual(fit.x) ** 2)))
    result.amplitude = float(sign) * amp
    result.baseline = float(sign) * base
    result.sigma = (s0, s1)
    result.rmse = rmse
    result.center = (lo0 + c0, lo1 + c1)
    result.boundary_hit = bool(center_hit or sigma_hit)
    if center_hit:
        result.reason = REASON_CENTER_AT_BOUNDARY
        return result
    if sigma_hit:
        result.reason = REASON_SIGMA_AT_BOUND
        return result
    if max_rmse_ratio and max_rmse_ratio > 0.0:
        if rmse > float(max_rmse_ratio) * abs(amp):
            result.reason = REASON_POOR_FIT
            return result
    if amp <= 0.0:
        result.reason = REASON_NO_PEAK
        return result
    result.success = True
    result.reason = REASON_OK
    return result


__all__ = [
    "MIN_FINITE_POINTS",
    "MIN_ROI_POINTS_PER_AXIS",
    "REASON_CENTER_AT_BOUNDARY",
    "REASON_FLAT_REGION",
    "REASON_INSUFFICIENT_DATA",
    "REASON_NON_FINITE",
    "REASON_NOT_2D",
    "REASON_NOT_CONVERGED",
    "REASON_NO_PEAK",
    "REASON_OK",
    "REASON_OPTIMIZER_ERROR",
    "REASON_POOR_FIT",
    "REASON_ROI_TOO_SMALL",
    "REASON_SIGMA_AT_BOUND",
    "SIGMA_MIN_POINTS",
    "GaussianFitResult",
    "fit_gaussian_2d",
]
