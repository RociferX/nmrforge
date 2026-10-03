# Python API

当前公开脚本契约为 `API_VERSION = "1.1"`；它独立于软件版本 1.0.2。
已发布的 AppImage 不随源码更新，请用包含 API v1.1 的源码安装。

项目里有两套 Python 接口,稳定性承诺不同。

| 接口 | 导入 | 稳定性 |
| --- | --- | --- |
| 项目/处理层 | `core`、`backend`、`workflow` | 内部接口。GUI 使用它;可能随版本变化。 |
| 脚本化 API | `nmrforge_api` | 公开接口。有文档、按 API 契约定版本、且不依赖 Qt。 |

如果你要围绕 nmrForge 写分析代码,请用 `nmrforge_api`。

## 一个例子看懂脚本化 API

```python
import csv
from pathlib import Path
from nmrforge_api import run_combination_study, run_reference_study

root = "~/studies/hsqc_params"

# 1) 自动优化并生成参考谱、参考峰表
run_reference_study(
    root,
    datasets={"A": "~/data/bmr12345/1"},
    sigma_multiplier=25,
)

# 2) 修改参数并生成新的候选谱、峰表，供下游分析读取
result = run_combination_study(
    root,
    combos=[{"zero_fill": 1}, {"zero_fill": 2}],
)
print(result.summary["status_counts"])

peak_table = Path(root).expanduser() / "study/records/peak_table_parabolic.csv"
with peak_table.open(encoding="utf-8") as fh:
    rows = list(csv.DictReader(fh))
print(rows[:3])
```

API 的范围是这两步处理和产物交接：生成参考谱/参考峰表，再按参数生成新谱/新峰表。
API 不建立不同谱或峰表之间的峰对应关系；组合峰表中的 `peak_id` 是该谱自己的序号，
`reference_peak_id` 留空，跨谱匹配应在下游分析中完成。每个组合会留下自己的脚本、
候选谱(不替换当前生效的谱)、峰位与告警，以及 `manifest.json`、`runs.json` 和
统一峰表 `peak_table_parabolic.csv`。峰定位使用三点抛物线；当前统一峰表共 38 列，
保存 F1/F2/F3 逻辑轴坐标、核名与等效线宽；H/N 仅作为唯一核素轴的兼容别名。
`localization_requested` 记录请求、`localization_method` 记录实际 none/parabolic，
`failure_reason` 与回退分开；不可表达的独占 cell 几何写 NaN。
参考缓存按完整规范化输入 fingerprint 复用，不同或旧指纹缺失要求显式 `force=True`。
旧 36 列峰表及冻结协议迁移见[输出契约](external-api/06-outputs-and-records.md)。

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
from nmrforge_api import SweepError, run_parameter_study

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
