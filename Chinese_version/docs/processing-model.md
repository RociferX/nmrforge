# 处理模型

桌面与脚本API使用Bruker读取器、工作流编排及NMRPipe/SMILE后端。
桌面提供导入、生成FID、生成谱图和选峰；API把参考构建与参数组合执行分开。

## 数据理解

| 判定 | 输入与实现 |
| --- | --- |
| 维度 | `core/data/bruker_reader.py`中的`PARMODE`及采集文件存在性 |
| 逻辑轴与核 | `NUC1`、`TD`、`SFO1`、偏移、采集顺序与正交模式 |
| 谱宽 | `resolve_sweep_width`比较`SW_h`与`SW×SFO1`，保留采用值/来源与原始元数据 |
| FT模式、ALT与NEG | `core/experiment/acquisition_mode_detector.py`根据逐轴FnMODE/模式证据解析 |
| 实验类型 | 脉冲程序、核组合、维序与采集参数；模板位于`nmrforge_data/presets/` |
| 采样 | `core/experiment/sampling_detector.py`及`core/data/nus_reader.py`联合采样表、网格、布局与采集证据 |

NUS表来自标准`nuslist`或`acqus.NUSLIST`，不扫描任意整数文件猜采样表。
标准顺序的完整覆盖可走uniform，乱序完整覆盖仍需按表归位；明确NUS但位置无法恢复时拒绝。
尾部存储padding不算未采样增量，不支持的动力学/伪维度布局在普通多维处理前过滤。

## 导入与FID生成

`workflow.stepwise`把处理绑定到项目实验/数据条目。导入登记来源、复制项目数据并写metadata。
多段输入保留源顺序，转换/合并前检查采集兼容性。

生成FID调用`backend.convert_to_fid`。转换解释Bruker物理行长、正交模式与采集顺序；
声明TD、NMRPipe实/复点及逻辑网格大小分别处理。自动转换可输出单FID或支持的平面布局，人工覆盖通过相同后端执行。

直接维数据诊断在FID生成末尾运行，写入`process/diagnostics.json`，报告DC偏置、非有限数、空行/高能量迹与坏点证据。
已应用的修正保留备份与审计。`segment_shift_hz`是用户指定的转换时偏移，首段0 Hz，软件不自动估计。
谱图处理复用匹配的诊断记录，缺失或过期时回退检查。

## 计划、执行与缓存

`ProcessingPlan`携带方法、理由、置信度与`ProcessingDag`。DAG校验依赖并提供拓扑顺序；
`PipelineRunner`为图式入口提供节点/缓存执行。桌面统一优化路线由`workflow/phase_routes.py`显式编排，
其相位/重构缓存不代表任意参数改变后都复用全部谱或重构。
参考缓存与组合续跑是独立API机制，指纹绑定规范化请求、条件输入、参数与冻结转换证据；
参考FID缺失/改变时需显式重建，不按需自动重转。

## uniform自动路线

`unified_route()`先间接维、后直接维。相敏轴逐一生成复型预览：搜索轴保留虚部，已固定轴采用选定相位，再在内存搜索。
直接维确定后，用该修正复核间接维。模谱根据模板跳过自动调相。

```text
已转换FID与诊断
  -> 逐轴复型预览和相位搜索
  -> 直接维确定后复核间接维
  -> 最终相位与自动填零的联合谱
  -> 基线评分
  -> 从FID评分直接维窗
  -> 从FID评分间接维窗
  -> 后端完整终跑
  -> 谱图QC与产物登记
```

基线候选先于窗函数候选评估。窗函数评分在内存读取FID，不需要在选定基线后额外重渲谱。
uniform候选可含无窗，间接维评分考虑分辨率保留；解析后的基线、窗和自动填零配置写入终跑脚本。
评分阶段无法评估时记录回退并保留可用设置，不编造评分结果。

## NUS自动路线

`_unified_nus()`先调用SMILE重建，保留重构数据供调相。间接维在内存等价finalize链中评估，
直接维在实型终谱及Hilbert变换正交分量上搜索，再把直接维修正用于间接维复核。

**仅2D NUS**可在首轮重构前从零间接增量正交对估计轻量P0 seed，经布局/置信度门控后采用。
后续只累加残差，seed不加两次，并绑定相位缓存证据。初始化不搜索P1，也不用于3D NUS。

```text
已转换FID与诊断
  -> 可选的门控2D P0初始化
  -> 首轮SMILE重建
  -> 间接维搜索、直接维搜索、间接维复核
  -> 联合finalize供基线评分
  -> 基线与窗函数参数选择
  -> 按选定参数/相位完整重建与finalize
  -> 谱图QC与产物登记
```

SMILE要求直接维SP窗，优化器不把该窗替换为无窗、Gaussian或指数加权。间接维窗候选利用重构平面信息评分。
最终直接维相位写入对应PS，最终间接维相位传入SMILE并与后续PS一致。
产物来自完整终跑，不是仅旋转预览平面用于显示。

## 范围、相位与人工执行

`final_ext_lo`/`final_ext_hi`指定终跑直接维范围，“应用范围到优化”开关决定预览/评分是否使用该范围。
终跑范围变化时直接维P1按最终窗宽重归一化，轴身份、单位与实际范围留档。
单符号模板消解180度歧义，mixed模板保留物理正负信号，模谱跳过相位优化。

`phase_route="none"`直接执行后端，不走统一调相。人工路线通过`workflow.manual`开放转换/谱图脚本，
执行编辑文本、核对产物，并按数据目标登记脚本、变化与结果。

## 参数解析与产物

模板/配置提供处理默认值，桌面调用提供选定覆盖，参考研究可逐条件覆盖共同参数。
API组合把候选值与冻结参考默认值合并，并在组合层锁定转换/相位路线键；空组合单元格表示未指定。
GUI与API不同操作没有统一的一张优先级表，具体输入见[API输入](external-api/05-inputs-and-data.md)。

每次运行保存请求与解析/应用参数、脚本、输入/产物引用、软件/引擎版本、状态、诊断及warnings。
产物登记到项目数据或API的workflow/条件目录。选峰/定位作用于终谱；API独立写表，不建立跨谱身份。

## 四条处理路径

| 路径 | 引擎路线 |
| --- | --- |
| 2D uniform | 常规NMRPipe FT，自动/人工入口 |
| 2D NUS | SMILE重建，自动/人工入口，可选首轮P0 seed |
| 3D uniform | 常规NMRPipe FT，解释逻辑/存储轴映射 |
| 3D NUS | SMILE重建/finalize，不使用2D seed |

桌面批量仅2D，API的3D NUS支持参考构建、拒绝参数组合执行。
详见[共享接口](API_CONTRACT.md)、[质量控制](qc-system.md)、[Backend架构](backend/architecture.md)和[API边界](external-api/09-limitations-and-roadmap.md)。
