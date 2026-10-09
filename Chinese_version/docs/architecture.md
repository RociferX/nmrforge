# 架构

NMRForge 将桌面界面、处理工作流、外部引擎集成和可复用的数据
操作分开。公开源码树中这些组件属于同一个项目。

    GUI 与 viewer
        |
        v
    workflow 与 nmrforge_api
        |
        v
    core 数据和处理操作
        |
        v
    backend 集成 -> NMRPipe / SMILE

## 职责

- gui/ 和 viewer/ 提供桌面应用与谱图查看功能。

- nmrforge_api/ 提供版本化的 Python 和命令行接口，并记录研究输入、
  输出及处理溯源。

- workflow/ 编排处理路径和用户请求的操作。

- core/ 负责数据处理、实验解释、优化、质量检查及项目记录。

- backend/ 将处理流程接入外部引擎，并生成或运行引擎命令。

- nmrforge_data/ 保存随包分发的配置和预设资源。

界面与处理代码通过明确的数据结构和 API 契约通信。GUI 负责展示；
引擎专用命令由后端边界处理。NMRPipe 和 SMILE 是外部依赖，不属于
Python 包。

## 数据流

core 数据模块读取并解释 Bruker 输入。所选工作流将转换或处理任务
交给 backend，生成谱图文件和运行记录。viewer 读取谱图数据并显示。
脚本 API 按已文档化的参考与参数组合流程运行，将研究产物写入独立目录。

脚本接口见 [API 契约](API_CONTRACT.md)，面向用户的处理路径见
[处理模型](processing-model.md)，引擎要求见[外部依赖](external-dependencies.md)。
