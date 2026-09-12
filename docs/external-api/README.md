# NMRForge 参数敏感性接口(`nmrforge_api`)· 对外文档

本目录是**给外部项目/合作者阅读**的接口文档,自成一套,不依赖 NMRForge 的内部
管理文档。你可以把整个 `docs/external-api/` 目录拷给别人。

接口版本:`0.1`(与 `nmrforge_api.API_VERSION` 一致)

## 这个接口解决什么问题

研究「**处理参数组合怎样影响最终谱图峰位置**」——把这种由处理引入的峰位不确定度
当作 **CSP(化学位移扰动)是否真的存在差异的下限指标**。接口提供:

1. 用 NMRForge 的自动优化跑一遍数据,得到并**冻结参考谱 + 参考脚本**;
2. 参考峰位**由软件在参考谱上自动选峰产生**(不需要你准备峰表);
3. 同一份 fid、同一相位,批量跑参数网格(候选谱不替换参考谱);
   支持 **2D uniform 与 2D NUS**(NUS 走 SMILE 重构,可扫 nSigma/thresh 等);
4. 用**亚像素**方法追踪同一批峰的峰位,输出 σ 与 Δδ 下限;
5. 全部结果带哈希与版本,落成可复算的 JSON/CSV。

## 一分钟上手

```python
from nmrforge_api import run_parameter_study

result = run_parameter_study(
    "~/studies/hsqc_params",              # 研究根(可复用、可断点续跑)
    "~/bruker_data/bmrXXXXX/1",           # 公开库下载解压后的 Bruker 目录
    axes={"window.F1.off": [0.35, 0.45], "zero_fill": [1, 2]},
)
print(result.summary["delta_std_ppm"])    # 峰位不确定度 → CSP 判据下限
print(result.records["uncertainty"])      # 逐峰 σ/Δδ 表(CSV)
```

命令行等价:

```bash
python -m nmrforge_api init      --study DIR --dataset BRUKER_DIR
python -m nmrforge_api reference --study DIR
python -m nmrforge_api peaks     --study DIR      # 软件自动选峰
python -m nmrforge_api sweep     --study DIR --grid grid.yaml
```

## 文档导航

| 文档 | 内容 |
| --- | --- |
| [01-overview.md](01-overview.md) | 定位、能/不能做什么、术语表 |
| [02-quickstart.md](02-quickstart.md) | 安装、第一次运行、常见操作(续跑/换数据/换网格) |
| [03-api-reference.md](03-api-reference.md) | 全部公开函数与数据类(签名、参数、返回、异常) |
| [04-cli-reference.md](04-cli-reference.md) | 命令行子命令、参数、网格文件格式、退出码 |
| [05-inputs-and-data.md](05-inputs-and-data.md) | 数据与峰表要求、公开库获取与校验、参数轴命名 |
| [06-outputs-and-records.md](06-outputs-and-records.md) | 目录布局与每个文件的字段定义、复算方法 |
| [07-methods-and-metrics.md](07-methods-and-metrics.md) | 峰位测量算法、不确定度定义、假设与边界 |
| [08-integration-guide.md](08-integration-guide.md) | 接到自建 pipeline(含最小侵入接法) |
| [09-limitations-and-roadmap.md](09-limitations-and-roadmap.md) | v0.1 边界与扩展路径 |
| [10-troubleshooting.md](10-troubleshooting.md) | 报错与排查、性能预期 |
| [examples/](examples/README.md) | 可直接运行的最小示例 |
| [CHANGELOG.md](CHANGELOG.md) | 接口自身的版本变更 |

## 设计背景(可选)

接口的内部设计记录(NMRForge 仓库内):`docs/proposals/external-api/`。
外部使用者只需要本目录。

## 版本与兼容

- 接口版本 `0.1`:**新增参数轴、增加记录字段**等向后兼容的改动会保持 `0.1`;
- **函数签名/返回结构/文件名的破坏性改动**会升到 `0.2+`,并在
  [CHANGELOG.md](CHANGELOG.md) 注明迁移方式;
- 每条记录里都带 `nmrforge_version` 与 `tool_versions`,引用结果时请一并给出。
