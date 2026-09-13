"""一步跑完一项参数组合研究(下游项目最主要的入口)。

流程(2026-09-13 规范)::

    Raw data(A/B…) → Reference workflow(1 脚本 + 2 峰表)
        → 用户参数组合表 → W0001… 逐 workflow 对全部条件跑处理
        → parabolic / gaussian 两张峰表 + 完整 provenance + QC

边界:本接口**只**执行处理并落档;CSP、robustness、统计与结论由下游独立
分析程序从统一峰表计算。

典型用法::

    from nmrforge_api import run_parameter_study

    result = run_parameter_study(
        "~/studies/hsqc_params",                     # 研究根(可复用/续跑)
        datasets={"A": "~/data/bmrxxxx/1",           # 条件 A
                  "B": "~/data/bmrxxxx/2"},          # 条件 B(同参数)
        combos=[{"zero_fill": 1}, {"zero_fill": 2}], # 用户参数组合表
    )
    print(result.workflows_by_status())
"""

from __future__ import annotations

import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nmrforge_api.errors import DatasetError, SensitivityError
from nmrforge_api.records import write_records
from nmrforge_api.reference import (
    ReferenceSpectrum,
    build_reference,
    ensure_reference_peaks,
    load_reference,
    load_references,
    sanitize_sweep_params,
    set_reference_peaks,
)
from nmrforge_api.session import (
    DatasetRef,
    StudySession,
    add_dataset,
    open_study,
)
from nmrforge_api.sweep import (
    DEFAULT_MAX_RUNS,
    STATUS_FAILED,
    STATUS_SUCCESS,
    STATUS_WARNING,
    SweepPlan,
    SweepRun,
    load_workflows,
    plan_sweep,
    run_sweep,
    workflow_summary,
)


@dataclass
class StudyResult:
    """一次研究的完整结果句柄(不含任何统计推断)。"""

    session: StudySession
    plan: SweepPlan
    references: dict[str, ReferenceSpectrum] = field(default_factory=dict)
    runs: list[SweepRun] = field(default_factory=list)
    workflows: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    records: dict[str, str] = field(default_factory=dict)

    @property
    def root(self) -> Path:
        return self.session.root

    @property
    def reference(self) -> ReferenceSpectrum | None:
        """主条件(第一个数据集)的参考谱。"""
        if self.session.dataset is None:
            return None
        return self.references.get(self.session.dataset.key)

    @property
    def peak_table_path(self) -> str:
        ref = self.reference
        return ref.peak_table_path if ref is not None else ""

    @property
    def peak_tables(self) -> dict[str, str]:
        """两张参考峰表(parabolic / gaussian)的路径。"""
        ref = self.reference
        if ref is None:
            return {}
        return {
            "parabolic": ref.peak_table_parabolic_path,
            "gaussian": ref.peak_table_gaussian_path,
        }

    @property
    def conditions(self) -> list[str]:
        return self.session.conditions

    @property
    def failed_runs(self) -> list[SweepRun]:
        return [run for run in self.runs if run.status == STATUS_FAILED]

    def workflows_by_status(self) -> dict[str, int]:
        return workflow_summary(self.runs)


def _resolve_conditions(
    datasets: Mapping[str, str] | Sequence[Any] | None,
    dataset: str | Path | None,
) -> list[tuple[str, str]]:
    """``datasets``/``dataset`` 参数 → [(条件标签, 数据集路径)]。

    - ``datasets={"A": path, "B": path}``(推荐,多条件);
    - ``datasets=[("A", path), ("B", path)]`` 或 ``[pathA, pathB]``(自动标 A/B);
    - ``dataset=path``:单条件(兼容旧调用)。
    """
    out: list[tuple[str, str]] = []
    if datasets is not None:
        if isinstance(datasets, Mapping):
            for label, path in datasets.items():
                out.append((str(label), str(path)))
        else:
            for item in datasets:
                if isinstance(item, (tuple, list)) and len(item) == 2:
                    out.append((str(item[0]), str(item[1])))
                else:
                    out.append(("", str(item)))
    if dataset is not None:
        out.append(("", str(dataset)))
    return out


def _register_conditions(
    session: StudySession, conditions: Sequence[tuple[str, str]]
) -> None:
    """把用户给的条件数据集登记进会话(已登记且来源一致则复用)。"""
    for label, path in conditions:
        source = str(Path(path).expanduser().resolve())
        existing = (
            session.dataset_by_condition(label)
            if label
            else (session.dataset if len(session.datasets) == 0 else None)
        )
        if existing is not None:
            if Path(existing.source).resolve() != Path(source):
                raise DatasetError(
                    f"条件 {existing.condition!r} 已绑定数据集 {existing.source};"
                    f"要用 {source} 请换条件标签或另建研究根"
                )
            continue
        add_dataset(
            session,
            source,
            condition=label,
            title="",
        )


def run_parameter_study(
    root: Path | str,
    dataset: Path | str | None = None,
    *,
    datasets: Mapping[str, str] | Sequence[Any] | None = None,
    axes: Mapping[str, Sequence[Any]] | None = None,
    combos: Sequence[Mapping[str, Any]] | None = None,
    name: str = "",
    params: dict[str, Any] | None = None,
    phase_route: str | None = None,
    peaks: Path | str | None = None,
    sigma_multiplier: float | None = None,
    max_peaks: int = 0,
    max_runs: int = DEFAULT_MAX_RUNS,
    window_pts: int | None = None,
    window_ppm: float | None = None,
    sign: str = "abs",
    roi_f1_ppm: float | None = None,
    roi_f2_ppm: float | None = None,
    localization_method: str = "parabolic",
    gaussian_roi_f1_ppm: float | None = None,
    gaussian_roi_f2_ppm: float | None = None,
    resume: bool = True,
    backend: Any | None = None,
    write: bool = True,
    progress: Callable[[str], None] | None = None,
) -> StudyResult:
    """建/开研究 → 导入条件数据 → 每个条件建参考 → 参考峰表 → workflow → 记录。

    - 参数组合二选一(必须且只能给一个):``axes``(接口展开全因子)或
      ``combos``(**用户给定的组合表**,原样按表序执行,接口不做设计决策);
    - ``datasets`` 给多条件(``{"A": path, "B": path}``);同一 workflow 对全部
      条件使用**同一份用户参数**,各条件各有一份参考(相位/噪声按该条件数据),
      峰身份(``reference_peak_id``)全条件共享;
    - 默认不要求外部峰表:参考谱与参考峰位都由 NMRForge 自动优化/自动选峰产生
      (``peaks`` 只在研究方另有公开库/指认峰表时才传);
    - 峰位搜索窗口 ``window_ppm``(物理半径,ppm)缺省按物理宽度自动;
    - 每个 workflow × 条件产出:完整脚本、候选谱、parabolic 与 gaussian 两张
      峰表、完整日志、版本与状态(success / success_with_warning / failed)。
    """
    session = open_study(root, name=name, backend=backend)
    try:
        conditions = _resolve_conditions(datasets, dataset)
        _register_conditions(session, conditions)
        if not session.datasets:
            raise DatasetError(
                "研究里还没有数据集:首次运行请传 datasets=<{条件: 目录}> 或 "
                "dataset=<Bruker 目录>"
            )

        primary = session.datasets[0]
        # 参考:每个条件各建一份(缺则建;已有则复用)
        references: dict[str, ReferenceSpectrum] = {}
        for ref in session.datasets:
            reference = load_reference(session, ref)
            if reference is None:
                reference = build_reference(
                    session,
                    ref,
                    params=params,
                    phase_route=phase_route,
                    progress=progress,
                )
            references[ref.key] = reference
        # 参考峰身份与两张参考峰表:主条件先选峰,其余条件共享峰身份
        for ref in session.datasets:
            references[ref.key] = ensure_reference_peaks(
                session,
                references[ref.key],
                sigma_multiplier=sigma_multiplier,
                max_peaks=max_peaks,
                localization_method=localization_method,
                gaussian_roi_f1_ppm=gaussian_roi_f1_ppm,
                gaussian_roi_f2_ppm=gaussian_roi_f2_ppm,
            )

        primary_reference = references[primary.key]
        if peaks is not None:
            # 可选:研究方自带的峰表(公开库/已指认),登记到主条件参考并
            # 重新生成两张参考峰表;非主条件沿用同一身份表。
            target = session.reference_dir_for(primary) / "reference.list"
            target.write_text(
                Path(peaks).read_text(encoding="utf-8-sig"), encoding="utf-8"
            )
            primary_reference = set_reference_peaks(
                session, target, primary_reference, source="external"
            )
            from nmrforge_api.reference import build_reference_peak_tables

            primary_reference = build_reference_peak_tables(
                session, primary_reference
            )
            references[primary.key] = primary_reference
            for ref in session.datasets[1:]:
                references[ref.key] = ensure_reference_peaks(
                    session,
                    references[ref.key],
                    sigma_multiplier=sigma_multiplier,
                    max_peaks=max_peaks,
                    force=True,
                    localization_method=localization_method,
                    gaussian_roi_f1_ppm=gaussian_roi_f1_ppm,
                    gaussian_roi_f2_ppm=gaussian_roi_f2_ppm,
                )

        # 扫描基底 = 主条件参考运行的有效参数(用户 params 只作局部覆盖)
        base_params = dict(primary_reference.sweep_params)
        if params:
            base_params.update(sanitize_sweep_params(params))
        plan = plan_sweep(
            primary_reference,
            axes=axes,
            combos=combos,
            max_runs=max_runs,
            base_params=base_params,
        )
        runs = run_sweep(
            session,
            plan,
            reference=references[primary.key],
            window_pts=window_pts,
            window_ppm=window_ppm,
            sign=sign,
            roi_f1_ppm=roi_f1_ppm,
            roi_f2_ppm=roi_f2_ppm,
            resume=resume,
            progress=progress,
        )
        summary = _summary(plan, runs, references)
        records: dict[str, str] = {}
        if write:
            records = write_records(
                session,
                references=references,
                plan=plan,
                runs=runs,
                peaks=reference_peaks(session, primary_reference),
            )
        workflows = load_workflows(session)
        session.save_state(reference=primary_reference.to_dict(), records=records)
        # 项目文件落盘:导入的数据集、fid/活动谱与运行记录在后续进程/会话可见
        session.manager.save()
    except SensitivityError:
        raise
    return StudyResult(
        session=session,
        plan=plan,
        references=references,
        runs=runs,
        workflows=workflows,
        summary=summary,
        records=records,
    )


def _summary(
    plan: SweepPlan,
    runs: Sequence[SweepRun],
    references: Mapping[str, ReferenceSpectrum],
) -> dict[str, Any]:
    """研究摘要:条件、workflow 数、逐 workflow 状态、参考峰表信息。

    只统计**执行结果**(状态/峰数),不含任何统计推断量。
    """
    counts = workflow_summary(runs)
    per_workflow: dict[str, dict[str, Any]] = {}
    for run in runs:
        entry = per_workflow.setdefault(
            run.workflow_id,
            {
                "workflow_id": run.workflow_id,
                "parameters_requested": run.parameters_requested,
                "conditions": {},
            },
        )
        entry["conditions"][run.condition or run.dataset.get("key", "")] = {
            "status": run.status,
            "warnings": [w.get("code") for w in run.warnings],
            "peak_table_parabolic": run.peak_table_path("parabolic"),
            "peak_table_gaussian": run.peak_table_path("gaussian"),
            "log_path": run.log_path,
        }
    return {
        "conditions": [ref.condition for ref in references.values()],
        "n_conditions": len(references),
        "n_workflows": plan.n_workflows,
        "workflow_ids": plan.workflow_ids(),
        "design": plan.design,
        "grid_sha256": plan.grid_sha256,
        "status_counts": counts,
        "per_workflow": per_workflow,
        "references": {
            key: {
                "condition": ref.condition,
                "peak_count": ref.peak_count,
                "peak_source": ref.peak_source,
                "peak_tables": ref.peak_tables,
                "script_sha256": ref.script_sha256,
                "spectrum_sha256": ref.spectrum_sha256,
            }
            for key, ref in references.items()
        },
        "boundary": "只执行处理与留档;CSP/robustness/统计由下游独立分析程序完成",
    }


def reference_peaks(
    session: StudySession,
    reference: ReferenceSpectrum | DatasetRef | None = None,
) -> list[dict[str, Any]]:
    """读取参考峰表(供记录/复核;失败返回空表)。

    ``reference`` 可以是参考谱对象、条件数据集(DatasetRef,按其取参考),
    或留空(用会话主条件)。
    """
    ref = (
        reference
        if isinstance(reference, ReferenceSpectrum)
        else load_reference(session, reference)
    )
    if ref is None or not ref.peak_table_path:
        return []
    try:
        from core.peaks.peak_table import load_peaks

        return load_peaks(Path(ref.peak_table_path))
    except Exception:  # noqa: BLE001 - 记录用途,读不到不阻断
        return []


def copy_peak_table(source: Path | str, target: Path | str) -> Path:
    """把外部峰表复制进研究目录(留档 + 稳定路径)。"""
    src = Path(source)
    dst = Path(target)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return dst


__all__ = [
    "STATUS_FAILED",
    "STATUS_SUCCESS",
    "STATUS_WARNING",
    "DatasetRef",
    "StudyResult",
    "copy_peak_table",
    "load_references",
    "reference_peaks",
    "run_parameter_study",
]
