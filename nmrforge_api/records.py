"""结果落盘:把一次研究写成可复算、可引用的表与清单。

产物(研究根 ``study/records/``):

- ``manifest.json``:数据来源、参考谱(参数+脚本/谱哈希+版本)、扫描网格哈希、
  组合数、峰表路径——论文/复算只需要这一个文件就能说清「怎么算出来的」;
- ``runs.json``:逐组合的完整记录(参数、脚本/谱哈希、峰位测量、日志尾部);
- ``peak_positions.csv``:长表(run × peak × 核),画「参数 → 峰位」散点直接用;
- ``uncertainty.csv``:逐峰 σ/极差/Δδ 下限;
- ``uncertainty_summary.json``:数据集级下限汇总(中位数/p90/最大)。
"""

from __future__ import annotations

import csv
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from core.version import software_version, tool_versions
from nmrforge_api.peaks import PeakMeasurement
from nmrforge_api.reference import ReferenceSpectrum
from nmrforge_api.session import StudySession, now_iso
from nmrforge_api.sweep import SweepPlan, SweepRun
from nmrforge_api.uncertainty import PeakUncertainty


def _write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path


def _run_measurements(run: SweepRun) -> Sequence[PeakMeasurement]:
    return list(run.measurements or [])


def _fmt(value: Any) -> str:
    if value is None:
        return ""
    try:
        return f"{float(value):.6g}"
    except (TypeError, ValueError):
        return str(value)


WINDOW_POLICY = (
    "峰位搜索窗口半径按物理宽度定义:缺省 = 1.5×该轴核素线宽(Hz)折算 "
    "ppm,运行时按该候选谱的点距换算成点数(core.peaks.axis_units);"
    "零填零 k 倍只改点距,不改变窗口覆盖的 ppm 宽度"
)


def measurement_record(
    reference: ReferenceSpectrum,
    runs: Sequence[SweepRun],
) -> dict[str, Any]:
    """测量口径留档:峰位窗口的逐轴换算(点数/ppm/点距)+ 选峰边距。

    ``window_by_axis`` 给出每个轴最后一次实际用到的口径;``window_points_seen``
    给出各轴在全部组合里出现过的点数集合——同一物理宽度在 1×/2×/4×
    填零下换成不同点数,这里一眼能看出「点数变了但 ppm 没变」。
    """
    by_axis: dict[str, dict[str, Any]] = {}
    seen: dict[str, dict[str, Any]] = {}
    for run in runs:
        for axis, spec in (run.window or {}).items():
            if not isinstance(spec, Mapping):
                continue
            key = str(axis)
            by_axis[key] = dict(spec)
            bucket = seen.setdefault(
                key,
                {
                    "nucleus": str(spec.get("nucleus", "")),
                    "points": [],
                    "ppm": [],
                    "effective_ppm": [],
                    "ppm_per_point": [],
                },
            )
            for field in ("points", "ppm", "effective_ppm", "ppm_per_point"):
                value = spec.get(field)
                if value is not None and value not in bucket[field]:
                    bucket[field].append(value)
    detection = (reference.peak_params or {}).get("detection") or None
    return {
        "peak_position_method": (
            "在参考峰位附近窗口内取 |强度| 极值,再对每个参与轴做 ±1 点 "
            "抛物线亚像素 refine"
        ),
        "window_policy": WINDOW_POLICY,
        "window_by_axis": by_axis,
        "window_points_seen": seen,
        "edge_margin": detection,
    }


def write_records(
    session: StudySession,
    *,
    reference: ReferenceSpectrum,
    plan: SweepPlan,
    runs: Sequence[SweepRun],
    uncertainties: Sequence[PeakUncertainty] | None = None,
    summary: dict[str, Any] | None = None,
    peaks: Sequence[dict[str, Any]] | None = None,
) -> dict[str, str]:
    """写出全部汇总产物,返回 {名称: 路径}。"""
    out_dir = session.records_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}
    measurement = measurement_record(reference, runs)
    manifest = {
        "api_version": "0.1",
        "created": now_iso(),
        "nmrforge_version": software_version(),
        "tool_versions": tool_versions(),
        "research_root": str(session.root),
        "dataset": session.dataset.to_dict() if session.dataset else None,
        "reference": {
            "run_id": reference.run_id,
            "phase_route": reference.phase_route,
            "ndim": reference.ndim,
            "sampling": reference.sampling,
            "script_path": reference.script_path,
            "script_sha256": reference.script_sha256,
            "spectrum_path": reference.frozen_spectrum,
            "spectrum_sha256": reference.spectrum_sha256,
            "params": reference.params,
            "direct_phase": reference.direct_phase,
            "created_at": reference.created_at,
        },
        "sweep": {
            "axes": plan.axes,
            "base_params": plan.base_params,
            "n_combos": plan.n_combos,
            "grid_sha256": plan.grid_sha256,
            "max_runs": plan.max_runs,
            "phase_locked": plan.phase_locked,
            "notes": plan.notes,
        },
        "peaks": {
            "path": reference.peak_table_path,
            "sha256": reference.peak_table_sha256,
            "count": reference.peak_count
            or (len(peaks) if peaks is not None else 0),
            "source": reference.peak_source,
            "params": reference.peak_params,
            "created_at": reference.peak_created_at,
        },
        "runs": {
            "total": len(runs),
            "success": sum(1 for r in runs if r.status == "success"),
            "failed": sum(1 for r in runs if r.status != "success"),
        },
        "measurement": measurement,
    }
    written["manifest"] = str(_write_json(out_dir / "manifest.json", manifest))
    written["sweep_plan"] = str(
        _write_json(out_dir / "sweep_plan.json", plan.to_dict())
    )
    written["runs"] = str(
        _write_json(out_dir / "runs.json", [r.to_dict() for r in runs])
    )
    written["measurement"] = str(
        _write_json(out_dir / "measurement.json", measurement)
    )

    positions_path = out_dir / "peak_positions.csv"
    with positions_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "run_id",
                "combo_index",
                "status",
                "peak_id",
                "assignment",
                "nucleus",
                "ppm",
                "reference_ppm",
                "delta_ppm",
                "intensity",
                "found",
                "window_edge",
                "boundary",
                "out_of_range",
            ]
        )
        for run in runs:
            for measurement in _run_measurements(run):
                nuclei = sorted(
                    set(measurement.positions) | set(measurement.reference)
                )
                for nucleus in nuclei:
                    writer.writerow(
                        [
                            run.run_id,
                            run.index,
                            run.status,
                            measurement.peak_id,
                            measurement.assignment,
                            nucleus,
                            _fmt(measurement.positions.get(nucleus)),
                            _fmt(measurement.reference.get(nucleus)),
                            _fmt(measurement.deltas.get(nucleus)),
                            _fmt(measurement.intensity),
                            int(bool(measurement.found)),
                            int(bool(measurement.window_edge)),
                            int(bool(measurement.boundary)),
                            int(bool(measurement.out_of_range)),
                        ]
                    )
    written["peak_positions"] = str(positions_path)

    if uncertainties is not None:
        nuclei = sorted({n for u in uncertainties for n in u.sigma})
        uncertainty_path = out_dir / "uncertainty.csv"
        with uncertainty_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                ["peak_id", "assignment", "n_runs", "missing_runs"]
                + [f"mean_{n}" for n in nuclei]
                + [f"sigma_{n}" for n in nuclei]
                + [f"range_{n}" for n in nuclei]
                + ["delta_std_ppm", "delta_max_ppm", "worst_run"]
            )
            for item in uncertainties:
                writer.writerow(
                    [item.peak_id, item.assignment, item.n_runs, item.missing_runs]
                    + [_fmt(item.mean.get(n)) for n in nuclei]
                    + [_fmt(item.sigma.get(n)) for n in nuclei]
                    + [_fmt(item.value_range.get(n)) for n in nuclei]
                    + [
                        f"{item.delta_std:.6g}",
                        f"{item.delta_max:.6g}",
                        item.worst_run,
                    ]
                )
        written["uncertainty"] = str(uncertainty_path)
    if summary is not None:
        written["uncertainty_summary"] = str(
            _write_json(out_dir / "uncertainty_summary.json", summary)
        )
    return written


__all__ = ["write_records"]
