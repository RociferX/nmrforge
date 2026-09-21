"""Unified peak table: isomorphic CSV from both localisation methods, with stable identity.

Specification (the user API spec of 2026-09-13); the compliance ledger is
``docs/reviews/2026-09-13-api-spec-compliance.md``:

- each workflow runs parabolic and 2D Gaussian localisation on **the same spectrum** and writes
  two peak tables with **exactly the same structure**;
- shared columns: ``workflow_id`` / ``reference_peak_id`` / ``assignment`` / ``H_ppm`` /
  ``N_ppm`` / ``intensity`` / ``SNR`` / ``detected`` / ``localization_method``;
- localization QC columns: ``fit_success`` / ``FWHM_H`` / ``FWHM_N`` / ``fit_rmse`` /
  ``boundary_hit``; since 2026-09-19 (P3-7) the parabolic table carries real values
  too (the three-point parabola gives an equivalent linewidth and an edge flag), and
  only ``fit_rmse`` stays Gaussian-only (``NaN`` for parabolic);
- ``duplicate_localization`` (2026-09-19, P2-5): true for rows sharing a coordinate;
- a reference peak that was **not** detected keeps its row (``detected=false``);
- a failed fit or fallback is **never silent**: ``fallback`` / ``fallback_reason`` land in the
  table.

Two further columns, ``condition`` / ``dataset``, tell the two datasets of one workflow apart
in a multi-condition study; provenance beyond the specification's "at least" list.
"""

from __future__ import annotations

import csv
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from nmrforge_api.peaks import PeakMeasurement

NAN_TEXT = "NaN"

#: Columns shared by both tables (order = header order); parabolic writes NaN where N/A
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
    "fit_success",
    "FWHM_H",
    "FWHM_N",
    "fit_rmse",
    "boundary_hit",
    "duplicate_localization",
    "cell_low_H",
    "cell_high_H",
    "cell_low_N",
    "cell_high_N",
    "cell_edge",
    "intensity_ratio_vs_picked",
    "shift_vs_picked_H",
    "shift_vs_picked_N",
)

#: The only remaining Gaussian-only column: the parabolic table writes NaN (since
#: 2026-09-19 / P3-7 its fit_success / FWHM_* / boundary_hit carry real values -
#: a three-point parabola gives an equivalent linewidth and an edge flag; only
#: fit_rmse needs a fitting residual, and a three-point parabola is exact)
GAUSSIAN_ONLY_COLUMNS: tuple[str, ...] = ("fit_rmse",)

#: cell/identity QC columns (P1-3): real values in reference tables, NaN in workflow ones
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
        "fit_rmse",
        "intensity_ratio_vs_picked",
        "shift_vs_picked_H",
        "shift_vs_picked_N",
    }
)
#: integer columns (grid-point indices; missing written as NaN, read back as int)
_INT_COLUMNS: frozenset[str] = frozenset(
    {"cell_low_H", "cell_high_H", "cell_low_N", "cell_high_N"}
)
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

    Spec E3: fields a run cannot produce are ``NaN``, not false/0 - otherwise both tables
    would have the same columns but not the same meaning. Since 2026-09-19 (P3-7) only
    ``fit_rmse`` is Gaussian-only for parabolic tables (a three-point parabola is exact
    and therefore residual-free); the parabolic fit_success / FWHM_* / boundary_hit come
    from that parabola.
    """
    if value is None:
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
            writer.writerow(
                [format_cell(column, row.get(column)) for column in PEAK_TABLE_COLUMNS]
            )
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
                row[key] = (
                    float('nan')
                    if token in ("", "nan")
                    else _as_bool(value)
                )
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
                row[key] = "" if value is None else value
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


def _coordinate_key(row: Mapping[str, Any]) -> tuple[int, int] | None:
    """The row coordinate key (ppm rounded to 1e-6); None when H or N is missing."""
    values: list[int] = []
    for column in ("H_ppm", "N_ppm"):
        number = _as_float(row.get(column))
        if number is None or not math.isfinite(number):
            return None
        values.append(int(round(number * 1e6)))
    return (values[0], values[1])


def mark_duplicate_localization(rows: Sequence[dict[str, Any]]) -> int:
    """Flag rows sharing a coordinate (P2-5); returns the number of extra rows.

    Every row of a duplicated group gets ``duplicate_localization=true``: which row is
    "the extra one" depends on the downstream convention (matching / detectability
    counting), so the table does not decide that for the caller. The picker's
    sub-grid refinement can pull two neighbouring detected peaks onto the same grid
    point, and even the reference table's one-cell-per-peak rule shares a cell at the
    resolution limit; such rows are flagged, never dropped.
    """
    buckets: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for row in rows:
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
    fallback_reason: str = "",
) -> dict[str, Any]:
    """One measurement -> one peak-table row (shared by both methods).

    The QC columns are driven by ``measurement.localization``: a Gaussian fit gives
    ``fit_success`` / ``FWHM_*`` / ``fit_rmse`` / ``boundary_hit``, a three-point
    parabola gives ``fit_success`` / ``FWHM_*`` (an equivalent linewidth) /
    ``boundary_hit``. Keys the record does not carry are written as NaN (only the
    Gaussian path has a ``fit_rmse``). Positions come from the nucleus (``H_ppm``
    for 1H, ``N_ppm`` for 15N).
    """
    record = _localization_record(measurement)
    fwhm = _fwhm_by_nucleus(record)
    cell_low = dict(getattr(measurement, "cell_low", None) or {})
    cell_high = dict(getattr(measurement, "cell_high", None) or {})
    requested = str(
        record.get("requested_method")
        or record.get("localization_method")
        or method
    )
    is_gaussian = str(method) == "gaussian"
    row: dict[str, Any] = {
        "workflow_id": str(workflow_id),
        "condition": str(condition),
        "dataset": str(dataset),
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
        "localization_method": str(method),
        "localization_requested": requested,
        "fallback": bool(record.get("fallback") or fallback_reason),
        "fallback_reason": str(
            record.get("fallback_reason") or fallback_reason or ""
        ),
        "duplicate_localization": False,
    }
    # P3-7: the QC columns follow the method this row actually used (Gaussian fit or
    # three-point parabola); keys the record does not carry become None -> NaN.
    success = record.get("fit_success")
    if success is None and is_gaussian:
        success = record.get("actual_method") == "gaussian"
    edge = record.get("boundary_hit")
    row["fit_success"] = None if success is None else bool(success)
    row["FWHM_H"] = fwhm.get("1H")
    row["FWHM_N"] = fwhm.get("15N")
    row["fit_rmse"] = _as_float(record.get("fit_rmse"))
    row["boundary_hit"] = None if edge is None else bool(edge)
    # P1-3 per-peak cell/identity QC. Only the reference measurement (which relocates
    # records from the peak identity table) writes real values; combination (workflow)
    # tables pick and localize in one step, so all eight columns stay NaN there.
    if cell_low or cell_high:
        row["cell_low_H"] = cell_low.get("1H")
        row["cell_high_H"] = cell_high.get("1H")
        row["cell_low_N"] = cell_low.get("15N")
        row["cell_high_N"] = cell_high.get("15N")
        row["cell_edge"] = bool(getattr(measurement, "cell_edge", False))
        row["intensity_ratio_vs_picked"] = _as_float(
            getattr(measurement, "intensity_ratio", None)
        )
        row["shift_vs_picked_H"] = _as_float(
            (measurement.deltas or {}).get("1H")
        )
        row["shift_vs_picked_N"] = _as_float(
            (measurement.deltas or {}).get("15N")
        )
    else:
        for column in _CELL_COLUMNS:
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


def gaussian_fallback_rows(
    measurements: Sequence[PeakMeasurement],
    *,
    workflow_id: str,
    condition: str = "",
    dataset: str = "",
    reason: str,
) -> list[dict[str, Any]]:
    """The Gaussian table for non-2D data: positions follow parabolic, the fallback is recorded.

    Nothing is skipped and no row is dropped: ``fit_success=false``, ``fallback=true``,
    ``fallback_reason=<reason>`` keep the two tables isomorphic.
    """
    return [
        peak_table_row(
            measurement,
            workflow_id=workflow_id,
            condition=condition,
            dataset=dataset,
            method="gaussian",
            fallback_reason=reason,
        )
        for measurement in measurements
    ]


def peak_table_digest(path: Path | str) -> dict[str, Any]:
    """Peak-table record summary: path, SHA-256, row count, detected count."""
    from core.project.manager import sha256_file

    target = Path(path)
    rows = read_peak_table(target)
    return {
        "path": str(target),
        "sha256": sha256_file(target),
        "n_rows": len(rows),
        "n_detected": sum(1 for row in rows if row.get("detected")),
    }


__all__ = [
    "GAUSSIAN_ONLY_COLUMNS",
    "NAN_TEXT",
    "PEAK_TABLE_COLUMNS",
    "REFERENCE_WORKFLOW_ID",
    "format_cell",
    "gaussian_fallback_rows",
    "mark_duplicate_localization",
    "peak_table_digest",
    "peak_table_row",
    "peak_table_rows",
    "read_peak_table",
    "reference_peak_id",
    "write_peak_table",
]
