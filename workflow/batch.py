"""批量处理引擎:对实验内一组数据按序执行指定步骤(与 GUI 批量组语义对齐)。

入口 ``run_batch(manager, exp_id, targets, steps, backend)``:

- ``targets`` 为 batch_id/组 id(如 "B1")时,先按 project.json 数据组
  (schema 1.4,core/project)解析成员,未命中再按 .pipeline_state.json 的
  batch 键解析(GUI 旧批量组语义兼容;Engine 不依赖 Qt);
- ``reference_data_id`` 非空时,取其最近一次成功谱图运行的有效参数
  (WorkflowRun.params)作为 spectrum 步骤参数基底,实现「按参考数据
  的处理脚本处理整组」;显式 params 覆盖参考参数;
- ``targets`` 为 data_id 列表时按显式列表执行;
- ``steps`` 按序执行 import(幂等确认)→ fid → spectrum → peaks → analysis;
  fid/spectrum 复用 workflow.stepwise.generate_fid/generate_spectrum,peaks
  复用 workflow.pick_peaks.pick_peaks,analysis 复用 workflow.analyze.analyze;
- 单数据失败不中断整组:失败数据记录 failed_step/error 后继续下一数据,
  汇总含 failed 列表与 summary。

返回 dict:
{
  "experiment_id": str,
  "batch_id": str,                 # targets 为 batch_id 时的组号,否则 ""
  "data_ids": list[str],
  "steps": list[str],
  "results": {data_id: {"data_id", "status", "steps", "failed_step", "error",
                        "logs"}},
  "failed": list[str],
  "summary": {"total", "success", "failed"},
}
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from core.project import ProjectManager

# 支持的批处理步骤(与 gui/pipeline_panel.PIPELINE_STEPS 前五步一致;
# Engine 不 import Qt,批量组语义通过读取状态文件对齐)
BATCH_STEPS = ("import", "fid", "spectrum", "peaks", "analysis")

STATE_FILENAME = ".pipeline_state.json"


class BatchError(Exception):
    """批处理引擎错误(参数校验/目标解析/步骤不支持)。"""


def _data_exists(manager: ProjectManager, exp_id: str, data_id: str) -> bool:
    """数据条目是否存在(组内成员可能已删除)。"""
    try:
        manager.data(exp_id, data_id)
        return True
    except Exception:  # noqa: BLE001 - ProjectError 统一视为不存在
        return False


def _batch_id_of(manager: ProjectManager, exp_id: str, data_id: str) -> str:
    """读 GUI 侧 .pipeline_state.json 的 batch 键(与 gui.pipeline_state.batch_id 同语义)。"""
    path = manager.data_base(exp_id, data_id) / STATE_FILENAME
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        value = raw.get("batch", "")
    except (OSError, json.JSONDecodeError):
        value = ""
    return str(value) if value else ""


def _resolve_data_ids(
    manager: ProjectManager, exp_id: str, targets: str | Iterable[str]
) -> tuple[list[str], str]:
    """把 batch_id(str) 或 data_ids(可迭代)解析为实验内数据 id 列表。

    返回 (data_ids, batch_id);显式 data_ids 时 batch_id 为空串。
    """
    entry = manager.project.experiment(exp_id) if manager.project is not None else None
    if entry is None:
        raise BatchError(f"实验不存在: {exp_id}")
    if isinstance(targets, str):
        batch = targets
        # schema 1.4 数据组优先(project.json);旧批量组标记(pipeline_state)兼容
        group = manager.group(exp_id, batch)
        if group is not None:
            data_ids = [
                d
                for d in group.data_ids
                if _data_exists(manager, exp_id, d)
            ]
            if not data_ids:
                raise BatchError(f"数据组 {batch} 在实验 {exp_id} 中没有数据")
            return data_ids, batch
        data_ids = [
            data.id
            for data in entry.data
            if _batch_id_of(manager, exp_id, data.id) == batch
        ]
        if not data_ids:
            raise BatchError(f"批量组 {batch} 在实验 {exp_id} 中没有数据")
        return data_ids, batch
    data_ids = [str(d) for d in targets]
    if not data_ids:
        raise BatchError("未指定数据(data_ids 为空)")
    for data_id in data_ids:
        try:
            manager.data(exp_id, data_id)
        except Exception as exc:  # noqa: BLE001 - ProjectError 统一转 BatchError
            raise BatchError(f"数据不存在: {exp_id}/{data_id}") from exc
    return data_ids, ""


def _reference_spectrum_params(
    manager: ProjectManager,
    exp_id: str,
    data_id: str,
) -> dict[str, Any]:
    """取参考数据最近一次成功谱图运行的有效参数(处理脚本/参数复用)。

    按 WorkflowRun 追加序取最后一个成功且 workflow_ref 属于谱图链的 run;
    无可用 run 返回空 dict(调用方回退默认统一自动处理)。
    """
    if manager.project is None:
        return {}
    spectrum_refs = (
        "process",
        "reconstruct_nus",
        "phase_optimize_unified",
        "finalize_nus",
        "generate_spectrum",
    )
    matches = [
        run
        for run in manager.project.workflow_runs
        if run.experiment_id == exp_id
        and (run.inputs or {}).get("data_id") == data_id
        and run.status == "success"
        and any(ref in str(run.workflow_ref or "") for ref in spectrum_refs)
    ]
    if not matches:
        return {}
    return dict(matches[-1].params or {})


def _run_step(
    manager: ProjectManager,
    exp_id: str,
    data_id: str,
    step: str,
    backend: Any,
    params: dict[str, Any],
) -> Any:
    """执行单个步骤,返回步骤产物(fid/spectrum 路径、peaks/analysis dict 等)。"""
    if step == "import":
        # 导入是批处理的输入:数据条目已存在即视为已导入(不重复建条目)
        data = manager.data(exp_id, data_id)
        raw = Path(data.raw_dir) if getattr(data, "raw_dir", "") else Path(data.source)
        if not raw.is_absolute():
            raw = manager.root / raw
        return {
            "status": "already_imported",
            "data_id": data_id,
            "source": str(raw),
        }
    if step == "fid":
        from workflow.stepwise import generate_fid

        return generate_fid(manager, exp_id, data_id, backend)
    if step == "spectrum":
        from workflow.stepwise import generate_spectrum

        return generate_spectrum(manager, exp_id, data_id, backend, params=params)
    if step == "peaks":
        from workflow.pick_peaks import pick_peaks

        return pick_peaks(manager, exp_id, data_id, backend)
    if step == "analysis":
        from workflow.analyze import analyze

        return analyze(manager, exp_id, data_id)
    raise BatchError(f"不支持的批处理步骤: {step}")


def run_batch(
    manager: ProjectManager,
    exp_id: str,
    targets: str | Iterable[str],
    steps: Iterable[str],
    backend: Any,
    *,
    params: dict[str, Any] | None = None,
    progress: Callable[[str], None] | None = None,
    reference_data_id: str | None = None,
) -> dict[str, Any]:
    """按序对组内每个数据执行指定步骤,单数据失败不中断整组。

    steps 中每个可执行步骤(import 幂等确认)都会经底层实现登记 WorkflowRun
    (convert_to_fid / process / reconstruct_nus / pick_peaks);失败数据
    记录 failed_step/error,整组继续。返回逐数据结果与汇总(见模块 docstring)。
    progress:每数据每步骤消息回调(如 "d_001: 开始 spectrum")。
    """
    if manager.project is None:
        raise BatchError("未加载项目,无法批量处理")
    steps = list(steps)
    unknown = [s for s in steps if s not in BATCH_STEPS]
    if unknown:
        raise BatchError(f"不支持的批处理步骤: {unknown}")
    data_ids, batch = _resolve_data_ids(manager, exp_id, targets)
    step_params = dict(params or {})
    ref_params = (
        _reference_spectrum_params(manager, exp_id, reference_data_id)
        if reference_data_id
        else {}
    )
    total = len(data_ids)
    results: dict[str, dict[str, Any]] = {}
    for index, data_id in enumerate(data_ids, start=1):
        per_data: dict[str, Any] = {
            "data_id": data_id,
            "status": "success",
            "steps": {},
            "failed_step": "",
            "error": "",
            "logs": [],
        }
        for step in steps:
            if progress is not None:
                progress(f"{data_id}: 开始 {step}")
            try:
                merged = dict(ref_params) if step == "spectrum" else {}
                merged.update(step_params)
                value = _run_step(manager, exp_id, data_id, step, backend, merged)
            except Exception as exc:  # noqa: BLE001 - 单数据失败不中断整组
                per_data["status"] = "failed"
                per_data["failed_step"] = step
                per_data["error"] = f"{type(exc).__name__}: {exc}"
                per_data["logs"].append(f"{step} 失败: {exc}")
                break
            per_data["steps"][step] = value
        results[data_id] = per_data
        if progress is not None:
            progress(
                f"{data_id}: "
                + (
                    "成功"
                    if per_data["status"] == "success"
                    else "失败 " + per_data["error"]
                )
            )
    manager.save()
    failed = [d for d, r in results.items() if r["status"] == "failed"]
    return {
        "experiment_id": exp_id,
        "batch_id": batch,
        "data_ids": data_ids,
        "steps": steps,
        "results": results,
        "failed": failed,
        "summary": {
            "total": total,
            "success": total - len(failed),
            "failed": len(failed),
        },
    }


__all__ = ["BATCH_STEPS", "BatchError", "run_batch"]
