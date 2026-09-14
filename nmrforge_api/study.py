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

from nmrforge_api.direct_range import parse_direct_range
from nmrforge_api.errors import DatasetError
from nmrforge_api.records import write_records, write_reference_records
from nmrforge_api.reference import (
    ReferenceSpectrum,
    build_reference,
    build_reference_peak_tables,
    ensure_reference_peaks,
    load_reference,
    load_references,
    parse_reference_spec,
    resolve_reference,
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


@dataclass
class ReferenceResult:
    """参考模式结果:每个条件一份参考(参考谱 + 脚本 + 两张峰表)。"""

    session: StudySession
    references: dict[str, ReferenceSpectrum] = field(default_factory=dict)
    records: dict[str, str] = field(default_factory=dict)

    @property
    def root(self) -> Path:
        return self.session.root

    @property
    def conditions(self) -> list[str]:
        return self.session.conditions

    def reference(self, condition: str = "") -> ReferenceSpectrum | None:
        """按条件取参考(缺省主条件)。"""
        if condition:
            dataset = self.session.dataset_by_condition(condition)
            return self.references.get(dataset.key) if dataset else None
        primary = self.session.dataset
        return self.references.get(primary.key) if primary else None

    @property
    def peak_tables(self) -> dict[str, str]:
        """主条件的两张参考峰表路径(parabolic / gaussian)。"""
        reference = self.reference()
        if reference is None:
            return {}
        return {
            "parabolic": reference.peak_table_parabolic_path,
            "gaussian": reference.peak_table_gaussian_path,
        }


def run_reference_study(
    root: Path | str,
    dataset: Path | str | None = None,
    *,
    datasets: Mapping[str, str] | Sequence[Any] | None = None,
    name: str = "",
    params: dict[str, Any] | None = None,
    phase_route: str | None = None,
    peaks: Path | str | None = None,
    direct_range: Any = None,
    ext_lo: Any = None,
    ext_hi: Any = None,
    sigma_multiplier: float | None = None,
    max_peaks: int = 0,
    localization_method: str = "parabolic",
    gaussian_roi_f1_ppm: float | None = None,
    gaussian_roi_f2_ppm: float | None = None,
    force: bool = False,
    backend: Any | None = None,
    write: bool = True,
    progress: Callable[[str], None] | None = None,
) -> ReferenceResult:
    """**参考模式**:导入条件数据 → 自动优化参考谱/参考脚本 → 两张参考峰表。

    只做参考,不跑任何参数组合。组合模式(:func:`run_combination_study`)必须
    **显式引用**本模式建好的参考。选峰阈值 ``sigma_multiplier`` 在本模式指定,
    随后与参考一起锁定(参考定了以后所有 workflow 只能沿用)。

    直接维范围(**ppm**,``ext_lo`` = 高端 / ``ext_hi`` = 低端)可用
    ``direct_range=(high, low)``(也接受反序,自动换回)、``direct_range={"lo":…,
    "hi":…}``、或显式 ``ext_lo=/ext_hi=``;也兼容 ``params={"ext_lo":…}``。
    范围与已建参考不一致时会**重建参考谱并重测两张参考峰表**(留档在参数里);
    ``force=True`` 无条件重建。
    """
    session = open_study(root, name=name, backend=backend)
    conditions = _resolve_conditions(datasets, dataset)
    _register_conditions(session, conditions)
    if not session.datasets:
        raise DatasetError(
            "研究里还没有数据集:首次运行请传 datasets=<{条件: 目录}> 或 "
            "dataset=<Bruker 目录>"
        )
    direct = parse_direct_range(
        direct_range, ext_lo=ext_lo, ext_hi=ext_hi, params=params
    )
    run_params = dict(params or {})
    if direct is not None:
        run_params.update(direct.params())
    references: dict[str, ReferenceSpectrum] = {}
    rebuilt: list[str] = []
    for ref in session.datasets:
        reference = None if force else load_reference(session, ref)
        if reference is not None and direct is not None:
            # 直接维范围是参考谱的定义之一:与已建参考不一致 → 重建参考
            if not direct.matches_params(reference.params):
                reference = None
                rebuilt.append(ref.condition or ref.key)
        if reference is None:
            if progress is not None and rebuilt:
                progress(
                    "直接维范围与已建参考不一致,重建参考:"
                    f" ext_lo={direct.lo:g} ext_hi={direct.hi:g} ppm"
                    if direct is not None
                    else "重建参考"
                )
            reference = build_reference(
                session,
                ref,
                params=run_params or None,
                phase_route=phase_route,
                progress=progress,
                force=True,
            )
        references[ref.key] = reference
    # 峰身份与两张参考峰表:主条件先选峰,其余条件共享峰身份
    for ref in session.datasets:
        references[ref.key] = ensure_reference_peaks(
            session,
            references[ref.key],
            sigma_multiplier=sigma_multiplier,
            max_peaks=max_peaks,
            localization_method=localization_method,
            gaussian_roi_f1_ppm=gaussian_roi_f1_ppm,
            gaussian_roi_f2_ppm=gaussian_roi_f2_ppm,
            force=bool(rebuilt),
        )
    if peaks is not None:
        # 可选:研究方自带的峰表(公开库/已指认),作为主条件的峰身份并重建两张表
        primary = session.datasets[0]
        target = session.reference_dir_for(primary) / "reference.list"
        target.write_text(
            Path(peaks).read_text(encoding="utf-8-sig"), encoding="utf-8"
        )
        primary_reference = set_reference_peaks(
            session, target, references[primary.key], source="external"
        )
        references[primary.key] = build_reference_peak_tables(
            session, primary_reference
        )
        # 非主条件:强制重新复制主条件的(新)身份表,保证峰身份跨条件一致
        for ref in session.datasets[1:]:
            references[ref.key] = ensure_reference_peaks(
                session,
                references[ref.key],
                force=True,
                sigma_multiplier=sigma_multiplier,
                max_peaks=max_peaks,
                localization_method=localization_method,
                gaussian_roi_f1_ppm=gaussian_roi_f1_ppm,
                gaussian_roi_f2_ppm=gaussian_roi_f2_ppm,
            )
    records: dict[str, str] = {}
    if write:
        records = write_reference_records(session, references)
    primary_reference = references[session.datasets[0].key]
    session.save_state(reference=primary_reference.to_dict(), records=records)
    session.manager.save()
    return ReferenceResult(session=session, references=references, records=records)


def run_combination_study(
    reference: Any,
    *,
    combos: Sequence[Mapping[str, Any]] | None = None,
    axes: Mapping[str, Sequence[Any]] | None = None,
    max_runs: int = DEFAULT_MAX_RUNS,
    localization: Any = "parabolic",
    edge_margin_ppm: float | None = None,
    # window_pts/window_ppm/sign:历史参数(组合模式独立选峰后不再使用)
    window_pts: int | None = None,
    window_ppm: float | None = None,
    sign: str = "abs",
    direct_range: Any = None,
    ext_lo: Any = None,
    ext_hi: Any = None,
    roi_f1_ppm: float | None = None,
    roi_f2_ppm: float | None = None,
    resume: bool = True,
    backend: Any | None = None,
    write: bool = True,
    progress: Callable[[str], None] | None = None,
) -> StudyResult:
    """**组合模式**:显式指定参考,按用户参数组合表跑 workflow(不生成参考)。

    ``reference`` **必需**:研究根(``<root>`` 或 ``<root>#<条件>``)或
    ``reference.json`` 路径。组合在该参考上执行:

    - 参数基底 = 该参考运行的有效参数(相位锁定),组合表只覆盖它显式指定的键;
    - **组合独立选峰**(2026-09-14 规范):选峰阈值随参考锁定(与参考一致,不能在
      这里改;组合表里写阈值键——sigma_multiplier / min_snr / threshold_sigma /
      detection.sigma_multiplier——直接报错)。每个组合在**自己的候选谱**上用该
      锁定阈值独立选峰,输出该组合自己的完整峰表;reference_peak_id / assignment
      留空,**与参考峰表的匹配由外部(下游分析)完成**;
    - localization = parabolic(默认)/ gaussian / both:只输出被选中的峰表
      (逐组合可用组合表 localization 键覆盖);
    - 直接维范围(``ext_lo``/``ext_hi``,ppm)可用 ``direct_range=`` 覆盖**本批
      workflow 的基值**(参考谱不重建),逐组合还可以用 ``ext_lo``/``ext_hi``
      再覆盖;实际取值逐 workflow 记进 ``parameters_resolved.direct_range``;
    - 指定了条件就只跑该条件;只给研究根则跑该研究的全部条件(各自已有参考);
    - 候选谱与峰表写到 ``study/workflows/<workflow_id>/<条件>/``;
    - window_pts / window_ppm / sign 为历史参数:组合模式不再有「峰位搜索窗口」,
      只用物理宽度排除边缘轴峰(edge_margin_ppm)。
    """
    handle = parse_reference_spec(reference)
    session, target, ref = resolve_reference(reference, backend=backend)
    targets = (
        [target]
        if (handle.condition or handle.reference_json)
        else list(session.datasets)
    )
    references: dict[str, ReferenceSpectrum] = {}
    for item in targets:
        if item.key == target.key:
            references[item.key] = ref
            continue
        other = load_reference(session, item)
        if other is None or not other.peak_table_path:
            raise ReferenceError(
                f"条件 {item.condition or item.key} 还没有可用的参考:"
                "先跑参考模式(run_reference_study)把每个条件的参考建好"
            )
        references[item.key] = other
    direct = parse_direct_range(direct_range, ext_lo=ext_lo, ext_hi=ext_hi)
    base_params = dict(ref.sweep_params)
    notes: list[str] = []
    if direct is not None:
        base_params.update(direct.params())
        notes.append(
            "直接维范围由组合模式指定:"
            f" ext_lo={direct.lo:g} ext_hi={direct.hi:g} ppm"
            "(参考谱不重建;逐组合可用 ext_lo/ext_hi 再覆盖)"
        )
    plan = plan_sweep(
        ref,
        axes=axes,
        combos=combos,
        max_runs=max_runs,
        base_params=base_params,
        notes=notes,
    )
    runs = run_sweep(
        session,
        plan,
        reference=ref,
        datasets=targets,
        localization=localization,
        edge_margin_ppm=edge_margin_ppm,
        roi_f1_ppm=roi_f1_ppm,
        roi_f2_ppm=roi_f2_ppm,
        resume=resume,
        progress=progress,
    )
    summary = _summary(plan, runs, references)
    summary["reference_spec"] = handle.describe()
    if direct is not None:
        summary["direct_range"] = direct.to_dict()
    records: dict[str, str] = {}
    if write:
        records = write_records(
            session,
            references=references,
            plan=plan,
            runs=runs,
            peaks=reference_peaks(session, ref),
            reference_spec=handle.describe(),
        )
    workflows = load_workflows(session)
    session.save_state(reference=ref.to_dict(), records=records)
    session.manager.save()
    return StudyResult(
        session=session,
        plan=plan,
        references=references,
        runs=runs,
        workflows=workflows,
        summary=summary,
        records=records,
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
    direct_range: Any = None,
    ext_lo: Any = None,
    ext_hi: Any = None,
    sigma_multiplier: float | None = None,
    max_peaks: int = 0,
    max_runs: int = DEFAULT_MAX_RUNS,
    window_pts: int | None = None,          # 历史参数(组合模式不再使用)
    window_ppm: float | None = None,        # 历史参数(组合模式不再使用)
    sign: str = "abs",
    roi_f1_ppm: float | None = None,
    roi_f2_ppm: float | None = None,
    localization: Any = "parabolic",        # 组合模式精修方式(含 both)
    localization_method: str = "parabolic",  # 参考峰位取法
    gaussian_roi_f1_ppm: float | None = None,
    gaussian_roi_f2_ppm: float | None = None,
    force: bool = False,
    resume: bool = True,
    backend: Any | None = None,
    write: bool = True,
    progress: Callable[[str], None] | None = None,
) -> StudyResult:
    """一步式便捷入口 = **参考模式 + 组合模式**(内部把参考显式传给组合模式)。

    2026-09-14 起两种模式已分开:参考由 :func:`run_reference_study` 生成、组合由
    :func:`run_combination_study` 执行且**必须显式给参考**;本函数保留为一键便利
    入口与向后兼容(内部先跑参考模式,再用研究根显式调用组合模式)。
    直接维范围(``direct_range=`` / ``ext_lo`` / ``ext_hi``)在参考层生效;
    localization 传给组合模式(parabolic 默认 / gaussian / both)。
    """
    reference_result = run_reference_study(
        root,
        dataset,
        datasets=datasets,
        name=name,
        params=params,
        phase_route=phase_route,
        peaks=peaks,
        direct_range=direct_range,
        ext_lo=ext_lo,
        ext_hi=ext_hi,
        sigma_multiplier=sigma_multiplier,
        max_peaks=max_peaks,
        localization_method=localization_method,
        gaussian_roi_f1_ppm=gaussian_roi_f1_ppm,
        gaussian_roi_f2_ppm=gaussian_roi_f2_ppm,
        force=force,
        backend=backend,
        write=write,
        progress=progress,
    )
    # 组合模式:显式给参考(研究根);只给研究根 → 跑该研究的全部条件
    spec = str(reference_result.session.root)
    result = run_combination_study(
        spec,
        combos=combos,
        axes=axes,
        max_runs=max_runs,
        localization=localization,
        roi_f1_ppm=roi_f1_ppm,
        roi_f2_ppm=roi_f2_ppm,
        resume=resume,
        backend=backend,
        write=write,
        progress=progress,
    )
    result.references = reference_result.references
    merged = dict(reference_result.records)
    merged.update(result.records)
    result.records = merged
    return result

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
                "sigma_multiplier": (ref.peak_params or {}).get(
                    "sigma_multiplier"
                ),
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
    "ReferenceResult",
    "StudyResult",
    "copy_peak_table",
    "load_references",
    "reference_peaks",
    "run_combination_study",
    "run_parameter_study",
    "run_reference_study",
]
