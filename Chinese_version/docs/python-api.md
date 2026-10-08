# Python API

当前公开脚本契约为 `API_VERSION = "1.1"`；当前源码软件版本为 1.0.4。
已发布的 AppImage 仍为 1.0.2，不随源码更新；请用包含 API v1.1 的源码安装。

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

如果一个条件由多个 Bruker 原始目录组成，可显式传完整有序段列表：

```python
run_reference_study(
    root,
    dataset=["~/data/segment1", "~/data/segment2"],
    segmented=True,
)
```

多条件时使用 `datasets={"A": ["~/data/a1", "~/data/a2"],
"B": ["~/data/b1", "~/data/b2"]}`；顶层
开启时 `datasets=[s1, s2]` 是一个条件。默认仍为单目录输入。分段导入必须至少两个目录，
严格检查参数、采集布局、频率与载频兼容；段源顺序参与复用比较。详见[完整 API 参考](external-api/03-api-reference.md)
和[输入与数据](external-api/05-inputs-and-data.md)。

API 的范围是这两步处理和产物交接：生成参考谱/参考峰表，再按参数生成新谱/新峰表。
参考阶段负责原始数据导入、转换和多段合并；组合模式只处理参考工作目录里已有的 FID，
再进行独立选峰。组合复用支持单文件 FID、3D uniform 切片目录和多段合并 FID。
若 FID 缺失/损坏、输入或转换证据与参考不一致，或请求参数需要重新转换/合并，组合会报错，
要求以 `force=True`（CLI `reference --force`）重建参考。组合不会自动重转、清理源数据，
也不会修改参考 FID 或采样表。GUI 默认转换逻辑不受此 API 契约影响。Linux 工程回归已通过，
不代表真实 NMRPipe/SMILE 引擎已完成验证。运行前请保留参考工作目录和原始输入；旧参考
缺冻结 FID 留档时，需要显式 `force=True` 重建一次。
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
