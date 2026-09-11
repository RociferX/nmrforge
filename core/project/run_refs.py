"""步骤 → 可能的工作流 run ref 对照表(单一来源)。

0.2.199-补29hz-修1:Pipeline 与项目树原来各存一份,统一到 gui/pipeline_state;
修24:再下沉到 core —— workflow 层(批量参考参数)也要用,而 workflow 不能反向
依赖 gui。gui/pipeline_state 继续 re-export 同名符号,外部 import 不变。
"""

from __future__ import annotations

STEP_RUN_REFS: dict[str, tuple[str, ...]] = {
    "fid": ("convert_to_fid", "manual_fid"),
    "spectrum": (
        "process",
        "reconstruct_nus",
        "manual_process",
        "manual_nus",
        "phase_optimize_unified",
    ),
    "smile": ("smile_optimize",),
    "peaks": ("pick_peaks", "manual_peaks"),
    "analysis": ("analyze",),
}

# 只属于人工路径的谱图 ref(手工跑脚本时登记)
MANUAL_SPECTRUM_RUN_REFS: tuple[str, ...] = ("manual_process", "manual_nus")

ALL_STEP_RUN_REFS: tuple[str, ...] = tuple(
    sorted({ref for refs in STEP_RUN_REFS.values() for ref in refs})
)
