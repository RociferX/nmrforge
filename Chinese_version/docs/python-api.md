# Python API

> **Track B:仍在演进。** 这套接口还在改动:名称、默认值、写出的记录都可能随版本变化。
> 请固定提交号,并在把一批数字当作可比之前核对 `nmrforge_api.compat_manifest()`
> (`behavior_digest` / `compat_level` / `affected`)。图形界面(Track A)是成熟路径。

项目里有两套 Python 接口,稳定性承诺不同。


| 接口 | 导入 | 稳定性 |

| --- | --- | --- |

| 项目/处理层 | `core`、`backend`、`workflow` | 内部接口。GUI 使用它;可能随版本变化。 |

| 脚本化 API | `nmrforge_api` | 公开接口。有文档、按 API 契约定版本、且不依赖 Qt。 |



如果你要围绕 nmrForge 写分析代码,请用 `nmrforge_api`。



## 一个例子看懂脚本化 API



```python

from nmrforge_api import (

    position_uncertainty,

    run_parameter_study,

    uncertainty_summary,

)



result = run_parameter_study(

    "~/studies/hsqc_params",        # 研究根(可复用、可断点续跑)

    "~/data/bmr12345/1",            # 解压后的 Bruker 数据集目录

    axes={"zero_fill": [1, 2, 4], "window.F1.off": [0.35, 0.45, 0.55]},

)

print(result.summary["status_counts"])       # 这次运行做了什么(执行摘要)



# 统计量是**独立**的一步:把一个 workflow 各次运行里的同一个峰配起来,

# 得到峰位不确定度(CSP 判据下限)。

# ``StudyResult.summary`` 只报执行结果,不含统计量。

by_run = {run.workflow_id: run.measurements for run in result.runs}

print(uncertainty_summary(position_uncertainty(by_run))["delta_std_ppm"])

```



这次调用做了什么:冻结一份参考谱、参考脚本与参考峰表(除非你自己给峰,否则自动优化),

运行每一个参数组合,并用选定的方法在每一张候选谱上定位同一批峰。每个组合都留下自己的脚本、

候选谱(当前生效的谱不会被替换)、峰位与告警;研究目录里累积

`manifest.json`、`runs.json`、`peak_positions.csv` 与 `uncertainty.csv`。



## 不依赖 Qt 就能导入



```python

import nmrforge_api          # 不会导入 Qt

```



脚本化 API 从不导入 Qt 绑定(GUI 层通过 `qtcompat` 使用 PySide6)。已核实的行为:



```bash

python -c "import sys, core, nmrforge_api; print([m for m in sys.modules if m.startswith('PyQt')])"

# -> []

```



这正是它能在无头集群节点上使用的原因。



## 更底层的构件



如果你需要的是零件而不是一整套研究:



```python

from core.data.bruker_reader import read_dataset, read_data

from core.experiment.sampling_detector import full_sampling_evidence

from core.qc import ...          # 质量指标

from core.peaks.localize import ...   # 峰定位

from workflow.direct_diagnostics import run_fid_diagnostics_paths

```



`core.data.bruker_reader.read_dataset` 是数据理解的入口:它解析 Bruker 参数、判定维度、

构建维度列表、判定实验类型与采样方式,全程不需要 NMRPipe。



这些模块属于内部层:写脚本用它们足够稳,但它们不承诺跨版本兼容。

`examples/quickstart.py` 里的可运行示例展示了受支持的用法。



## 错误处理



脚本化 API 抛出 `nmrforge_api.errors` 里的类型,而不是把处理路径深处的

`KeyError`/`TypeError` 漏出来,因此调用方能区分「你的网格写错了」和「引擎失败了」:



```python

from nmrforge_api import SweepError



try:

    run_parameter_study(study, dataset, axes={"window.F1.off": [0.35]})

except SweepError as exc:

    print(f"the sweep request is not valid: {exc}")

```



## 文档



- [external-api/README.md](external-api/README.md) —— 总览

- [external-api/02-quickstart.md](external-api/02-quickstart.md) —— 快速上手

- [external-api/03-api-reference.md](external-api/03-api-reference.md) —— 完整 API 参考

- [external-api/05-inputs-and-data.md](external-api/05-inputs-and-data.md) —— 参数轴

- [external-api/09-limitations-and-roadmap.md](external-api/09-limitations-and-roadmap.md) —— 边界
