# 外部依赖

nmrForge 自动化 NMRPipe。它不包含、不下载、也不安装 NMRPipe。本页说明哪些东西是外部的、
它们是怎么被找到的,以及缺失时会发生什么。

## 需要你自己安装的东西

| 工具 | 用途 | 安装来源 |
| --- | --- | --- |
| NMRPipe | 转换(Bruker 转 NMRPipe 格式)、傅里叶变换、相位校正、基线校正、窗函数、填零 | 上游 NMRPipe 发行包(见其自身文档) |
| SMILE | NUS 重构 | 随 NMRPipe 提供 |
| `bruker`(NMRPipe 附件) | Bruker 转 NMRPipe 的转换脚本 | 随 NMRPipe 提供 |
| Java 运行时 | 视安装方式而定,部分 NMRPipe 附件工具需要 | 你的操作系统包管理器,或某个 OpenJDK 发行版 |

这些软件包里的任何内容都不会被复制进本仓库,也没有任何来自 NMRPipe 安装的脚本被收进
`scripts/` 或 `nmrforge_data/presets/`。见 [THIRD_PARTY.md](../../THIRD_PARTY.md)。

## nmrForge 如何找到它们

`backend/nmrpipe_finder.py` 按这个顺序查找:

1. 配置里显式给出的路径(`backend.nmrpipe.path`)—— 一个目录或一个可执行文件;
2. `backend.nmrpipe.nmrpipe_bin`;
3. `PATH`;
4. `csh` 环境,也就是终端 source 过 NMRPipe 环境文件之后得到的环境;
5. 常见安装位置。

默认配置在 `nmrforge_data/config/nmrforge.yaml`。要做机器本地覆盖,就把它复制成
`nmrforge_data/config/nmrforge.local.yaml`;该文件被 git 忽略,正是为了让机器路径永远不会进仓库。

```yaml
backend:
  provider: nmrpipe
  nmrpipe:
    nmrpipe_bin: ""      # 留空表示通过 PATH / csh / 常见位置自动探测
    path: ""             # 显式的 bin 目录或可执行文件;设置后优先
```

## 缺少工具时如何报告

- 只有在某个步骤真正需要时才去定位 NMRPipe。如果找不到,报出的是面向用户的提示
  (例如「NMRPipe executable was not found」),而不是一个导入错误。
- 版本探测尽力而为:后端只有在真正定位到 NMRPipe/SMILE 之后,才把版本写进运行记录;
  否则该字段留空,而不是写一个编造的值。见 `core/version.py`。
- `examples/quickstart.py` 在做任何事情之前,先打印是否找到了 NMRPipe 与 SMILE。

## 没有 NMRPipe 时哪些可用

| 能力 | 无 NMRPipe 时 |
| --- | --- |
| Bruker 参数解析 | 可用 |
| 维度数与维度布局 | 可用 |
| 实验类型判定 | 可用 |
| 采样判定(uniform / NUS / uncertain) | 可用 |
| 读取时域数据 | 可用 |
| 处理(转换、FT、相位、基线、窗函数、填零) | 不可用 |
| SMILE 重构 | 不可用 |
| 谱图质量指标 | 不可用(它们需要一张处理过的谱) |
| 在谱图上选峰 | 不可用 |
| 峰表解析/导出 | 可用(文件级操作) |

这条边界是有意为之:结果永远不会由某个替代引擎悄悄产出。

## 资源说明

- NUS 重构的内存主要由直接维尺寸乘以迭代间接 FT 网格决定,因此填零会让它迅速膨胀。
  nmrForge 在运行 SMILE 之前先估算峰值,并传入显式的 `-maxMem`,
  对预计会超过可用内存的重构直接拒绝启动。
- 中间谱图可以放到 RAM 盘(`processing.memory_disk_path`、`processing.intermediate_memory: auto`)
  以减少 I/O;在 Windows 上这需要你自己创建 RAM 盘,否则该设置会退回磁盘。
- SMILE 的线程数是 `smile.nthread`(默认 2,并夹在「CPU 数减二」以内)。

## 版本兼容性

后端会把它用过的 NMRPipe 与 SMILE 版本写进每一次运行记录,因此日后复现时能证明结果由
哪套引擎产生。如果你在「处理参考谱」与「处理由它派生出的参数组合」之间换了 NMRPipe 版本,
请重建参考谱:宏语义(因而脚本的含义)可能随版本不同。
