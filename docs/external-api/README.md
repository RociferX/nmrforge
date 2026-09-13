# nmrforge_api 对外文档(v0.2)

`nmrforge_api` 是 NMRForge 的**参数组合处理执行器**:输入原始 NMR 数据与用户
参数组合表,自动生成参考工作流,按组合批量运行处理,对同一张谱用 parabolic 与
2D gaussian 两种算法各出一张峰表,并留下完整 provenance 与 QC。

软件只负责执行处理与留档;**CSP、robustness、统计与显著性判断由下游独立分析
程序完成**(σ/Δδ 汇总只作**测试/检测辅助**、不进入处理产物,见
`docs/tasks/archive/2026-09-13-csp-statistics-boundary.md`)。

## 阅读顺序

| 文档 | 内容 |
| --- | --- |
| [01-overview.md](01-overview.md) | 定位、术语、运行语义、软件边界 |
| [02-quickstart.md](02-quickstart.md) | 一步式 / 分步 / 两条件 A/B / CLI 上手 |
| [03-api-reference.md](03-api-reference.md) | 全部公开函数与数据结构 |
| [04-cli-reference.md](04-cli-reference.md) | `python -m nmrforge_api` 六个命令 |
| [05-inputs-and-data.md](05-inputs-and-data.md) | 数据、条件、参数键、组合表 |
| [06-outputs-and-records.md](06-outputs-and-records.md) | 目录布局、统一峰表字段、状态与警告码 |
| [07-methods-and-metrics.md](07-methods-and-metrics.md) | 参考工作流、两种定位、窗口口径、QC |
| [08-integration-guide.md](08-integration-guide.md) | 向下游分析程序交接(读什么、怎么读) |
| [09-limitations-and-roadmap.md](09-limitations-and-roadmap.md) | 支持矩阵、NUS 边界、分片、roadmap |
| [10-troubleshooting.md](10-troubleshooting.md) | 常见错误、warning 处理、断点续跑 |
| [examples/](examples/) | 可运行示例(一步式/分步/只测量) |
| [CHANGELOG.md](CHANGELOG.md) | 版本变更 |

规范来源与符合性台账:

- 用户规范(2026-09-13):`docs/reviews/2026-09-13-api-spec-compliance.md`
  (逐条要求 → 实现 → 判定 → 处置);
- 契约:API_CONTRACT §11(v0.2);
- 设计记录:docs/proposals/external-api/001-parameter-sweep-api.md。

## 安装与运行

无需额外依赖:用 NMRForge 自身环境即可(`numpy`/`scipy`/`nmrglue`;
真机处理需要 NMRPipe)。命令行入口:

```bash
python -m nmrforge_api --help
```

## 一分钟示例

```python
from nmrforge_api import run_parameter_study

result = run_parameter_study(
    "~/studies/hsqc_params",
    datasets={"A": "~/data/apo", "B": "~/data/holo"},
    combos=[{"zero_fill": 1}, {"zero_fill": 2}],
)
print(result.summary["status_counts"])
print(result.records["peak_table_parabolic"])
```
