"""Unified peak localization entry point: dispatch the **sub-grid refinement** method that runs
after detection.

Flow and responsibility boundary (2026-09-13, user requirement):

    peak detection (core.qc.peak_detection)
              |  integer-grid maxima of the candidate peaks
        peak localization (this module)
          +-- parabolic  the existing 3-point parabola (default; behaviour **completely
          unchanged**)
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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from core.project.manager import atomic_write_text
from core.qc import peak_detection
from ui_support.i18n import tr

LOCALIZATION_METHODS: tuple[str, ...] = ("parabolic",)
DEFAULT_LOCALIZATION_METHOD = "parabolic"
REMOVED_METHOD_ALIASES: tuple[str, ...] = (
    "gaussian",
    "gaussian_fit",
    "gauss",
    "高斯",  # i18n: keep(方法名输入别名)
    "高斯拟合",  # i18n: keep(方法名输入别名)
)

LOCALIZATION_LABELS: dict[str, str] = {
    "parabolic": tr("parabolic line"),
}

LOCALIZATION_RECORDS_SUFFIX = ".localization.json"


class LocalizationError(ValueError):
    """Invalid peak-localization parameters (unknown method / unsupported dimensionality)."""


@dataclass
class PeakLocalization:
    """The localization result of one candidate peak (requested and actual kept apart for
    records and
    comparison).
    """

    requested_method: str
    actual_method: str
    position: tuple[float, ...] = ()
    success: bool = True
    fallback: bool = False
    reason: str = ""
    boundary_hit: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Dictionary for records: method provenance plus Gaussian diagnostics (the field names
        of the
        user requirement §8).
        """
        return {
            "localization_method": str(self.actual_method),
            "requested_method": str(self.requested_method),
            "actual_method": str(self.actual_method),
            "fallback": bool(self.fallback),
            "fallback_reason": str(self.reason),
            "boundary_hit": bool(self.boundary_hit),
        }


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
        "抛物线": "parabolic",  # i18n: keep(方法名输入别名)
    }
    if name in REMOVED_METHOD_ALIASES:
        raise LocalizationError(
            tr(
                "The Gaussian peak-fitting method was removed; peak localisation is parabolic only",
            )
        )
    if name not in aliases:
        raise LocalizationError(
            tr(
                "Unknown peak localization method: {p0!r}(Available: parabolic)",
                p0=method,
            )
        )
    return aliases[name]


def localization_supported(method: Any, ndim: int) -> bool:
    """Whether this dimensionality supports the method (Gaussian is 2D only; parabolic any)."""
    try:
        normalize_localization_method(method)
    except LocalizationError:
        return False
    return True


def localization_label(method: Any) -> str:
    """Display name of the method (for logs and the GUI)."""
    try:
        return LOCALIZATION_LABELS[normalize_localization_method(method)]
    except LocalizationError:
        return str(method or "")


def load_localization_defaults(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Defaults of the config section ``peaks.localization`` (built-in defaults when unreadable).

    Returns ``{"method": "parabolic", "gaussian_roi_f1_ppm": float, "gaussian_roi_f2_ppm":
    float}``;
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
        method = normalize_localization_method(section.get("method", DEFAULT_LOCALIZATION_METHOD))
    except LocalizationError:
        method = DEFAULT_LOCALIZATION_METHOD
    return {"method": method}


def localize_peak_parabolic(data: Any, index: Sequence[int]) -> PeakLocalization:
    """The existing parabolic localization (the same implementation as
    ``peak_detection._refined_index``).
    """
    real = np.real(np.asarray(data)).astype(float, copy=False)
    idx = [int(min(max(int(v), 0), real.shape[axis] - 1)) for axis, v in enumerate(index)]
    position = tuple(peak_detection.refine_parabolic(real, idx, axis) for axis in range(real.ndim))
    edge = any(
        not np.isfinite(value)
        or abs(float(value) - float(idx[axis])) >= 0.5 - 1e-9
        or idx[axis] <= 0
        or idx[axis] >= int(real.shape[axis]) - 1
        for axis, value in enumerate(position)
    )
    return PeakLocalization(
        requested_method="parabolic",
        actual_method="parabolic",
        position=position,
        success=True,
        boundary_hit=edge,
    )


def localize_peak(data: Any, index: Sequence[int]) -> PeakLocalization:
    """Unified peak-localization entry point: ``method="parabolic"`` (default) or ``"gaussian"``
    (2D only).

    ``index`` is the integer-grid maximum of the candidate peak (one per axis); the parabolic
    method
    refines each axis independently while the Gaussian method uses the parabolic result as the
    seed of a
    2D local fit. Both run on exactly the same candidate, so their results compare directly.

    Parameters
    ----------
    data : Any
        2D spectrum data (the real part is refined); axis 0 = indirect dimension, axis 1 =
        direct.
    index : Sequence[int]
        Integer-grid maximum position of the candidate peak (one per axis).
    method : Any, default "parabolic"
        ``parabolic`` (default) or ``gaussian``; case and the local-language aliases are
        accepted, an
        unknown method raises.
    sign : int, default 1
        Peak sign (``+1`` positive / ``-1`` negative); it only affects the Gaussian fit.
    ppm_axes : Sequence[Any], optional
        The ppm axis array of each data axis; **required** for the Gaussian method (not used by
        the
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
    real = np.real(np.asarray(data))
    return localize_peak_parabolic(real, index)


def localization_records_path(peak_path: Path | str) -> Path:
    """Peak-table attachment path: ``<peak table>.localization.json`` (same directory as the
    .list).
    """
    path = Path(peak_path)
    return path.with_name(path.name + LOCALIZATION_RECORDS_SUFFIX)


def write_localization_records(
    peak_path: Path | str,
    records: Sequence[dict[str, Any]],
    *,
    meta: dict[str, Any] | None = None,
) -> Path:
    """Write the per-peak localization diagnostics (a JSON attachment; the Poky .list format is
    unchanged).
    """
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
    with a 76-row attachment while the reference table had 60 peaks, each self-consistent
    already. A
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
    list.
    """
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
    method: str = DEFAULT_LOCALIZATION_METHOD,
) -> dict[str, Any]:
    """Per-peak records -> a run-record summary (user requirement §13: method + ROI + counts).

    The parabolic mode performs no Gaussian fit, so all three gaussian_*_count values are 0
    (nothing is
    overstated).
    """
    total = len(records)
    reasons: dict[str, int] = {}
    for row in records:
        if not row.get("fallback"):
            continue
        reason = str(row.get("fallback_reason", "") or "")
        reasons[reason] = reasons.get(reason, 0) + 1
    return {
        "peak_localization_method": str(method),
        "n_peaks": int(total),
        "n_boundary_hit": sum(1 for row in records if row.get("boundary_hit")),
        "fallback_count": sum(1 for row in records if row.get("fallback")),
        "fallback_reasons": reasons,
    }


__all__ = [
    "DEFAULT_LOCALIZATION_METHOD",
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
    "localize_peak_parabolic",
    "normalize_localization_method",
    "ppm_from_point",
    "read_localization_records",
    "summarize_localization",
    "trim_localization_records",
    "write_localization_records",
]
