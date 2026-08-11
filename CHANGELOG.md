# 变更日志

## [0.1.0] - 2026-08-11

- 初始化项目工作树（依据《自动化 NMR 2D/3D 数据处理与优化软件：完整技术框架》重建）。
- 建立 core/ 八大子模块：experiment / data / planning / processing / optimization / qc / experiments / reporting。
- 建立 backend/（ProcessingBackend 协议 + NMRPipe/Native 占位）、workflow/（AutoProcessor 编排占位）、gui/（面板占位）。
- 建立 presets/（实验模板 YAML）、config/、scripts/、docs/、tests/。
- 开发流程：pyproject（pytest/ruff 配置）、master 分支 git 仓库、README / PROJECT_STATE / CHANGELOG。
- 正式定名 NMRForge；规划 AppImage 打包（packaging/linux/ + docs/packaging.md）。
