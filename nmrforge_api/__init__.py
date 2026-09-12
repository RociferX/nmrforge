"""NMRForge 参数敏感性研究接口(v0.1)。

给「处理参数怎样影响 2D 谱峰位置、这个不确定度能否当作 CSP 判据下限」这类
研究用的**独立、无 Qt、可脚本化**接口。它复用 NMRForge 的处理链与峰位口径,
但把结果组织成研究需要的形式:参考谱/参考脚本冻结、参数网格扫描、同一批峰
的亚像素峰位、逐峰与数据集级的峰位不确定度、以及可复算的记录文件。

最小用法::

    from nmrforge_api import run_parameter_study

    result = run_parameter_study(
        "~/studies/hsqc_params",
        "~/data/bmr12345/1",
        axes={"zero_fill": [1, 2, 4], "window.F1.off": [0.35, 0.45, 0.55]},
    )
    print(result.summary["delta_std_ppm"])

分步用法(需要精细控制时):

.. code-block:: python

    from nmrforge_api import (
        open_study, add_dataset, build_reference, pick_reference_peaks,
        plan_sweep, run_sweep, position_uncertainty, write_records,
    )

对外文档(安装/上手/API 与 CLI 参考/输出记录/方法与指标/接入指南/边界与排查):
``docs/external-api/README.md``;内部设计记录:
``docs/proposals/external-api/001-parameter-sweep-api.md``。
"""

from __future__ import annotations

from nmrforge_api.errors import (
    DatasetError,
    MeasurementError,
    ReferenceError,
    SensitivityError,
    SweepError,
)
from nmrforge_api.peaks import (
    PeakMeasurement,
    measure_peak_positions,
    pick_reference_peaks,
    read_reference_peaks,
)
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
    dataset_info,
    open_study,
)
from nmrforge_api.study import StudyResult, run_parameter_study
from nmrforge_api.sweep import (
    SweepPlan,
    SweepRun,
    expand_grid,
    load_plan,
    load_runs,
    merge_overrides,
    plan_sweep,
    run_sweep,
)
from nmrforge_api.uncertainty import (
    PeakUncertainty,
    position_uncertainty,
    uncertainty_summary,
)

API_VERSION = "0.1"

__all__ = [
    "API_VERSION",
    "DatasetError",
    "DatasetRef",
    "MeasurementError",
    "PeakMeasurement",
    "PeakUncertainty",
    "ReferenceError",
    "ReferenceSpectrum",
    "SensitivityError",
    "StudyResult",
    "StudySession",
    "SweepError",
    "SweepPlan",
    "SweepRun",
    "add_dataset",
    "build_reference",
    "dataset_info",
    "ensure_reference_peaks",
    "expand_grid",
    "load_plan",
    "load_reference",
    "load_runs",
    "measure_peak_positions",
    "merge_overrides",
    "open_study",
    "pick_reference_peaks",
    "plan_sweep",
    "position_uncertainty",
    "read_reference_peaks",
    "run_parameter_study",
    "run_sweep",
    "sanitize_sweep_params",
    "set_reference_peaks",
    "uncertainty_summary",
    "write_records",
]
