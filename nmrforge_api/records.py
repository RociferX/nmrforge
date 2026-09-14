"""结果落盘:workflow 记录、统一峰表长表与清单(不含 CSP / 统计推断)。

产物(研究根 ``study/records/``):

- ``manifest.json``:条件数据集、每个条件的参考(脚本/谱/两张峰表哈希)、
  计划与网格哈希、峰身份、软件/工具版本、workflow 状态计数、软件边界声明;
- ``workflows.json``:逐 workflow 的完整记录(``parameters_requested`` /
  ``parameters_used`` / ``parameters_resolved``、状态、警告、两张峰表、
  运行日志、版本);
- ``runs.json``:逐 (workflow, 条件) 的扁平记录;
- ``peak_table_parabolic.csv`` / ``peak_table_gaussian.csv``:全部
  workflow × 条件的**长表**(统一字段),下游独立分析程序直接读这两张表;
- ``measurement.json``:测量口径(窗口物理宽度↔点数换算、定位方法、QC 计数)。

边界(规范 J):这里**只**汇总处理产物与溯源;σ、Δδ 下限、robustness、
显著性判断等一律不在此计算。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from core.version import software_version, tool_versions
from nmrforge_api.peak_tables import read_peak_table, write_peak_table
from nmrforge_api.reference import ReferenceSpectrum
from nmrforge_api.session import StudySession, now_iso
from nmrforge_api.sweep import (
    SweepPlan,
    SweepRun,
    load_workflows,
    workflow_summary,
)

WINDOW_POLICY = (
    "峰位搜索窗口半径按物理宽度定义:缺省 = 1.5×该轴核素线宽(Hz)折算 "
    "ppm,运行时按该候选谱的点距换算成点数(core.peaks.axis_units);"
    "零填零 k 倍只改点距,不改变窗口覆盖的 ppm 宽度"
)

BOUNDARY_STATEMENT = (
    "本软件只执行处理,并输出谱、峰表与处理记录(provenance + QC)。"
    "CSP 计算、robustness 计算、统计分析与显著性判断、科学结论均不在本软件"
    "范围内,由下游独立分析程序基于统一峰表完成。"
)


def _write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path


def _reference_record(
    reference: ReferenceSpectrum, include_peak_tables: bool = True
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "dataset_key": reference.dataset_key,
        "condition": reference.condition,
        "ndim": reference.ndim,
        "sampling": reference.sampling,
        "sampling_schedule": reference.sampling_schedule,
        "sampling_evidence": reference.sampling_evidence,
        "run_id": reference.run_id,
        "phase_route": reference.phase_route,
        "script_path": reference.script_path,
        "script_sha256": reference.script_sha256,
        "spectrum_path": reference.frozen_spectrum,
        "spectrum_sha256": reference.spectrum_sha256,
        "params": reference.params,
        "direct_phase": reference.direct_phase,
        "phase": reference.phase_record(),
        "created_at": reference.created_at,
        "peak_list_path": reference.peak_table_path,
        "peak_list_sha256": reference.peak_table_sha256,
        "peak_count": reference.peak_count,
        "peak_source": reference.peak_source,
        "peak_params": reference.peak_params,
    }
    if include_peak_tables:
        record["peak_tables"] = reference.peak_tables
        record["peak_localization"] = reference.peak_localization
    return record


def measurement_record(
    references: Mapping[str, ReferenceSpectrum] | Sequence[ReferenceSpectrum],
    runs: Sequence[SweepRun],
) -> dict[str, Any]:
    """测量口径留档:定位方法、窗口逐轴换算、逐峰 QC 计数。

    ``window_by_axis`` 给出每个轴最后一次实际用到的口径;``window_points_seen``
    给出各轴在全部组合里出现过的点数集合——同一物理宽度在 1×/2×/4× 填零下
    换成不同点数,这里一眼能看出「点数变了但 ppm 没变」。
    """
    refs = list(references.values()) if isinstance(references, Mapping) else list(
        references
    )
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
    localization: dict[str, Any] = {}
    for method in ("parabolic", "gaussian"):
        totals = {"n_peaks": 0, "n_detected": 0, "n_fallback": 0, "n_boundary_hit": 0}
        reasons: dict[str, int] = {}
        for run in runs:
            summary = (run.peak_localization or {}).get(method) or {}
            for key in totals:
                totals[key] += int(summary.get(key, 0) or 0)
            for reason, count in (summary.get("fallback_reasons") or {}).items():
                reasons[str(reason)] = reasons.get(str(reason), 0) + int(count)
        localization[method] = {**totals, "fallback_reasons": reasons}
    return {
        "peak_position_method": {
            "parabolic": "窗口内 |强度| 极值 + ±1 点三点抛物线亚像素 refine",
            "gaussian": "同一 candidate 上的 2D 高斯最小二乘拟合(仅 2D;失败回退抛物线并记原因)",
        },
        "window_policy": WINDOW_POLICY,
        "window_by_axis": by_axis,
        "window_points_seen": seen,
        "reference": [
            {
                "condition": ref.condition,
                "peak_localization": ref.peak_localization,
                "edge_margin": (ref.peak_params or {}).get("detection"),
            }
            for ref in refs
        ],
        "workflow_localization": localization,
    }


def combined_peak_table(
    runs: Sequence[SweepRun], method: str
) -> list[dict[str, Any]]:
    """把各 workflow/条件的峰表拼成长表(同一套统一字段)。"""
    rows: list[dict[str, Any]] = []
    for run in runs:
        path = run.peak_table_path(method)
        if not path:
            continue
        rows.extend(read_peak_table(path))
    return rows


def write_reference_records(
    session: StudySession,
    references: Mapping[str, ReferenceSpectrum] | Sequence[ReferenceSpectrum],
    *,
    reference_spec: str = "",
) -> dict[str, str]:
    """参考模式产物:``records/reference.json``。

    记录每个条件的参考谱/参考脚本/两张参考峰表(路径 + SHA-256)、有效参数、采样
    口径(含「实际满采样」证据)、选峰阈值与版本表;组合模式只在 manifest 里引用
    这些哈希(参考由外部显式指定)。
    """
    out_dir = session.records_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    refs = (
        list(references.values())
        if isinstance(references, Mapping)
        else list(references)
    )
    payload = {
        "api_version": "0.2",
        "created": now_iso(),
        "nmrforge_version": software_version(),
        "tool_versions": tool_versions(),
        "research_root": str(session.root),
        "mode": "reference",
        "reference_spec": reference_spec,
        "boundary": BOUNDARY_STATEMENT,
        "datasets": [ref.to_dict() for ref in session.datasets],
        "references": [_reference_record(ref) for ref in refs],
    }
    return {"reference": str(_write_json(out_dir / "reference.json", payload))}



def write_records(
    session: StudySession,
    *,
    reference: ReferenceSpectrum | None = None,
    references: Mapping[str, ReferenceSpectrum] | Sequence[ReferenceSpectrum] | None = None,
    plan: SweepPlan,
    runs: Sequence[SweepRun],
    peaks: Sequence[dict[str, Any]] | None = None,
    reference_spec: str = "",
) -> dict[str, str]:
    """写出全部汇总产物,返回 {名称: 路径}。"""
    out_dir = session.records_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}
    if references is None:
        ref_list: list[ReferenceSpectrum] = [reference] if reference else []
    elif isinstance(references, Mapping):
        ref_list = list(references.values())
    else:
        ref_list = list(references)
    workloads = workflow_summary(runs)
    manifest = {
        "api_version": "0.2",
        "created": now_iso(),
        "nmrforge_version": software_version(),
        "tool_versions": tool_versions(),
        "research_root": str(session.root),
        "mode": "combination",
        "reference_spec": str(reference_spec),
        "boundary": BOUNDARY_STATEMENT,
        "datasets": [ref.to_dict() for ref in session.datasets],
        "references": [_reference_record(ref) for ref in ref_list],
        "plan": {
            "axes": plan.axes,
            "base_params": plan.base_params,
            "n_workflows": plan.n_workflows,
            "n_combos": plan.n_combos,
            "workflow_ids": plan.workflow_ids(),
            "grid_sha256": plan.grid_sha256,
            "max_runs": plan.max_runs,
            "design": plan.design,
            "phase_locked": plan.phase_locked,
            "notes": plan.notes,
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
        "peak_identity": {
            "reference_peak_id_scheme": "R0001…(参考峰表行序;所有条件与 "
            "workflow 共享同一身份)",
            "reference": [
                {
                    "condition": ref.condition,
                    "peak_list_path": ref.peak_table_path,
                    "peak_list_sha256": ref.peak_table_sha256,
                    "peak_count": ref.peak_count,
                    "source": ref.peak_source,
                }
                for ref in ref_list
            ],
        },
        "peaks": (
            {
                "path": ref_list[0].peak_table_path if ref_list else "",
                "sha256": ref_list[0].peak_table_sha256 if ref_list else "",
                "count": ref_list[0].peak_count if ref_list else 0,
                "source": ref_list[0].peak_source if ref_list else "",
                "params": ref_list[0].peak_params if ref_list else {},
                "created_at": ref_list[0].peak_created_at if ref_list else "",
            }
        ),
        "workflows": workloads,
        "runs": workloads,
        "measurement": measurement_record(ref_list, runs),
    }
    written["manifest"] = str(_write_json(out_dir / "manifest.json", manifest))
    written["sweep_plan"] = str(
        _write_json(out_dir / "sweep_plan.json", plan.to_dict())
    )
    written["runs"] = str(
        _write_json(out_dir / "runs.json", [run.to_dict() for run in runs])
    )
    written["measurement"] = str(
        _write_json(out_dir / "measurement.json", manifest["measurement"])
    )
    stored = load_workflows(session)
    written["workflows"] = str(
        _write_json(
            out_dir / "workflows.json",
            stored
            or [
                _workflow_record_from_runs(runs, workflow_id)
                for workflow_id in _workflow_ids(runs)
            ],
        )
    )
    for method in ("parabolic", "gaussian"):
        path = write_peak_table(
            out_dir / f"peak_table_{method}.csv",
            combined_peak_table(runs, method),
        )
        written[f"peak_table_{method}"] = str(path)
    # 兼容旧名:峰位长表(列 = 统一字段)
    positions_path = out_dir / "peak_positions.csv"
    positions_path.write_text(
        Path(written["peak_table_parabolic"]).read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    written["peak_positions"] = str(positions_path)
    return written


def _workflow_ids(runs: Sequence[SweepRun]) -> list[str]:
    seen: list[str] = []
    for run in runs:
        if run.workflow_id not in seen:
            seen.append(run.workflow_id)
    return seen


def _workflow_record_from_runs(
    runs: Sequence[SweepRun], workflow_id: str
) -> dict[str, Any]:
    """按 workflow 分组(盘上的 workflow.json 是权威;这里给出汇总副本)。"""
    from nmrforge_api.sweep import _workflow_record

    selected = [run for run in runs if run.workflow_id == workflow_id]
    plan = SweepPlan(grid_sha256="")
    return _workflow_record(selected, plan)


__all__ = [
    "BOUNDARY_STATEMENT",
    "WINDOW_POLICY",
    "write_reference_records",
    "combined_peak_table",
    "measurement_record",
    "write_records",
]
