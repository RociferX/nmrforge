# 02 · 快速上手(v1.0)

## 1. 两个模式(2026-09-14 起)

接口把工作拆成两个模式:**参考模式**(生成参考)与**组合模式**(按参数组合跑
处理),组合模式**必须显式指定参考**。

```python
from nmrforge_api import run_reference_study, run_combination_study

# 1) 参考模式:导入数据 + 自动优化参考谱/脚本 + 两张参考峰表
reference = run_reference_study(
    "~/studies/hsqc_params",              # 研究根(可复用/断点续跑)
    datasets={"A": "~/data/bmr12345/1"},  # 条件 A(原始 Bruker 目录)
    sigma_multiplier=25,                   # 选峰阈值:只在参考模式指定
)

# 2) 组合模式:显式给参考(这里用研究根 = 主条件参考)
result = run_combination_study(
    "~/studies/hsqc_params",              # 或 ".../study/reference/<key>/reference.json"
    combos=[                               # 用户参数组合表(原样执行)
        {"zero_fill": 1},
        {"zero_fill": 2},
        {"zero_fill": 1, "window.F1.off": 0.45},
    ],
    localization="both",                   # parabolic(默认)/ gaussian / both
)

print(result.summary["workflow_ids"])      # ['W0001', 'W0002', 'W0003']
print(result.summary["reference_spec"])    # 显式指定的参考
for run in result.runs:
    print(run.workflow_id, run.condition, run.status,
          run.peak_table_path("parabolic"), run.peak_table_path("gaussian"))
```

- 参考模式只建参考(1 个脚本 + 2 张峰表),不跑任何组合;选峰阈值在这里确定,
  之后**全程锁定**(组合表里写阈值键直接报错,要改阈值请重建参考);
- 组合模式不生成参考:参数基底取参考的有效参数,组合表只覆盖它显式指定的键;
  参考不存在或峰表缺失 → `ReferenceError`(提示先跑参考模式);
- **组合独立选峰**(2026-09-14):每个组合在自己的候选谱上用参考的锁定阈值独立
  选峰 → 该组合自己的完整峰表;`reference_peak_id`/`assignment` 留空,峰与参考
  峰表的匹配由使用者自己的分析完成;
- 精修方式外部指定:`localization="parabolic"`(默认)/ `"gaussian"`(仅 2D)/
  `"both"`(两张峰表都出);
- `run_parameter_study(...)` 仍是一键便利入口(内部 = 参考模式 + 用研究根显式
  调用组合模式),供快速试用;
- 不需要外部峰表;`peaks=<外部峰表>` 只在研究方另有公开库/已指认峰表时才用;
- **窗函数与参数必须成对**:`window.<轴>.type` + `off/end/pow/lb/g1/g2`;
  窗型为 `none` 时写这些子参数会被拒绝(历史上会静默空转);
- 基线:`baseline.<轴>.mode=order` 现在真实渲染 `POLY -ord N -auto`;
  `mode=auto` 渲染 `POLY -auto`;某条件没有变化时软件会报 `no_spectrum_change`。

## 2. 两条件(A/B)同参数

```python
result = run_parameter_study(
    "~/studies/titration",
    datasets={"A": "~/data/titr/apo", "B": "~/data/titr/holo"},
    combos=[{"zero_fill": 2, "window.F1.off": 0.45}],
)
```

同一个 `W0001` 对 A、B 使用**同一份**用户参数,各自输出峰值表:

```text
study/workflows/W0001/A/peak_table_parabolic.csv      # 默认精修方式
study/workflows/W0001/B/peak_table_parabolic.csv
```

(要高斯表显式写 `localization="gaussian"`,或 `"both"` 同时出
`peak_table_gaussian.csv`。)

组合模式的峰表是**该组合自己那张谱**的峰表:`peak_id` 是本谱峰序号,
`reference_peak_id`/`assignment` 留空——把峰匹配回参考峰身份由你的分析程序做。
统计同样由**你自己的分析**读这些表计算。

## 3. 分步用法(需要精细控制时)

```python
from nmrforge_api import (
    add_dataset, build_reference, ensure_reference_peaks, open_study,
    plan_sweep, run_sweep, write_records,
)

session = open_study("~/studies/step_by_step")          # 或名称
add_dataset(session, "~/data/bmr12345/1", condition="A")
reference = build_reference(session)                     # 参考谱 + 参考脚本
reference = ensure_reference_peaks(session, reference)   # 峰身份 + 两张参考峰表

plan = plan_sweep(reference, combos=[{"zero_fill": 1}, {"zero_fill": 2}])
runs = run_sweep(session, plan, reference=reference, localization="both")
records = write_records(session, references={reference.dataset_key: reference},
                        plan=plan, runs=runs)
print(records)
```

## 4. 命令行

```bash
python -m nmrforge_api init      --study ~/studies/s1 --dataset ~/data/a --condition A
python -m nmrforge_api init      --study ~/studies/s1 --dataset ~/data/b --condition B
python -m nmrforge_api reference --study ~/studies/s1
python -m nmrforge_api peaks     --study ~/studies/s1
python -m nmrforge_api sweep     --study ~/studies/s1 --reference ~/studies/s1 \
    --combos design.csv --localization both
python -m nmrforge_api status    --study ~/studies/s1
python -m nmrforge_api report    --study ~/studies/s1
```

## 5. 看什么文件

| 想知道 | 看 |
| --- | --- |
| 每个组合是什么、状态如何 | `study/workflows/<id>/workflow.json` |
| 实际用了哪些参数 | `run.json.parameters_used` + `parameters_resolved` |
| 峰表(使用者分析入口) | `study/workflows/<id>/<条件>/peak_table_*.csv`;长表见 `study/records/peak_table_*.csv` |
| 处理脚本 / 谱 | `study/workflows/<id>/<条件>/process.com` / `spectrum.ft2` |
| 日志 | `study/workflows/<id>/<条件>/log.txt`(完整)+ `workflows/<id>/log.txt` |
| 版本与哈希 | `run.json.versions` / `manifest.json` |
