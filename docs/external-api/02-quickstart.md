# 02 · 快速上手(v0.2)

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
)

print(result.summary["workflow_ids"])      # ['W0001', 'W0002', 'W0003']
print(result.summary["reference_spec"])    # 显式指定的参考
for run in result.runs:
    print(run.workflow_id, run.condition, run.status,
          run.peak_table_path("parabolic"), run.peak_table_path("gaussian"))
```

- 参考模式只建参考(1 个脚本 + 2 张峰表),不跑任何组合;
- 组合模式不生成参考:参数基底取参考的有效参数,组合表只覆盖它显式指定的键;
  参考不存在或峰表缺失 → `ReferenceError`(提示先跑参考模式);
- `run_parameter_study(...)` 仍是一键便利入口(内部 = 参考模式 + 用研究根显式
  调用组合模式),快速试用与向后兼容用;
- 不需要外部峰表;`peaks=<外部峰表>` 只在研究方另有公开库/已指认峰表时才用。

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
study/workflows/W0001/A/peak_table_parabolic.csv
study/workflows/W0001/A/peak_table_gaussian.csv
study/workflows/W0001/B/peak_table_parabolic.csv
study/workflows/W0001/B/peak_table_gaussian.csv
```

两张表用同一个 `reference_peak_id` 标识同一个峰,`detected=false` 表示该条件下
没测到但**保留记录**。CSP/统计由你**自己的分析程序**读这两张表计算。

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
runs = run_sweep(session, plan, reference=reference, window_ppm=0.5)
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
python -m nmrforge_api sweep     --study ~/studies/s1 --combos design.csv
python -m nmrforge_api status    --study ~/studies/s1
python -m nmrforge_api report    --study ~/studies/s1
```

## 5. 看什么文件

| 想知道 | 看 |
| --- | --- |
| 每个组合是什么、状态如何 | `study/workflows/<id>/workflow.json` |
| 实际用了哪些参数 | `run.json.parameters_used` + `parameters_resolved` |
| 峰表(下游分析入口) | `study/workflows/<id>/<条件>/peak_table_*.csv`;长表见 `study/records/peak_table_*.csv` |
| 处理脚本 / 谱 | `study/workflows/<id>/<条件>/process.com` / `spectrum.ft2` |
| 日志 | `study/workflows/<id>/<条件>/log.txt`(完整)+ `workflows/<id>/log.txt` |
| 版本与哈希 | `run.json.versions` / `manifest.json` |
