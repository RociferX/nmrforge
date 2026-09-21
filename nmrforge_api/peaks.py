"""Peak-position measurement: track the same reference peaks across every candidate spectrum.

Why not simply re-pick peaks for this study? Picking is threshold detection: changing a
processing parameter can add or drop peaks and mix "a different peak" into the position
difference. This module freezes the reference table, finds an extremum in a small window
around each position and refines it parabolically, so **the same peaks stay tracked**.

Method (usable in a methods section):

1. the reference table gives each peak's nucleus positions (ppm) and assignment;
2. ppm -> a fractional index on the spectrum's data axis (same source as picking: ORIG
      first, CAR as fallback, logical axes mapped by FDDIMORDER; see ``workflow.pick_peaks``);
3. take the ``|intensity|`` extremum (the default) inside a window defined by a **physical
      width**: the window defaults to that axis's linewidth in ppm (``core.peaks.axis_units``),
      converted at the current spacing; ``window_ppm`` gives ppm explicitly while
      ``window_pts`` forces points (that width moves with zero filling; avoid comparing runs);
4. for each measured axis a three-point parabola over ±1 point gives the vertex offset d;
      fractional index = integer index + d, with d in [-0.5, 0.5];
5. fractional index -> ppm by linear interpolation on the axis array.

Quality flags: ``window_edge`` (the extremum sits on the **physical window** edge, so the
true peak may lie outside), ``boundary`` (against the spectrum edge), ``out_of_range``,
``found`` and ``cell_edge`` (the extremum was cut by the **exclusive-cell** edge, see
``measure_peak_positions``). ``window_edge`` and ``cell_edge`` are orthogonal: the first
describes the physical window only, the second the neighbour's cell only.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from core.peaks import axis_units
from nmrforge_api.errors import MeasurementError
from ui_support.i18n import tr
from workflow.pick_peaks import SpectrumAxes, read_spectrum_axes

# peak-table named key -> nucleus (the 2D row convention of core.peaks.peak_table)
_NAMED_KEYS: tuple[tuple[str, str], ...] = (
    ("H_shift", "1H"),
    ("N_shift", "15N"),
    ("C_shift", "13C"),
)


def reference_peak_id(peak_id: Any) -> str:
    """Peak index -> stable identity ``R0001`` (frozen once the reference table exists).

    The reference table assigns each peak a reference_peak_id; every later workflow table carries
    that column (undetected peaks stay, with detected=false), so downstream can match them back.
    """
    try:
        number = int(peak_id)
    except (TypeError, ValueError):
        return str(peak_id or "")
    return f"R{number:04d}"


@dataclass
class PeakMeasurement:
    """The measured position of one reference peak on one spectrum."""

    peak_id: int
    assignment: str
    # stable identity (the reference_peak_id, e.g. R0001)
    reference_peak_id: str = ""
    reference: dict[str, float] = field(default_factory=dict)
    positions: dict[str, float] = field(default_factory=dict)
    deltas: dict[str, float] = field(default_factory=dict)
    intensity: float = 0.0
    # this spectrum's noise sigma (robust MAD) and per-peak SNR = |intensity| / sigma
    noise_sigma: float = 0.0
    snr: float = 0.0
    found: bool = True
    window_edge: bool = False
    boundary: bool = False
    out_of_range: bool = False
    # P1-3 localisation QC: the effective search interval (data-axis integer grid,
    # closed), whether the extremum sits on the **exclusive-cell** edge (the neighbour's
    # cell cut it short -- orthogonal to window_edge), and the measured intensity
    # divided by the identity table's Height (NaN when the Height is missing or 0)
    cell_low: dict[str, int] = field(default_factory=dict)
    cell_high: dict[str, int] = field(default_factory=dict)
    cell_edge: bool = False
    intensity_ratio: float = float("nan")
    # localisation diagnostics (2026-09-13): method, fallback, fit QC; empty = reference only
    localization: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "peak_id": int(self.peak_id),
            "assignment": self.assignment,
            "reference_peak_id": self.reference_peak_id,
            "reference": {k: float(v) for k, v in self.reference.items()},
            "positions": {k: float(v) for k, v in self.positions.items()},
            "deltas": {k: float(v) for k, v in self.deltas.items()},
            "intensity": float(self.intensity),
            "noise_sigma": float(self.noise_sigma),
            "snr": float(self.snr),
            "found": bool(self.found),
            "window_edge": bool(self.window_edge),
            "boundary": bool(self.boundary),
            "out_of_range": bool(self.out_of_range),
            "cell_low": {str(k): int(v) for k, v in self.cell_low.items()},
            "cell_high": {str(k): int(v) for k, v in self.cell_high.items()},
            "cell_edge": bool(self.cell_edge),
            "intensity_ratio": float(self.intensity_ratio),
            "localization": {
                str(k): v for k, v in (self.localization or {}).items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PeakMeasurement:
        return cls(
            peak_id=int(data.get("peak_id", 0) or 0),
            assignment=str(data.get("assignment", "")),
            reference_peak_id=str(
                data.get("reference_peak_id")
                or reference_peak_id(data.get("peak_id", 0))
            ),
            reference={k: float(v) for k, v in (data.get("reference") or {}).items()},
            positions={k: float(v) for k, v in (data.get("positions") or {}).items()},
            deltas={k: float(v) for k, v in (data.get("deltas") or {}).items()},
            intensity=float(data.get("intensity", 0.0) or 0.0),
            noise_sigma=float(data.get("noise_sigma", 0.0) or 0.0),
            snr=float(data.get("snr", 0.0) or 0.0),
            found=bool(data.get("found", True)),
            window_edge=bool(data.get("window_edge", False)),
            boundary=bool(data.get("boundary", False)),
            out_of_range=bool(data.get("out_of_range", False)),
            cell_low={
                str(k): int(v) for k, v in (data.get("cell_low") or {}).items()
            },
            cell_high={
                str(k): int(v) for k, v in (data.get("cell_high") or {}).items()
            },
            cell_edge=bool(data.get("cell_edge", False)),
            intensity_ratio=float(data.get("intensity_ratio") or float("nan")),
            localization=dict(data.get("localization") or {}),
        )


def _read_ppm_csv(path: Path) -> list[dict[str, Any]] | None:
    """Read the study-project CSV format (``peak_id,H_ppm,N_ppm[,height,linewidth,volume]``).

    Reference tables exported from a public archive often have this shape; anything else
    returns None and is left to the POKY or legacy CSV reader.
    """
    import csv

    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return None
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return None
    header = [cell.strip().lower() for cell in lines[0].split(",")]
    if "h_ppm" not in header or "n_ppm" not in header:
        return None
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(csv.DictReader(lines), start=1):
        normalized = {
            str(key).strip().lower(): value
            for key, value in row.items()
            if key
        }

        def _num(name: str) -> str:
            value = normalized.get(name, "")
            return "" if value in (None, "") else str(value)

        raw_id = str(normalized.get("peak_id", "") or "").strip()
        label = raw_id.split(":", 1)[1] if ":" in raw_id else raw_id
        rows.append(
            {
                "Peak_ID": index,
                "label": label,
                "H_shift": _num("h_ppm"),
                "N_shift": _num("n_ppm"),
                "Intensity": _num("height") or _num("intensity"),
                "peak_id_raw": raw_id,
            }
        )
    return rows


def read_reference_peaks(path: Path | str) -> list[dict[str, Any]]:
    """Read the reference peak table, keeping peaks with at least one nucleus position.

    Three formats are accepted:

    - POKY/Sparky ``.list`` (the standard table NMRForge writes);
    - the legacy NMRForge CSV (``H_shift``/``N_shift`` columns);
    - the study-project CSV (``peak_id,H_ppm,N_ppm,height,linewidth,volume``,
        a reference table exported from a public archive).
    """
    from core.peaks.peak_table import load_peaks

    target = Path(path)
    if not target.is_file():
        raise MeasurementError(tr("reference peak table does not exist: {p0}", p0=target))
    peaks = _read_ppm_csv(target)
    if peaks is None:
        peaks = load_peaks(target)
    # stable identity: the reference table assigns reference_peak_id, later tables reuse it
    for position, row in enumerate(peaks, start=1):
        if not row.get('reference_peak_id'):
            row['reference_peak_id'] = reference_peak_id(
                row.get('Peak_ID') or position
            )
    usable = [row for row in peaks if peak_coordinates(row, None)]
    if not usable:
        raise MeasurementError(tr(
            "reference peak table has no measurable positions: "
            "{p0}",
            p0=path,
        ))
    return usable


def peak_coordinates(
    row: dict[str, Any], axes: SpectrumAxes | None
) -> dict[str, float]:
    """A peak-table row -> {nucleus: ppm}.

    - 2D rows use the named keys ``H_shift`` / ``N_shift`` / ``C_shift``;
    - ``F{k}_shift`` maps logical dimension k (FDDIMORDER) onto a data-axis nucleus;
    - with ``axes=None`` only the named keys are parsed, which is enough to drop empty rows.
    """
    coords: dict[str, float] = {}
    if axes is not None:
        for logical, storage in enumerate(axes.logical_to_storage):
            value = row.get(f"F{logical + 1}_shift")
            if value is None or str(value) == "":
                continue
            nucleus = axes.nuclei[storage] if storage < len(axes.nuclei) else ""
            if nucleus:
                coords.setdefault(nucleus, float(value))
    for key, nucleus in _NAMED_KEYS:
        value = row.get(key)
        if value is not None and str(value) != "":
            coords.setdefault(nucleus, float(value))
    return coords


def _add_nucleus_localization(
    record: dict[str, Any], axes: SpectrumAxes
) -> None:
    """Attach the Gaussian diagnostics by **nucleus** (FWHM_H/FWHM_N cannot be guessed by axis).

    ``core.peaks.localize`` reports its derived values in data-axis order (``fwhm_f1`` = axis 0),
    while the unified table's H/N columns are keyed by nucleus: map through ``axes.nuclei`` and
    write nothing for an unknown nucleus (the table falls back to NaN) rather than guessing.
    """
    if str(record.get('actual_method', '')) != 'gaussian':
        return
    fwhm: dict[str, float] = {}
    sigma: dict[str, float] = {}
    for index, nucleus in enumerate(axes.nuclei[:2]):
        if not nucleus:
            continue
        value = record.get(f'fwhm_f{index + 1}')
        if value is not None:
            fwhm[nucleus] = float(value)
        value = record.get(f'sigma_f{index + 1}')
        if value is not None:
            sigma[nucleus] = float(value)
    if fwhm:
        record['fwhm_by_nucleus'] = fwhm
    if sigma:
        record['sigma_by_nucleus'] = sigma


def _parabolic_offset(y_minus: float, y_zero: float, y_plus: float) -> float:
    """Three-point parabolic vertex offset, in points, clamped to ±0.5."""
    denom = y_minus - 2.0 * y_zero + y_plus
    if not np.isfinite(denom) or abs(denom) < 1e-12:
        return 0.0
    offset = 0.5 * (y_minus - y_plus) / denom
    if not np.isfinite(offset):
        return 0.0
    return float(max(-0.5, min(0.5, offset)))


def _axis_step_ppm(axes: SpectrumAxes, axis: int) -> float:
    """ppm per point on that data axis (first two points; NaN when there are fewer)."""
    ppm_axis = np.asarray(axes.ppm[axis], dtype=float)
    if ppm_axis.size < 2:
        return float("nan")
    return abs(float(ppm_axis[1] - ppm_axis[0]))


def _parabolic_qc(
    y_minus: float, y_zero: float, y_plus: float, step_ppm: float
) -> dict[str, Any]:
    """Three-point parabola -> vertex offset + equivalent linewidth + edge flag (P3-7).

    With vertex height ``H = c - b^2/(4a)`` and curvature ``a``, the equivalent Gaussian
    linewidth is ``FWHM = 2.3548 * sigma`` with ``sigma^2 = H/(2|a|)`` - the same wording
    as the Gaussian table's FWHM, so the two are directly comparable. When the parabola
    is not a peak (``a >= 0`` or ``H <= 0``) or the conversion is impossible, ``fwhm_ppm``
    is None (NaN in the table) rather than a guess. A vertex offset sitting on the +-0.5
    point limit sets ``boundary_hit`` (the true top may lie outside the stencil).
    """
    a = (y_minus + y_plus) / 2.0 - y_zero
    b = (y_plus - y_minus) / 2.0
    offset = 0.0 if abs(a) < 1e-12 else -b / (2.0 * a)
    if not np.isfinite(offset):
        offset = 0.0
    vertex = y_zero - (b * b) / (4.0 * a) if abs(a) > 1e-12 else y_zero
    fwhm: float | None = None
    if a < 0 and np.isfinite(vertex) and vertex > 0 and step_ppm > 0:
        sigma = float(np.sqrt(vertex / (2.0 * abs(a))))
        estimate = float(2.3548200450309493 * sigma * step_ppm)
        if np.isfinite(estimate):
            fwhm = estimate
    offset = float(max(-0.5, min(0.5, offset)))
    return {
        "offset": offset,
        "vertex_height": float(vertex),
        "fwhm_ppm": fwhm,
        "fit_success": fwhm is not None,
        "boundary_hit": bool(abs(offset) >= 0.5 - 1e-9),
    }


def _parabolic_stencil_qc(
    data: np.ndarray, center: Sequence[int], axes: SpectrumAxes
) -> dict[str, Any]:
    """Integer grid point -> three-point parabola QC record (the workflow path).

    Shares :func:`_parabolic_qc` with the reference path; the record keys mirror the
    Gaussian path (``fit_success`` / ``boundary_hit`` / ``fwhm_by_nucleus``) so the
    unified peak table can take its values by method.
    """
    fwhm: dict[str, float] = {}
    ok = True
    edge = False
    for axis in range(int(data.ndim)):
        nucleus = axes.nuclei[axis] if axis < len(axes.nuclei) else ""
        position = int(center[axis])
        size = int(data.shape[axis])
        if position <= 0 or position >= size - 1:
            ok = False
            edge = True
            continue
        profile: list[float] = []
        for shift in (-1, 0, 1):
            index_tuple = [int(v) for v in center]
            index_tuple[axis] = position + shift
            profile.append(float(data[tuple(index_tuple)]))
        qc = _parabolic_qc(*profile, step_ppm=_axis_step_ppm(axes, axis))
        if qc["boundary_hit"]:
            edge = True
        value = qc["fwhm_ppm"]
        if value is None:
            ok = False
        elif nucleus:
            fwhm[nucleus] = float(value)
    return {
        "requested_method": "parabolic",
        "actual_method": "parabolic",
        "fit_success": bool(ok),
        "boundary_hit": bool(edge),
        "fwhm_by_nucleus": fwhm,
        "source": tr("core.peaks(three-point parabola)"),
    }


DEFAULT_WINDOW_PTS_FALLBACK = 3  # point fallback when ppm cannot be converted


def window_points_by_axis(
    axes: SpectrumAxes,
    *,
    window_pts: int | None = None,
    window_ppm: float | None = None,
) -> dict[int, dict[str, Any]]:
    """Per-axis search windows -> {axis: {points, ppm, source, nucleus, obs_mhz, ppm_per_point}}.

    Precedence: ``window_ppm`` (physical width, per axis) > ``window_pts`` (explicit points,
    not comparable across resolutions) > automatic (the axis linewidth times
    ``axis_units.MEASUREMENT_WINDOW_LINEWIDTH_FACTOR``, in ppm).
    Each axis returns ``ppm`` (the requested width), ``effective_ppm`` (what the rounded point
    count actually covers) and that axis's ``ppm_per_point`` spacing, for comparing runs.
    """
    out: dict[int, dict[str, Any]] = {}
    for axis in range(axes.ndim):
        nucleus = axes.nuclei[axis] if axis < len(axes.nuclei) else ""
        obs = axes.obs[axis] if axis < len(axes.obs) else 0.0
        axis_ppm = axes.ppm[axis]
        step = axis_units.ppm_per_point(axis_ppm)
        if window_pts is not None:
            points = max(0, int(window_pts))
            width = axis_units.ppm_for_points(axis_ppm, points)
            source = tr("points_explicit")
        else:
            width = (
                float(window_ppm)
                if window_ppm is not None
                else axis_units.measurement_window_ppm(nucleus, obs)
            )
            source = (
                tr("ppm_explicit") if window_ppm is not None else tr("ppm_auto_linewidth")
            )
            points = axis_units.points_for_ppm(axis_ppm, width)
            if points <= 0:
                points = int(DEFAULT_WINDOW_PTS_FALLBACK)
                width = axis_units.ppm_for_points(axis_ppm, points)
                source = tr("points_fallback")
        out[axis] = {
            "points": int(points),
            "ppm": round(float(width), 6),
            "effective_ppm": round(
                axis_units.ppm_for_points(axis_ppm, points), 6
            ),
            "source": source,
            "nucleus": nucleus,
            "obs_mhz": round(float(obs), 4),
            "ppm_per_point": round(step, 6),
        }
    return out


def measure_peak_positions(
    spectrum_path: Path | str,
    peaks: Sequence[dict[str, Any]],
    *,
    window_pts: int | None = None,
    window_ppm: float | None = None,
    axes: SpectrumAxes | None = None,
    sign: str = "abs",
    refine: str = "parabolic",
    nuclei: Iterable[str] | None = None,
    roi_f1_ppm: float | None = None,
    roi_f2_ppm: float | None = None,
    noise_sigma: float | None = None,
    exclusive_windows: bool = True,
) -> list[PeakMeasurement]:
    """Measure the sub-pixel positions of ``peaks`` on ``spectrum_path``.

    sign: ``"abs"`` (default, follow either sign) | ``"positive"`` | ``"negative"``;
    refine: ``"parabolic"`` (three-point parabola) | ``"none"`` (integer maximum
    only) | ``"gaussian"`` (a 2D fit, **2D only**; the ROI is a physical width in ppm,
    ``roi_f1_ppm``/``roi_f2_ppm``, defaulting to config ``peaks.localization``;
    a failed fit falls back to parabolic and records requested/actual/why, never silently).
    A non-empty nuclei list measures only those nuclei; the rest keep their reference values.

    Search window: defaults to a physical width (linewidth x1.5 in ppm) converted per axis,
    so **zero filling never changes the ppm width the window covers**; ``window_ppm`` sets that
    width and ``window_pts`` forces points (not recommended). Pass already-read axes via ``axes``
to avoid re-reading the spectrum.

    ``exclusive_windows`` is on by default: the half-width is only an **upper bound**, and each
    reference peak's search region is truncated at the midpoints to its neighbouring reference
    peaks, so a peak only takes the extremum inside its own cell. Without that, a window wider
    than the spacing between neighbouring peaks makes two reference records hit the same
    extremum cell and the table gets duplicate rows differing only in ``reference_peak_id``
    (fixed 2026-09-19); pass ``False`` for the previous "all peaks share one fixed window"
    wording.

    ``noise_sigma``: this spectrum's noise. Defaults to the robust MAD in ``core.qc.noise``;
    each peak's ``SNR = |intensity| / sigma`` is written with sigma (the unified SNR column),
    so downstream can recheck whether a peak is strong enough to be called detected.

    Parameters
    ----------
    spectrum_path : Path | str
        the spectrum to measure (2D ``.ft2``; Gaussian refinement is 2D only).
    peaks : Sequence[dict[str, Any]]
        the candidate peaks (unified fields: ``N_shift``/``H_shift``/``label``...).
    window_pts : int, optional
        half-width of the search window in points; exclusive with ``window_ppm``.
    window_ppm : float, optional
        half-width in ppm. Prefer this: it converts at the current spacing and does not drift.
    axes : SpectrumAxes, optional
        already-read axes, reused across peaks to avoid re-reading.
    sign : str, default "abs"
        which peak signs to follow.
    refine : str, default "parabolic"
        ``parabolic`` or ``gaussian``; Gaussian on non-2D data raises instead of degrading quietly.
    nuclei : Iterable[str], optional
        axis nuclei; inferred from the header by default.
    roi_f1_ppm, roi_f2_ppm : float, optional
        Gaussian ROI radius (ppm).
    noise_sigma : float, optional
        known noise sigma; estimated from the spectrum by default.
    exclusive_windows : bool, default True
        each reference peak searches only its own cell (bounded by the midpoints to its
        neighbours), so two reference records are never relocated onto the same grid point;
        ``False`` restores the shared fixed window.

    Returns
    -------
    list[PeakMeasurement]
        per-peak results: position, intensity/SNR, detection, method used and fit diagnostics.

    Raises
    ------
    MeasurementError
        the spectrum is unreadable, Gaussian was asked for on non-2D data, or a peak is invalid.

    Side effects
    ------------
    Reads the spectrum only; the caller writes records.

    Examples
    --------
        rows = measure_peak_positions("spectra/a.ft2", peaks, window_ppm=1.0)
    """
    path = Path(spectrum_path)
    if not path.is_file():
        raise MeasurementError(tr("spectrum does not exist: {p0}", p0=path))
    if window_pts is not None and int(window_pts) < 0:
        raise MeasurementError(tr("window_pts cannot be negative"))
    if sign not in ("abs", "positive", "negative"):
        raise MeasurementError(tr("unknown sign: {p0}", p0=sign))
    if refine not in ("parabolic", "none", "gaussian"):
        raise MeasurementError(tr("unknown refine: {p0}", p0=refine))

    spectrum_axes = axes if axes is not None else read_spectrum_axes(path)
    axes = spectrum_axes
    data = np.asarray(axes.data, dtype=float)
    # Gaussian is 2D only (checked after reading, the same rule as localisation)
    if refine == "gaussian" and data.ndim != 2:
        from core.peaks.localize import GAUSSIAN_UNSUPPORTED_MESSAGE

        raise MeasurementError(GAUSSIAN_UNSUPPORTED_MESSAGE)
    window_by_axis = window_points_by_axis(
        axes, window_pts=window_pts, window_ppm=window_ppm
    )
    from core.qc import noise as _noise

    sigma = (
        float(noise_sigma)
        if noise_sigma is not None
        else float(_noise.estimate(data).global_sigma)
    )
    wanted = {str(n) for n in nuclei} if nuclei else None
    # Pre-pass: work out each reference peak's integer grid centre on every searched axis -
    # the "one cell per peak" split below needs the positions of all peaks at once.
    plans: list[dict[str, Any]] = []
    centers_by_axis: dict[int, list[int]] = {}
    for index, row in enumerate(peaks, start=1):
        coords = peak_coordinates(row, axes)
        search_axes: list[int] = []
        search_nuclei: list[str] = []
        centers: list[int] = []
        out_of_range = False
        for nucleus, ppm_value in coords.items():
            if wanted is not None and nucleus not in wanted:
                continue
            axis = axes.storage_of(nucleus)
            if axis is None:
                continue
            axis_ppm = np.asarray(axes.ppm[axis], dtype=float)
            size = int(data.shape[axis])
            if axis_ppm.size and (
                ppm_value < float(np.min(axis_ppm))
                or ppm_value > float(np.max(axis_ppm))
            ):
                # a reference position outside the spectrum is still searched near the edge,
                # so the user can see how far off it is instead of losing the peak.
                out_of_range = True
            fraction = axes.fraction_from_ppm(axis, ppm_value)
            search_axes.append(axis)
            search_nuclei.append(nucleus)
            centers.append(int(min(max(round(fraction), 0), size - 1)))
        for axis, center in zip(search_axes, centers):
            centers_by_axis.setdefault(axis, []).append(center)
        plans.append(
            {
                "index": index,
                "row": row,
                "coords": coords,
                "search_axes": search_axes,
                "search_nuclei": search_nuclei,
                "centers": centers,
                "out_of_range": out_of_range,
            }
        )
    # Split every axis into cells at the midpoints between neighbouring reference peaks, so a
    # reference peak only takes the extremum inside its own cell. Two records with different
    # integer grid centres then have disjoint search regions and can no longer be relocated
    # onto the same point (fixed 2026-09-19: the previous shared fixed half-width window let a
    # denser reference peak be swallowed by its stronger neighbour, producing duplicate rows
    # differing only in reference_peak_id). Records that round to the same grid point still
    # share a cell - the resolution limit of that spectrum, recorded as-is.
    cell_bounds: dict[tuple[int, int], tuple[int, int]] = {}
    if exclusive_windows:
        for axis, centres in centers_by_axis.items():
            size = int(data.shape[axis])
            ordered = sorted(set(centres))
            for position, center in enumerate(ordered):
                low = (
                    0
                    if position == 0
                    else (ordered[position - 1] + center) // 2 + 1
                )
                high = (
                    size - 1
                    if position == len(ordered) - 1
                    else (center + ordered[position + 1]) // 2
                )
                cell_bounds[(axis, center)] = (low, high)
    results: list[PeakMeasurement] = []
    for plan in plans:
        index = int(plan["index"])
        row = plan["row"]
        coords = plan["coords"]
        peak_id = int(row.get("Peak_ID", index) or index)
        assignment = str(row.get("label", "") or row.get("Assignment", "") or "")
        if assignment in ("?-?", "?-?-?"):
            assignment = ""
        measurement = PeakMeasurement(
            peak_id=peak_id,
            assignment=assignment,
            reference_peak_id=str(
                row.get('reference_peak_id') or reference_peak_id(peak_id)
            ),
            reference=coords,
            noise_sigma=sigma,
        )
        measurement.out_of_range = bool(plan["out_of_range"])
        search_axes = plan["search_axes"]
        search_nuclei = plan["search_nuclei"]
        centers = plan["centers"]
        if not search_axes:
            measurement.found = False
            results.append(measurement)
            continue

        # bounds must be indexed by data axis: search_axes follows the order nuclei appear in the
        # peak table (1H usually before 15N), so building in that order would transpose the window.
        bounds: dict[int, tuple[int, int]] = {}
        # the physical window bounds (+-half, clipped by the spectrum edge): window_edge
        # uses only these, never the exclusive cell
        window_bounds: dict[int, tuple[int, int]] = {}
        # axes whose search was really truncated by the exclusive cell: only those can
        # produce a cell_edge; the physical window edge stays with window_edge. The
        # tuple records (low, high, low raised by the neighbour cell, high lowered).
        exclusive_bounds: dict[int, tuple[int, int, bool, bool]] = {}
        for axis, center in zip(search_axes, centers):
            size = int(data.shape[axis])
            half = int(window_by_axis.get(axis, {}).get("points", 0))
            window_low = max(0, center - half)
            window_high = min(size - 1, center + half)
            low, high = window_low, window_high
            # window_edge keeps its old meaning: the extremum sits on the **physical
            # window** bound (+-half, clipped by the spectrum edge). The edge cut out by
            # the exclusive cell belongs to cell_edge, so the two stay orthogonal.
            window_bounds[axis] = (window_low, window_high)
            if (axis, center) in cell_bounds:
                cell_low, cell_high = cell_bounds[(axis, center)]
                low = max(low, cell_low)
                high = min(high, cell_high)
                low_from_cell = low > window_low
                high_from_cell = high < window_high
                if low_from_cell or high_from_cell:
                    exclusive_bounds[axis] = (
                        low,
                        high,
                        low_from_cell,
                        high_from_cell,
                    )
            bounds[axis] = (low, high)
        measurement.cell_low = {
            nucleus: int(bounds[axis][0])
            for nucleus, axis in zip(search_nuclei, search_axes)
        }
        measurement.cell_high = {
            nucleus: int(bounds[axis][1])
            for nucleus, axis in zip(search_nuclei, search_axes)
        }
        slices = tuple(
            slice(bounds[axis][0], bounds[axis][1] + 1)
            if axis in bounds
            else slice(None)
            for axis in range(data.ndim)
        )
        block = data[slices]
        if block.size == 0 or not np.any(np.isfinite(block)):
            measurement.found = False
            results.append(measurement)
            continue
        if sign == "abs":
            values = np.abs(block)
        elif sign == "positive":
            values = block
        else:
            values = -block
        flat = int(np.nanargmax(values))
        relative = np.unravel_index(flat, values.shape)
        best = [
            (bounds[axis][0] + int(relative[axis]))
            if axis in bounds
            else int(relative[axis])
            for axis in range(data.ndim)
        ]
        measurement.intensity = float(
            data[tuple(best)] if np.isfinite(data[tuple(best)]) else 0.0
        )
        measurement.snr = (
            abs(measurement.intensity) / sigma if sigma > 0 else 0.0
        )
        # the extremum sits on **that** exclusive-cell edge = the real top may lie in the
        # neighbour's cell (2026-09-19). Only the side the neighbour truncated counts;
        # stopping on the physical window edge is still just window_edge.
        for axis in search_axes:
            cell = exclusive_bounds.get(axis)
            if cell is None:
                continue
            low, high, low_from_cell, high_from_cell = cell
            position = int(best[axis])
            if (low_from_cell and position == low) or (
                high_from_cell and position == high
            ):
                measurement.cell_edge = True
        # Measured intensity / the identity-table Height: about 1 means the record stopped
        # on its own top (>1 means it is more likely a shoulder). Fixed 2026-09-19: the
        # denominator is |Height| - in a negative-peak data set the .list Height is
        # negative, and the old `> 0` test wrote NaN into the whole column. The ratio is
        # |measured| / |picked|, so about 1 still means "its own peak top" when the two
        # share a sign.
        picked = row.get("Intensity")
        if picked in (None, ""):
            picked = row.get("Height", row.get("height"))
        try:
            picked_value = float(picked)
        except (TypeError, ValueError):
            picked_value = 0.0
        measurement.intensity_ratio = (
            abs(float(measurement.intensity)) / abs(picked_value)
            if picked_value
            else float("nan")
        )
        for axis in search_axes:
            lo, hi = window_bounds[axis]
            size = int(data.shape[axis])
            if best[axis] <= 0 or best[axis] >= size - 1:
                measurement.boundary = True
            if best[axis] in (lo, hi) and not (
                (best[axis] == lo == 0) or (best[axis] == hi == size - 1)
            ):
                measurement.window_edge = True

        # sub-pixel refine per measured axis, the others held at their best point
        fractions = [float(index_) for index_ in best]
        if refine == "gaussian":
            # 2D Gaussian localisation (core.peaks.localize): physical ROI width plus fallback
            from core.peaks.localize import localize_peak

            peak_sign = 1 if float(data[tuple(best)]) >= 0 else -1
            if sign == "positive":
                peak_sign = 1
            elif sign == "negative":
                peak_sign = -1
            ppm_axes = [np.asarray(a, dtype=float) for a in axes.ppm]
            loc = localize_peak(
                data,
                best,
                method="gaussian",
                sign=peak_sign,
                ppm_axes=ppm_axes,
                roi_f1_ppm=roi_f1_ppm,
                roi_f2_ppm=roi_f2_ppm,
                logical_axes=list(axes.logical_to_storage),
            )
            fractions = [float(v) for v in loc.position]
            measurement.localization = loc.to_dict(ppm_axes)
            _add_nucleus_localization(measurement.localization, axes)
        elif refine == "parabolic":
            # P3-7: the parabola reports per-axis QC too (equivalent linewidth / edge
            # flag), so the QC columns are no longer Gaussian-only.
            qc_fwhm: dict[str, float] = {}
            qc_ok = True
            qc_edge = False
            for axis, nucleus in zip(search_axes, search_nuclei):
                position = best[axis]
                size = int(data.shape[axis])
                if position <= 0 or position >= size - 1:
                    # the stencil touches the spectrum edge: no linewidth is available,
                    # so fit_success is reported as false
                    qc_ok = False
                    qc_edge = True
                    continue
                profile = []
                for offset in (-1, 0, 1):
                    index_tuple = list(best)
                    index_tuple[axis] = position + offset
                    value = float(data[tuple(index_tuple)])
                    if sign == "abs":
                        value = abs(value)
                    elif sign == "negative":
                        value = -value
                    profile.append(value)
                qc = _parabolic_qc(*profile, step_ppm=_axis_step_ppm(axes, axis))
                fractions[axis] = position + float(qc["offset"])
                if qc["boundary_hit"]:
                    qc_edge = True
                value = qc["fwhm_ppm"]
                if value is None:
                    qc_ok = False
                elif nucleus:
                    qc_fwhm[nucleus] = float(value)
            measurement.localization = {
                "requested_method": "parabolic",
                "actual_method": "parabolic",
                "fit_success": qc_ok,
                "boundary_hit": qc_edge,
                "fwhm_by_nucleus": qc_fwhm,
                "source": tr("core.peaks(three-point parabola)"),
            }

        for nucleus, _ppm_value in coords.items():
            if wanted is not None and nucleus not in wanted:
                continue
            axis = axes.storage_of(nucleus)
            if axis is None:
                continue
            measurement.positions[nucleus] = axes.ppm_at_fraction(
                axis, fractions[axis]
            )
            measurement.deltas[nucleus] = (
                measurement.positions[nucleus] - float(coords[nucleus])
            )
        results.append(measurement)
    return results


def pick_reference_peaks(
    session: Any,
    *,
    sigma_multiplier: float | None = None,
    out_path: Path | str | None = None,
    details: dict[str, Any] | None = None,
    localization_method: str = "parabolic",
    gaussian_roi_f1_ppm: float | None = None,
    gaussian_roi_f2_ppm: float | None = None,
    dataset: Any | None = None,
) -> Path:
    """Pick peaks on the reference spectrum, producing the fixed table the sweep tracks.

    A non-empty ``out_path`` copies the table there, kept inside the study;
    a non-empty ``details`` receives the picking convention (``detection``: margin width in
    ppm, point counts, per-axis spacing); ``dataset`` chooses the condition dataset
    (the session's primary condition by default).

    Parameters
    ----------
    session : StudySession
        the session.
    sigma_multiplier : float, optional
        picking threshold (multiples of sigma); config default when omitted.
    out_path : Path | str, optional
        target ``.list`` path; written in the reference directory by default.
    details : dict[str, Any], optional
        an empty dict passed in is filled with the picking details (count, source, method).
    localization_method : str, default "parabolic"
        sub-grid refinement (``gaussian`` is 2D only).
    gaussian_roi_f1_ppm, gaussian_roi_f2_ppm : float, optional
        Gaussian ROI radius (ppm).
    dataset : Any, optional
        which dataset (for a multi-condition study).

    Returns
    -------
    Path
        the POKY ``.list`` path that was written.

    Raises
    ------
    MeasurementError
        picking failed or an argument is invalid; an empty table is never written silently.

    Side effects
    ------------
    Writes the table and records a ``pick_peaks`` run; the spectrum is untouched.

    Examples
    --------
        details: dict = {}
        path = pick_reference_peaks(study, sigma_multiplier=35, details=details)
    """
    from workflow.pick_peaks import pick_peaks

    dataset = dataset or session.dataset
    if dataset is None:
        raise MeasurementError(tr("this study has no dataset yet"))
    result = pick_peaks(
        session.manager,
        dataset.exp_id,
        dataset.data_id,
        sigma_multiplier=sigma_multiplier,
        localization_method=localization_method,
        gaussian_roi_f1_ppm=gaussian_roi_f1_ppm,
        gaussian_roi_f2_ppm=gaussian_roi_f2_ppm,
    )
    if details is not None:
        details["detection"] = result.get("detection") or {}
        details["localization"] = result.get("localization") or {}
        details["peak_count"] = int(result.get("peak_count", 0) or 0)
    peak_path = Path(str(result.get("peak_path", "")))
    if not peak_path.is_file():
        raise MeasurementError(tr("picking produced no peak table: {p0}", p0=result))
    if out_path is not None:
        target = Path(out_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(peak_path.read_text(encoding="utf-8"), encoding="utf-8")
        return target
    return peak_path


#: default detection threshold (sigma) for independent picking; the reference value wins
DEFAULT_DETECTION_SIGMA = 35.0


def detect_and_localize(
    spectrum_path: Path | str,
    *,
    sigma_multiplier: float | None = None,
    edge_margin_ppm: float | None = None,
    edge_margin_points: int | None = None,
    method: str = "parabolic",
    roi_f1_ppm: float | None = None,
    roi_f2_ppm: float | None = None,
    sign_mode: str = "dominant",
    axes: SpectrumAxes | None = None,
    targets: Collection[int] | None = None,
    allow_empty_targets: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Pick independently on **this combination's own spectrum** at the given threshold.

    - the detection convention matches project picking: physical margin (`axis_units`) +
        `peak_detection.detect` (`sigma_multiplier` doubling as `min_snr`) + dominant sign;
    - refinement `method`: `"parabolic"` (the three-point position from detection) |
        `"gaussian"` (per-peak 2D fit, 2D only);
    - `targets` (targeted localization, 2026-09-19): let only the listed peaks take part
        in the `method` refinement; detection, row count and `peak_id` numbering are
        **unchanged**, unlisted peaks stay in the table with the detection-stage
        three-point position and this method's QC columns as `NaN` (not fitted, which is
        not a failure); per-peak failures still write `fallback`/`fallback_reason` and do
        not re-fit another candidate. `targets=None` is today's whole-spectrum behaviour;
        with `allow_empty_targets=True` an **explicit empty list** means "refine nothing
        for this condition" (a per-condition target list with `on_missing="none"`),
        otherwise an empty list raises;
    - returns `(rows, meta)`: `rows` are this spectrum's own peaks (``peak_id`` = its index,
        ``reference_peak_id`` empty - matching back to the reference is **external** work);
        `meta` records the threshold, margin, sign convention, noise sigma, count and QC.

    There is no `max_peaks`: write every peak the locked threshold finds (user, 2026-09-14).

    Parameters
    ----------
    spectrum_path : Path | str
        the spectrum to pick on.
    sigma_multiplier : float, optional
        picking threshold (multiples of sigma); config default when omitted.
    edge_margin_ppm : float, optional
        edge exclusion radius (physical width); exclusive with ``edge_margin_points``.
    edge_margin_points : int, optional
        edge exclusion radius (points).
    method : str, default "parabolic"
        sub-grid refinement (``gaussian`` is 2D only).
    roi_f1_ppm, roi_f2_ppm : float, optional
        Gaussian ROI radius (ppm).
    sign_mode : str, default "dominant"
        peak sign convention.
    axes : SpectrumAxes, optional
        already-read axes.
    targets : Collection[int], optional
        refine only these ``peak_id`` values (targeted localization); by default every
        detected peak is refined. An empty collection or an undetected ``peak_id`` raises
        :class:`MeasurementError` (nothing is silently ignored).
    allow_empty_targets : bool, default False
        explicitly allow an empty collection ("no refinement for this condition");
        ``details`` then reports ``localization_scope`` = ``none`` and ``n_skipped`` =
        the number of detected peaks.

    Returns
    -------
    tuple[list[dict[str, Any]], dict[str, Any]]
        ``(rows, details)``: peak rows (same field convention as the unified table) and details.

    Raises
    ------
    MeasurementError
        the spectrum is unreadable or an argument is invalid.

    Side effects
    ------------
    Reads the spectrum; whether to write a table is the caller's decision.

    Examples
    --------
        rows, details = detect_and_localize("spectra/a.ft2", sigma_multiplier=35)
    """
    from core.qc import noise as _noise
    from core.qc import peak_detection as _detect
    from workflow.pick_peaks import read_spectrum_axes as _read_axes

    path = Path(spectrum_path)
    if not path.is_file():
        raise MeasurementError(tr("spectrum does not exist: {p0}", p0=path))
    spectrum_axes = axes if axes is not None else _read_axes(path)
    data = np.asarray(spectrum_axes.data, dtype=float)
    threshold = (
        float(sigma_multiplier)
        if sigma_multiplier is not None and float(sigma_multiplier) > 0
        else float(DEFAULT_DETECTION_SIGMA)
    )
    # validate the refinement first: an unsupported method or dimension raises
    wanted = str(method).strip().lower() or "parabolic"
    if wanted not in ("parabolic", "gaussian"):
        raise MeasurementError(
            tr(
            "unknown peak-localisation method: {p0!r} (parabolic / "
            "gaussian)",
            p0=method,
        )
        )
    if wanted == "gaussian" and data.ndim != 2:
        from core.peaks.localize import GAUSSIAN_UNSUPPORTED_MESSAGE as _msg

        raise MeasurementError(_msg)
    nuclei = list(spectrum_axes.nuclei)
    axis0_nucleus = nuclei[0] if nuclei else ""
    obs0 = float(spectrum_axes.obs[0]) if spectrum_axes.obs else 0.0
    try:
        from backend.config import load_processing_defaults

        linewidths = load_processing_defaults().get("linewidth_hz") or {}
    except Exception:  # noqa: BLE001 - unreadable config falls back to the built-in default
        linewidths = {}
    if edge_margin_points is not None:
        edge_points = max(0, int(edge_margin_points))
        edge_ppm = axis_units.ppm_for_points(spectrum_axes.ppm[0], edge_points)
        edge_source = tr("points_explicit")
    else:
        if edge_margin_ppm is not None:
            edge_ppm = float(edge_margin_ppm)
            edge_source = tr("ppm_explicit")
        else:
            edge_ppm = axis_units.edge_margin_ppm(
                axis0_nucleus, obs0, linewidth_hz_by_nucleus=linewidths
            )
            edge_source = tr("ppm_physical_width")
        edge_points = axis_units.points_for_ppm(spectrum_axes.ppm[0], edge_ppm)
        if edge_points <= 0:
            edge_points = 1
            edge_ppm = axis_units.ppm_for_points(spectrum_axes.ppm[0], edge_points)
            edge_source = tr("points_fallback")
    sigma = float(_noise.estimate(data).global_sigma)
    peaks = _detect.detect(
        data,
        _detect.PeakDetectionParams(
            sign_mode="both",
            sigma_multiplier=threshold,
            min_snr=threshold,
            edge_margin=edge_points,
        ),
    )
    resolved_sign = str(sign_mode or "dominant")
    if resolved_sign in ("dominant", "auto"):
        peaks = _detect.keep_dominant(peaks)
        resolved_sign = "dominant"
    # targeted localization (2026-09-19): only the target peaks take the chosen
    # method's refinement.
    target_ids: set[int] | None = None
    if targets is not None:
        target_ids = {int(value) for value in targets}
        if not target_ids and not allow_empty_targets:
            raise MeasurementError(
                tr(
                    "the target peak list is empty: give at least one peak_id (an empty list "
                    "raises instead of silently falling back to whole-spectrum localization; pass "
                    "allow_empty_targets for \"this condition refines "
                    "nothing\")",
                )
            )
        unknown = sorted(
            value for value in target_ids if value < 1 or value > len(peaks)
        )
        if unknown:
            shown = ", ".join(str(value) for value in unknown[:20])
            tail = tr(" ... ({p0} in total)", p0=len(unknown)) if len(unknown) > 20 else ""
            raise MeasurementError(
                tr(
                    "the target list names peak_id values that were not detected: [{p0}]{p1} -- "
                    "this spectrum detected {p2} peaks, peak_id ranges over 1..{p3}. The target "
                    "list must match this spectrum's peak numbering; unknown ids are never "
                    "silently "
                    "ignored.",
                    p0=shown,
                    p1=tail,
                    p2=len(peaks),
                    p3=len(peaks),
                )
            )
    axis_h = spectrum_axes.storage_of("1H")
    axis_n = spectrum_axes.storage_of("15N")
    rows: list[dict[str, Any]] = []
    fallback_reasons: dict[str, int] = {}
    n_fallback = 0
    n_boundary = 0
    n_skipped = 0
    for index, peak in enumerate(peaks, start=1):
        position = tuple(float(v) for v in peak.position)
        record: dict[str, Any] = {}
        skipped = target_ids is not None and index not in target_ids
        if skipped:
            # Not a target: skip this method's refinement and keep the detection-stage
            # parabola (the method this row actually used); QC stays NaN = "not done",
            # not "failed".
            n_skipped += 1
        elif wanted == "gaussian":
            from core.peaks.localize import localize_peak as _localize

            loc = _localize(
                data,
                [int(round(v)) for v in peak.position],
                method="gaussian",
                sign=int(peak.sign),
                ppm_axes=list(spectrum_axes.ppm),
                roi_f1_ppm=roi_f1_ppm,
                roi_f2_ppm=roi_f2_ppm,
                logical_axes=list(spectrum_axes.logical_to_storage),
            )
            position = tuple(float(v) for v in loc.position)
            record = loc.to_dict(spectrum_axes.ppm)
            _add_nucleus_localization(record, spectrum_axes)
            if record.get("fallback"):
                n_fallback += 1
                reason = str(record.get("fallback_reason", "") or "")
                fallback_reasons[reason] = fallback_reasons.get(reason, 0) + 1
            if record.get("boundary_hit"):
                n_boundary += 1
        else:
            # P3-7: the workflow path records parabolic QC with the same conversion
            record = _parabolic_stencil_qc(
                data, [int(round(v)) for v in peak.position], spectrum_axes
            )
            if record.get("boundary_hit"):
                n_boundary += 1
        # P3-7: the QC columns follow the method this row actually used - a
        # gaussian failure/fallback is False, and only a parabola with no
        # value at all leaves NaN. Under targeted localization an untargeted
        # peak never ran this method, so it stays NaN rather than failed.
        if skipped:
            fit_success = None
        elif wanted == "gaussian":
            fit_success = bool(record.get("gaussian_fit_success"))
        else:
            fit_success = (
                None
                if record.get("fit_success") is None
                else bool(record["fit_success"])
            )
        h_ppm = (
            float(spectrum_axes.ppm_at_fraction(axis_h, position[axis_h]))
            if axis_h is not None
            else float("nan")
        )
        n_ppm = (
            float(spectrum_axes.ppm_at_fraction(axis_n, position[axis_n]))
            if axis_n is not None
            else float("nan")
        )
        fwhm_map = {
            str(k): float(v) for k, v in (record.get("fwhm_by_nucleus") or {}).items()
        }
        rows.append(
            {
                "peak_id": int(index),
                "reference_peak_id": "",
                "assignment": "",
                "H_ppm": h_ppm,
                "N_ppm": n_ppm,
                "intensity": float(peak.height),
                "SNR": float(peak.snr),
                "detected": True,
                "localization_method": wanted,
                "localization_requested": str(record.get("requested_method") or wanted),
                "fallback": bool(record.get("fallback")),
                "fallback_reason": str(record.get("fallback_reason", "") or ""),
                # P3-7: the QC columns follow the method this row actually used
                # P3-7: the QC columns follow the method this row used
                "fit_success": fit_success,
                "FWHM_H": fwhm_map.get("1H"),
                "FWHM_N": fwhm_map.get("15N"),
                "fit_rmse": (
                    float(record["fit_rmse"])
                    if record.get("fit_rmse") is not None
                    else None
                ),
                "boundary_hit": (
                    None
                    if record.get("boundary_hit") is None
                    else bool(record.get("boundary_hit"))
                ),
                "duplicate_localization": False,
            }
        )
    # P2-5: flag rows sharing a coordinate (the picker's sub-grid refinement can pull
    # two neighbouring detected peaks onto the same grid point)
    from nmrforge_api.peak_tables import mark_duplicate_localization

    n_duplicate = mark_duplicate_localization(rows)
    meta = {
        "sigma_multiplier": threshold,
        "min_snr": threshold,
        "sign_mode": resolved_sign,
        "edge_margin_ppm": round(float(edge_ppm), 6),
        "edge_margin_points": int(edge_points),
        "edge_margin_source": edge_source,
        "noise_sigma": sigma,
        "localization_method": wanted,
        "n_peaks": len(rows),
        "n_fallback": int(n_fallback),
        "fallback_reasons": fallback_reasons,
        "n_boundary_hit": int(n_boundary),
        # targeted localization (2026-09-19): scope plus counts. Untargeted peaks
        # are not refined (position from the detection-stage parabola, this method's
        # QC columns are NaN).
        "localization_scope": (
            "all" if target_ids is None else ("none" if not target_ids else "subset")
        ),
        "n_targeted": len(target_ids) if target_ids is not None else len(rows),
        "n_skipped": int(n_skipped),
        "targeted_peak_ids": (
            [int(value) for value in sorted(target_ids)]
            if target_ids is not None
            else []
        ),
        "duplicate_localization": {
            "n_rows": len(rows),
            "n_extra": int(n_duplicate),
        },
    }
    return rows, meta


__all__ = [
    "DEFAULT_DETECTION_SIGMA",
    "DEFAULT_WINDOW_PTS_FALLBACK",
    "PeakMeasurement",
    "detect_and_localize",
    "measure_peak_positions",
    "peak_coordinates",
    "pick_reference_peaks",
    "read_reference_peaks",
    "reference_peak_id",
    "window_points_by_axis",
]
