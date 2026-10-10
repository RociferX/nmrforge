# nmrforge_api 使用指南（API 1.1.1）

`nmrforge_api`是软件**1.0.5**中不依赖Qt的Python/CLI接口，导入Bruker数据，逐条件建立并冻结独立参考谱/峰表，
运行显式参数组合，逐谱独立检测并写入三点抛物线峰表、来源及QC记录。

条件默认使用单个原始目录，`segmented=True`允许至少两个目录的有序列表。
参考构建导入/转换/合并源，组合复用冻结FID、不回退到转换或合并。
证据缺失/改变时需用`force=True`（CLI `reference --force`）重建参考。

统一峰表**38列**，保留完整F1/F2/F3坐标、核名与等效线宽。
峰ID属于单张谱，组合`reference_peak_id`留空；跨谱匹配、指认及统计推断由下游实现。
`compat_manifest()`保存代码/契约指纹及受影响步骤。

## 检查与证据

模拟引擎测试检查工作流与记录行为，真实NMRPipe/SMILE对照在[证据报告](../evidence/real-data-comparison.md)保存输入、命令/参数来源与终谱。
检出/定位QC与候选匹配描述对应测量，不建立已指认峰身份。详见[支持范围与执行边界](09-limitations-and-roadmap.md)。

## 阅读顺序

| 文档 | 内容 |
| --- | --- |
| [概览](01-overview.md) | 术语、运行语义与边界 |
| [快速上手](02-quickstart.md) | 一步式、分步、多条件及CLI示例 |
| [API参考](03-api-reference.md) | 公开签名与返回结构 |
| [CLI参考](04-cli-reference.md) | 命令与选项 |
| [输入与数据](05-inputs-and-data.md) | 条件、多段导入、参数键与组合表 |
| [输出与记录](06-outputs-and-records.md) | 布局、38列、状态、warnings与来源 |
| [方法与指标](07-methods-and-metrics.md) | 参考处理、定位、阈值、符号与QC |
| [集成](08-integration-guide.md) | 读取产物与下游接口 |
| [支持范围与执行边界](09-limitations-and-roadmap.md) | NUS、参数校验、续跑与FID复用 |
| [故障排查](10-troubleshooting.md) | 错误、warnings与恢复 |
| [示例](examples/) | 可执行API走查 |

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
