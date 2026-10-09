# nmrForge

![nmrForge](../gui/assets/nmrforge.png)

nmrForge通过NMRPipe自动处理Bruker多维NMR数据，提供参数优化、质量控制、桌面界面、命令行及Python API，并记录实际采用的参数和输出。

当前源码版本为 **1.0.4**。支持 2D、3D uniform 数据及 2D、3D NUS 处理；批量处理目前仅支持 2D。当前可用的 Linux AppImage 为较早的 **1.0.2**。源码 API 契约为 **1.1**；API 版本与软件版本分别管理。

## 在 Linux 上安装和启动

需要 Python 3.12 或更高版本。在源码目录中运行：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install .
python main.py
```

真实数据处理需要另行安装 **NMRPipe**，并确保 nmrForge 能找到其程序。NUS 重构还需要随 NMRPipe 提供的 **SMILE**。nmrForge 不会捆绑或安装这两个工具。桌面程序需要图形桌面会话。

## API 快速入口

安装后可查看命令行 API：

```bash
python -m nmrforge_api --help
```

Python 与 CLI 示例见 [API 指南](docs/external-api/README.md)；各入口文档见[文档索引](docs/README.md)。[examples/quickstart.py](../examples/quickstart.py) 提供简短走查，[examples/make_synthetic_dataset.py](../examples/make_synthetic_dataset.py) 可生成小型示例数据。

## 实测证据

四组实测见[完整报告](docs/evidence/real-data-comparison.md)：

- BMRB 27493：2D uniform
- 受控 2D NUS：目标 75%，68/90 个复增量（实际 75.56%）
- BMRB 15750：3D uniform
- BMRB 52533：实采 3D NUS，25%

四组主要信号与注明来源的参考对应良好，支持常规处理的良好可靠性。候选匹配不等于已指认真峰回收率，适用范围与限制见完整报告。

## 文档与项目信息

- [文档索引](docs/README.md) · [GUI 指南](docs/gui.md) · [安装详情](docs/installation.md)
- [发布说明与下载](https://github.com/RociferX/nmrforge/releases)
- 引用信息见 [CITATION.cff](../CITATION.cff)；项目概念 DOI：[10.5281/zenodo.22909415](https://doi.org/10.5281/zenodo.22909415)
- 源码许可：[Apache-2.0](../LICENSE)；版权声明：[NOTICE](../NOTICE)；[第三方声明](../THIRD_PARTY.md)与[许可说明](../LICENSE_OPTIONS.md)（捆绑的 Qt/PySide6 库采用 LGPL-3.0）
- [贡献指南](../CONTRIBUTING.md) · [安全问题报告](../SECURITY.md)

桌面界面默认跟随系统语言。单次运行可设置 `NMRFORGE_LANG=zh` 使用中文。
