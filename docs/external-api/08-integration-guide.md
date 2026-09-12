# 08 · 接入现有 pipeline

本接口不要求你放弃现有分析代码。按需要选择三种接法。

## 8.1 全套接管(推荐起步)

处理参数网格、候选谱、峰位测量都由本接口完成,你的项目只负责读结果与统计/画图。

```python
from nmrforge_api import run_parameter_study

result = run_parameter_study(
    "~/studies/hsqc_params",
    "~/bruker_data/bmrXXXXX/1",
    axes={"window.F1.off": [0.35, 0.45, 0.55], "zero_fill": [1, 2, 4]},
)

# 逐峰长表(推荐直接喂 pandas)
import pandas as pd
positions = pd.read_csv(result.records["peak_positions"])
uncertainty = pd.read_csv(result.records["uncertainty"])
summary = result.summary            # 也可读 records/uncertainty_summary.json
```

统计/画图仍在你自己的项目里做(热图、敏感性排序、稳健参数区等)。

## 8.2 最小侵入(只借参考与峰位测量)

你的 pipeline 继续自己生成 `.com` 并运行,只把两件事交给本接口:

1. **参考**:用 NMRForge 自动优化一次,拿到参考谱 + 参考脚本(冻结、可哈希);
2. **峰位**:用同一套口径在你产出的谱上测量同一批峰(自动选出的参考峰表)。

```python
from nmrforge_api import (
    SweepRun, add_dataset, build_reference, ensure_reference_peaks,
    measure_peak_positions, open_study, position_uncertainty,
    read_reference_peaks, uncertainty_summary,
)

session = open_study("~/studies/hsqc_params")
add_dataset(session, "~/bruker_data/bmrXXXXX/1")     # 只需一次
reference = build_reference(session)                  # 参考谱 + 参考脚本(冻结)
reference = ensure_reference_peaks(session, reference)  # 软件自动选峰
peaks = read_reference_peaks(reference.peak_table_path)

# ↓ 你自己的 runner:每个参数组合产出一张谱,记下 run_id 与路径
my_runs: list[SweepRun] = []
for index, combo in enumerate(my_combos, start=1):
    spectrum = my_pipeline(combo)                     # 你的处理路径
    measurements = measure_peak_positions(spectrum, peaks, window_pts=3)
    my_runs.append(
        SweepRun(
            run_id=f"m{index:04d}",
            index=index,
            combo=combo,
            params=combo,
            status="success",
            spectrum_path=str(spectrum),
            measurements=measurements,
        )
    )

uncertainties = position_uncertainty(my_runs, csp_n_weight=0.2)
summary = uncertainty_summary(uncertainties, csp_n_weight=0.2)
```

要点:只要 `peaks` 与参考谱同源(都用本接口选出的 `reference.list`),你现有的
`PPM_error` 类指标就能与本接口的 σ/Δδ 口径对齐。

## 8.3 只借参考脚本(把 NMRForge 当脚本生成器)

如果你的 runner 需要按行改写脚本(例如自己插 SMILE 行):

```python
from nmrforge_api import open_study, add_dataset, build_reference

session = open_study("~/studies/hsqc_params")
add_dataset(session, "~/bruker_data/bmrXXXXX/1")
reference = build_reference(session)

script_text = Path(reference.script_path).read_text(encoding="utf-8")
print(reference.script_sha256, reference.direct_phase)   # 溯源与相位
```

`reference.direct_phase`(各轴 PS)可直接写进你生成的脚本,保证相位与参考一致。

## 8.4 与现有指标的对应关系

| 你的指标 | 用本接口怎么算 |
| --- | --- |
| 峰位误差 / PPM error | `peak_positions.csv` 的 `delta_ppm`(或按核加权后 `Δδ`) |
| 强度/线宽误差 | 本接口只给极值处 `intensity`;强度/线宽请在候选谱上按你的方法测 |
| TP/FP/FN | 本接口给的是固定峰集的 `found` 标记,不含新峰检测;TP/FP/FN 建议用你的选峰流程单独算 |
| 稳健参数区 | 用 `uncertainty.csv`(逐峰 σ)或逐组合指标做阈值筛选 |
| 敏感性排序 | `peak_positions.csv` + 你自己的统计(方差分析/回归/相关性) |

## 8.5 可复现建议

- 一个网格一个研究根;换网格不要复用同一个 `runs/`(组合编号会撞);
- 固定 `peak_source`、`sigma_multiplier`、`max_peaks`、`window_pts`、`sign`、
  `refine`、`csp_n_weight`,把它们的取值写进论文材料;
- 把 `records/manifest.json` 与研究根一起归档(体积很小,足以重建全部结论);
- 记录 NMRForge/NMRPipe 版本(manifest 里已有);升级版本后若要比较结果,
  建议在同一研究根上重跑并注明版本变化。
