# 09 · 支持范围与执行边界（v1.1.1）

## 9.1 支持矩阵

| 项目 | 支持 | 实现边界 |
| --- | --- | --- |
| uniform 1D/2D/3D | 参考与组合 | 后端`process()`调用NMRPipe |
| 2D NUS | 参考与组合 | SMILE `reconstruct_nus()`，候选产物隔离 |
| 3D NUS | 仅参考 | 组合执行抛`SweepError` |
| 多条件 | 逐条件独立参考/峰表 | 共同参数可逐条件覆盖 |
| 峰定位 | 三点抛物线 | 可指定目标子集，单谱独立ID |
| 标作NUS的完整表 | 取决于顺序 | 标准满网格可走uniform，乱序完整覆盖需归位 |
| 重叠去卷积、Lorentzian/Voigt/多峰拟合 | 不提供 | 检测/定位作用于局部极值 |
| 并行/集群调度 | 不提供 | 串行与续跑；调用者可拆分到独立研究根 |
| 参数键校验 | 部分 | 锁定键报错，确定性/未知键产生warnings/notes |
| 跨谱匹配、指认与统计推断 | 不提供 | 由下游分析实现 |

## 9.2 NUS执行

仅标准`nuslist`或`acqus.NUSLIST`明确指定的文件提供位置，名义采样比例/原始长度本身不是采样表。
乱序完整覆盖仍需按表归位；明确NUS但无法恢复位置时导入拒绝，尾部零padding不算未采样点。

2D参考与组合均调用`reconstruct_nus()`，传入间接维`phases`与直接维`direct_phase`。
组合在`study/workflows/<workflow_id>/<condition>/spectrum.ft2`写隔离产物，不覆盖当前谱/参考谱。
SMILE参数包括`nsigma`（别名`nSigma`）、`thresh`、`nthread`和`smile_scaling`，
自动值写入`parameters_resolved.smile`。3D NUS参考支持重建/finalize，API组合不执行该路线。

## 9.3 批次与续跑

默认组合上限256（`max_runs`），更大网格可拆研究根或分批提交。
重跑跳过与当前请求/参考证据匹配的成功workflow/条件组合。多机可分别分配子网格与独立根，再在下游合并表。
plan/manifest的`grid_sha256`标识组合设计，源段顺序属于条件输入的一部分。

## 9.4 参数校验

转换校准与源绑定在参考阶段执行，组合不能修改；相位路线与FT-alt锁定，布尔FT-neg/flip可作为候选。
FT-neg候选不触发重新调相。未知或确定性键可能只产生warnings而不抛无效键异常，
核对参数效果需查看`notes`、warnings、解析参数与实际脚本。

## 9.5 检查与证据

普通pytest通过模拟引擎检查编排与记录；真实引擎对照另行记录输入、引擎版本、脚本/参数来源与终谱。
[四路径证据](../evidence/real-data-comparison.md)包含保留68/90增量的受控人工二维下采样、实采25%三维NUS及uniform作者参考。
候选覆盖率是检测/匹配指标，不是指认真峰回收率；三维投影计数不建立独立峰身份，也不能消除全部重叠。

## 9.6 共享处理组件

API复用桌面NMRPipe/SMILE链、轴映射与选峰组件；入口自身负责参数合并、参考冻结、独立检测、序列化与续跑。
需同时核对入口记录与终谱。API不依赖Qt，不写GUI显示状态。

## 9.7 记录解释

状态、warnings、请求/解析参数及谱图需联合查看。兼容元数据标识处理代码/契约，定位QC描述数值测量而非峰指认。
统一表保留完整F1/F2/F3坐标、核名与等效线宽；同核重复造成歧义时H/N别名留空。

targeted定位精修所选峰，保持检出、行数与ID。非目标检出峰保留整数格点，`localization_method="none"`，
未计算QC为`NaN`；正常跳过的`failure_reason`为空，检出/数值失败另有原因，并与实际fallback分开。

## 9.8 来源、校准与输出归属

每条件独立参考/峰表，外部身份表仅用于主条件。组合表`reference_peak_id`与`assignment`留空，局部峰ID不建立跨谱链接。

参考的正有限`sweep_width_hz`与显式`carrier_ppm`按轴应用/留档。
`params_by_condition`/`--condition-params`逐条件覆盖共同参考参数，段偏移属于转换时设置。
转换来源记录数字滤波输入、脚本哈希及命令；请求/解析/实际FT命令与阶段计时分别记录。
无法核实的实际修正记为`unknown`，不从输入元数据推定已执行。

CLI的`--study`与`--reference`在写入前必须解析到同一根目录。
根内组合执行会写workflow/结果记录与候选产物，只读的是冻结参考FID/采样表。

## 9.9 冻结FID复用

参考按需导入源、逐段转换、合并并冻结FID、转换证据及采样表；组合处理这些产物并独立选峰。
支持单FID、3D uniform切片与合并多段FID，不自动重转/重合并、不删源、不改参考FID或采样表。

FID缺失/损坏、源或转换证据改变、冻结证据缺失，或请求参数需要转换/合并时抛错，
要求在参考构建用`force=True`（CLI `reference --force`）。`force`不允许组合按需重转。
指纹对≤8 MiB文件使用内容SHA-256，更大文件使用`size + mtime_ns`；后者检测元数据变化，不认证完整内容。
