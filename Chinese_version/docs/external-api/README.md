# nmrforge_api 对外文档(API 契约 1.1 · 2026-10-03)

> `nmrforge_api` 是公开、版本化且不依赖 Qt 的脚本接口(`API_VERSION = "1.1"`,2026-10-03 当前源码契约)。
> 跨版本比较数值前，请查看 `compat_manifest()` 中的行为指纹、兼容级别与受影响步骤。
> 已发布 AppImage 对应特定源码版本；更新源码不会自动更新它。


`nmrforge_api` 用于运行参数组合处理研究。它导入 Bruker 数据、建立并冻结参考工作流、
执行用户提供的参数组合，并对每张候选谱写出一份三点抛物线峰表及处理溯源和 QC 记录。

软件只负责执行处理与留档;**统计推断与科学结论不在本软件范围内**,由使用者自己的分析
完成(σ/Δδ 汇总只作**测试/检测辅助**、不进入处理产物)。

## 验证边界:工程回归 vs 科学验证

引用本软件产物时,请把两件事分开写:

- **工程回归**(常规 CI 与使用模拟引擎边界的测试)证明的是「已覆盖的软件路径与留档
  自洽」;它不调用 NMRPipe/SMILE,也不等同于真实引擎或科学验证;
- **科学验证**(处理结果在真实体系上是否科学正确)不在本软件范围内:工程回归与真机
  冒烟**不能**说明这一点,是否成立由使用者自己的分析决定。

支持的处理路径与验证边界见[限制与路线图](09-limitations-and-roadmap.md)。

当前源码1.0.4/API v1.1，旧 AppImage 仍1.0.2。API 多段导入默认关闭；开启
`segmented=True` 后传完整有序原始目录列表，同一列表组成一个条件。参考阶段生成 FID，
组合只读复用参考 FID；失效或旧参考缺冻结证据时要求 `force=True` 重建参考，不自动转换兜底。
详见[输入规则](05-inputs-and-data.md)与[FID 复用边界](09-limitations-and-roadmap.md#99-fid-复用边界)。

## 阅读顺序

| 文档 | 内容 |
| --- | --- |
| [01-overview.md](01-overview.md) | 定位、术语、运行语义、软件边界 |
| [02-quickstart.md](02-quickstart.md) | 一步式 / 分步 / 两条件 A/B / CLI 上手 |
| [03-api-reference.md](03-api-reference.md) | 全部公开函数与数据结构 |
| [04-cli-reference.md](04-cli-reference.md) | `python -m nmrforge_api` 六个命令 |
| [05-inputs-and-data.md](05-inputs-and-data.md) | 数据、条件、参数键、组合表 |
| [06-outputs-and-records.md](06-outputs-and-records.md) | 目录布局、统一峰表字段、状态与警告码 |
| [07-methods-and-metrics.md](07-methods-and-metrics.md) | 参考工作流、抛物线定位、选峰阈值/边距口径、QC |
| [08-integration-guide.md](08-integration-guide.md) | 读产物做自己的分析(读什么、怎么读) |
| [09-limitations-and-roadmap.md](09-limitations-and-roadmap.md) | 支持矩阵、NUS 边界、分片、roadmap |
| [10-troubleshooting.md](10-troubleshooting.md) | 常见错误、warning 处理、断点续跑 |
| [examples/](examples/) | 可运行示例(一步式/分步/只测量) |

契约版本:`API_VERSION = "1.1"`；公开入口和输出字段见本组文档。旧 AppImage 1.0.2 不包含 v1.1 API 契约。

## 安装与运行

无需额外依赖:用 NMRForge 自身环境即可(`numpy`/`scipy`/`nmrglue`;
真机处理需要 NMRPipe)。命令行入口:

```bash
python -m nmrforge_api --help
```

## 一分钟示例

```python
from nmrforge_api import run_reference_study, run_combination_study

# 参考模式:参考谱 + 脚本 + 一张抛物线参考峰表(选峰阈值在这里定,之后锁定)
run_reference_study(
    "~/studies/hsqc_params",
    datasets={"A": "~/data/apo", "B": "~/data/holo"},
    sigma_multiplier=25,
)

# 组合模式:显式指定参考(必填),按组合表跑处理
result = run_combination_study(
    "~/studies/hsqc_params",
    combos=[{"zero_fill": 1}, {"zero_fill": 2}],
)
print(result.summary["status_counts"])
print(result.records["peak_table_parabolic"])
```
