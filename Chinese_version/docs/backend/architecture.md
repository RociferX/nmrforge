# 后端架构

后端边界将 `Experiment`、`ProcessingPlan` 和显式参数覆盖转换为外部处理命令及记录产物。
`backend/base.py` 定义 `ProcessingBackend` 协议与能力描述。`backend/factory.py` 当前创建
`NMRPipeBackend`；其操作包括健康检查、Bruker 到 FID 的转换、均匀采样处理和 NUS 重构。
返回结果包含成功状态、消息、日志、输出路径和有效参数，供调用方登记或报告。

## 职责

- `nmrpipe_backend.py` 管理工作目录并协调输入转换、分段采集、均匀采样处理、NUS 重构、
  终处理和结果报告。它根据实验模型与采样元数据选择相应路径。
- `script_generator.py` 根据实验元数据、处理计划和参数值，生成确定性的 `fid.com`、
  均匀采样处理脚本和 SMILE/NUS 脚本。
- `bruker_workflow.py` 处理 `bruker -AUTO` 的转换输出及转换相关元数据。
- `runtime.py` 定位 `csh`/`tcsh`，在存在时读取用户的 `.cshrc`，并以已登记的子进程运行命令。
  它捕获 stdout/stderr，可逐行转发进度，执行超时限制，并支持停止活动进程树。
- `config.py`、`nmrpipe_finder.py` 和 `nmrpipe_version.py` 解析配置、工具位置和引擎版本；
  `memory_guard.py` 与 `memory_disk.py` 支持 NUS 内存检查和临时处理存储。

## 输入、轴与采样边界

`core.data.bruker_reader.read_dataset()` 将 `acqus`、`acqu2s` 和 `acqu3s` 解析为 `Experiment`，
其中包含逻辑维度、采集参数、实验类型和 `Sampling` 记录。逻辑维度名为 F1/F2/F3；Bruker
采集文件描述物理采集顺序，不能与逻辑维度名称混用。工作流将实验对象及其源目录传给后端，
后端依据解析出的采集设置生成转换与处理脚本。

采样检测只接受标准名 `nuslist` 或由 `acqus.NUSLIST` 明确指向的文件。该采样表是转换和重构
输入的一部分：系统根据实验维数检查行数及坐标列，后端将解析出的采样表准备给转换和 SMILE
命令。`Sampling` 模型将检测到的采样模式、网格和采样表信息交给 workflow 与 backend；它
本身不执行傅里叶变换，也不重构缺失点。对于分段输入，workflow 与 backend 协调各段转换和
合并，并保留样品顺序及对应的采样元数据。

## 执行与产物

桌面应用的 `ProcessingController` 通过 `workflow.stepwise` 调用 FID 和谱图步骤。工作流将实验
和计划传给 `NMRPipeBackend`，并转发参数覆盖及进度回调。后端生成脚本并通过 `CshRuntime`
运行；NUS 路径调用 SMILE 重构，随后执行 NMRPipe 处理/终处理命令。stdout 进度和后端日志
会传回 GUI 或 API 调用方。成功结果包含输出路径和有效参数；工作流将终谱移动/登记到项目
数据条目并保存运行记录。

人工脚本执行也使用相同的运行时边界。GUI 通过 `workflow.manual` 编辑并提交脚本内容；NMRPipe
命令由已配置的外部程序解释。Python/CLI API 经由自己的参考和参数组合工作流访问同一后端，
不加载 GUI 或 viewer 状态。

引擎边界需要外部 NMRPipe 工具完成转换和傅里叶处理，NUS 重构需要 SMILE。另见[外部依赖]
(../external-dependencies.md)、[处理模型](../processing-model.md)和共享[架构](../architecture.md)。
