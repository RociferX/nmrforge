# 开发指南

本页介绍公开源码树的开发方式。Linux 是目标运行平台，项目要求
Python 3.12 或更高版本。处理行为和受支持参数以实现及公开参考
文档为准。

## 配置源码环境

    git clone https://github.com/RociferX/nmrforge.git
    cd nmrforge
    python -m venv .venv
    source .venv/bin/activate
    python -m pip install -e ".[dev]"

使用 python main.py 启动桌面应用。test 依赖组安装 pytest；dev 组
还会安装 Ruff。打包安装和运行资源说明见[安装指南](installation.md)。

NMRPipe 与 SMILE 是单独安装的外部工具，不随源码打包。数据转换和
处理需要 NMRPipe；NUS 重构需要 SMILE。工具发现方式和能力边界见
[外部依赖](external-dependencies.md)。

## 验证改动

先运行相关测试。可单独选择配置的 unit、integration 和 regression
测试组；再在 Linux 上运行完整测试与静态检查：

    python -m pytest -m unit
    python -m pytest -m integration
    python -m pytest -m regression
    python -m pytest -q
    python -m ruff check .

修改处理路径时，还应运行完整路径回归：

    python -m pytest tests/test_full_paths.py

需要显示器的测试可设置 QT_QPA_PLATFORM=offscreen。真实引擎检查需要
安装相应工具，并应与 mock 测试分开报告。测试通过不代表科学结论
已得到验证。

## 贡献与兼容性参考

保持改动聚焦；行为或输出变化时同步更新面向用户的文档。公开 API
变化应同步更新实现、测试、API 参考和兼容性元数据。

- [贡献指南](../../CONTRIBUTING.md)说明评审要求。

- [API 契约](API_CONTRACT.md#11-public-nmrforge_api-contract)概述
  版本化脚本接口边界。

- [外部 API 指南](external-api/README.md)链接到函数、CLI、输入和
  输出参考。

- [总体架构](architecture.md)说明当前组件职责。

不要公开机器专用路径、凭据、私人数据集或内部项目记录。
