"""步骤化处理编排(API_CONTRACT §8.3 / G2B-002)。

三步:
1. import_data —— workflow.import_workflow.import_data(只读参数 + 链接(G2B-009));
2. generate_fid —— backend.convert_to_fid(生成 NMRPipe fid);
3. generate_spectrum —— backend.process / reconstruct_nus(含 NUS SMILE 重构)。

相位优化:先用 SMILE 重构生成谱,再逐候选反复跑后端(暴力)优化,
最终谱由真实管线产出(workflow.phase_optimize 的暴力搜索 + 相位写回)。
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from core.data.bruker_reader import read_dataset
from core.data.internal_data_model import Experiment, SamplingMode
from core.planning.method_selector import select_method
from core.project import ProjectManager
from workflow.import_workflow import ImportResult, import_data


class StepwiseError(Exception):
    """步骤化处理错误。"""


def _require_data(manager: ProjectManager, exp_id: str, data_id: str) -> Any:
    return manager.data(exp_id, data_id)


def _read_experiment(manager: ProjectManager, exp_id: str, data_id: str) -> Experiment:
    """从数据条目读取 Experiment(优先项目内 raw 副本;单数据分段采集用各段目录)。"""
    data_entry = _require_data(manager, exp_id, data_id)
    if data_entry.segments:
        # 分段采集:source 是容器目录(无 acqus),各段在 data_entry.segments
        from core.data.bruker_reader import read_segments

        seg_paths: list[Path] = []
        for seg in data_entry.segments:
            seg_path = Path(seg)
            if not seg_path.is_absolute():
                seg_path = manager.root / seg_path
            seg_paths.append(seg_path)
        experiment = read_segments(seg_paths)
    else:
        source = Path(data_entry.raw_dir) if data_entry.raw_dir else Path(data_entry.source)
        if not source.is_absolute():
            source = manager.root / source
        experiment = read_dataset(source)
    # 2026-08-19:中间产物/终谱前缀统一用数据 id(d_001),不随重命名变化;
    # read_dataset 的 dataset_id 取自 raw 目录名(常为 raw),必须覆盖
    experiment.dataset_id = data_id
    return experiment


def _work_dir(manager: ProjectManager, exp_id: str, data_id: str) -> Path:
    """每数据独立工作目录:<exp_id>/<data_id>/process/(契约 §9.2)。"""
    return manager.data_dir(exp_id, data_id, "process")


def _register_spectrum(
    manager: ProjectManager,
    exp_id: str,
    data_id: str,
    spectrum_path: str,
) -> str:
    """把后端产出的终谱归位到 <exp_id>/<data_id>/spectra/(move,process 不留副本)并登记。"""
    source = Path(spectrum_path)
    spectra_dir = manager.data_dir(exp_id, data_id, "spectra")
    spectra_dir.mkdir(parents=True, exist_ok=True)
    # 2026-08-19:终谱命名前缀用数据 id(d_001),重命名不影响
    target = spectra_dir / f"{data_id}{source.suffix}"
    if source.is_file() and source.resolve() != target.resolve():
        shutil.move(str(source), str(target))
    manager.set_data_spectrum(exp_id, data_id, target)
    return str(target)


def _ensure_work_dir(backend: Any, work: Path) -> None:
    """把后端工作目录固定到数据级目录(backend.work_dir 可写时)。"""
    if hasattr(backend, "work_dir"):
        backend.work_dir = str(work)


def _finish_step(
    manager: ProjectManager,
    exp_id: str,
    data_id: str,
    workflow_ref: str,
    outputs: dict[str, str],
    message: str,
    params: dict[str, Any] | None = None,
) -> str:
    """登记一次步骤 WorkflowRun(只追加,审计)。"""
    run = manager.start_run(
        exp_id,
        workflow_ref=workflow_ref,
        inputs={"data_id": data_id},
        params=dict(params or {}),
    )
    manager.finish_run(run.run_id, "success", outputs=outputs, message=message)
    return run.run_id


def generate_fid(
    manager: ProjectManager,
    exp_id: str,
    data_id: str,
    backend: Any,
    *,
    work_dir: Path | str | None = None,
    progress: Callable[[str], None] | None = None,
) -> str:
    """第 2 步:转换 Bruker 原始数据为 NMRPipe fid(独立阶段)。"""
    experiment = _read_experiment(manager, exp_id, data_id)
    data_entry = _require_data(manager, exp_id, data_id)
    data_dir = Path(data_entry.raw_dir) if data_entry.raw_dir else Path(data_entry.source)
    if not data_dir.is_absolute():
        data_dir = manager.root / data_dir
    work = Path(work_dir) if work_dir else _work_dir(manager, exp_id, data_id)
    _ensure_work_dir(backend, work)
    resp = backend.convert_to_fid(experiment, data_dir, progress=progress)
    logs = list(resp.get("logs", []))
    if not resp.get("success"):
        raise StepwiseError(
            str(resp.get("message", "转换失败")) + " | " + " | ".join(logs)
        )
    fid_path = str(resp.get("fid_path", ""))
    manager.set_data_fid(exp_id, data_id, fid_path)
    merged_params = dict(resp.get("effective_params") or {})
    _finish_step(
        manager,
        exp_id,
        data_id,
        "convert_to_fid",
        outputs={"fid_path": fid_path},
        message="生成 FID",
        params=merged_params,
    )
    return fid_path


def generate_spectrum(
    manager: ProjectManager,
    exp_id: str,
    data_id: str,
    backend: Any,
    *,
    params: dict[str, Any] | None = None,
    work_dir: Path | str | None = None,
    progress: Callable[[str], None] | None = None,
) -> str:
    """第 3 步:生成谱图(NUS 自动走 SMILE 重构;复用已转换 fid)。

    params["phase_route"] 选择处理途径:
    - "unified"(默认):统一方案——第一遍逐维复型预览(仅搜索轴
      不加 -di),内存调相(旧算法判断标准,零额外后端),完整终跑;
    - "none":保持旧路径,直接 process/reconstruct_nus,不额外优化
      (逃生口)。
    """
    experiment = _read_experiment(manager, exp_id, data_id)
    work = Path(work_dir) if work_dir else _work_dir(manager, exp_id, data_id)
    _ensure_work_dir(backend, work)
    params = dict(params or {})
    route = str(params.pop("phase_route", "unified"))
    plan = select_method(experiment)
    if route == "none":
        if experiment.sampling.mode is SamplingMode.NUS:
            workflow_ref = "reconstruct_nus"
            resp = backend.reconstruct_nus(experiment, params, progress=progress)
        else:
            workflow_ref = "process"
            resp = backend.process(
                experiment, plan, params=params, progress=progress
            )
        logs = list(resp.get("logs", []))
        if not resp.get("success"):
            raise StepwiseError(
                str(resp.get("message", "谱图生成失败")) + " | " + " | ".join(logs)
            )
        spectrum_path = _register_spectrum(
            manager, exp_id, data_id, str(resp.get("spectrum_path", ""))
        )
        merged_params = dict(resp.get("effective_params") or {})
        merged_params.update(params)
        _finish_step(
            manager,
            exp_id,
            data_id,
            workflow_ref,
            outputs={"spectrum_path": spectrum_path},
            message="生成谱图",
            params=merged_params,
        )
        return spectrum_path

    if route != "unified":
        raise StepwiseError(f"未知 phase_route: {route}")

    from workflow.phase_routes import unified_route

    result = unified_route(
        experiment,
        backend,
        plan=plan,
        work_dir=work,
        base_params=params,
        progress=progress,
    )
    workflow_ref = "phase_optimize_unified"

    if not result.get("spectrum_path"):
        raise StepwiseError("相位优化未产出谱图")
    spectrum_path = _register_spectrum(
        manager, exp_id, data_id, str(result.get("spectrum_path"))
    )
    merged_params = dict(params)
    merged_params["phase_route"] = route
    for key in (
        "phases",
        "baseline",
        "zero_fill",
        "window",
        "fill",
        "backend_runs",
        "direct_phase",
    ):
        if key in result:
            merged_params[key] = result[key]
    run_id = _finish_step(
        manager,
        exp_id,
        data_id,
        workflow_ref,
        outputs={"spectrum_path": spectrum_path},
        message="生成谱图(相位优化)",
        params=merged_params,
    )
    # Task E(0.2.133):3D 终谱用 NMRPipe proj3D.tcl 生成三个投影,落
    # spectra/<id>_<核A>-<核B>.ft2(文件名含平面实际两核,GUI 以
    # <data_id>_*.ft2 通配扫描,旧 *_proj_*.ft2 亦兼容)。
    if experiment.ndim >= 3 and getattr(backend, "project_3d", None):
        try:
            spectra_dir = manager.data_dir(exp_id, data_id, "spectra")
            proj = backend.project_3d(
                spectrum_path,
                spectra_dir,
                prefix=f"{data_id}_proj",
            )
            labels = proj.get("labels", {})
            nuclei = proj.get("nuclei", {})
            for tag, path in proj.get("paths", {}).items():
                fixed_nucleus = str(labels.get(tag, "") or "")
                logical = next(
                    (
                        dim.logical_axis
                        for dim in experiment.dimensions
                        if dim.nucleus == fixed_nucleus
                    ),
                    "",
                )
                target = spectra_dir / projection_filename(
                    data_id, nuclei.get(tag), logical, tag
                )
                if Path(path) != target:
                    if target.exists():
                        target.unlink()
                    Path(path).replace(str(target))
                merged_params.setdefault("projections", {})[logical or tag] = str(
                    target
                )
        except Exception as exc:  # noqa: BLE001 - 投影失败不阻断谱图
            merged_params.setdefault("projections", {})["error"] = str(exc)
        _run = manager.project.run(run_id)
        if _run is not None and "projections" in merged_params:
            # 0.2.133:投影注册写回运行参数(供 GUI/汇报读取)
            _run.params["projections"] = merged_params["projections"]
    return spectrum_path


def projection_filename(
    data_id: str,
    nuclei: list[str] | None,
    logical: str,
    tag: str,
) -> str:
    """投影文件名(0.2.133):d_001_15N-1H.ft2 —— 含平面实际两核。

    核缺失/不可用时回退旧名 d_001_proj_<logical|tag>.ft2(仍被 GUI
    <data_id>_*.ft2 与 *_proj_*.ft2 通配扫描命中,兼容历史文件)。
    """
    if nuclei and len(nuclei) >= 2:
        safe = [
            re.sub(r"[^A-Za-z0-9]", "", str(nuc or ""))
            for nuc in nuclei[:2]
        ]
        if all(safe):
            return f"{data_id}_{safe[0]}-{safe[1]}.ft2"
    return f"{data_id}_proj_{logical or tag}.ft2"




def optimize_phase_brute_force(
    manager: ProjectManager,
    exp_id: str,
    data_id: str,
    backend: Any,
    *,
    candidates: list[dict[str, float]] | None = None,
    work_dir: Path | str | None = None,
    score_fn: Any | None = None,
    embed_baseline: bool = True,
    embed_processing: bool = True,
) -> dict[str, Any]:
    """相位优化:逐维暴力/多尺度(直接维→间接维依次固定;NUS 先 SMILE 一次)。

    嵌入其它优化(除 SMILE):相位搜索结束后对最优谱内存内跑基线优化
    (optimize_baseline,0 次后端运行),配置变化时以「最优相位+最优基线」
    重渲终谱 1 次(uniform 全轴;NUS 2D 仅间接维 F1——直接维 F2 基线在
    SMILE 重构时固化,调整需重跑 SMILE,仅报告)。
    嵌入窗函数/填零(uniform;NUS 窗函数在 SMILE 内仅报告):小网格
    (sine_bell/sine_bell²/gaussian × 填零 auto/none)重渲评分,填零受
    文件大小上限约束(默认 256MB,同分选最小文件),取最优重渲终谱。

    返回 {"phase", "spectrum_path", "method", "backend_runs",
    "logs", "optimized", "skipped", "baseline", "processing"}。

    0.2.87:相位候选谱零填零(数据最小化),搜索结束后立即以最终相位+完整
    填零计划(auto)重渲生产终谱并归位——填零只出现在优化最后,不在候选
    阶段/SMILE 前。
    """
    from workflow.phase_optimize import optimize_phase_sequential

    experiment = _read_experiment(manager, exp_id, data_id)
    data_entry = _require_data(manager, exp_id, data_id)
    work = Path(work_dir) if work_dir else _work_dir(manager, exp_id, data_id)
    _ensure_work_dir(backend, work)
    if not data_entry.spectrum_path or not Path(data_entry.spectrum_path).is_file():
        generate_spectrum(
            manager, exp_id, data_id, backend, work_dir=work, params={"phase_route": "none"}
        )

    p1_values: tuple[float, ...] | None = None
    if candidates is not None:
        p1_values = tuple(
            float(c["p1"])
            for c in candidates
            if c.get("p1") is not None
            and isinstance(c.get("p1"), (int, float))
        ) or None

    result = optimize_phase_sequential(
        experiment,
        backend,
        p1_values=p1_values,
        score_fn=score_fn,
        work_dir=work,
    )
    logs = list(result.logs)
    # 0.2.87:候选谱零填零(数据最小化);搜索结束后立即以最终相位+完整填零
    # 计划渲染生产终谱(填零只在优化最后执行,不在候选阶段/SMILE 前)。
    axes = [dim.logical_axis for dim in experiment.dimensions]
    is_nus = experiment.sampling.mode is SamplingMode.NUS
    full_zf_params = {
        "zero_fill": {a: {"mode": "auto"} for a in axes},
    }
    if is_nus:
        resp = backend.finalize_nus(
            experiment,
            phases=result.phases,
            work_dir=work,
            params=full_zf_params,
        )
    else:
        plan = select_method(experiment)
        resp = backend.process(
            experiment,
            plan,
            direct_phase_override=result.phases,
            params=full_zf_params,
        )
    if resp.get("success"):
        spectrum_path = _register_spectrum(
            manager,
            exp_id,
            data_id,
            str(resp.get("spectrum_path", result.spectrum_path)),
        )
        logs.append(
            "终谱: 候选谱零填零最小化,搜索结束以完整填零计划重渲"
        )
    else:
        logs.append(
            "终谱完整填零重渲失败: "
            + str(resp.get("message"))
            + "; 暂用零填零候选谱"
        )
        spectrum_path = _register_spectrum(
            manager, exp_id, data_id, result.spectrum_path
        )

    baseline_result: dict[str, Any] | None = None
    applied_baseline: dict[str, Any] | None = None
    if embed_baseline:
        from workflow.baseline_optimize import optimize_baseline

        try:
            baseline_opt = optimize_baseline(experiment, spectrum_path)
        except Exception as exc:  # noqa: BLE001 - 基线评估失败不影响相位结果
            logs.append(f"基线优化(嵌入)失败: {exc}")
            baseline_opt = None
        if baseline_opt is not None:
            baseline_result = {
                "config": baseline_opt.baseline,
                "scores": baseline_opt.scores,
                "optimized": baseline_opt.optimized,
                "skipped": baseline_opt.skipped,
                "logs": baseline_opt.logs,
            }
            default_cfg: dict[str, Any] = {
                "enabled": True, "mode": "auto", "order": 0
            }
            changed = [
                axis
                for axis, cfg in baseline_opt.baseline.items()
                if cfg != default_cfg
            ]
            is_nus = experiment.sampling.mode is SamplingMode.NUS
            re_render = False
            apply_cfg: dict[str, Any] = {}
            if not is_nus and changed:
                re_render = True
                apply_cfg = dict(baseline_opt.baseline)
            elif is_nus and experiment.ndim == 2 and changed:
                f1_changed = [a for a in changed if a == "F1"]
                skipped_axes = [a for a in changed if a != "F1"]
                if f1_changed:
                    re_render = True
                    apply_cfg = {"F1": dict(baseline_opt.baseline["F1"])}
                if skipped_axes:
                    logs.append(
                        "基线(嵌入): 轴 " + ",".join(skipped_axes)
                        + " 基线调整需重跑 SMILE 重构,已跳过(仅报告)"
                    )
            elif is_nus and changed:
                logs.append(
                    "基线(嵌入): 3D NUS 基线调整需重跑 SMILE 重构,"
                    "已跳过(仅报告)"
                )
            if re_render:
                if is_nus:
                    resp = backend.finalize_nus(
                        experiment,
                        phases=result.phases,
                        work_dir=work,
                        baseline=apply_cfg,
                    )
                else:
                    plan = select_method(experiment)
                    resp = backend.process(
                        experiment,
                        plan,
                        direct_phase_override=result.phases,
                        params={"baseline": apply_cfg},
                    )
                if resp.get("success"):
                    spectrum_path = _register_spectrum(
                        manager,
                        exp_id,
                        data_id,
                        str(resp.get("spectrum_path", spectrum_path)),
                    )
                    applied_baseline = dict(apply_cfg)
                    logs.append(
                        "基线(嵌入): 终谱已用「最优相位+最优基线」重渲"
                    )
                else:
                    logs.append(f"基线(嵌入)重渲失败: {resp.get('message')}")
            logs.extend(baseline_opt.logs)

    processing_result: dict[str, Any] | None = None
    if embed_processing:
        is_nus = experiment.sampling.mode is SamplingMode.NUS
        if is_nus:
            logs.append(
                "窗函数/填零(嵌入): NUS 窗函数在 SMILE 重构内,调整需"
                "重跑 SMILE,已跳过(仅报告)"
            )
        else:
            import numpy as np

            from core.qc import spectrum_quality

            windows = [
                {"type": "sine_bell"},
                {"type": "sine_bell_squared"},
                {"type": "gaussian", "lb": 5.0, "gb": 0.1},
            ]
            zf_modes = ["auto"]  # 0.2.47:移除 none——填零关闭会减半数字
            # 分辨率且综合 QC 此前不惩罚(审查结论);候选另经 min_shape
            # 分辨率下限过滤,防未来回归。
            max_bytes = 256 * 1024 * 1024
            axes = [dim.logical_axis for dim in experiment.dimensions]

            def _est_bytes(zf_mode: str) -> int:
                from backend.script_generator import effective_td, zero_fill_plan

                zf_param = (
                    {a: {"mode": "none"} for a in axes}
                    if zf_mode == "none"
                    else {a: {"mode": "auto"} for a in axes}
                )
                plan = zero_fill_plan(experiment, zf_param)
                td = effective_td(experiment)
                total = 1
                for index, axis in enumerate(axes):
                    cfg = plan.get(axis, {})
                    size = int(cfg.get("size") or 0)
                    if size <= 0:
                        # mode=none:按有效 TD 估算(消除恒 4 字节退化)
                        size = int(td[index]) if index < len(td) else 1
                    total *= max(size, 1)
                return total * 4

            # 分辨率下限 = auto 填零计划的目标 SI(直接维 2×TD、间接维动态)
            from backend.script_generator import zero_fill_plan

            auto_plan = zero_fill_plan(
                experiment, {a: {"mode": "auto"} for a in axes}
            )
            min_shape = tuple(
                int(auto_plan.get(a, {}).get("size") or 1) for a in axes
            )

            def _score_path(path: str) -> float:
                import nmrglue as ng

                _dic, data = ng.pipe.read(str(path))
                q = spectrum_quality.evaluate(
                    np.asarray(data), min_shape=min_shape
                )
                return float(q.score.overall)

            try:
                base_score = _score_path(spectrum_path)
            except Exception as exc:  # noqa: BLE001
                logs.append(f"窗函数/填零(嵌入)基准评分失败: {exc}")
                base_score = -1.0
            candidates: list[tuple[dict[str, Any], str, float, int]] = []
            for w in windows:
                for zf_mode in zf_modes:
                    est = _est_bytes(zf_mode)
                    if zf_mode == "auto" and est > max_bytes:
                        logs.append(
                            f"窗函数/填零(嵌入): auto 填零估计"
                            f" {est // 1048576}MB > 上限,跳过"
                        )
                        continue
                    wname = str(w.get("type", "sine_bell"))
                    plan = select_method(experiment)
                    resp = backend.process(
                        experiment,
                        plan,
                        direct_phase_override=result.phases,
                        params={
                            "window": {a: dict(w) for a in axes},
                            "zero_fill": {a: {"mode": zf_mode} for a in axes},
                            "baseline": applied_baseline,
                        },
                    )
                    if not resp.get("success"):
                        logs.append(
                            f"窗函数/填零(嵌入): {wname}+{zf_mode} "
                            f"运行失败 {resp.get('message')}"
                        )
                        continue
                    path = str(resp.get("spectrum_path", ""))
                    try:
                        score = _score_path(path)
                    except Exception as exc:  # noqa: BLE001
                        logs.append(
                            f"窗函数/填零(嵌入): {wname}+{zf_mode} 评分失败 {exc}"
                        )
                        continue
                    candidates.append((dict(w), zf_mode, score, est))
                    logs.append(
                        f"窗函数/填零(嵌入): {wname}+填零={zf_mode} "
                        f"score={score:.1f} 大小≈{est // 1048576}MB"
                    )
            if candidates:
                best_score = max(c[2] for c in candidates)
                # 同分容忍 0.5 分内选高分辨率优先(0.2.47,审查结论:填零不改变
                # 真实频率分辨率,文件大小不再是优化目标;低分辨率已由 min_shape
                # 惩罚项排除)
                best = max(
                    (c for c in candidates if c[2] >= best_score - 0.5),
                    key=lambda c: (c[3], -c[2]),
                )
                processing_result = {
                    "window": best[0],
                    "zero_fill": best[1],
                    "score": best[2],
                    "estimated_bytes": best[3],
                }
                base_bytes = _est_bytes("auto")
                better = best[2] > base_score + 0.5 or (
                    best[2] >= base_score - 0.5 and best[3] >= base_bytes - 1
                )
                if better:
                    plan = select_method(experiment)
                    resp = backend.process(
                        experiment,
                        plan,
                        direct_phase_override=result.phases,
                        params={
                            "window": {a: dict(best[0]) for a in axes},
                            "zero_fill": {a: {"mode": best[1]} for a in axes},
                            "baseline": applied_baseline,
                        },
                    )
                    if resp.get("success"):
                        spectrum_path = _register_spectrum(
                            manager,
                            exp_id,
                            data_id,
                            str(resp.get("spectrum_path", spectrum_path)),
                        )
                        logs.append(
                            f"窗函数/填零(嵌入): 终谱已用 "
                            f"{best[0].get('type')}+填零={best[1]} "
                            f"(score={best[2]:.1f}) 重渲"
                        )
                    else:
                        logs.append(
                            f"窗函数/填零(嵌入)重渲失败: {resp.get('message')}"
                        )
                else:
                    logs.append("窗函数/填零(嵌入): 候选未优于当前,保持默认")

    _finish_step(
        manager,
        exp_id,
        data_id,
        "phase_optimize",
        outputs={
            "spectrum_path": spectrum_path,
            "phase": str(result.phases),
            "baseline": (
                str(baseline_result["config"]) if baseline_result else ""
            ),
            "processing": (
                str(processing_result) if processing_result else ""
            ),
        },
        message="相位优化(逐维暴力,嵌入基线/窗函数/填零)",
        params={
            "backend_runs": result.backend_runs,
            "phases": {k: list(v) for k, v in result.phases.items()},
            "baseline": (
                baseline_result["config"] if baseline_result else None
            ),
            "window": processing_result["window"] if processing_result else None,
            "zero_fill": (
                processing_result["zero_fill"] if processing_result else None
            ),
        },
    )
    return {
        "phase": result.phases,
        "spectrum_path": spectrum_path,
        "method": result.method,
        "backend_runs": result.backend_runs,
        "logs": logs,
        "optimized": result.optimized,
        "skipped": result.skipped,
        "baseline": baseline_result,
        "processing": processing_result,
    }


__all__ = [
    "ImportResult",
    "StepwiseError",
    "generate_fid",
    "generate_spectrum",
    "import_data",
    "optimize_phase_brute_force",
]
