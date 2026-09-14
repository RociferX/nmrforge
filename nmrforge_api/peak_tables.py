"""统一峰表:两种定位算法同构的 CSV + 稳定峰身份(reference_peak_id)。

规范(2026-09-13 用户 API 规范,见 ``docs/reviews/2026-09-13-api-spec-compliance.md``):

- 每个 workflow 对**同一张谱**分别做 parabolic 与 2D gaussian 定位,输出两张
  **结构完全一致**的峰表;
- 公共列:``workflow_id`` / ``reference_peak_id`` / ``assignment`` / ``H_ppm`` /
  ``N_ppm`` / ``intensity`` / ``SNR`` / ``detected`` / ``localization_method``;
- gaussian 附加列:``fit_success`` / ``FWHM_H`` / ``FWHM_N`` / ``fit_rmse`` /
  ``boundary_hit``;parabolic 不适用的字段写 ``NaN``(不是空串);
- 未检测到的参考峰**保留记录**(``detected=false``),不删除行;
- 拟合失败/回退**不得静默**:``fallback`` / ``fallback_reason`` 直接落在表里。

表里另有 ``condition`` / ``dataset`` 两列(多条件 A/B 时区分同一 workflow 的
两组数据),属于规范「至少包括」之外的溯源列。
"""

from __future__ import annotations

import csv
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from nmrforge_api.peaks import PeakMeasurement

NAN_TEXT = "NaN"

#: 两表共用的列(顺序即表头顺序);parabolic 不适用列写 NaN
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
)

#: Gaussian 专属列(parabolic 表按规范填 NaN,保证两表结构一致)
GAUSSIAN_ONLY_COLUMNS: tuple[str, ...] = (
    "fit_success",
    "FWHM_H",
    "FWHM_N",
    "fit_rmse",
    "boundary_hit",
)

#: 布尔列(写 true/false;读回 bool)
_BOOL_COLUMNS: frozenset[str] = frozenset(
    {"detected", "fallback", "fit_success", "boundary_hit"}
)
#: 数值列(缺失写 NaN;读回 float,NaN 保留)
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
    }
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

#: 参考峰表里的 workflow_id(参考不是参数组合,用固定标记)
REFERENCE_WORKFLOW_ID = "reference"


def reference_peak_id(peak_id: Any) -> str:
    """峰序号 → 稳定身份 ``R0001``(参考峰表建立后不变)。"""
    try:
        number = int(peak_id)
    except (TypeError, ValueError):
        return str(peak_id or "")
    return f"R{number:04d}"


def _as_float(value: Any) -> float | None:
    """值 → float;None/空/非法 → None(写表时落 NaN)。"""
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
    """单元格文本:数值 %.10g,缺失一律 ``NaN``。"""
    number = _as_float(value)
    if number is None or not math.isfinite(number):
        return NAN_TEXT
    return f"{number:.10g}"


def format_cell(column: str, value: Any) -> str:
    """一列取值 → CSV 文本(类型由列名决定;None 按列语义落 NaN/空串)。

    规范 E3:parabolic 不适用的 Gaussian 字段(fit_success / FWHM_* /
    fit_rmse / boundary_hit)写 ``NaN``,不是 false/0——否则两张表虽然列相同,
    语义仍然不一致。
    """
    if value is None:
        return "" if column in _TEXT_COLUMNS else NAN_TEXT
    if column in _BOOL_COLUMNS:
        return "true" if _as_bool(value) else "false"
    if column in _FLOAT_COLUMNS:
        return _fmt(value)
    text = str(value)
    return text if text else ""


def write_peak_table(path: Path | str, rows: Sequence[Mapping[str, Any]]) -> Path:
    """写统一峰表 CSV(表头 = ``PEAK_TABLE_COLUMNS``,缺列按类型落 NaN/空)。"""
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
    """读统一峰表 CSV:数值列 NaN/空 → ``float('nan')``,布尔列 → bool。"""
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


def peak_table_row(
    measurement: PeakMeasurement,
    *,
    workflow_id: str,
    condition: str = "",
    dataset: str = "",
    method: str = "parabolic",
    fallback_reason: str = "",
) -> dict[str, Any]:
    """一条测量 → 一行统一峰表(两种算法同一函数,保证列结构一致)。

    ``method="parabolic"`` 时 Gaussian 专属列留空(None → 写表落 NaN);
    位置按核名取 ``H_ppm``(1H)/``N_ppm``(15N),缺该核则 NaN。
    """
    record = _localization_record(measurement)
    fwhm = _fwhm_by_nucleus(record)
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
    }
    if is_gaussian:
        success = record.get("fit_success")
        row["fit_success"] = bool(
            success
            if success is not None
            else record.get("actual_method") == "gaussian"
        )
        row["FWHM_H"] = fwhm.get("1H")
        row["FWHM_N"] = fwhm.get("15N")
        row["fit_rmse"] = _as_float(record.get("fit_rmse"))
        row["boundary_hit"] = bool(record.get("boundary_hit"))
    else:
        row["fit_success"] = None
        row["FWHM_H"] = None
        row["FWHM_N"] = None
        row["fit_rmse"] = None
        row["boundary_hit"] = None
    return row


def peak_table_rows(
    measurements: Sequence[PeakMeasurement],
    *,
    workflow_id: str,
    condition: str = "",
    dataset: str = "",
    method: str = "parabolic",
) -> list[dict[str, Any]]:
    """一批测量 → 统一峰表行(行序 = 参考峰序,未检测到的峰也保留)。"""
    return [
        peak_table_row(
            measurement,
            workflow_id=workflow_id,
            condition=condition,
            dataset=dataset,
            method=method,
        )
        for measurement in measurements
    ]


def gaussian_fallback_rows(
    measurements: Sequence[PeakMeasurement],
    *,
    workflow_id: str,
    condition: str = "",
    dataset: str = "",
    reason: str,
) -> list[dict[str, Any]]:
    """非 2D(高斯不适用)时的 Gaussian 表:位置沿用抛物线,显式记录回退。

    不静默跳过、不删行:``fit_success=false``、``fallback=true``、
    ``fallback_reason=<reason>``,使两种算法表结构一致。
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
    """峰表落档摘要:路径 + SHA-256 + 行数 + detected 计数。"""
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
    "peak_table_digest",
    "peak_table_row",
    "peak_table_rows",
    "read_peak_table",
    "reference_peak_id",
    "write_peak_table",
]
