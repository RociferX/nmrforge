"""一步跑完一项参数敏感性研究(下游项目最主要的入口)。

典型用法::

    from nmrforge_api import run_parameter_study

    result = run_parameter_study(
        "~/studies/hsqc_params",          # 研究根(可复用/续跑)
        "~/data/bmrxxxx/1",               # 公开库下载的 Bruker 目录
        axes={
            "zero_fill": [1, 2, 4],
            "window.F1.off": [0.35, 0.45, 0.55],
            "points_per_line": [2.0, 4.0],
        },
    )
    print(result.summary["delta_std_ppm"])

每一步都被记录在 ``study/`` 下;重复运行会复用已有参考谱与已完成的组合
(断点续跑),换数据集时自动重建参考谱。
"""

from __future__ import annotations

import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nmrforge_api.errors import DatasetError
from nmrforge_api.records import write_records
from nmrforge_api.reference import (
    ReferenceSpectrum,
    build_reference,
    ensure_reference_peaks,
    load_reference,
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
    SweepPlan,
    SweepRun,
    plan_sweep,
    run_sweep,
)
from nmrforge_api.uncertainty import (
    DEFAULT_CSP_N_WEIGHT,
    PeakUncertainty,
    position_uncertainty,
    uncertainty_summary,
)


@dataclass
class StudyResult:
    """一次研究的完整结果句柄。"""

    session: StudySession
    reference: ReferenceSpectrum
    plan: SweepPlan
    runs: list[SweepRun] = field(default_factory=list)
    uncertainties: list[PeakUncertainty] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    records: dict[str, str] = field(default_factory=dict)

    @property
    def root(self) -> Path:
        return self.session.root

    @property
    def peak_table_path(self) -> str:
        return self.reference.peak_table_path

    @property
    def failed_runs(self) -> list[SweepRun]:
        return [run for run in self.runs if run.status != "success"]


def run_parameter_study(
    root: Path | str,
    dataset: Path | str | None = None,
    *,
    axes: Mapping[str, Sequence[Any]],
    name: str = "",
    params: dict[str, Any] | None = None,
    phase_route: str | None = None,
    peaks: Path | str | None = None,
    sigma_multiplier: float | None = None,
    max_peaks: int = 0,
    max_runs: int = DEFAULT_MAX_RUNS,
    window_pts: int = 3,
    sign: str = "abs",
    refine: str = "parabolic",
    csp_n_weight: float = DEFAULT_CSP_N_WEIGHT,
    resume: bool = True,
    backend: Any | None = None,
    write: bool = True,
    progress: Callable[[str], None] | None = None,
) -> StudyResult:
    """建/开研究 → (可选)导入数据 → 参考谱 → 峰表 → 扫描 → 汇总。

    ``dataset`` 只在首次建研究或换数据集时需要。
    **默认不要求外部峰表**:参考谱与参考峰位都由 NMRForge 自动优化产生
    (``build_reference`` 出参考谱/脚本,``ensure_reference_peaks`` 在参考谱上
    自动选峰并冻结);``peaks`` 只在研究方另有公开库/指认峰表时才传。
    """
    session = open_study(root, name=name, backend=backend)
    rebuild = False
    if dataset is not None:
        source = str(Path(dataset).expanduser().resolve())
        current = session.dataset
        if current is None or Path(current.source).resolve() != Path(source):
            reference_existed = load_reference(session) is not None
            session.dataset = add_dataset(session, source, title=name)
            rebuild = reference_existed
    if session.dataset is None:
        raise DatasetError(
            "研究里还没有数据集:首次运行请传 dataset=<Bruker 目录>"
        )

    reference = build_reference(
        session,
        params=params,
        phase_route=phase_route,
        progress=progress,
        force=rebuild,
    )
    if peaks is not None:
        # 可选:研究方自带的峰表(公开库/已指认),同样冻结留档
        target = session.reference_dir_for() / "reference.list"
        target.write_text(
            Path(peaks).read_text(encoding="utf-8-sig"), encoding="utf-8"
        )
        reference = set_reference_peaks(
            session, target, reference, source="external"
        )
    else:
        # 默认:NMRForge 在参考谱上自动选峰,峰位即研究的基准峰位
        reference = ensure_reference_peaks(
            session,
            reference,
            sigma_multiplier=sigma_multiplier,
            max_peaks=max_peaks,
        )

    # 扫描基底 = 参考谱的有效参数(相位/窗/填零/基线都是参考运行的结论),
    # 用户显式传入的 params 只作为局部覆盖,不整体替换参考。
    base_params = dict(reference.sweep_params)
    if params:
        base_params.update(sanitize_sweep_params(params))
    plan = plan_sweep(
        reference, axes=axes, max_runs=max_runs, base_params=base_params
    )
    runs = run_sweep(
        session,
        plan,
        reference=reference,
        window_pts=window_pts,
        sign=sign,
        refine=refine,
        resume=resume,
        progress=progress,
    )
    uncertainties = position_uncertainty(
        runs, csp_n_weight=csp_n_weight
    )
    summary = uncertainty_summary(
        uncertainties,
        csp_n_weight=csp_n_weight,
        n_runs=sum(1 for run in runs if run.status == "success"),
    )
    records: dict[str, str] = {}
    if write:
        records = write_records(
            session,
            reference=reference,
            plan=plan,
            runs=runs,
            uncertainties=uncertainties,
            summary=summary,
            peaks=reference_peaks(session, reference),
        )
    session.save_state(reference=reference.to_dict(), records=records)
    # 项目文件落盘:导入的数据集、fid/活动谱与运行记录在后续进程/会话可见
    session.manager.save()
    return StudyResult(
        session=session,
        reference=reference,
        plan=plan,
        runs=runs,
        uncertainties=uncertainties,
        summary=summary,
        records=records,
    )


def reference_peaks(
    session: StudySession, reference: ReferenceSpectrum | None = None
) -> list[dict[str, Any]]:
    """读取参考峰表(供记录/复核;失败返回空表)。"""
    ref = reference or load_reference(session)
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
    "DatasetRef",
    "StudyResult",
    "copy_peak_table",
    "reference_peaks",
    "run_parameter_study",
]
