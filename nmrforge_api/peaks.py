"""Independent peak detection and single-spectrum reference-list measurement.

The main API detects each spectrum independently. Reference-list measurement accepts
multidimensional local peaks within physical windows and assigns overlapping candidates
by joint distance; absent peaks retain explicit failure states. Logical F-axis coordinates
survive storage permutations and repeated nuclei. Three-point parabolic interpolation and
polarity-normalized stencil QC do not establish cross-spectrum correspondence, uncertainty,
or line-shape validity.
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
    """Convert a local peak index to a stable identifier such as ``R0001``.

    This identifier belongs to one spectrum's reference list; it does not establish identity
    or correspondence across spectra.
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

    reference_peak_id: str = ""
    reference: dict[str, float] = field(default_factory=dict)
    positions: dict[str, float] = field(default_factory=dict)
    axis_nuclei: dict[str, str] = field(default_factory=dict)
    deltas: dict[str, float] = field(default_factory=dict)
    intensity: float = 0.0

    noise_sigma: float = 0.0
    snr: float = 0.0
    found: bool = True
    window_edge: bool = False
    boundary: bool = False
    out_of_range: bool = False
    # Legacy separable cell geometry is unavailable for joint multidimensional ownership.
    # Intensity ratio is measured height / input Height (missing or zero => NaN).
    cell_low: dict[str, int] = field(default_factory=dict)
    cell_high: dict[str, int] = field(default_factory=dict)
    cell_edge: bool | None = None
    intensity_ratio: float = float("nan")
    # Actual method, failure/fallback reasons and parabolic stencil QC.
    localization: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "peak_id": int(self.peak_id),
            "assignment": self.assignment,
            "reference_peak_id": self.reference_peak_id,
            "reference": {k: float(v) for k, v in self.reference.items()},
            "positions": {k: float(v) for k, v in self.positions.items()},
            "axis_nuclei": dict(self.axis_nuclei),
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
            "cell_edge": self.cell_edge,
            "intensity_ratio": float(self.intensity_ratio),
            "localization": {
                str(k): v for k, v in (self.localization or {}).items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PeakMeasurement:
        return cls(
            peak_id=int(data.get("peak_id", 0) or 0),
            assignment=str(data.get("assignment") or ""),
            reference_peak_id=str(
                data.get("reference_peak_id", reference_peak_id(data.get("peak_id", 0))) or ""
            ),
            reference={k: float(v) for k, v in (data.get("reference") or {}).items()},
            positions={k: float(v) for k, v in (data.get("positions") or {}).items()},
            axis_nuclei=dict(data.get("axis_nuclei") or {}),
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
            cell_edge=None if data.get("cell_edge") is None else bool(data["cell_edge"]),
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
    if not ({"h_ppm", "n_ppm"} <= set(header) or "f1_ppm" in header):
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
        parsed = {
                "Peak_ID": index,
                "label": str(normalized.get("assignment") or "")
                if "assignment" in normalized else label,
                "H_shift": _num("h_ppm"),
                "N_shift": _num("n_ppm"),
                "C_shift": _num("c_ppm"),
                "Intensity": _num("height") or _num("intensity"),
                "peak_id_raw": raw_id,
            }
        for logical in range(1, 4):
            value = _num(f"f{logical}_ppm")
            if value and np.isfinite(float(value)):
                parsed[f"F{logical}_shift"] = value
        parsed["reference_peak_id"] = normalized.get("reference_peak_id") or ""
        rows.append(parsed)
    return rows


def read_reference_peaks(
    path: Path | str, *, axes: SpectrumAxes | None = None,
) -> list[dict[str, Any]]:
    """Read the reference peak table, keeping peaks with at least one nucleus position.

    Three formats are accepted:

    - POKY/Sparky ``.list`` (the standard table NMRForge writes);
    - the legacy NMRForge CSV (``H_shift``/``N_shift`` columns);
    - the study-project CSV (``peak_id,H_ppm,N_ppm,height,linewidth,volume``,
        a reference table exported from a public archive).
    """
    from core.peaks.peak_table import import_peaks_poky, load_peaks

    target = Path(path)
    if not target.is_file():
        raise MeasurementError(tr("reference peak table does not exist: {p0}", p0=target))
    peaks = _read_ppm_csv(target)
    if peaks is None:
        if axes is not None and target.suffix.lower() == ".list":
            logical_nuclei = [axes.nuclei[storage] for storage in axes.logical_to_storage]
            peaks = import_peaks_poky(target, nuclei=logical_nuclei)
            if axes.ndim == 2:
                conventional_hn = (axes.nuclei.count("1H") == 1
                                   and axes.nuclei.count("15N") == 1)
                for row in peaks:
                    by_storage = (
                        {axes.storage_of("15N"): row.get("N_shift"),
                         axes.storage_of("1H"): row.get("H_shift")}
                        if conventional_hn else {0: row.get("N_shift"), 1: row.get("H_shift")}
                    )
                    for logical, storage in enumerate(axes.logical_to_storage, start=1):
                        row[f"F{logical}_shift"] = by_storage[storage]
                    if not conventional_hn:
                        row.pop("N_shift", None)
                        row.pop("H_shift", None)
        else:
            peaks = load_peaks(target)
    # Stable identity is local to this condition's reference, not inherited by workflows.
    for position, row in enumerate(peaks, start=1):
        if not row.get('reference_peak_id'):
            row['reference_peak_id'] = reference_peak_id(
                row.get('Peak_ID') or position
            )
    usable = [row for row in peaks if peak_coordinates(row, axes)]
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
    if axes is None:
        for logical in range(1, 4):
            value = row.get(f"F{logical}_shift", row.get(f"F{logical}_ppm"))
            if value not in (None, "") and np.isfinite(float(value)):
                coords[f"F{logical}"] = float(value)
    else:
        from workflow.peak_align import coordinate_key

        logical_nuclei = [axes.nuclei[storage] for storage in axes.logical_to_storage]
        for logical, storage in enumerate(axes.logical_to_storage):
            value = row.get(f"F{logical + 1}_shift", row.get(f"F{logical + 1}_ppm"))
            if value is None or str(value) == "":
                continue
            if not np.isfinite(float(value)):
                continue
            nucleus = axes.nuclei[storage] if storage < len(axes.nuclei) else ""
            if nucleus:
                coords[coordinate_key(logical_nuclei, logical)] = float(value)
    for key, nucleus in _NAMED_KEYS:
        value = row.get(key)
        if value is not None and str(value) != "":
            if not np.isfinite(float(value)):
                continue
            if axes is not None and axes.nuclei.count(nucleus) > 1:
                if all(
                    row.get(f"F{logical + 1}_shift", row.get(f"F{logical + 1}_ppm"))
                    not in (None, "")
                    for logical, storage in enumerate(axes.logical_to_storage)
                    if axes.nuclei[storage] == nucleus
                ):
                    continue
                raise MeasurementError(tr(
                    "Repeated nuclei require explicit F-axis coordinates: {p0}", p0=nucleus,
                ))
            coords.setdefault(nucleus, float(value))
    return coords


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


def _parabolic_qc(y_minus: float, y_zero: float, y_plus: float, step_ppm: float) -> dict[str, Any]:
    """Convert a three-point parabola to a vertex offset and linewidth QC record.

    Invalid curvature or height yields no linewidth estimate. An offset at the half-point
    limit sets ``boundary_hit`` because the true maximum may lie outside the stencil.
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
    data: np.ndarray, center: Sequence[int], axes: SpectrumAxes, *, baseline: float = 0.0,
) -> dict[str, Any]:
    """Create the three-point parabolic QC record for an integer-grid peak."""
    fwhm: dict[str, float] = {}
    fwhm_axis: dict[str, float] = {}
    ok = True
    edge = False
    polarity = 1.0 if float(data[tuple(center)]) - baseline >= 0 else -1.0
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
            profile.append(polarity * (float(data[tuple(index_tuple)]) - baseline))
        qc = _parabolic_qc(*profile, step_ppm=_axis_step_ppm(axes, axis))
        if qc["boundary_hit"]:
            edge = True
        value = qc["fwhm_ppm"]
        if value is None:
            ok = False
        else:
            logical = axes.logical_to_storage.index(axis) + 1
            fwhm_axis[f"F{logical}"] = float(value)
            if nucleus and axes.nuclei.count(nucleus) == 1:
                fwhm[nucleus] = float(value)
    return {
        "requested_method": "parabolic",
        "actual_method": "parabolic",
        "fit_success": bool(ok),
        "boundary_hit": bool(edge),
        "fwhm_by_nucleus": fwhm,
        "fwhm_by_axis": fwhm_axis,
        "failure_reason": "" if ok else "invalid_or_boundary_parabolic_stencil",
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
            source = tr("ppm_explicit") if window_ppm is not None else tr("ppm_auto_linewidth")
            points = axis_units.points_for_ppm(axis_ppm, width)
            if points <= 0:
                points = int(DEFAULT_WINDOW_PTS_FALLBACK)
                width = axis_units.ppm_for_points(axis_ppm, points)
                source = tr("points_fallback")
        out[axis] = {
            "points": int(points),
            "ppm": round(float(width), 6),
            "effective_ppm": round(axis_units.ppm_for_points(axis_ppm, points), 6),
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
    noise_sigma: float | None = None,
    exclusive_windows: bool = True,
    min_snr: float = 3.0,
) -> list[PeakMeasurement]:
    """Measure reference-list positions on one spectrum.

    Search windows default to a physical linewidth width, so zero filling does not change
    their ppm span. ``window_ppm`` sets a physical half-width; ``window_pts`` forces points.
    By default, candidates must be full-dimensional local maxima above ``min_snr`` and
    overlapping windows are resolved by joint distance. Missing peaks remain in the result
    with ``found=False`` and empty positions. ``nuclei`` limits the axes to localize; other
    coordinates are left unchanged.

    ``refine`` accepts ``"parabolic"`` (three-point vertex interpolation) or ``"none"``
    (integer-grid maximum). Gaussian fitting has been removed and is rejected. Noise defaults
    to a robust MAD estimate; the result records intensity, SNR, detection, and localization QC.

        Parameters
        ----------
        spectrum_path : Path | str
            the spectrum to measure.
        peaks : Sequence[dict[str, Any]]
            the candidate peaks (unified fields: ``N_shift``/``H_shift``/``label``...).
        window_pts : int, optional
            half-width of the search window in points; exclusive with ``window_ppm``.
        window_ppm : float, optional
            half-width in ppm. Prefer this: it converts at the current spacing and does not
            drift.
        axes : SpectrumAxes, optional
            already-read axes, reused across peaks to avoid re-reading.
        sign : str, default "abs"
            which peak signs to follow.
        refine : str, default "parabolic"
            ``parabolic`` or ``none``; Gaussian fitting is not supported.
        nuclei : Iterable[str], optional
            axis nuclei; inferred from the header by default.
        noise_sigma : float, optional
            known noise sigma; estimated from the spectrum by default.
        exclusive_windows : bool, default True
            require local peaks and resolve overlapping windows by joint distance.

        Returns
        -------
        list[PeakMeasurement]
            per-peak position, intensity/SNR, detection, and localization diagnostics.

        Raises
        ------
        MeasurementError
            the spectrum or parameters are invalid, or a removed refinement is requested.

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
    if not np.isfinite(min_snr) or min_snr <= 0:
        raise MeasurementError("min_snr must be a positive finite number")
    if window_ppm is not None and (not np.isfinite(window_ppm) or window_ppm <= 0):
        raise MeasurementError("window_ppm must be a positive finite number")
    if refine not in ("parabolic", "none"):
        raise MeasurementError(tr(
            "unknown refine: {p0!r} (parabolic / none; the Gaussian fit was removed)",
            p0=refine,
        ))

    spectrum_axes = axes if axes is not None else read_spectrum_axes(path)
    axes = spectrum_axes
    data = np.asarray(axes.data, dtype=float)
    window_by_axis = window_points_by_axis(
        axes, window_pts=window_pts, window_ppm=window_ppm
    )
    from core.qc import noise as _noise

    estimate = _noise.estimate(data)
    data = data - estimate.baseline
    sigma = (
        float(noise_sigma)
        if noise_sigma is not None
        else float(estimate.global_sigma)
    )
    if not np.isfinite(sigma) or sigma < 0:
        raise MeasurementError("noise_sigma must be finite and non-negative")
    # Require a real full-dimensional local peak, not an arbitrary window maximum.
    # Reuse the detector's compact-plateau policy and the same median baseline.
    from core.qc.peak_detection import PeakDetectionParams, _candidates

    detector = PeakDetectionParams(sigma_multiplier=min_snr, min_snr=min_snr, refine=False)
    candidates = []
    for polarity in ((1, -1) if sign == "abs" else ((1,) if sign == "positive" else (-1,))):
        candidates.extend(_candidates(data, sigma, detector, polarity))
    wanted = {str(n) for n in nuclei} if nuclei else None


    plans: list[dict[str, Any]] = []
    for index, row in enumerate(peaks, start=1):
        coords = peak_coordinates(row, axes)
        search_axes: list[int] = []
        search_nuclei: list[str] = []
        centers: list[int] = []
        out_of_range = False
        for nucleus, ppm_value in coords.items():
            if (wanted is not None and nucleus not in wanted
                    and nucleus.split(":", 1)[0] not in wanted):
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


                out_of_range = True
            fraction = axes.fraction_from_ppm(axis, ppm_value)
            search_axes.append(axis)
            search_nuclei.append(nucleus)
            centers.append(int(min(max(round(fraction), 0), size - 1)))
        window_bounds = {
            axis: (max(0, center - int(window_by_axis[axis]["points"])),
                   min(data.shape[axis] - 1, center + int(window_by_axis[axis]["points"])))
            for axis, center in zip(search_axes, centers)
        }
        # Non-searched axes remain at the reference coordinate, never a projection.
        for nucleus, ppm_value in coords.items():
            axis = axes.storage_of(nucleus)
            if axis is not None and axis not in window_bounds:
                center = int(round(axes.fraction_from_ppm(axis, ppm_value)))
                window_bounds[axis] = (center, center)
        plans.append(
            {
                "index": index,
                "row": row,
                "coords": coords,
                "search_axes": search_axes,
                "search_nuclei": search_nuclei,
                "centers": centers,
                "out_of_range": out_of_range,
                "bounds": window_bounds,
                "complete": len(window_bounds) == data.ndim,
            }
        )
    available: dict[int, list[Any]] = {p["index"]: [] for p in plans}
    lost: set[int] = set()
    for candidate in candidates:
        eligible = [p for p in plans if p["search_axes"] and p["complete"] and all(
            low <= candidate.position[axis] <= high
            for axis, (low, high) in p["bounds"].items()
        )]
        if not eligible:
            continue
        if exclusive_windows:
            # Joint distance only among windows containing this local peak.
            # Distant H positions cannot clip a nearby N search (or vice versa).
            def distance(plan: dict[str, Any]) -> float:
                return sum(
                    ((candidate.position[axis] - center)
                     / max(1, int(window_by_axis[axis]["points"]))) ** 2
                    for axis, center in zip(plan["search_axes"], plan["centers"])
                )
            owner = min(eligible, key=lambda p: (distance(p), p["index"]))
            lost.update(p["index"] for p in eligible if p is not owner)
            eligible = [owner]
        for plan in eligible:
            available[plan["index"]].append(candidate)
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
            axis_nuclei={f"F{i + 1}": axes.nuclei[storage]
                         for i, storage in enumerate(axes.logical_to_storage)},
            found=False,
        )
        measurement.out_of_range = bool(plan["out_of_range"])
        measurement.localization = {
            "requested_method": refine,
            "actual_method": "none",
            "candidate_ownership_conflict": index in lost,
            "search_bounds_by_axis": {
                f"F{axes.logical_to_storage.index(axis) + 1}": {
                    "low": int(low), "high": int(high), "storage_axis": axis,
                    "unit": "points", "inclusive": True,
                } for axis, (low, high) in plan["bounds"].items()
            },
        }
        search_axes = plan["search_axes"]
        search_nuclei = plan["search_nuclei"]
        centers = plan["centers"]
        if not search_axes:
            measurement.found = False
            measurement.localization["failure_reason"] = "incomplete_reference_coordinates"
            results.append(measurement)
            continue

        # Physical bounds are audited separately, never exported as exclusive cells.
        window_bounds: dict[int, tuple[int, int]] = {}
        for axis, center in zip(search_axes, centers):
            size = int(data.shape[axis])
            half = int(window_by_axis.get(axis, {}).get("points", 0))
            window_low = max(0, center - half)
            window_high = min(size - 1, center + half)
            window_bounds[axis] = (window_low, window_high)
        eligible = available[index]
        if not eligible:
            measurement.localization["failure_reason"] = (
                "incomplete_reference_coordinates" if not plan["complete"] else
                "candidate_owned_by_other_reference" if index in lost else
                "no_local_peak_above_threshold"
            )
            results.append(measurement)
            continue
        candidate = max(eligible, key=lambda p: abs(p.height))
        best = [int(v) for v in candidate.position]
        measurement.found = True
        measurement.intensity = float(
            data[tuple(best)] if np.isfinite(data[tuple(best)]) else 0.0
        )
        measurement.snr = (
            abs(measurement.intensity) / sigma if sigma > 0 else 0.0
        )




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


        fractions = [float(index_) for index_ in best]
        if refine == "parabolic":

            qc_fwhm: dict[str, float] = {}
            qc_fwhm_axis: dict[str, float] = {}
            qc_ok = True
            qc_edge = False
            for axis, nucleus in zip(search_axes, search_nuclei):
                position = best[axis]
                size = int(data.shape[axis])
                if position <= 0 or position >= size - 1:

                    qc_ok = False
                    qc_edge = True
                    continue
                profile = []
                for offset in (-1, 0, 1):
                    index_tuple = list(best)
                    index_tuple[axis] = position + offset
                    value = float(data[tuple(index_tuple)])
                    value *= candidate.sign
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
                    qc_fwhm_axis[f"F{axes.logical_to_storage.index(axis) + 1}"] = float(value)
            measurement.localization.update({
                "requested_method": "parabolic",
                "actual_method": "parabolic",
                "fit_success": qc_ok,
                "boundary_hit": qc_edge,
                "fwhm_by_nucleus": qc_fwhm,
                "fwhm_by_axis": qc_fwhm_axis,
                "failure_reason": "" if qc_ok else "invalid_or_boundary_parabolic_stencil",
                "source": tr("core.peaks(three-point parabola)"),
                "baseline_offset": float(estimate.baseline),
                "height_reference": "global_median_baseline",
            })

        for nucleus, _ppm_value in coords.items():
            if (wanted is not None and nucleus not in wanted
                    and nucleus.split(":", 1)[0] not in wanted):
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
        for logical, storage in enumerate(axes.logical_to_storage, start=1):
            if storage in search_axes:
                measurement.positions[f"F{logical}"] = axes.ppm_at_fraction(
                    storage, fractions[storage]
                )
        results.append(measurement)
    return results


def pick_reference_peaks(
    session: Any,
    *,
    sigma_multiplier: float | None = None,
    out_path: Path | str | None = None,
    details: dict[str, Any] | None = None,
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
    sign_mode: str = "auto",
    axes: SpectrumAxes | None = None,
    targets: Collection[int] | None = None,
    allow_empty_targets: bool = False,
    experiment: Any | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Pick independently on **this combination's own spectrum** at the given threshold.

    - the detection convention matches project picking: physical margin (`axis_units`) +
        `peak_detection.detect` (`sigma_multiplier` doubling as `min_snr`) + dominant sign;
    - localization uses three-point parabolic interpolation only;
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
    if sigma_multiplier is not None and (
        not np.isfinite(sigma_multiplier) or sigma_multiplier <= 0
    ):
        raise MeasurementError("sigma_multiplier must be a positive finite number")
    threshold = (
        float(sigma_multiplier)
        if sigma_multiplier is not None and float(sigma_multiplier) > 0
        else float(DEFAULT_DETECTION_SIGMA)
    )
    try:
        from backend.config import load_processing_defaults

        linewidths = load_processing_defaults().get("linewidth_hz") or {}
    except Exception:
        linewidths = {}
    if edge_margin_points is not None:
        edge_points = max(0, int(edge_margin_points))
        edge_ppm = axis_units.ppm_for_points(spectrum_axes.ppm[0], edge_points)
        edge_source = tr("points_explicit")
    elif edge_margin_ppm is not None:
        edge_ppm = max(0.0, float(edge_margin_ppm))
        edge_source = tr("ppm_explicit")
        edge_points = axis_units.points_for_ppm(spectrum_axes.ppm[0], edge_ppm)
        if edge_points <= 0 and edge_ppm > 0:
            edge_points = 1
            edge_ppm = axis_units.ppm_for_points(spectrum_axes.ppm[0], edge_points)
            edge_source = tr("points_fallback")
    else:
        edge_points = 0
        edge_ppm = 0.0
        edge_source = "acquisition_and_spectral_evidence"
    estimate = _noise.estimate(data)
    sigma = float(estimate.global_sigma)
    centered = data - estimate.baseline
    peaks = _detect.detect(
        data,
        _detect.PeakDetectionParams(
            sign_mode="both",
            sigma_multiplier=threshold,
            min_snr=threshold,
            edge_margin=edge_points,
            refine=False,
        ),
    )
    axial = None
    if edge_margin_points is None and edge_margin_ppm is None:
        from core.peaks.axial import filter_axial_peaks

        peaks, axial = filter_axial_peaks(
            data, peaks, experiment, dic=spectrum_axes.dic, axes_ppm=spectrum_axes.ppm,
            linewidth_hz_by_nucleus=linewidths,
        )
    resolved_sign = str(sign_mode or "auto")
    if resolved_sign not in ("auto", "dominant", "positive", "negative", "both"):
        raise MeasurementError(tr("Unknown peak sign mode: {p0}", p0=resolved_sign))
    if resolved_sign == "auto":
        from core.experiments.registry import get as get_template
        from workflow.pick_peaks import strong_two_sign_evidence

        kind = getattr(experiment, "experiment_type", None)
        template = get_template(str(getattr(kind, "name", "") or ""))
        confidence = float(getattr(kind, "confidence", 0.0) or 0.0)
        uncertain = template is None or not np.isfinite(confidence) or confidence < 0.6
        resolved_sign = (
            "both" if template is not None and template.peak_sign == "mixed"
            or uncertain and strong_two_sign_evidence(peaks) else "dominant"
        )
    if resolved_sign == "dominant":
        peaks = _detect.keep_dominant(peaks)
    elif resolved_sign in ("positive", "negative"):
        sign = 1 if resolved_sign == "positive" else -1
        peaks = [peak for peak in peaks if peak.sign == sign]

    target_ids: set[int] | None = None
    if targets is not None:
        target_ids = set()
        for value in targets:
            try:
                number = int(value)
                if isinstance(value, (bool, np.bool_)) or float(value) != number:
                    raise ValueError("not an integer peak ID")
            except (TypeError, ValueError, OverflowError) as exc:
                raise MeasurementError("Target peak IDs must be integers") from exc
            target_ids.add(number)
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
    axis_h = (spectrum_axes.storage_of("1H")
              if spectrum_axes.nuclei.count("1H") == 1 else None)
    axis_n = (spectrum_axes.storage_of("15N")
              if spectrum_axes.nuclei.count("15N") == 1 else None)
    rows: list[dict[str, Any]] = []
    n_boundary = 0
    n_skipped = 0
    qc_failure_reasons: dict[str, int] = {}
    for index, peak in enumerate(peaks, start=1):
        position = tuple(float(v) for v in peak.position)
        record: dict[str, Any] = {}
        skipped = target_ids is not None and index not in target_ids
        if skipped:

            n_skipped += 1
        else:
            integer = np.asarray(peak.position, dtype=int)
            # The vertex quotient is invariant to sign: no whole-array copy per peak.
            position = tuple(
                _detect.refine_parabolic(centered, integer, axis)
                for axis in range(data.ndim)
            )

            record = _parabolic_stencil_qc(
                data, [int(round(v)) for v in peak.position], spectrum_axes,
                baseline=estimate.baseline,
            )
            if record.get("boundary_hit"):
                n_boundary += 1
            reason = str(record.get("failure_reason") or "")
            if reason:
                qc_failure_reasons[reason] = qc_failure_reasons.get(reason, 0) + 1


        if skipped:
            fit_success = None
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
                "localization_requested": "parabolic",
                "localization_method": "none" if skipped else "parabolic",
                "fallback": bool(record.get("fallback")),
                "fallback_reason": str(record.get("fallback_reason", "") or ""),
                "failure_reason": str(record.get("failure_reason") or ""),

                "fit_success": fit_success,
                "FWHM_H": fwhm_map.get("1H"),
                "FWHM_N": fwhm_map.get("15N"),
                "boundary_hit": (
                    None
                    if record.get("boundary_hit") is None
                    else bool(record.get("boundary_hit"))
                ),
                "duplicate_localization": False,
            }
        )
        for logical, storage in enumerate(spectrum_axes.logical_to_storage, start=1):
            rows[-1][f"F{logical}_ppm"] = float(
                spectrum_axes.ppm_at_fraction(storage, position[storage])
            )
            rows[-1][f"F{logical}_nucleus"] = spectrum_axes.nuclei[storage]
            rows[-1][f"FWHM_F{logical}"] = (record.get("fwhm_by_axis") or {}).get(
                f"F{logical}"
            )

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
        "baseline_offset": float(estimate.baseline),
        "height_reference": "global_median_baseline",
        "localization_method": "parabolic",
        "n_peaks": len(rows),
        "n_fallback": 0,
        "fallback_reasons": {},
        "qc_failure_reasons": qc_failure_reasons,
        "n_boundary_hit": int(n_boundary),


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
    if axial is not None:
        meta["axial_screening"] = axial
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
