# 常见问题

### nmrForge 会取代 NMRPipe 吗?

不会。nmrForge 决定**该跑什么**、并记录**为什么**,
然后驱动 NMRPipe 去执行。你需要一套 NMRPipe 安装。

### 用 nmrForge 必须装 Python 吗?

用 AppImage 不需要(它自带 Python 与 Qt)。用源码则需要 Python 3.12+ 与可编辑的仓库检出。

### 能在 Windows 或 macOS 上跑吗?

Linux 是目标运行平台，Windows 仅作为编辑环境；macOS 未验证。
当前软件版本为 1.0.5，公开 API 版本为 v1.1.1。Linux AppImage 与源码可从 [1.0.5 Release](https://github.com/RociferX/nmrforge/releases/tag/v1.0.5) 获取；API 参数和 38 列峰表契约见 [Python API](python-api.md)。

### nmrForge 会把我的数据发到什么地方吗?

不会。没有遥测、没有上传、不需要账号。处理全在本地,应用自身不发起网络请求。
源码安装需要网络获取依赖；AppImage 自带运行时。

### 它会改我的原始数据吗?

不会有意去改,也绝不悄悄改。凡是必须调整采集文件头的地方(例如删掉坏点后重算采样网格),
第一次修改前都会把原文件备份为 `.bak`,并且是对副本操作而不是原地修改。
自动坏点**修复**是可选的;检测是无条件的,而且在做任何决定之前,发现会连同索引与指标
一起报出来。在 GUI 里删除项目是把它移进操作系统回收站,而不是抹掉。

### 它支持非均匀采样吗?

支持,2D 与 3D 都支持,用 SMILE。3D NUS 处理可用;3D SMILE 参数优化界面目前是隐藏的,
SMILE 扫描与「按名次重跑」控件只对 2D NUS 开放。

### 为什么批量处理只支持 2D?

因为 3D(尤其是 3D NUS/SMILE)在目标机器上的内存风险尚未解决,
而一个可能把主机拖垮的进程不适合无人值守地跑。非 2D 数据集会被跳过并给出原因,
而不是用一个近似去处理。批量也只施加分组配置,不替每个数据集挑参数,
因此两次输出之间的差异反映的是数据本身的差异。

### 采样判为「uncertain」时它为什么拒绝处理?

因为 uniform 与 NUS 会改变每一个处理参数的含义。在这里猜,会产出一张「看起来处理过、
但其实悄悄错了」的谱。请先解决元数据冲突;日志会说明触发了哪条规则。

### 有英文界面吗?

有。英文是界面的源语言,公开仓那一版的菜单、面板、状态与提示默认就是英文;同一份构建也
带中文界面:跟随系统语言,或用 `NMRFORGE_LANG=zh` 钉死(中文文案在
`ui_support/locales/zh.json`)。一份代码、两种语言;公开仓的中文文档在
`Chinese_version/docs/`。

**也可以直接在 GUI 里改**:`设置 → 软件设置 → 界面语言`,选「跟随系统语言 / 中文 / 英文」,
保存到本地覆盖配置(重启后生效)。解析顺序是「显式设置 → `NMRFORGE_LANG` 环境变量 →
设置里的偏好 → 系统区域(QLocale / `LANG` / `LC_ALL`)→ 本树默认语言」。`NMRFORGE_LANG`
排在设置之前(所以 `NMRFORGE_LANG=zh ./NMRForge.AppImage` 这类临时钉死不会被陈旧设置压掉),
而 `LANG=en_US.UTF-8` 只是系统区域,**不会**盖掉你在设置里选的中文。

### nmrForge 是否包含 HSQC CSP 分析?

当前不提供 HSQC CSP 分析；`nmrforge_api` 可供下游分析代码使用。

### 版本号是怎么管的?

`core/__init__.py` 定义软件 `__version__`。`pyproject.toml` 和AppImage构建读取它，运行记录也会保存它。
脚本API版本由 `nmrforge_api.API_VERSION` 单独管理。

### 怎么引用 nmrForge?

引用元数据见 [CITATION.cff](../../CITATION.cff)：作者李宣锋(Xuanfeng Li)，单位中国科学技术大学
(University of Science and Technology of China)。概念DOI为
[10.5281/zenodo.22909415](https://doi.org/10.5281/zenodo.22909415)。引用时附上所用软件版本；
需要版本专属DOI时，从对应发布的归档记录获取。
如果你发表了结果,请同时引用 NMRPipe 与 SMILE —— 见 [THIRD_PARTY.md](../../THIRD_PARTY.md)。

### 能商用吗?

可以:本项目自己的源码是 Apache-2.0,正文见根目录 [LICENSE](../../LICENSE),版权行与 SPDX 标识在
[NOTICE](../../NOTICE)。打包产物里捆绑的 Qt/PySide6 单独按 LGPL-3.0 授权,义务落在那些库上,不限制你
对本项目源码的使用。源码及打包依赖的许可说明见 [LICENSE_OPTIONS.md](../../LICENSE_OPTIONS.md),第三方组件的许可见
[THIRD_PARTY.md](../../THIRD_PARTY.md)。

### 怎么确认一个已发表的结果能从 nmrForge 复现?

运行记录包含解析后的参数、所用脚本、软件和外部工具版本及告警；生成的脚本和谱图也会保留。
见 [processing-model.md](processing-model.md) 与
[external-api/06-outputs-and-records.md](external-api/06-outputs-and-records.md)。
