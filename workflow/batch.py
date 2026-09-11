"""批量处理引擎:对实验内一组数据按序执行指定步骤(与 GUI 批量组语义对齐)。

入口 ``run_batch(manager, exp_id, targets, steps, backend)``:

- ``targets`` 为 batch_id/组 id(如 "B1")时,先按 project.json 数据组
  (schema 1.4,core/project)解析成员,未命中再按 .pipeline_state.json 的
  batch 键解析(旧批量组标记兼容读取;0.2.164-补1 起 GUI 只写数据组);
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
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from core.project import ProjectManager
from core.project.run_refs import STEP_RUN_REFS

# 支持的批处理步骤(与 gui/pipeline_panel.PIPELINE_STEPS 前五步一致;
# Engine 不 import Qt,批量组语义通过读取状态文件对齐)
BATCH_STEPS = ("import", "fid", "spectrum", "peaks", "analysis")

# 0.2.199-补29hd:批量相邻数据之间冷却秒数——连续 SMILE 背靠背高负载
# 会顶到不稳定主机(电源/散热)断电(problems.md 记录);加间隔让主机冷却。
BATCH_COOLDOWN_SECONDS = 2.0

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
            if not getattr(data, "trashed", False)
            and _batch_id_of(manager, exp_id, data.id) == batch
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

    修24:ref 表与 Pipeline/项目树同源(core.project.run_refs),data_id 与
    workflow_ref 都改**精确匹配**——原来的子串匹配会把名字相近的 ref 误当
    谱图运行,且表里 finalize_nus/generate_spectrum 从来不是登记的 ref。
    """
    if manager.project is None:
        return {}
    spectrum_refs = STEP_RUN_REFS["spectrum"]
    wanted = str(data_id)
    matches = [
        run
        for run in manager.project.workflow_runs
        if run.experiment_id == exp_id
        and str((run.inputs or {}).get("data_id", "")) == wanted
        and run.status == "success"
        and run.workflow_ref in spectrum_refs
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
    progress: Callable[[str], None] | None = None,
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

        return generate_fid(
            manager, exp_id, data_id, backend, progress=progress
        )
    if step == "spectrum":
        from workflow.stepwise import generate_spectrum

        return generate_spectrum(
            manager,
            exp_id,
            data_id,
            backend,
            params=params,
            progress=progress,
        )
    if step == "peaks":
        from workflow.pick_peaks import pick_peaks

        return pick_peaks(manager, exp_id, data_id, backend)
    if step == "analysis":
        from workflow.analyze import analyze

        return analyze(manager, exp_id, data_id)
    raise BatchError(f"不支持的批处理步骤: {step}")




def _step_already_done(manager: object, exp_id: str, data_id: str, step: str) -> bool:
    """该数据指定步骤是否已完成(输出文件存在),用于组批跳过已处理数据。"""
    if step == "import":
        return False  # 幂等确认步骤,不跳过(保持既有返回值语义)
    try:
        data = manager.data(exp_id, data_id)
    except Exception:
        return False
    if step == "fid":
        p = getattr(data, "fid_path", "")
        return bool(p) and Path(p).is_file()
    if step == "spectrum":
        p = getattr(data, "spectrum_path", "")
        return bool(p) and Path(p).is_file()
    if step == "peaks":
        try:
            peaks_dir = manager.data_dir(exp_id, data_id, "peaks")
        except Exception:  # noqa: BLE001 - 目录不可解析时按未完成处理
            return False
        for pat in (f"{exp_id}-{data_id}.list", f"{exp_id}-{data_id}.csv"):
            if (peaks_dir / pat).is_file():
                return True
        return False
    return False


def _data_source_path(manager: object, exp_id: str, data_id: str):
    """取数据原始目录(raw_dir 或 source)。"""
    data = manager.data(exp_id, data_id)
    source = Path(data.raw_dir) if getattr(data, "raw_dir", "") else Path(data.source)
    if not source.is_absolute():
        source = manager.root / source
    return source


def _data_fingerprint(manager: object, exp_id: str, data_id: str):
    """数据谱指纹:维数 + 每维(核,谱宽,载频),用于比较类型/条件。"""
    from core.data.bruker_reader import read_dataset
    exp = read_dataset(_data_source_path(manager, exp_id, data_id))
    dims = tuple(
        (d.nucleus, round(float(d.sw or 0.0), 3), round(float(d.o1 or 0.0), 3))
        for d in exp.dimensions
    )
    return (exp.ndim, dims)


def _fingerprints_match(ref, member):
    """比较两个谱指纹是否一致(类型/条件);返回(是否一致, 原因)。"""
    if ref[0] != member[0]:
        return False, "维数不同"
    if len(ref[1]) != len(member[1]):
        return False, "维度数不同"
    for (rn, rsw, ro1), (mn, msw, mo1) in zip(ref[1], member[1]):
        if rn != mn:
            return False, f"核不同({rn} vs {mn})"
        if abs(rsw - msw) > 1e-3 * max(abs(rsw), abs(msw), 1.0):
            return False, f"谱宽差异大({rsw:.1f} vs {msw:.1f})"
        if abs(ro1 - mo1) > 1e-3 * max(abs(ro1), abs(mo1), 1.0):
            return False, f"载频差异大({ro1:.1f} vs {mo1:.1f})"
    return True, ""


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
    on_data_done: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """按序对组内每个数据执行指定步骤,单数据失败不中断整组。

    steps 中每个可执行步骤(import 幂等确认)都会经底层实现登记 WorkflowRun
    (convert_to_fid / process / reconstruct_nus / pick_peaks);失败数据
    记录 failed_step/error,整组继续。返回逐数据结果与汇总(见模块 docstring)。
    progress:每数据每步骤消息回调(如 "d_001: 开始 spectrum")。
    """
    if manager.project is None:
        raise BatchError("未加载项目,无法批量处理")
    from backend.runtime import cancel_requested

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
    # 0.2.199-补29pq:参考整组处理时,先取参考数据谱指纹用于类型/条件校验
    ref_fp = None
    if reference_data_id and "spectrum" in steps:
        try:
            ref_fp = _data_fingerprint(manager, exp_id, reference_data_id)
        except Exception:  # noqa: BLE001 - 参考数据无法识别则不跳过
            ref_fp = None
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
        # 0.2.199-补29hf:停止按钮已请求取消 → 剩余数据标记已取消并终止整组
        if cancel_requested():
            for _remaining in data_ids[index - 1:]:
                _per = {
                    "data_id": _remaining,
                    "status": "cancelled",
                    "steps": {},
                    "failed_step": "",
                    "error": "用户已停止批量处理",
                    "logs": ["批量已停止,该数据未处理"],
                }
                results[_remaining] = _per
                if on_data_done is not None:
                    on_data_done(_per)
            break
        # 0.2.199-补29gv:开始处理每个数据前输出进度 x/y,便于用户了解进度
        if progress is not None:
            progress(f"[{index}/{total}] 开始处理数据 {data_id}")
        # 参考整组处理:与参考数据(类型/条件)不一致的成员无法套用参考参数,
        # 跳过并告知,不处理。
        if ref_fp is not None and data_id != reference_data_id:
            try:
                member_fp = _data_fingerprint(manager, exp_id, data_id)
            except Exception:  # noqa: BLE001 - 读不到参数视为不可套用
                member_fp = None
            if member_fp is not None:
                ok, reason = _fingerprints_match(ref_fp, member_fp)
                if not ok:
                    per_data["status"] = "skipped"
                    per_data["error"] = (
                        f"与参考数据 {reference_data_id} 不一致"
                        ",无法应用其处理参数,已跳过: " + reason
                    )
                    per_data["logs"].append(per_data["error"])
                    results[data_id] = per_data
                    if on_data_done is not None:
                        on_data_done(per_data)
                    if progress is not None:
                        progress(f"{data_id}: 跳过({reason})")
                    continue
        # 0.2.199-补29hd:批量仅支持 2D 谱——1D/3D 数据(尤其 3D NUS/SMILE 在
        # 不稳定主机易断电)直接跳过,不跑任何步骤。
        try:
            from workflow.stepwise import _read_experiment

            _exp = _read_experiment(manager, exp_id, data_id)
            _ndim = int(getattr(_exp, "ndim", 2) or 2)
        except Exception:  # noqa: BLE001 - 读不到维度当作 2D 放宽(不误拦)
            _ndim = 2
        if _ndim != 2:
            per_data["status"] = "skipped"
            _msg = f"批量暂仅支持 2D 谱,{_ndim}D 数据跳过"
            per_data["error"] = _msg
            per_data["logs"].append(_msg)
            results[data_id] = per_data
            if on_data_done is not None:
                on_data_done(per_data)
            if progress is not None:
                progress(f"{data_id}: {_msg}")
            continue
        for step in steps:
            # 0.2.199-补29gt:组批直接跳过已做过的步骤(避免重跑已处理数据)
            if _step_already_done(manager, exp_id, data_id, step):
                per_data["steps"][step] = "already_done"
                per_data["logs"].append(f"{step} 已完成,跳过")
                if progress is not None:
                    progress(f"{data_id}: {step} 已完成,跳过")
                continue
            if progress is not None:
                progress(f"{data_id}: 开始 {step}")
            step_logs: list[str] = []
            def _collect_log(_msg: str) -> None:
                """收集到本数据日志,并同步转发到组作用域(详细依次输出)。"""
                step_logs.append(_msg)
                if progress is not None:
                    progress(_msg)
            try:
                merged = dict(ref_params) if step == "spectrum" else {}
                merged.update(step_params)
                value = _run_step(
                    manager,
                    exp_id,
                    data_id,
                    step,
                    backend,
                    merged,
                    progress=_collect_log,
                )
            except Exception as exc:  # noqa: BLE001 - 单数据失败不中断整组
                if cancel_requested():
                    per_data["status"] = "cancelled"
                    per_data["error"] = "用户已停止批量处理"
                    per_data["logs"].append(f"{step} 中断(用户停止)")
                else:
                    per_data["status"] = "failed"
                    per_data["failed_step"] = step
                    per_data["error"] = f"{type(exc).__name__}: {exc}"
                    per_data["logs"].append(f"{step} 失败: {exc}")
                per_data["logs"].extend(step_logs)
                break
            per_data["steps"][step] = value
            per_data["logs"].extend(step_logs)
        results[data_id] = per_data
        if on_data_done is not None:
            on_data_done(per_data)
        if progress is not None:
            progress(
                f"{data_id}: "
                + (
                    "成功"
                    if per_data["status"] == "success"
                    else "失败 " + per_data["error"]
                )
            )
        if index < total:
            # 0.2.199-补29hd:相邻数据之间冷却,避免连续 SMILE 背靠背顶到
            # 不稳定主机(电源/散热)断电(problems.md 记录)。
            time.sleep(BATCH_COOLDOWN_SECONDS)
    manager.save()
    failed = [d for d, r in results.items() if r["status"] == "failed"]
    skipped = [d for d, r in results.items() if r["status"] == "skipped"]
    cancelled = [d for d, r in results.items() if r["status"] == "cancelled"]
    return {
        "experiment_id": exp_id,
        "batch_id": batch,
        "data_ids": data_ids,
        "steps": steps,
        "results": results,
        "failed": failed,
        "skipped": skipped,
        "cancelled": cancelled,
        "summary": {
            "total": total,
            "success": total - len(failed) - len(skipped) - len(cancelled),
            "failed": len(failed),
        },
    }


__all__ = ["BATCH_STEPS", "BatchError", "run_batch"]
