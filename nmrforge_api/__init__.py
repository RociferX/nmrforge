"""NMRForge 参数组合执行接口(v0.2,2026-09-13 规范更新)。

它做的事(全部无 Qt、可脚本化、可在集群上跑):

1. 读原始 NMR 数据与**用户定义的参数组合表**;
2. 自动优化生成**参考工作流**:1 个参考处理脚本 + 2 张参考峰表
   (parabolic / 2D gaussian);参考只作后续参数扰动的基准,不声称全局最优;
3. 每个参数组合 = 一个 **workflow_id**(W0001…):以参考脚本为模板、只替换该
   组合指定的参数、自动跑处理,并对**同一张谱**分别做 parabolic 与
   gaussian 定位,输出两张结构一致的峰表;
4. 保存完整 provenance:``parameters_requested`` / ``parameters_used`` /
   自动参数的**实际结果**(``actual_p0``/``actual_p1``、SMILE 实际 nSigma 与
   噪声 σ)、完整脚本与日志、软件/NMRPipe/SMILE 版本、状态
   (success / success_with_warning / failed);
5. 两条件数据(A/B)用同一个 workflow 的同一组参数处理,共享
   ``reference_peak_id``,各自输出峰值表。

它**不**做的事(软件边界,规范 2026-09-13):CSP 计算、robustness 计算、
统计分析与显著性判断、科学结论——这些由下游独立分析程序基于统一峰表完成。

最小用法::

    from nmrforge_api import run_parameter_study

    result = run_parameter_study(
        "~/studies/hsqc_params",
        datasets={"A": "~/data/bmr12345/1", "B": "~/data/bmr12345/2"},
        combos=[{"zero_fill": 1}, {"zero_fill": 2}],
    )

分步用法::

    from nmrforge_api import (
        open_study, add_dataset, build_reference, ensure_reference_peaks,
        plan_sweep, run_sweep, write_records,
    )

对外文档:``docs/external-api/README.md``;规范符合性台账:
``docs/reviews/2026-09-13-api-spec-compliance.md``;内部设计记录:
``docs/proposals/external-api/001-parameter-sweep-api.md``。
"""

from __future__ import annotations

from nmrforge_api.direct_range import DirectRange, parse_direct_range
from nmrforge_api.errors import (
    DatasetError,
    MeasurementError,
    ReferenceError,
    SensitivityError,
    SweepError,
)
from nmrforge_api.peak_tables import (
    GAUSSIAN_ONLY_COLUMNS,
    PEAK_TABLE_COLUMNS,
    REFERENCE_WORKFLOW_ID,
    gaussian_fallback_rows,
    peak_table_digest,
    peak_table_row,
    peak_table_rows,
    read_peak_table,
    reference_peak_id,
    write_peak_table,
)
from nmrforge_api.peaks import (
    PeakMeasurement,
    measure_peak_positions,
    pick_reference_peaks,
    read_reference_peaks,
    window_points_by_axis,
)
from nmrforge_api.records import (
    BOUNDARY_STATEMENT,
    WINDOW_POLICY,
    combined_peak_table,
    write_records,
    write_reference_records,
)
from nmrforge_api.reference import (
    ReferenceHandle,
    ReferenceSpectrum,
    build_reference,
    build_reference_peak_tables,
    ensure_reference_peaks,
    load_reference,
    load_references,
    parse_reference_spec,
    resolve_reference,
    sanitize_sweep_params,
    set_reference_peaks,
)
from nmrforge_api.session import (
    CONDITION_LETTERS,
    DatasetRef,
    StudySession,
    add_dataset,
    condition_token,
    dataset_info,
    open_study,
)
from nmrforge_api.study import (
    ReferenceResult,
    StudyResult,
    run_combination_study,
    run_parameter_study,
    run_reference_study,
)
from nmrforge_api.sweep import (
    STATUS_FAILED,
    STATUS_SUCCESS,
    STATUS_WARNING,
    SweepPlan,
    SweepRun,
    combos_from_rows,
    design_diagnostics,
    expand_grid,
    infer_axes,
    load_combo_table,
    load_plan,
    load_runs,
    load_workflows,
    merge_overrides,
    plan_sweep,
    run_sweep,
    workflow_id_for,
    workflow_summary,
    write_combo_table,
)

# --- 验证/检测辅助(不属于处理契约)----------------------------------------
# σ/极差/Δδ 下限只供测试、复核与下游分析参考实现使用:处理链(study/sweep/
# records/CLI)不调用它,也不产出对应文件。守护见
# tests/test_nmrforge_api.py::test_statistics_helper_is_not_in_processing_contract
from nmrforge_api.uncertainty import (
    DEFAULT_CSP_N_WEIGHT,
    PeakUncertainty,
    position_uncertainty,
    uncertainty_summary,
)

API_VERSION = "0.2"

__all__ = [
    "API_VERSION",
    "DirectRange",
    "BOUNDARY_STATEMENT",
    "ReferenceHandle",
    "ReferenceResult",
    "CONDITION_LETTERS",
"DEFAULT_CSP_N_WEIGHT",
    "DatasetError",
    "DatasetRef",
    "GAUSSIAN_ONLY_COLUMNS",
    "MeasurementError",
    "PEAK_TABLE_COLUMNS",
    "PeakMeasurement",
"PeakUncertainty",
    "REFERENCE_WORKFLOW_ID",
    "ReferenceError",
    "ReferenceSpectrum",
    "STATUS_FAILED",
    "STATUS_SUCCESS",
    "STATUS_WARNING",
    "SensitivityError",
    "StudyResult",
    "StudySession",
    "SweepError",
    "SweepPlan",
    "SweepRun",
    "WINDOW_POLICY",
    "add_dataset",
    "build_reference",
    "build_reference_peak_tables",
    "combined_peak_table",
    "combos_from_rows",
    "condition_token",
    "dataset_info",
    "design_diagnostics",
    "ensure_reference_peaks",
    "expand_grid",
    "gaussian_fallback_rows",
    "infer_axes",
    "load_combo_table",
    "load_plan",
    "load_reference",
    "load_references",
    "load_runs",
    "load_workflows",
    "measure_peak_positions",
    "merge_overrides",
    "open_study",
    "parse_direct_range",
    "peak_table_digest",
    "peak_table_row",
    "peak_table_rows",
    "pick_reference_peaks",
    "plan_sweep",
"position_uncertainty",
    "read_peak_table",
    "read_reference_peaks",
    "parse_reference_spec",
    "reference_peak_id",
    "resolve_reference",
    "run_combination_study",
    "run_parameter_study",
    "run_reference_study",
    "run_sweep",
    "sanitize_sweep_params",
    "set_reference_peaks",
"uncertainty_summary",
    "workflow_id_for",
    "workflow_summary",
    "window_points_by_axis",
    "write_combo_table",
    "write_peak_table",
    "write_records",
    "write_reference_records",
]
