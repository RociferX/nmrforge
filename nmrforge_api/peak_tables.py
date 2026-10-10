"""Unified peak table CSV with stable per-spectrum peak identities and localisation QC.

Each workflow independently picks peaks on its own spectrum and writes one fixed-schema table.
The current v1.1.1 contract has 38 columns: workflow and dataset identity, 1H/15N compatibility
coordinates, logical F1/F2/F3 coordinates and nuclei, requested and actual localisation method,
failure/fallback details, fit QC, duplicate-coordinate flags, and per-peak cell diagnostics.
Gaussian fitting and ``fit_rmse`` were removed; localisation uses three-point parabolic
refinement. H/N aliases are populated only when the corresponding nucleus is unique, and
duplicate detection uses complete logical-dimension coordinates. Undetected reference peaks
remain as rows with ``detected=false``.
"""

from __future__ import annotations

import csv
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from nmrforge_api.peaks import PeakMeasurement

NAN_TEXT = "NaN"

PEAK_TABLE_COLUMNS: tuple[str, ...] = (
    "workflow_id",
    "condition",
    "dataset",
    "peak_id",
    "reference_peak_id",
    "assignment",
    "H_ppm",
    "N_ppm",
    "intensity",
    "SNR",
    "detected",
    "localization_method",
    "localization_requested",
    "fallback",
    "fallback_reason",
    "failure_reason",
    "fit_success",
    "FWHM_H",
    "FWHM_N",
    "boundary_hit",
    "duplicate_localization",
    "F1_ppm",
    "F1_nucleus",
    "FWHM_F1",
    "F2_ppm",
    "F2_nucleus",
    "FWHM_F2",
    "F3_ppm",
    "F3_nucleus",
    "FWHM_F3",
    "cell_low_H",
    "cell_high_H",
    "cell_low_N",
    "cell_high_N",
    "cell_edge",
    "intensity_ratio_vs_picked",
    "shift_vs_picked_H",
    "shift_vs_picked_N",
)

_CELL_COLUMNS: tuple[str, ...] = (
    "cell_low_H",
    "cell_high_H",
    "cell_low_N",
    "cell_high_N",
    "cell_edge",
    "intensity_ratio_vs_picked",
    "shift_vs_picked_H",
    "shift_vs_picked_N",
)

#: Boolean columns (written true/false, read back as bool)
_BOOL_COLUMNS: frozenset[str] = frozenset(
    {
        "detected",
        "fallback",
        "fit_success",
        "boundary_hit",
        "cell_edge",
        "duplicate_localization",
    }
)
#: Numeric columns (missing -> NaN; read back as float, NaN preserved)
_FLOAT_COLUMNS: frozenset[str] = frozenset(
    {
        "peak_id",
        "H_ppm",
        "N_ppm",
        "intensity",
        "SNR",
        "FWHM_H",
        "FWHM_N",
        "F1_ppm",
        "FWHM_F1",
        "F2_ppm",
        "FWHM_F2",
        "F3_ppm",
        "FWHM_F3",
        "intensity_ratio_vs_picked",
        "shift_vs_picked_H",
        "shift_vs_picked_N",
    }
)
#: integer columns (grid-point indices; missing written as NaN, read back as int)
_INT_COLUMNS: frozenset[str] = frozenset({"cell_low_H", "cell_high_H", "cell_low_N", "cell_high_N"})
_TEXT_COLUMNS: frozenset[str] = frozenset(
    {
        "workflow_id",
        "condition",
        "dataset",
        "reference_peak_id",
        "assignment",
        "localization_method",
        "localization_requested",
        "fallback_reason",
        "failure_reason",
        "F1_nucleus",
        "F2_nucleus",
        "F3_nucleus",
    }
)

#: workflow_id used inside the reference peak table (a reference is not a combination)
REFERENCE_WORKFLOW_ID = "reference"


def reference_peak_id(peak_id: Any) -> str:
    """Peak index -> stable identity ``R0001`` (frozen once the reference table exists)."""
    try:
        number = int(peak_id)
    except (TypeError, ValueError):
        return str(peak_id or "")
    return f"R{number:04d}"


def _as_float(value: Any) -> float | None:
    """Value -> float; None/blank/invalid -> None (written as NaN)."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return float(int(value))
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "y")
    return bool(value)


def _fmt(value: Any) -> str:
    """Cell text: numbers use %.10g; anything missing becomes ``NaN``."""
    number = _as_float(value)
    if number is None or not math.isfinite(number):
        return NAN_TEXT
    return f"{number:.10g}"


def format_cell(column: str, value: Any) -> str:
    """One column's values -> CSV text (the column name decides the type).

    Fields a run cannot produce are ``NaN``, not false/0, so the shared table schema keeps a
    consistent meaning. Localisation QC comes from the three-point parabolic refinement.
    """
    if value is None:
        return "" if column in _TEXT_COLUMNS else NAN_TEXT
    if str(value).strip().lower() == "nan":
        return "" if column in _TEXT_COLUMNS else NAN_TEXT
    if column in _BOOL_COLUMNS:
        return "true" if _as_bool(value) else "false"
    if column in _FLOAT_COLUMNS:
        return _fmt(value)
    if column in _INT_COLUMNS:
        number = _as_float(value)
        if number is None or not math.isfinite(number):
            return NAN_TEXT
        return str(int(round(number)))
    text = str(value)
    return text if text else ""


def write_peak_table(path: Path | str, rows: Sequence[Mapping[str, Any]]) -> Path:
    """Write the unified peak-table CSV (header = ``PEAK_TABLE_COLUMNS``)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(list(PEAK_TABLE_COLUMNS))
        for row in rows:
            writer.writerow([format_cell(column, row.get(column)) for column in PEAK_TABLE_COLUMNS])
    return target


def read_peak_table(path: Path | str) -> list[dict[str, Any]]:
    """Read the unified peak-table CSV: numeric NaN/blank -> ``float('nan')``, booleans -> bool."""
    target = Path(path)
    if not target.is_file():
        return []
    rows: list[dict[str, Any]] = []
    text = target.read_text(encoding="utf-8-sig")
    for raw in csv.DictReader(text.splitlines()):
        row: dict[str, Any] = {}
        for key, value in raw.items():
            if key is None:
                continue
            if key in _BOOL_COLUMNS:
                token = str(value).strip().lower()
                row[key] = float("nan") if token in ("", "nan") else _as_bool(value)
            elif key in _FLOAT_COLUMNS:
                number = _as_float(value)
                row[key] = float("nan") if number is None else number
            elif key in _INT_COLUMNS:
                number = _as_float(value)
                row[key] = (
                    float("nan")
                    if number is None or not math.isfinite(number)
                    else int(round(number))
                )
            else:
                row[key] = (
                    "" if value is None or key in _TEXT_COLUMNS
                    and str(value).strip().lower() == "nan" else value
                )
        rows.append(row)
    return rows


def _localization_record(measurement: PeakMeasurement) -> dict[str, Any]:
    return dict(getattr(measurement, "localization", None) or {})


def _fwhm_by_nucleus(record: Mapping[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for nucleus, value in (record.get("fwhm_by_nucleus") or {}).items():
        number = _as_float(value)
        if number is not None:
            out[str(nucleus)] = number
    return out


def _coordinate_key(row: Mapping[str, Any]) -> tuple[Any, ...] | None:
    """Use complete logical F-axis identity for duplicate checks; retain legacy H/N rows."""
    if any(row.get(f"F{axis}_nucleus") for axis in (1, 2, 3)):
        values: list[tuple[str, str, int]] = []
        for axis in (1, 2, 3):
            nucleus = str(row.get(f"F{axis}_nucleus") or "")
            if not nucleus:
                continue
            number = _as_float(row.get(f"F{axis}_ppm"))
            if number is None or not math.isfinite(number):
                return None
            values.append((f"F{axis}", nucleus, int(round(number * 1e6)))
            )
        return tuple(values) if values else None
    values: list[int] = []
    for column in ("H_ppm", "N_ppm"):
        number = _as_float(row.get(column))
        if number is None or not math.isfinite(number):
            return None
        values.append(int(round(number * 1e6)))
    return ("HN", values[0], values[1])


def mark_duplicate_localization(rows: Sequence[dict[str, Any]]) -> int:
    """Flag rows sharing a coordinate (P2-5); returns the number of extra rows.

    Every row of a duplicated group gets ``duplicate_localization=true``: which row is
    "the extra one" depends on the downstream convention (matching / detectability
    counting), so the table does not decide that for the caller. The picker's
    sub-grid refinement can pull two neighbouring detected peaks onto the same grid
    point, and even the reference table's one-cell-per-peak rule shares a cell at the
    resolution limit; such rows are flagged, never dropped.
    """
    buckets: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for row in rows:
        if not _as_bool(row.get("detected", True)):
            row["duplicate_localization"] = False
            continue
        key = _coordinate_key(row)
        if key is None:
            row["duplicate_localization"] = False
            continue
        buckets.setdefault(key, []).append(row)
    extra = 0
    for group in buckets.values():
        duplicated = len(group) > 1
        if duplicated:
            extra += len(group) - 1
        for row in group:
            row["duplicate_localization"] = duplicated
    return extra


def peak_table_row(
    measurement: PeakMeasurement,
    *,
    workflow_id: str,
    condition: str = "",
    dataset: str = "",
    method: str = "parabolic",
    requested_method: str | None = None,
    fallback_reason: str = "",
) -> dict[str, Any]:
    """One measurement -> one peak-table row (shared by both methods).

    QC columns come from ``measurement.localization`` and the three-point parabolic refinement.
    Keys the record does not carry are written as NaN. Positions are stored by logical axis;
    the H/N compatibility aliases are populated only for unique matching nuclei.
    """
    record = _localization_record(measurement)
    fwhm = _fwhm_by_nucleus(record)
    fwhm_axis = {
        str(axis): value
        for axis, value in (record.get("fwhm_by_axis") or {}).items()
        if _as_float(value) is not None
    }
    positions = dict(measurement.positions or {})
    axis_nuclei = dict(getattr(measurement, "axis_nuclei", None) or {})
    cell_low = dict(getattr(measurement, "cell_low", None) or {})
    cell_high = dict(getattr(measurement, "cell_high", None) or {})
    row: dict[str, Any] = {
        "workflow_id": str(workflow_id),
        "condition": str(condition),
        "dataset": str(dataset),
        "peak_id": int(measurement.peak_id),
        "reference_peak_id": str(
            getattr(measurement, "reference_peak_id", "")
            or reference_peak_id(getattr(measurement, "peak_id", 0))
        ),
        "assignment": str(getattr(measurement, "assignment", "") or ""),
        "H_ppm": _as_float((measurement.positions or {}).get("1H")),
        "N_ppm": _as_float((measurement.positions or {}).get("15N")),
        "intensity": _as_float(getattr(measurement, "intensity", None)),
        "SNR": _as_float(getattr(measurement, "snr", None)),
        "detected": bool(getattr(measurement, "found", False)),
        "localization_method": str(
            "none" if not getattr(measurement, "found", False)
            else record.get("actual_method") or method
        ),
        "localization_requested": str(
            record.get("requested_method") or requested_method or method
        ),
        "fallback": bool(record.get("fallback", False)),
        "fallback_reason": str(
            record.get("fallback_reason") or fallback_reason or ""
        ),
        "failure_reason": str(record.get("failure_reason") or ""),
        "duplicate_localization": False,
    }
    for axis in ("F1", "F2", "F3"):
        nucleus = str(axis_nuclei.get(axis) or "")
        position = positions.get(axis)
        if position is None and nucleus:
            position = positions.get(nucleus)
        row[f"{axis}_ppm"] = _as_float(position)
        if row[f"{axis}_ppm"] is None:
            row[f"{axis}_ppm"] = float("nan")
        row[f"{axis}_nucleus"] = nucleus
        row[f"FWHM_{axis}"] = _as_float(fwhm_axis.get(axis))
        if row[f"FWHM_{axis}"] is None:
            row[f"FWHM_{axis}"] = float("nan")
    success = record.get("fit_success")
    edge = record.get("boundary_hit")
    row["fit_success"] = None if success is None else bool(success)
    row["FWHM_H"] = fwhm.get("1H")
    row["FWHM_N"] = fwhm.get("15N")
    row["boundary_hit"] = None if edge is None else bool(edge)
    # Per-peak cell and identity QC. The reference measurement writes real values;
    # combination tables pick and localize in one step, so these fields remain NaN.
    if cell_low or cell_high:
        row["cell_low_H"] = cell_low.get("1H")
        row["cell_high_H"] = cell_high.get("1H")
        row["cell_low_N"] = cell_low.get("15N")
        row["cell_high_N"] = cell_high.get("15N")
        edge_value = getattr(measurement, "cell_edge", None)
        row["cell_edge"] = None if edge_value is None else bool(edge_value)
    else:
        for column in _CELL_COLUMNS[:5]:
            row[column] = None
    if getattr(measurement, "reference", None):
        row["intensity_ratio_vs_picked"] = _as_float(
            getattr(measurement, "intensity_ratio", None)
        )
        row["shift_vs_picked_H"] = _as_float((measurement.deltas or {}).get("1H"))
        row["shift_vs_picked_N"] = _as_float((measurement.deltas or {}).get("15N"))
    else:
        for column in _CELL_COLUMNS[5:]:
            row[column] = None
    return row


def peak_table_rows(
    measurements: Sequence[PeakMeasurement],
    *,
    workflow_id: str,
    condition: str = "",
    dataset: str = "",
    method: str = "parabolic",
) -> list[dict[str, Any]]:
    """A batch of measurements -> rows in reference-peak order, undetected peaks included.

    The exit marks ``duplicate_localization`` (P2-5) so that a downstream consumer can
    never count rows sharing one coordinate as two independent observations.
    """
    rows = [
        peak_table_row(
            measurement,
            workflow_id=workflow_id,
            condition=condition,
            dataset=dataset,
            method=method,
        )
        for measurement in measurements
    ]
    mark_duplicate_localization(rows)
    return rows


def peak_table_digest(path: Path | str) -> dict[str, Any]:
    """Peak-table record summary: path, SHA-256, row count, detected count."""
    from core.project.manager import sha256_file

    target = Path(path)
    rows = read_peak_table(target)
    return {
        "path": str(target),
        "sha256": sha256_file(target),
        "n_rows": len(rows),
        "n_detected": sum(1 for row in rows if row.get("detected") is True),
    }


__all__ = [
    "NAN_TEXT",
    "PEAK_TABLE_COLUMNS",
    "REFERENCE_WORKFLOW_ID",
    "format_cell",
    "mark_duplicate_localization",
    "peak_table_digest",
    "peak_table_row",
    "peak_table_rows",
    "read_peak_table",
    "reference_peak_id",
    "write_peak_table",
]
