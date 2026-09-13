"""最小侵入示例:只借「参考峰表 + 峰位测量」,谱来自你自己的 pipeline。

适用:处理照旧由你的代码完成,只把峰位测量口径对齐到 NMRForge(同一批峰、
同一 SUB-PIXEL 方法)。
"""

from __future__ import annotations

import os
from pathlib import Path

from nmrforge_api import (
    PeakMeasurement,
    ensure_reference_peaks,
    load_reference,
    measure_peak_positions,
    position_uncertainty,
    read_reference_peaks,
    uncertainty_summary,
)

STUDY = Path(os.environ.get("NMRFORGE_API_STUDY", "~/studies/hsqc_params")).expanduser()


def my_pipeline(combo: dict) -> Path:
    """占位:你自己的处理实现,返回候选谱路径。"""
    raise NotImplementedError("替换为你的处理路径")


def main() -> int:
    import json

    from nmrforge_api import open_study

    session = open_study(STUDY)
    reference = load_reference(session)
    if reference is None:
        print("先跑一次 run_parameter_study / reference 步骤生成参考谱")
        return 2
    reference = ensure_reference_peaks(session, reference)
    peaks = read_reference_peaks(reference.peak_table_path)
    print(f"参考峰表 {len(peaks)} 峰:{reference.peak_table_path}")

    combos = [{"window.F1.off": 0.35}, {"window.F1.off": 0.45}]
    measurements: dict[str, list[PeakMeasurement]] = {}
    for index, combo in enumerate(combos, start=1):
        spectrum = my_pipeline(combo)                  # ← 你的谱
        # 窗口给**物理宽度**(ppm):缺省 1.5×该轴线宽折算 ppm;这样即使
        # 你的 pipeline 改了填零(数字分辨率),窗口覆盖的 ppm 也不变。
        measurements[f"m{index:04d}"] = measure_peak_positions(
            spectrum, peaks, window_ppm=0.5
        )

    uncertainties = position_uncertainty(measurements, csp_n_weight=0.2)
    summary = uncertainty_summary(uncertainties, csp_n_weight=0.2)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
