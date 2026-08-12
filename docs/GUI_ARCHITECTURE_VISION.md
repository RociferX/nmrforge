# NMR 数据处理软件 GUI 与交互架构说明

## 1. 文档目的

本文档用于指导 Codex 对现有 NMR 数据处理软件进行 GUI 架构整理、重构和功能整合。

目标不是简单增加 GUI 控件，而是建立一个：

- 简洁
- 清晰
- 流程化
- 状态驱动
- 以数据为核心
- 以 Pipeline 为核心处理流程
- 尽量减少窗口和重复操作

的科研数据处理软件界面。

在修改现有项目之前，必须先阅读并理解现有项目结构、模块职责、数据流和已有谱图查看器，不要为了实现本文档而重复实现已有功能。

---

# 2. 核心设计思想

整个软件采用：

> 左侧选择对象 → 中间显示当前对象可执行的功能/Pipeline → 右侧显示对应谱图或结果

三个区域围绕同一个当前上下文工作。

核心关系：

```text
Project
  ↓
Experiment
  ↓
Data
  ↓
Pipeline
  ↓
Processing Step
  ↓
Output
```

GUI 不应该只是传统的“文件浏览器 + 一堆按钮”，而应该是对上述数据和处理状态的可视化。

用户任何时候都应该能够回答：

1. 我现在在哪个项目？
2. 我现在在哪个实验？
3. 我当前处理的是哪组数据？
4. 当前数据已经完成了哪些步骤？
5. 下一步可以做什么？
6. 当前结果对应哪个文件？
7. 如果处理失败，为什么失败？

---

# 3. 总体 GUI 布局

采用三栏主界面：

```text
┌──────────────────────────────────────────────────────────────┐
│ 文件  项目  处理  查看  工具  窗口  设置  帮助               │
├──────────────┬──────────────────────────┬────────────────────┤
│              │                          │                    │
│  项目管理    │       Pipeline / 功能区   │     谱图查看器      │
│              │                          │                    │
│  Project     │ ① 导入数据               │                    │
│   └Experiment│      ↓                   │    Spectrum        │
│      └Data   │ ② 生成 FID               │      Viewer        │
│        ├Input│      ↓                   │                    │
│        ├Proc │ ③ 数据处理               │                    │
│        └Output      ↓                   │                    │
│              │ ④ 后处理                 │                    │
│              │                          │                    │
├──────────────┴──────────────────────────┴────────────────────┤
│ Task / Log / Progress                                         │
└──────────────────────────────────────────────────────────────┘
```

推荐初始比例：

- 左侧：20–25%
- 中间：40–45%
- 右侧：30–35%

三个区域应支持拖动调整宽度。

底部 Task / Log 区域默认可以折叠，只有运行任务、查看日志或发生错误时才展开。

---

# 4. 左侧：项目管理树

## 4.1 目标

左侧不是单纯的操作系统文件浏览器，而是软件自己的“逻辑项目树”。

建议层级：

```text
Project
├── Experiment
│   ├── Data
│   │   ├── Input
│   │   ├── Processing
│   │   ├── Output
│   │   └── Figures
│   └── Data
└── Experiment
```

例如：

```text
Project_A
├── Experiment_01
│   ├── Data_001
│   │   ├── Input
│   │   ├── Processing
│   │   ├── Output
│   │   └── Figures
│   └── Data_002
└── Experiment_02
```

实际目录结构可以根据现有项目情况调整，但逻辑层级必须清晰。

---

# 5. 项目树中的对象

至少区分：

- Project
- Experiment
- Data
- Input
- Processing
- Output
- Figures
- Spectrum / 谱图文件
- Processing Script（如果现有项目确实需要暴露给用户）

不同类型对象应该有不同图标和右键菜单。

---

# 6. 文件夹与数据对象的关系

非常重要：

用户点击左侧任何文件夹时，软件必须能够确定：

```text
当前对象 = 用户点击的对象
当前 Data = 该对象所属的 Data
当前 Experiment = 该 Data 所属 Experiment
当前 Project = 该 Experiment 所属 Project
```

例如：

```text
Project_A
└── Experiment_01
    └── Data_001
        └── Processing
```

用户点击 `Processing`：

```text
Current Object = Processing
Current Data = Data_001
Current Experiment = Experiment_01
Current Project = Project_A
```

中间 Pipeline 仍然应该围绕 `Data_001` 工作，而不是把 `Processing` 当成一个完全独立的数据对象。

---

# 7. 左侧双击行为

双击文件夹可以进入/展开对应内容。

但双击的核心意义不是简单打开操作系统文件夹，而是：

> 更新当前上下文，并让中间和右侧区域同步。

例如：

用户点击：

```text
Data_001
```

中间显示：

```text
Data Dashboard
```

用户点击：

```text
Data_001/Processing
```

中间仍然围绕：

```text
Data_001
```

显示处理状态和相关操作。

---

# 8. 右键菜单

不同对象提供不同菜单，不要所有对象都使用相同菜单。

## Project

```text
打开项目
新建实验
项目设置
刷新
导出项目
打开所在目录
删除项目
```

删除项目必须明确提示可能删除整个项目目录。

## Experiment

```text
打开
新建数据
重命名
复制
刷新
打开所在目录
删除
```

## Data

```text
打开数据
导入数据
继续处理
重新处理
查看谱图
打开所在目录
重命名
删除
```

## Input / Processing / Output / Figures

```text
打开
打开所在目录
复制路径
刷新
删除
```

原始 Input 数据默认应该谨慎删除，最好增加确认。

## Spectrum

```text
打开谱图
在谱图查看器中打开
复制路径
打开所在目录
删除
```

---

# 9. 原始数据保护

原始数据是科研数据，不能像普通缓存文件一样处理。

建议：

- Input / 原始数据默认只读或至少删除时二次确认
- Processing 中间文件可以普通删除
- 软件生成的 Output 可以删除并重新生成
- 删除 Project 必须强确认
- 不要让 Pipeline 默认覆盖原始数据

如果现有项目已经有数据保护机制，应优先复用。

---

# 10. 左侧点击数据后的行为

当用户选择一个 Data 时，中间区域进入：

## Data Dashboard

例如：

```text
Data_001

数据状态

✓ 原始数据
✓ FID
✓ 处理结果
× SMILE Reconstruction
× Peak Picking
× Assignment

Pipeline

[继续处理]
```

同时右侧可以自动显示当前最相关的谱图。

---

# 11. 点击高级别对象后的行为

如果用户选择 Project：

显示：

## Project Dashboard

包括：

- 项目名称
- 项目路径
- 实验数量
- 数据数量
- 最近处理任务
- 数据处理完成情况
- 快速创建 Experiment / Data

例如：

```text
Project_A

Experiments: 3
Datasets: 12

Processing Status
Data_001   ██████████ 100%
Data_002   ███████░░░  70%
Data_003   ██░░░░░░░░  20%

[新建实验]
[新建数据]
```

---

# 12. Experiment Dashboard

选择 Experiment 后显示：

```text
Experiment_01

Data
├── Data_001
├── Data_002
└── Data_003

处理进度

Data_001   100%
Data_002    70%
Data_003    20%

[批量处理]
```

可以提供：

- 数据列表
- Pipeline 状态
- 批量处理
- 实验设置

---

# 13. 中间区域：Pipeline

Pipeline 是软件核心区域之一。

不要把 Pipeline 写死成一组普通按钮。

底层应该设计成：

> Pipeline = 一组具有依赖关系的 Processing Task

例如：

```text
① 导入数据
      ↓
② 生成 FID
      ↓
③ 数据处理
      ↓
④ SMILE Reconstruction
      ↓
⑤ Peak Picking
      ↓
⑥ Assignment
      ↓
⑦ Figure Generation
```

实际步骤以现有项目已经实现的功能为准。

---

# 14. Pipeline 状态系统

每一个 Pipeline Step 都必须有明确状态。

建议至少支持：

| 状态 | 含义 |
|---|---|
| LOCKED | 前置条件不满足 |
| READY | 可以运行 |
| RUNNING | 正在运行 |
| SUCCESS | 已成功 |
| FAILED | 执行失败 |
| OUTDATED | 上游数据发生变化，需要重新运行 |
| CANCELLED | 用户取消 |

GUI 可以用：

```text
✓ SUCCESS
▶ READY
🔒 LOCKED
… RUNNING
! OUTDATED
× FAILED
```

图标和颜色可以根据现有 UI 风格实现，但必须保证状态含义清楚。

---

# 15. Pipeline 的依赖检查

后续步骤不能仅仅通过“按钮是否被点击”判断是否可以运行。

应该根据：

> 前置 Task 的状态 + 必需输出文件 + 参数/输入是否发生变化

判断当前 Task 是否可执行。

例如：

```text
Import Data
    ↓
Generate FID
    ↓
Process Data
    ↓
Peak Picking
```

如果 FID 不存在：

```text
Generate FID = READY
Process Data = LOCKED
Peak Picking = LOCKED
```

如果 FID 生成完成：

```text
Generate FID = SUCCESS
Process Data = READY
Peak Picking = LOCKED
```

---

# 16. 必须处理“结果过期”

这是整个 Pipeline 系统非常重要的一点。

例如：

```text
Import Data ✓
    ↓
Generate FID ✓
    ↓
Process Data ✓
    ↓
Peak Picking ✓
```

如果用户重新运行 `Process Data`，那么：

```text
Import Data ✓
    ↓
Generate FID ✓
    ↓
Process Data ✓
    ↓
Peak Picking !
```

Peak Picking 必须变成：

```text
OUTDATED
```

后面的 Assignment、Figure 等也应该变成 OUTDATED。

不能继续显示“已完成”。

---

# 17. Pipeline Step 点击后的行为

用户点击 Pipeline 中某一步后：

> 中间区域切换成该功能的专用界面。

例如点击“数据处理”：

```text
数据处理

Input
    spectrum.fid

Processing Script
    processing.com

Parameters

    Size       [1024]
    SW         [12.0]
    SF         [600]

[编辑脚本]
[运行]
[停止]

Execution Log
----------------
...
```

点击“SMILE Reconstruction”：

```text
SMILE Reconstruction

Input Spectrum
    spectrum.ft2

Parameters

    Iterations
    Threshold
    Regularization
    ...

[运行]
[自动优化参数]

Optimization Result
-------------------
...
```

不要让所有参数同时堆在主界面。

---

# 18. Pipeline 不应该与 GUI 强耦合

GUI 只负责：

- 显示 Task
- 显示 Task 状态
- 收集参数
- 调用 Task
- 显示执行结果

实际处理逻辑应该留在独立的 backend / processing module。

理想结构：

```text
GUI
 ↓
Pipeline Controller
 ↓
Task
 ↓
Processing Backend
 ↓
Output
```

而不是：

```text
GUI Button
 ↓
直接执行大量处理代码
```

这样以后才能方便增加新的 NMRPipe、SMILE、Peak Picking 等处理模块。

---

# 19. 右侧：谱图查看器

现有谱图查看器已经存在，因此：

> 不要重新实现谱图查看器。

应该把现有 Viewer 作为独立组件嵌入右侧区域。

核心联动：

```text
左侧选择 Spectrum
        ↓
右侧自动打开 Spectrum
        ↓
中间显示该 Spectrum 对应的处理状态
```

例如：

```text
Data_001
├── Input
├── Processing
└── Output
    └── processed.ft2
```

用户点击：

```text
processed.ft2
```

应该：

1. 左侧选中文件
2. 中间显示该结果属于哪个 Pipeline Step
3. 右侧自动加载 processed.ft2

---

# 20. 谱图查看器与 Pipeline 联动

如果 Pipeline 当前运行的是：

```text
Process Data
```

完成后生成：

```text
processed.ft2
```

应该能够提供：

```text
[查看结果]
```

点击后直接在右侧打开。

类似：

```text
SMILE Reconstruction
      ↓
output.ft2
      ↓
[查看谱图]
```

---

# 21. 底部 Task / Log 区域

增加一个可折叠的底部区域。

默认可以隐藏。

运行任务时显示：

```text
Task / Log

[RUNNING]
NMRPipe processing...

Command:
nmrPipe ...

stdout:
...

stderr:
...
```

失败时：

```text
[FAILED]

Error:
...

[查看完整日志]
[重新运行]
```

这样用户不需要打开额外终端窗口就能判断处理失败原因。

---

# 22. 顶部菜单栏

顶部使用传统软件菜单，但保持精简。

建议：

```text
文件
项目
处理
查看
工具
窗口
设置
帮助
```

---

## 22.1 文件

```text
新建项目
打开项目
打开最近项目
保存
退出
```

## 22.2 项目

```text
项目管理
新建实验
新建数据
项目设置
项目结构
```

## 22.3 处理

```text
Pipeline
批量处理
任务队列
处理历史
```

## 22.4 查看

```text
谱图查看器
日志
文件浏览器
显示/隐藏面板
```

## 22.5 工具

根据实际已有功能加入：

```text
Peak Picking
Spectrum Optimization
数据转换
脚本管理
```

不要提前加入尚未实现的功能。

## 22.6 窗口

```text
恢复默认布局
左侧面板
Pipeline 面板
谱图面板
日志面板
```

## 22.7 设置

```text
软件设置
NMRPipe 路径
外部程序
默认参数
计算资源
```

这些设置必须根据现有项目实际支持的功能决定。

## 22.8 帮助

```text
文档
快捷键
日志目录
关于
```

---

# 23. 不要增加过多独立窗口

核心原则：

> 能在中间功能区完成的功能，不要额外打开窗口。

例如：

不推荐：

```text
主界面
  ↓
点击数据处理
  ↓
弹出新窗口
  ↓
NMRPipe窗口
```

推荐：

```text
主界面
  ↓
中间区域切换到 Data Processing
```

只有真正需要独立窗口的高级功能才使用独立窗口。

---

# 24. 快捷操作

可以逐步增加：

```text
双击 Data → 打开 Data Dashboard
双击 Spectrum → 在 Viewer 打开
右键 → 上下文菜单
Enter → 打开当前对象
Delete → 删除
Ctrl+Z → 如果现有架构支持则撤销
Ctrl+S → 保存项目状态
```

快捷键不是第一优先级，不能因为快捷键影响核心架构。

---

# 25. 数据与 Pipeline 的核心数据模型

建议内部至少存在以下概念：

```text
Project
Experiment
Dataset
Pipeline
PipelineStep
Artifact
Task
```

其中 Artifact 表示软件产生或使用的文件。

例如：

```text
Dataset
    |
    +-- raw data
    |
    +-- FID
    |
    +-- processed spectrum
    |
    +-- reconstructed spectrum
    |
    +-- peak list
    |
    +-- assignment
    |
    +-- figure
```

PipelineStep 则定义：

```text
输入 Artifact
      ↓
处理逻辑
      ↓
输出 Artifact
```

---

# 26. Pipeline Step 的基本定义

每一个 Step 应该能够描述：

```text
id
name
description

inputs
outputs

dependencies

parameters

status

run()
validate()
get_output()
```

具体实现必须结合现有项目架构，不要求机械照搬这个接口。

---

# 27. 文件存在性不能作为唯一状态依据

不要只使用：

```text
os.path.exists(output)
```

判断任务是否完成。

因为：

- 文件可能存在但属于旧版本
- 输入文件可能已经改变
- 参数可能已经改变
- 处理脚本可能已经改变
- Pipeline 逻辑可能已经改变

更可靠的方法是保存必要的任务元数据，例如：

```text
input fingerprint
parameter hash
script/version information
output information
timestamp
status
```

如果现有项目已经有类似机制，优先复用。

第一版如果暂时无法完整实现 fingerprint，也至少应该建立清晰的状态管理接口，为以后扩展留下空间。

---

# 28. 错误处理

处理失败时不要只弹一个：

```text
Processing failed.
```

应该告诉用户：

```text
处理失败

Step:
NMRPipe Processing

原因：
xxx

输入：
xxx

输出：
xxx

[查看日志]
[重新运行]
```

如果 stderr 有明确错误，应尽可能展示关键部分。

---

# 29. 批量处理

当用户选中 Experiment 时，可以提供：

```text
[批量处理]
```

例如：

```text
Experiment_01

Data_001 ✓
Data_002 ✓
Data_003 ▶
Data_004 🔒
```

批处理必须遵守每个 Data 自己的 Pipeline 依赖。

不要为了批处理重新实现一套处理逻辑。

---

# 30. 当前上下文必须始终明确

界面中应该有一个清晰的当前上下文，例如：

```text
Project_A / Experiment_01 / Data_001
```

可以放在顶部工具栏或中间区域标题附近。

这样用户不会在处理多个实验时迷失当前对象。

---

# 31. GUI 状态与文件系统状态必须同步

如果用户：

- 删除文件
- 从外部程序修改文件
- 新增文件
- 修改 Pipeline 输出

软件需要有刷新/重新扫描机制。

至少提供：

```text
刷新项目
```

必要时可以自动检测变化。

但不要频繁无条件扫描整个项目导致性能问题。

---

# 32. 重要的 UX 原则

整个软件应该遵循以下原则：

### 原则 1：用户不应该猜下一步做什么

Pipeline 应明确告诉用户：

```text
下一步：数据处理
```

### 原则 2：不能执行的功能应该解释为什么

不要只有：

```text
🔒
```

最好提供 tooltip：

```text
请先完成 Generate FID
```

### 原则 3：已完成和过期必须区分

```text
SUCCESS ≠ OUTDATED
```

### 原则 4：文件和功能必须联动

选择文件之后：

- 中间区域知道它属于哪个 Pipeline
- 右侧 Viewer 能打开它

### 原则 5：避免重复窗口

核心功能尽量在主界面完成。

### 原则 6：原始数据优先保护

不能因为方便而允许误删原始数据。

---

# 33. 推荐的最终用户工作流

用户第一次使用软件：

```text
打开软件
 ↓
新建 Project
 ↓
创建 Experiment
 ↓
创建/导入 Data
 ↓
导入原始 NMR 数据
 ↓
Pipeline 自动识别状态
 ↓
点击 Generate FID
 ↓
点击 Data Processing
 ↓
查看谱图
 ↓
SMILE Reconstruction
 ↓
Peak Picking
 ↓
Assignment
 ↓
Figure Generation
```

用户不需要记住：

- 哪个脚本先运行
- 哪个文件放在哪里
- 哪个输出是哪个步骤生成的
- 哪一步依赖哪一步

这些应该由软件管理。

---

# 34. 对现有项目的重构要求

在 Codex 开始修改代码之前：

## 第一阶段：只分析，不修改

依次读取现有项目中的脚本和模块，建立：

1. 文件清单
2. 每个脚本的功能
3. 每个模块的依赖
4. 核心调用链
5. 数据流
6. GUI结构
7. Pipeline结构
8. 谱图查看器结构
9. 文件生成逻辑
10. 已存在的状态管理机制

输出项目架构报告。

---

## 第二阶段：建立逻辑关系

根据第一阶段分析结果建立：

```text
GUI
 ↓
Controller
 ↓
Pipeline
 ↓
Processing Modules
 ↓
File System / Artifacts
```

同时确认：

- 哪些模块重复
- 哪些模块职责冲突
- 哪些地方存在循环依赖
- 哪些地方存在状态不同步
- 哪些地方直接操作 GUI
- 哪些地方直接操作文件系统
- 哪些 Pipeline Step 的依赖关系不明确

---

## 第三阶段：只提出问题，不立即修改

列出：

```text
问题编号
问题位置
当前逻辑
冲突原因
潜在影响
建议解决方式
涉及文件
```

不要在没有确认架构之前大规模重写。

---

## 第四阶段：制定重构计划

按照优先级：

### P0
导致程序无法正确工作的错误。

### P1
导致数据处理结果错误或状态错误的问题。

### P2
GUI与后端逻辑耦合、维护困难的问题。

### P3
UX、性能和代码质量优化。

优先解决 P0/P1，不要先做视觉优化。

---

# 35. 对 Codex 的具体工作方式要求

面对大型项目，不要一次性假设自己已经理解整个项目。

建议：

```text
逐文件分析
    ↓
记录每个文件功能
    ↓
汇总模块关系
    ↓
汇总数据流
    ↓
分析 Pipeline
    ↓
检查逻辑冲突
    ↓
定位具体代码
    ↓
制定修改方案
    ↓
小范围修改
    ↓
测试
    ↓
再进入下一部分
```

每完成一个阶段，都应该保存分析结果，而不是完全依赖对话上下文。

建议项目中维护：

```text
PROJECT_ARCHITECTURE.md
PROJECT_DATA_FLOW.md
PROJECT_PIPELINE.md
PROJECT_ISSUES.md
```

如果已有项目文档，应优先更新现有文档，而不是制造大量重复文档。

---

# 36. 最重要的开发约束

Codex 修改项目时必须遵守：

1. 不要为了实现新 GUI 而重复实现已有功能。
2. 不要删除已有谱图查看器。
3. 不要把处理逻辑直接塞进 GUI。
4. 不要把 Pipeline 写死在 GUI Button 中。
5. 不要未经分析就大规模重构。
6. 不要改变已有数据格式，除非明确需要。
7. 不要破坏现有脚本接口。
8. 不要默认覆盖原始数据。
9. 不要用文件是否存在作为唯一的任务完成判断。
10. 修改后必须检查原有功能是否仍然可用。
11. 新功能应尽量复用已有 backend。
12. 如果发现现有架构存在冲突，先报告再修改。
13. 如果无法确定某个模块的用途，不要猜测，应继续追踪调用关系。
14. GUI、Pipeline、Processing Backend、File/Artifact 管理应尽量保持职责分离。

---

# 37. 第一版 GUI 的优先级

第一版不需要一次实现所有高级功能。

优先实现：

```text
P0
├── 三栏主布局
├── Project / Experiment / Data 项目树
├── 文件夹与数据选择
├── 当前上下文
├── Pipeline 显示
├── Pipeline 状态
├── 前置依赖检查
├── 现有谱图查看器嵌入
└── Task / Log

P1
├── Data Dashboard
├── Experiment Dashboard
├── Project Dashboard
├── 右键菜单
├── OUTDATED 状态
└── 批量处理

P2
├── Pipeline 参数管理
├── Pipeline 历史
├── 更完善的任务队列
├── 快捷键
└── 高级设置
```

不要因为追求完整而让第一版 GUI 变得复杂。

---

# 38. 最终目标

最终软件应该让用户感觉：

> “我只需要找到我的数据，然后软件告诉我下一步应该做什么。”

而不是：

> “我需要知道哪个脚本、哪个目录、哪个参数、哪个输出文件应该先处理。”

因此：

**项目树负责“我在哪里”。**

**Pipeline 负责“我能做什么/下一步做什么”。**

**功能区负责“怎么做”。**

**谱图查看器负责“结果是什么”。**

**Task/Log 负责“发生了什么/为什么失败”。**

这是整个 GUI 架构最核心的逻辑。

---

# 39. Codex 开始修改前的要求

在真正修改代码之前，请先完成以下任务：

1. 阅读整个现有项目。
2. 按文件记录每个脚本的功能。
3. 建立模块依赖关系。
4. 建立数据流。
5. 找出现有 GUI 结构。
6. 找出现有 Pipeline/处理流程。
7. 找出现有谱图查看器。
8. 找出文件生成和状态判断逻辑。
9. 对照本文档检查现有架构。
10. 列出冲突和问题。
11. 给出重构方案。
12. **先不要大规模修改代码。**

只有完成上述分析后，才开始分阶段实施 GUI 重构。

每次修改都应保持项目可运行，并尽量进行对应测试。

