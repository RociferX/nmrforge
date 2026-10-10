# nmrForge 文档导航

软件 **1.0.5** · Python/CLI API **1.1.1**。
[项目README](../README.md)介绍功能、工作流、示例和证据。
Linux桌面使用可直接下载[AppImage](https://github.com/RociferX/nmrforge/releases/tag/v1.0.5)，按[安装说明](installation.md)启动；NMRPipe和SMILE另行安装。

## 安装与使用

- [开始使用](getting-started.md)、[安装](installation.md)与[GUI指南](gui.md)。
- [CLI参考](cli.md)与[Python API](python-api.md)。
- [外部依赖](external-dependencies.md)：引擎查找、配置与执行。
- [故障排查](troubleshooting.md)与[常见问题](faq.md)。

## 处理与输出行为

- [处理模型](processing-model.md)：导入、生成FID、uniform/NUS处理与人工路线。
- [质量控制](qc-system.md)：诊断、修正、指标与审计记录。
- [选峰](peak-picking.md)：检测、抛物线定位与峰表导出。
- [批量处理](batch-processing.md)：数据组与2D批量执行。
- [能力与限制](roadmap.md)：支持的操作及其边界。

## 架构与接口

- [总体架构](architecture.md)：组件、依赖与数据流。
- [GUI架构](gui/architecture.md)：控制器、后台任务、信号、日志作用域与读谱。
- [Backend架构](backend/architecture.md)：转换、脚本生成、执行与产物。
- [共享接口与API契约](API_CONTRACT.md)：数据/项目模型、后端协议、谱轴、分步操作与脚本契约。
- [参数研究API指南](external-api/README.md)：函数签名、CLI、参数表、记录、定位、集成与错误。

## 实测证据

[四路径对照](evidence/real-data-comparison.md)展示按当前处理流程得到的谱图、脚本、参数与候选统计，
包括2D uniform、受控人工2D NUS、3D uniform及实采3D NUS。
[BMRB15750脚本对照](evidence/bmrb15750-script-comparison.md)列出作者/自动处理命令。

## 开发与分发

- [开发说明](development.md)：环境、检查、兼容与运行资源。
- [打包说明](packaging.md)：wheel资源、AppImage输入、构建布局与启动。
- [贡献指南](../../CONTRIBUTING.md)与[安全问题报告](../../SECURITY.md)。
- [源码许可](../../LICENSE)、[分发说明](../../LICENSE_OPTIONS.md)、[第三方声明](../../THIRD_PARTY.md)。
- [引用元数据](../../CITATION.cff)。
