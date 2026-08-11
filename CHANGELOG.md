# 变更日志

## [0.1.2] - 2026-08-11

- Phase 1（处理与 QC）：numpy 原生处理原语（apodization/ZF/FT/phase/baseline/calibration/transpose/sign）。
- QC 指标实现：robust MAD 噪声、峰检测、SNR、相位/基线质量、孤立峰簇伪影、综合质量评分（ACCEPT/WARNING/ROLLBACK）。
- DAG 拓扑排序 + 缓存命中 + 失败隔离的 PipelineRunner；默认处理计划（逐维 SP→ZF→FT→PS）。
- AutoProcessor.process_matrix 最小闭环（uniform 2D/3D 合成矩阵）；新增 21 项测试。

## [0.1.1] - 2026-08-11

- Phase 1（数据理解）：实现 Bruker 参数解析（acqus/acqu2s/acqu3s，跨行数组、引号/尖括号剥离）。
- 实现 NUS 检测、采集模式检测（FnMODE）、PULPROG 多证据基础分类与采集/处理/显示轴映射。
- 新增 Bruker fixture（HSQC/NUS-HSQC/HNCA/unknown）与 13 项数据理解测试。

## [0.1.0] - 2026-08-11

- 初始化项目工作树（依据《自动化 NMR 2D/3D 数据处理与优化软件：完整技术框架》重建）。
- 建立 core/ 八大子模块：experiment / data / planning / processing / optimization / qc / experiments / reporting。
- 建立 backend/（ProcessingBackend 协议 + NMRPipe/Native 占位）、workflow/（AutoProcessor 编排占位）、gui/（面板占位）。
- 建立 presets/（实验模板 YAML）、config/、scripts/、docs/、tests/。
- 开发流程：pyproject（pytest/ruff 配置）、master 分支 git 仓库、README / PROJECT_STATE / CHANGELOG。
- 正式定名 NMRForge；规划 AppImage 打包（packaging/linux/ + docs/packaging.md）。
