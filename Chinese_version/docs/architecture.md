# 架构

NMRForge 1.0.5 由 Qt 桌面应用、可复用的数据与处理核心、工作流编排、NMRPipe 后端以及
独立的 Python/CLI API（API 1.1.1）组成。这些组件使用相同的数据和处理能力，但入口、记录
和产物各不相同。

```text
桌面应用：gui/ ──> ProcessingController ──> workflow/ ──> core/ 数据、规划与项目服务
                                             │                    │
                                             └──── backend/ <─────┘
                                                   │
                                                   └── NMRPipe / SMILE

Python/CLI API：nmrforge_api/ ──> core/、workflow/ 与 backend/
谱图查看：viewer/ ──> NMRPipe 谱图文件（.ft1/.ft2/.ft3）
```

此图按职责展示组件关系，并非严格的单向依赖层级：工作流协调 core 的领域操作与 backend
执行，部分 workflow 和 core 操作也会使用 backend 的运行时或配置服务。NMRPipe 专用命令语义
位于 backend 边界及其脚本生成器中。

## 组件与数据流

- `gui/` 包含主窗口、项目树、流水线与数据组面板、导入和脚本对话框、设置及日志。GUI
  通过 `core.project` 访问项目状态，并由 `gui.processing.ProcessingController` 调用处理操作。
- `nmrforge_api/` 提供无 Qt 依赖的 Python 与 CLI 入口，当前 API 版本为 1.1.1。它建立参考
  产物并运行显式参数组合；记录和输出属于 API 会话，不属于当前桌面项目。详见
  [API 契约](API_CONTRACT.md)。
- `workflow/` 实现应用操作，包括 Bruker 导入、导入/FID/谱图分步处理、批处理、人工脚本、
  处理路径、选峰和导出。它组合 `core/` 中的领域对象与处理计划，并调用后端协议。
- `core/` 解析 Bruker 元数据和数据，定义实验与采样模型，识别实验类型、选择处理计划，
  并提供项目、工作区、优化、质控和选峰服务。`read_dataset()` 从采集参数构建 `Experiment`，
  检测采样模式并识别实验类型。项目服务负责项目记录与路径。
- `backend/` 实现 `ProcessingBackend`。工厂当前创建 `NMRPipeBackend`，负责 Bruker 转换、
  均匀采样处理或 NUS 重构，并返回结果路径、有效参数、指标和日志。
- `viewer/` 读取处理后的 NMRPipe 文件并显示 1D、2D 或 3D 谱图。它既可嵌入桌面应用，也可
  通过独立查看器入口运行。
- `nmrforge_data/` 提供随包分发的默认配置、预设及其他运行资源；机器本地路径和设置由配置
  层解析。

桌面流程中，导入操作将 Bruker 输入存入项目数据条目，或为其建立引用，并通过项目服务写入
元数据和运行记录。FID 步骤调用后端转换；谱图步骤根据采样模式选择均匀采样的 `process()`
或 NUS 的 `reconstruct_nus()` 路径，随后将结果登记到该数据条目的 spectra 目录。查看器读取
这些谱图文件进行显示。API 复用较低层的数据和处理服务，但生成自己的研究/会话产物。

NMRPipe 和 SMILE 是外部程序。`backend/runtime.py` 通过 C-shell 子进程运行命令，并将输出
传回进度和运行日志。另见[外部依赖](external-dependencies.md)、[GUI 架构](gui/architecture.md)
和[后端架构](backend/architecture.md)。
