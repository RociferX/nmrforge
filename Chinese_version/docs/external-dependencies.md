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

NMRPipe 主程序由 `find_nmrpipe_bin` 查找,顺序如下:

1. 显式配置 `backend.nmrpipe.path`;
2. 兼容别名 `backend.nmrpipe.nmrpipe_bin`;
3. 未设置显式路径时,启动用户的 `csh`/`tcsh`,执行 `source ~/.cshrc` 后用 `which` 查询;
4. `PATH`。

两个配置项都按显式路径处理,可以指向安装目录或可执行文件。若已设置的路径无效,会报告错误,
不会静默忽略并尝试后续来源。自动发现不扫描固定的常见安装目录。

其它 NMRPipe companion 工具优先使用已配置且有效的单工具路径；否则先在 `csh` 环境中查找，
再从已发现的安装目录及其父目录下的 `com/` 子目录查找。

默认配置在 `nmrforge_data/config/nmrforge.yaml`。要做机器本地覆盖,就把它复制成
`nmrforge_data/config/nmrforge.local.yaml`;该文件被 git 忽略,正是为了让机器路径永远不会进仓库。

```yaml
backend:
  provider: nmrpipe
  nmrpipe:
    nmrpipe_bin: ""      # 兼容的显式路径别名
    path: ""             # 主显式路径；优先于 nmrpipe_bin
```

## 缺少工具时如何报告

- GUI 启动时会探测外部工具；处理步骤也会在需要时检查能力。缺少 NMRPipe 时给出面向用户的
  提示(例如「NMRPipe executable was not found」),而不是导入错误。
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
| 生成新的处理谱(转换、FT、相位、基线、窗函数、填零) | 不可用 |
| SMILE 重构 | 不可用 |
| 已有处理谱的纯 Python QC 与选峰 | 可用(无需 NMRPipe) |
| 峰表解析/导出 | 可用(文件级操作) |

这条边界是有意为之:结果永远不会由某个替代引擎悄悄产出。

## 资源说明

- NUS 重构的内存主要由直接维尺寸乘以迭代间接 FT 网格决定,因此填零会让它迅速膨胀。
  nmrForge 在运行 SMILE 之前先估算峰值,并传入显式的 `-maxMem`,
  对预计会超过可用内存的重构直接拒绝启动。
- Linux 是目标运行平台；不要将 RAM 盘或其它 Windows 专用资源配置当作受支持的运行建议。
- SMILE 的线程数是 `smile.nthread`(默认 2,并夹在「CPU 数减二」以内)。

## 版本兼容性

后端会把它用过的 NMRPipe 与 SMILE 版本写进每一次运行记录,因此日后复现时能证明结果由
哪套引擎产生。如果你在「处理参考谱」与「处理由它派生出的参数组合」之间换了 NMRPipe 版本,
请重建参考谱:宏语义(因而脚本的含义)可能随版本不同。
