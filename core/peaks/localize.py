"""Unified peak localization entry point: dispatch the **sub-grid refinement** method that runs
after detection.

Flow and responsibility boundary (2026-09-13, user requirement):

    peak detection (core.qc.peak_detection)
              |  integer-grid maxima of the candidate peaks
        peak localization (this module)
          +-- parabolic  the existing 3-point parabola (default; behaviour **completely unchanged**)
          +-- gaussian   2D Gaussian fitting (**2D only**; see core.peaks.gaussian_fit)

Gaussian fitting **never performs peak detection**: it only fits locally around candidates that
have already been detected, leaving thresholds, noise estimation and sign modes untouched.

The ROI is always given as a **physical width (ppm)** (config
``peaks.localization.gaussian_roi_f*_ppm`` or a function argument) and converted to points at
runtime
by ``core.peaks.axis_units`` from the current point spacing -- the same convention as the
peak-picking
margin and the measurement window, so zero filling cannot change the ppm range the ROI covers.

Failures are never silent: ``PeakLocalization`` carries ``requested_method``/``actual_method``/
``success``/``fallback``/``reason`` and the caller writes them into the run record and the
peak-table
attachment.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from core.peaks import axis_units
from core.peaks.gaussian_fit import (
    GaussianFitResult,
    fit_gaussian_2d,
)
from core.project.manager import atomic_write_text
from core.qc import peak_detection
from ui_support.i18n import tr

LOCALIZATION_METHODS: tuple[str, ...] = ("parabolic", "gaussian")
DEFAULT_LOCALIZATION_METHOD = "parabolic"
#: Gaussian fitting supports 2D only (user requirement: the GUI/API must refuse other dimensions
#: explicitly)
GAUSSIAN_SUPPORTED_NDIM = 2
GAUSSIAN_UNSUPPORTED_MESSAGE = (
    "Gaussian peak fitting is currently supported only for 2D spectra."
)
#: Default ROI radius (ppm). The direct dimension has narrow peaks and the indirect ones wide ones,
#: so these are reasonable orders of magnitude; the real values can be overridden through the config
#: section `peaks.localization` or a function argument.
DEFAULT_GAUSSIAN_ROI_F1_PPM = 1.5
DEFAULT_GAUSSIAN_ROI_F2_PPM = 0.25
#: **Maximum half-width in points** of the fitting window per axis: 0 = unlimited (the default,
#: which
#: matches the older behaviour). A positive value (48, say) caps the fitting size on fine
#: (zero-filled)
#: grids in exchange for speed; when it triggers it is recorded per peak (roi_capped) and a
#: failure is
#: retried once with the full ROI (see localize_peak_gaussian_2d).
DEFAULT_GAUSSIAN_ROI_MAX_POINTS = 0
#: Maximum number of least-squares function evaluations per peak: a failing fit no longer runs to
#: the
#: cap (400 by default before, 200 now).
DEFAULT_GAUSSIAN_MAX_NFEV = 200
FWHM_FACTOR = 2.3548200450309493  # 2*sqrt(2 ln 2)

LOCALIZATION_LABELS: dict[str, str] = {
    "parabolic": tr("parabolic line"),
    "gaussian": tr("2D Gaussian fitting"),
}

# Peak-table attachment (same directory and stem as the Poky .list, plus a suffix): per-peak
# Gaussian
# diagnostics
LOCALIZATION_RECORDS_SUFFIX = ".localization.json"


class LocalizationError(ValueError):
    """Invalid peak-localization parameters (unknown method / unsupported dimensionality)."""


@dataclass
class PeakLocalization:
    """The localization result of one candidate peak (requested and actual kept apart for
    records and
    comparison)."""

    requested_method: str
    actual_method: str
    position: tuple[float, ...] = ()
    success: bool = True
    fallback: bool = False
    reason: str = ""
    gaussian: GaussianFitResult | None = None
    ppm: dict[str, float] = field(default_factory=dict)
    # precomputed centre/width (ppm) per logical F1/F2 axis, so to_dict never has to guess the axis
    # order
    derived: dict[str, float] = field(default_factory=dict)
    # fitting-size record (ROI points / whether the cap truncated them / the iteration budget)
    fit_meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, ppm_axes: Sequence[Any] | None = None) -> dict[str, Any]:
        """Dictionary for records: method provenance plus Gaussian diagnostics (the field names
        of the
        user requirement §8)."""
        out: dict[str, Any] = {
            "localization_method": str(self.actual_method),
            "requested_method": str(self.requested_method),
            "actual_method": str(self.actual_method),
            "gaussian_fit_success": bool(
                self.success and self.requested_method == "gaussian"
            ),
            "fallback": bool(self.fallback),
            "fallback_reason": str(self.reason),
        }
        if self.fit_meta:
            out.update({str(k): v for k, v in self.fit_meta.items()})
        if self.ppm:
            out.update({f"position_ppm_{k}": float(v) for k, v in self.ppm.items()})
        if self.position:
            out["position_points"] = [float(v) for v in self.position]
        fit = self.gaussian
        if fit is None:
            return out
        out.update(fit.to_dict())
        out["fit_failure_reason"] = str(fit.reason)
        if self.derived:
            out.update({k: float(v) for k, v in self.derived.items()})
            return out
        if ppm_axes is not None and len(ppm_axes) >= 2 and len(fit.center) >= 2:
            # data axis order: 0 = F1 (indirect), 1 = F2 (direct)
            axes = list(ppm_axes)
            step0 = axis_units.ppm_per_point(axes[0])
            step1 = axis_units.ppm_per_point(axes[1])
            out.update(
                {
                    "center_f1": ppm_from_point(axes[0], fit.center[0]),
                    "center_f2": ppm_from_point(axes[1], fit.center[1]),
                    "sigma_f1": float(fit.sigma[0]) * step0,
                    "sigma_f2": float(fit.sigma[1]) * step1,
                    "fwhm_f1": float(fit.sigma[0]) * step0 * FWHM_FACTOR,
                    "fwhm_f2": float(fit.sigma[1]) * step1 * FWHM_FACTOR,
                }
            )
        return out


def ppm_from_point(axis_ppm: Sequence[float], point: float) -> float:
    """Fractional index -> ppm (linear interpolation, the same convention as the peak table)."""
    arr = np.asarray(axis_ppm, dtype=float)
    if arr.size == 0:
        return 0.0
    if arr.size == 1:
        return float(arr[0])
    return float(np.interp(float(point), np.arange(arr.size, dtype=float), arr))


def normalize_localization_method(method: Any) -> str:
    """Normalise a method name (case- and alias-insensitive); an unknown method raises."""
    name = str(method or DEFAULT_LOCALIZATION_METHOD).strip().lower()
    aliases = {
        "parabolic": "parabolic",
        "parabola": "parabolic",
        "parabolic line": "parabolic",
        "gaussian": "gaussian",
        "gaussian_fit": "gaussian",
        "gauss": "gaussian",
        "Gaussian": "gaussian",
        "Gaussian fitting": "gaussian",
    }
    if name not in aliases:
        raise LocalizationError(
            tr(
                "Unknown peak localization method: {p0!r}(Available: parabolic / "
                "gaussian)",
                p0=method,
            )
        )
    return aliases[name]


def localization_supported(method: Any, ndim: int) -> bool:
    """Whether this dimensionality supports the method (Gaussian is 2D only; parabolic any)."""
    try:
        name = normalize_localization_method(method)
    except LocalizationError:
        return False
    if name == "gaussian":
        return int(ndim) == GAUSSIAN_SUPPORTED_NDIM
    return True


def localization_label(method: Any) -> str:
    """Display name of the method (for logs and the GUI)."""
    try:
        return LOCALIZATION_LABELS[normalize_localization_method(method)]
    except LocalizationError:
        return str(method or "")


def load_localization_defaults(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Defaults of the config section ``peaks.localization`` (built-in defaults when unreadable).

    Returns ``{"method": "parabolic", "gaussian_roi_f1_ppm": float, "gaussian_roi_f2_ppm": float}``;
    an invalid value always falls back to the default instead of raising.
    """
    section: dict[str, Any] = {}
    try:
        from backend.config import load_config

        cfg = load_config(config) or {}
        section = (cfg.get("peaks") or {}).get("localization") or {}
        if not isinstance(section, dict):
            section = {}
    except Exception:  # noqa: BLE001 - an unreadable config falls back to the built-in defaults
        section = {}
    try:
        method = normalize_localization_method(
            section.get("method", DEFAULT_LOCALIZATION_METHOD)
        )
    except LocalizationError:
        method = DEFAULT_LOCALIZATION_METHOD

    def _positive(key: str, fallback: float) -> float:
        try:
            value = float(section.get(key, fallback))
        except (TypeError, ValueError):
            return float(fallback)
        if not np.isfinite(value) or value <= 0:
            return float(fallback)
        return value

    return {
        "method": method,
        "gaussian_roi_f1_ppm": _positive(
            "gaussian_roi_f1_ppm", DEFAULT_GAUSSIAN_ROI_F1_PPM
        ),
        "gaussian_roi_f2_ppm": _positive(
            "gaussian_roi_f2_ppm", DEFAULT_GAUSSIAN_ROI_F2_PPM
        ),
        "gaussian_roi_max_points": _nonnegative_int(
            section.get("gaussian_roi_max_points"),
            DEFAULT_GAUSSIAN_ROI_MAX_POINTS,
        ),
        "gaussian_max_nfev": _positive_int(
            section.get("gaussian_max_nfev"), DEFAULT_GAUSSIAN_MAX_NFEV
        ),
    }


def _positive_int(value: Any, fallback: int) -> int:
    """Positive integer (an invalid or non-positive value falls back)."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return int(fallback)
    return number if number > 0 else int(fallback)


def _nonnegative_int(value: Any, fallback: int) -> int:
    """Non-negative integer (0 is valid = unlimited; invalid or negative falls back)."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return int(fallback)
    return number if number >= 0 else int(fallback)


def _finite_position(
    position: Sequence[float], index: Sequence[float]
) -> tuple[float, ...]:
    """Non-finite components of a position (the parabola yields NaN when the spectrum has NaN) fall
    back to the integer-grid candidate.

    The new Gaussian path must not let NaN into the peak table or the records; the existing
    parabolic
    function itself is left as it was (backwards compatible) and this guard only sits at the
    **Gaussian
    fallback exit**.
    """
    return tuple(
        float(v) if np.isfinite(v) else float(index[i])
        for i, v in enumerate(position)
    )


def localize_peak_parabolic(data: Any, index: Sequence[int]) -> PeakLocalization:
    """The existing parabolic localization (the same implementation as
    ``peak_detection._refined_index``)."""
    real = np.real(np.asarray(data)).astype(float, copy=False)
    idx = [
        int(min(max(int(v), 0), real.shape[axis] - 1))
        for axis, v in enumerate(index)
    ]
    position = tuple(
        peak_detection.refine_parabolic(real, idx, axis)
        for axis in range(real.ndim)
    )
    return PeakLocalization(
        requested_method="parabolic",
        actual_method="parabolic",
        position=position,
        success=True,
    )


def localize_peak_gaussian_2d(
    data: Any,
    index: Sequence[int],
    *,
    sign: int = 1,
    ppm_axes: Sequence[Any] | None = None,
    roi_f1_ppm: float | None = None,
    roi_f2_ppm: float | None = None,
    logical_axes: Sequence[int] | None = None,
    max_rmse_ratio: float = 0.0,
    max_nfev: int = DEFAULT_GAUSSIAN_MAX_NFEV,
    roi_max_points: int | None = None,
) -> PeakLocalization:
    """2D Gaussian localization: ROI (ppm) -> points -> local fit seeded with the parabolic result.

    A non-2D spectrum raises ``LocalizationError`` (no downgrade, nothing silent); a failed fit, a
    non-converged one or a boundary hit **falls back to the parabola** and says why in ``reason``
    (never silently).
    """
    real = np.real(np.asarray(data)).astype(float, copy=False)
    if real.ndim != GAUSSIAN_SUPPORTED_NDIM:
        raise LocalizationError(GAUSSIAN_UNSUPPORTED_MESSAGE)
    if ppm_axes is None or len(ppm_axes) < 2:
        raise LocalizationError(tr(
            "2D Gaussian fitting requires ppm axis array for each data "
            "axis",
        ))
    axes_map = (
        [int(v) for v in logical_axes]
        if logical_axes is not None
        else list(range(real.ndim))
    )
    if len(axes_map) < 2:
        axes_map = list(range(real.ndim))
    defaults = load_localization_defaults()
    roi1 = float(
        roi_f1_ppm if roi_f1_ppm is not None else defaults["gaussian_roi_f1_ppm"]
    )
    roi2 = float(
        roi_f2_ppm if roi_f2_ppm is not None else defaults["gaussian_roi_f2_ppm"]
    )
    axis_f1, axis_f2 = int(axes_map[0]), int(axes_map[1])
    # initial centre = the parabolic result (user requirement §4): both methods start from the same
    # candidate and can be compared independently
    parabolic = localize_peak_parabolic(real, index)
    idx = [
        float(min(max(int(v), 0), real.shape[axis] - 1))
        for axis, v in enumerate(index)
    ]
    seed = (
        float(parabolic.position[0])
        if np.isfinite(parabolic.position[0])
        else idx[0],
        float(parabolic.position[1])
        if np.isfinite(parabolic.position[1])
        else idx[1],
    )
    # the ROI is a **physical width**: the ppm radius of F1/F2 is converted with the current point
    # spacing of that axis
    roi_points = [
        axis_units.points_for_ppm(ppm_axes[axis_f1], roi1, minimum=1),
        axis_units.points_for_ppm(ppm_axes[axis_f2], roi2, minimum=1),
    ]
    # fitting-size cap: on a fine (zero-filled) grid the ROI point count grows linearly with the
    # point
    # spacing, so the cost per peak grows linearly; capping the half-width per axis (48 points by
    # default) only affects grids finer than the cap (recorded as roi_capped).
    cap = int(
        roi_max_points
        if roi_max_points is not None
        else defaults["gaussian_roi_max_points"]
    )
    fit_points = (
        [int(p) for p in roi_points]
        if cap <= 0
        else [min(int(p), cap) for p in roi_points]
    )
    roi_capped = [
        int(capped) != int(raw)
        for capped, raw in zip(fit_points, roi_points)
    ]
    if min(roi_points) <= 0:
        return PeakLocalization(
            requested_method="gaussian",
            actual_method="parabolic",
            position=_finite_position(parabolic.position, idx),
            success=False,
            fallback=True,
            reason="roi_unavailable",
        )
    roi_by_axis = [0.0, 0.0]
    roi_by_axis[axis_f1] = float(fit_points[0])
    roi_by_axis[axis_f2] = float(fit_points[1])
    roi_by_axis_raw = [0.0, 0.0]
    roi_by_axis_raw[axis_f1] = float(roi_points[0])
    roi_by_axis_raw[axis_f2] = float(roi_points[1])
    fit = fit_gaussian_2d(
        real,
        seed=seed,
        roi=(roi_by_axis[0], roi_by_axis[1]),
        sign=1 if int(sign) >= 0 else -1,
        max_rmse_ratio=float(max_rmse_ratio or 0.0),
        max_nfev=int(max_nfev),
    )
    if not fit.success and any(roi_capped):
        # the cap may have shrunk the ROI -> retry once with the full ROI (the cost is paid only on
        # hard-to-converge peaks); only if that still fails does it fall back to the parabola,
        # and both
        # attempts stay in fit_meta.
        retry = fit_gaussian_2d(
            real,
            seed=seed,
            roi=(roi_by_axis_raw[0], roi_by_axis_raw[1]),
            sign=1 if int(sign) >= 0 else -1,
            max_rmse_ratio=float(max_rmse_ratio or 0.0),
            max_nfev=int(max_nfev),
        )
        if retry.success:
            position = tuple(
                retry.center[axis] if axis in (0, 1) else parabolic.position[axis]
                for axis in range(real.ndim)
            )
            ppm = {
                "F1": ppm_from_point(ppm_axes[axis_f1], position[axis_f1]),
                "F2": ppm_from_point(ppm_axes[axis_f2], position[axis_f2]),
            }
            meta = _fit_meta(fit_points, roi_points, roi_capped, max_nfev)
            meta["fit_retry_uncapped"] = True
            meta["roi_half_points"] = [int(p) for p in roi_points]
            meta["retry_n_iter"] = int(retry.n_iter)
            step_f1 = axis_units.ppm_per_point(ppm_axes[axis_f1])
            step_f2 = axis_units.ppm_per_point(ppm_axes[axis_f2])
            return PeakLocalization(
                requested_method="gaussian",
                actual_method="gaussian",
                position=position,
                success=True,
                gaussian=retry,
                ppm=ppm,
                derived={
                    "center_f1": ppm["F1"],
                    "center_f2": ppm["F2"],
                    "sigma_f1": float(retry.sigma[axis_f1]) * step_f1,
                    "sigma_f2": float(retry.sigma[axis_f2]) * step_f2,
                    "fwhm_f1": float(retry.sigma[axis_f1]) * step_f1 * FWHM_FACTOR,
                    "fwhm_f2": float(retry.sigma[axis_f2]) * step_f2 * FWHM_FACTOR,
                },
                fit_meta=meta,
            )
    if not fit.success:
        # fall back to the parabola: the position is still usable, but requested/actual/reason
        # are all
        # recorded (never silent)
        return PeakLocalization(
            requested_method="gaussian",
            actual_method="parabolic",
            position=_finite_position(parabolic.position, idx),
            success=False,
            fallback=True,
            reason=fit.reason,
            gaussian=fit,
        )
    position = tuple(
        fit.center[axis] if axis in (0, 1) else parabolic.position[axis]
        for axis in range(real.ndim)
    )
    ppm = {
        "F1": ppm_from_point(ppm_axes[axis_f1], position[axis_f1]),
        "F2": ppm_from_point(ppm_axes[axis_f2], position[axis_f2]),
    }
    step_f1 = axis_units.ppm_per_point(ppm_axes[axis_f1])
    step_f2 = axis_units.ppm_per_point(ppm_axes[axis_f2])
    sigma_f1 = float(fit.sigma[axis_f1])
    sigma_f2 = float(fit.sigma[axis_f2])
    derived = {
        "center_f1": ppm["F1"],
        "center_f2": ppm["F2"],
        "sigma_f1": sigma_f1 * step_f1,
        "sigma_f2": sigma_f2 * step_f2,
        "fwhm_f1": sigma_f1 * step_f1 * FWHM_FACTOR,
        "fwhm_f2": sigma_f2 * step_f2 * FWHM_FACTOR,
    }
    return PeakLocalization(
        requested_method="gaussian",
        actual_method="gaussian",
        position=position,
        success=True,
        gaussian=fit,
        ppm=ppm,
        derived=derived,
        fit_meta=_fit_meta(fit_points, roi_points, roi_capped, max_nfev),
    )


def _fit_meta(
    fit_points: list[int],
    roi_points: list[int],
    roi_capped: list[bool],
    max_nfev: int,
) -> dict[str, Any]:
    """Fitting-size record: the actual half-width in points, the axes truncated by the cap and the
    iteration budget."""
    return {
        "roi_half_points": [int(p) for p in fit_points],
        "roi_half_points_uncapped": [int(p) for p in roi_points],
        "roi_capped": bool(any(roi_capped)),
        "roi_capped_axes": [
            axis for axis, flag in zip(("F1", "F2"), roi_capped) if flag
        ],
        "max_nfev": int(max_nfev),
    }


def localize_peak(
    data: Any,
    index: Sequence[int],
    *,
    method: Any = DEFAULT_LOCALIZATION_METHOD,
    sign: int = 1,
    ppm_axes: Sequence[Any] | None = None,
    roi_f1_ppm: float | None = None,
    roi_f2_ppm: float | None = None,
    logical_axes: Sequence[int] | None = None,
    max_rmse_ratio: float = 0.0,
    max_nfev: int = DEFAULT_GAUSSIAN_MAX_NFEV,
    roi_max_points: int | None = None,
) -> PeakLocalization:
    """Unified peak-localization entry point: ``method="parabolic"`` (default) or ``"gaussian"``
    (2D only).

    ``index`` is the integer-grid maximum of the candidate peak (one per axis); the parabolic method
    refines each axis independently while the Gaussian method uses the parabolic result as the
    seed of a
    2D local fit. Both run on exactly the same candidate, so their results compare directly.

    Parameters
    ----------
    data : Any
        2D spectrum data (the real part is refined); axis 0 = indirect dimension, axis 1 = direct.
    index : Sequence[int]
        Integer-grid maximum position of the candidate peak (one per axis).
    method : Any, default "parabolic"
        ``parabolic`` (default) or ``gaussian``; case and the local-language aliases are
        accepted, an
        unknown method raises.
    sign : int, default 1
        Peak sign (``+1`` positive / ``-1`` negative); it only affects the Gaussian fit.
    ppm_axes : Sequence[Any], optional
        The ppm axis array of each data axis; **required** for the Gaussian method (not used by the
        parabola).
    roi_f1_ppm, roi_f2_ppm : float, optional
        Gaussian fitting ROI radius (a physical width in ppm; converted to points at the current
        point
        spacing).
    logical_axes : Sequence[int], optional
        Data axis -> logical axis mapping (give it when the axis order is not the default).
    max_rmse_ratio : float, default 0.0
        Upper limit as a ratio of the fit residual (0 = disabled).
    max_nfev : int, default 200
        Maximum number of least-squares function evaluations per peak.
    roi_max_points : int | None, optional
        Cap on the ROI half-width in points per axis (0/None = unlimited; when capped, a failure
        retries once with the full ROI).

    Returns
    -------
    PeakLocalization
        ``requested_method``/``actual_method``/``position``/``success``/``fallback``/``reason``
        plus the
        Gaussian diagnostics (centre, sigma, FWHM, RMSE, whether the boundary was hit).

    Raises
    ------
    LocalizationError
        An unknown method, or Gaussian requested for non-2D data (no silent downgrade to the
        parabola).

    Side effects
    ------------
    Pure computation: writes no files and does not modify the spectrum.

    Examples
    --------
        refined = localize_peak(data, (30, 60), method="gaussian", ppm_axes=axes)
        if refined.fallback:
            ...  # the fit failed: actual_method is parabolic and reason says why
    """
    name = normalize_localization_method(method)
    real = np.real(np.asarray(data))
    if name == "gaussian":
        if real.ndim != GAUSSIAN_SUPPORTED_NDIM:
            raise LocalizationError(GAUSSIAN_UNSUPPORTED_MESSAGE)
        return localize_peak_gaussian_2d(
            real,
            index,
            sign=sign,
            ppm_axes=ppm_axes,
            roi_f1_ppm=roi_f1_ppm,
            roi_f2_ppm=roi_f2_ppm,
            logical_axes=logical_axes,
            max_rmse_ratio=max_rmse_ratio,
            max_nfev=max_nfev,
            roi_max_points=roi_max_points,
        )
    return localize_peak_parabolic(real, index)


def localization_records_path(peak_path: Path | str) -> Path:
    """Peak-table attachment path: ``<peak table>.localization.json`` (same directory as the
    .list)."""
    path = Path(peak_path)
    return path.with_name(path.name + LOCALIZATION_RECORDS_SUFFIX)


def write_localization_records(
    peak_path: Path | str,
    records: Sequence[dict[str, Any]],
    *,
    meta: dict[str, Any] | None = None,
) -> Path:
    """Write the per-peak localization diagnostics (a JSON attachment; the Poky .list format is
    unchanged)."""
    target = localization_records_path(peak_path)
    payload = {
        "peak_table": str(Path(peak_path)),
        "meta": dict(meta or {}),
        "peaks": [dict(record) for record in records],
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        target,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )
    return target


def trim_localization_records(peak_path: Path | str, keep: int) -> bool:
    """When the peak table at **the same path** is trimmed in place, truncate the attachment too
    (keeping
    the row order aligned).

    The attachment row order matches the peak-table row order (both descending by |Intensity|),
    so keeping
    the first ``keep`` rows is enough. Note that the API ``max_peaks`` trims the **frozen
    reference table**
    (``reference.list``, copied from the data-side `.list` and carrying no attachment), so in
    the normal
    flow this function is a defensive no-op -- verified on real data: the data-side `.list` had
    76 peaks
    with a 76-row attachment while the reference table had 60 peaks, each self-consistent already. A
    missing or corrupt attachment returns False (nothing is raised).
    """
    target = localization_records_path(peak_path)
    if not target.is_file():
        return False
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    peaks = payload.get("peaks") if isinstance(payload, dict) else None
    if not isinstance(peaks, list) or len(peaks) <= int(keep):
        return False
    payload["peaks"] = peaks[: int(keep)]
    atomic_write_text(
        target,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )
    return True


def read_localization_records(peak_path: Path | str) -> list[dict[str, Any]]:
    """Read the per-peak localization diagnostics; a missing or corrupt file yields an empty
    list."""
    target = localization_records_path(peak_path)
    if not target.is_file():
        return []
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    peaks = payload.get("peaks") if isinstance(payload, dict) else None
    if not isinstance(peaks, list):
        return []
    return [dict(row) for row in peaks if isinstance(row, dict)]


def summarize_localization(
    records: Sequence[dict[str, Any]],
    *,
    method: str,
    roi_f1_ppm: float | None = None,
    roi_f2_ppm: float | None = None,
) -> dict[str, Any]:
    """Per-peak records -> a run-record summary (user requirement §13: method + ROI + counts).

    The parabolic mode performs no Gaussian fit, so all three gaussian_*_count values are 0
    (nothing is
    overstated).
    """
    total = len(records)
    if str(method) != "gaussian":
        return {
            "peak_localization_method": str(method),
            "n_peaks": int(total),
            "gaussian_fit_success_count": 0,
            "gaussian_fit_failure_count": 0,
            "gaussian_fallback_count": 0,
            "fallback_reasons": {},
        }
    success = sum(
        1
        for row in records
        if row.get("actual_method") == "gaussian" and row.get("gaussian_fit_success")
    )
    fallback = sum(1 for row in records if row.get("fallback"))
    failures: dict[str, int] = {}
    for row in records:
        if not row.get("fallback"):
            continue
        reason = str(row.get("fallback_reason", "") or "")
        failures[reason] = failures.get(reason, 0) + 1
    summary: dict[str, Any] = {
        "peak_localization_method": str(method),
        "n_peaks": total,
        "gaussian_fit_success_count": int(success),
        "gaussian_fit_failure_count": int(total - success),
        "gaussian_fallback_count": int(fallback),
        "fallback_reasons": failures,
    }
    if roi_f1_ppm is not None:
        summary["gaussian_roi_f1_ppm"] = float(roi_f1_ppm)
    if roi_f2_ppm is not None:
        summary["gaussian_roi_f2_ppm"] = float(roi_f2_ppm)
    return summary


__all__ = [
    "DEFAULT_GAUSSIAN_MAX_NFEV",
    "DEFAULT_GAUSSIAN_ROI_F1_PPM",
    "DEFAULT_GAUSSIAN_ROI_F2_PPM",
    "DEFAULT_GAUSSIAN_ROI_MAX_POINTS",
    "DEFAULT_GAUSSIAN_ROI_F2_PPM",
    "DEFAULT_LOCALIZATION_METHOD",
    "FWHM_FACTOR",
    "GAUSSIAN_SUPPORTED_NDIM",
    "GAUSSIAN_UNSUPPORTED_MESSAGE",
    "LOCALIZATION_LABELS",
    "LOCALIZATION_METHODS",
    "LOCALIZATION_RECORDS_SUFFIX",
    "LocalizationError",
    "PeakLocalization",
    "localization_label",
    "localization_records_path",
    "localization_supported",
    "load_localization_defaults",
    "localize_peak",
    "localize_peak_gaussian_2d",
    "localize_peak_parabolic",
    "normalize_localization_method",
    "ppm_from_point",
    "read_localization_records",
    "summarize_localization",
    "trim_localization_records",
    "write_localization_records",
]
