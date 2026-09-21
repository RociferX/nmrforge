# 09 · 边界与扩展路径(v1.0)

## 9.1 支持矩阵

| 项目 | v1.0 | 说明 |
| --- | --- | --- |
| uniform 1D/2D/3D 数据 | ✅ 组合执行 | 走 NMRPipe `process()`;研究以 2D 为主 |
| NUS **2D** 数据 | ✅ 组合执行 | 走 `reconstruct_nus()`(SMILE),候选谱隔离输出;可扫 SMILE 参数 |
| NUS 3D 数据 | ⚠️ 只能建参考谱 | 组合执行会抛 `SweepError`(见 9.2) |
| 参考工作流(1 脚本 + 2 峰表) | ✅ | 参考只作基准,不声称全局最优 |
| workflow_id 批量执行 | ✅ | `W0001…`;两条件 A/B 同参数同峰身份 |
| 峰定位:parabolic / 2D gaussian | ✅ | 同一 candidate 两法都跑,两张同结构峰表 |
| 多条件(A/B) | ✅ | 每条件一份参考;峰身份与用户参数共享 |
| **实际满采样但标注 NUS** | ✅ 按 uniform 处理 | `nuslist` 覆盖全格,或 2D `ser` 是「全格+零填充」且无零行 → 视为满采样,走常规 FT(不跑 SMILE),证据写入 `sampling_evidence` |
| 峰重叠/去卷积 | ❌ | 只做窗口内极值 + 抛物线/单峰高斯 |
| Lorentzian / Voigt / 多峰分解 | ❌ | 路线图项 |
| 并行/集群调度 | ❌ | 串行 + 断点续跑;按参数轴分片(见 8.4) |
| 参数轴合法性校验 | 部分 | 锁定键报错、确定性/未知键提示;键名有效性以 notes 提示为主 |
| 统计推断与科学结论 | ❌ **(不属本软件)** | 由使用者自己的分析从统一峰表计算 |

## 9.2 NUS 的支持范围

> **满采样优先**:检测到「标注 NUS 但实际满采样」(nuslist 列满全格,或 2D `ser`
> 全格且无零行)时,按 **uniform** 处理并留档(`sampling="uniform"`,
> `sampling_schedule="full_sampling"`,证据在 `sampling_evidence`);真 NUS
> (采样子集/稀疏文件)才走下面的 SMILE 路径。

**已支持:2D NUS**。参考与 workflow 都走 `reconstruct_nus()`,差别只是工作流
批量执行会为每个组合隔离候选输出:

- 相位锁定:`phases`(间接维)+ 扁平 `direct_phase`(直接维);
- 候选输出 `study/workflows/<id>/<条件>/spectrum.ft2`(后端 `out_file`/
  `script_name` 语义),不覆盖工作目录里的终谱;
- 可扫参数:`nsigma`(别名 `nSigma`)、`thresh`、`nthread`、`smile_scaling` 等;
  自动分档时实际值写进 `parameters_resolved.smile`。

**仍未支持:3D NUS** 的组合执行(切片流按平面目录分桶 + 独立 finalize 命名)。

## 9.3 长批量怎么切分

- 组合数上限默认 256(`max_runs`);超过请拆成多个研究根或分批;
- 同一研究根内可反复重跑,已成功的 workflow × 条件会跳过(断点续跑);
- 多机并行:按**参数轴**拆分(各机器不同子网格、各自研究根),分析侧合并长表;
- 每次改参数表前建议记录 `grid_sha256`(plan/manifest 里都有),便于核对是同一
  批设计。

## 9.4 roadmap

| 优先级 | 项 | 交付形态 |
| --- | --- | --- |
| 高 | 3D NUS 组合执行 | 切片流按 `workflow_id` 分目录 + finalize 输出隔离 |
| 中 | 峰拟合扩展到 Lorentzian/Voigt/多峰分解 | 目前只有 2D 单峰高斯 |
| 中 | 参数键 schema 校验 | 报错而不是仅提示未知键 |
| 中 | 进度文件 | 每组合更新 `records/progress.json`,便于外部监控 |
| 中 | 3D 平面显式指定 | 峰表带固定维取值时按平面测量 |

用户新需求请附 `records/manifest.json` 与 `status` 输出,便于复现与定位。


## 9.5 验证边界:工程回归 vs 科学验证

引用产物时最常问的是「这批数字被什么验证过」。本软件把两件事分开写:

### 工程回归(证明「链路与留档自洽、可复现」)

| 证据 | 怎么跑 | 产物 / 日志 |
| --- | --- | --- |
| 全量测试 | `python -m pytest`(不需要 NMRPipe:测试在引擎边界打桩) | 终端输出,不落仓库 |
| 装有 NMRPipe 的机器上的全量测试 | `bash scripts/vm_test.sh`(与上一行**同一套**测试:引擎边界仍然打桩) | 测试日志;字节码与 ruff 缓存重定向到临时目录,工作副本保持干净 |
| CI | `.github/workflows/ci.yml` 的 `static`(ruff)/ `tests`(3.12、3.13)/ `release-readiness` | GitHub Actions 日志 |
| 装在 NMRPipe 的机器上的 CI 作业 | `external-engine`:在自托管 runner 上**再跑一遍同一套打桩测试**并上传日志;**不调用引擎** | **未武装就不跑**:只有注册了自托管 runner 且仓库变量 `NMRFORGE_SELF_HOSTED_CI` 为 `true` 时才执行;许可依赖不该成为 PR 的闸门,GitHub 托管 runner 上也没有 NMRPipe |
| 真实引擎 API 冒烟 | 在装有 NMRPipe 的机器上 `python scripts/vm_api_smoke.py --data <Bruker 目录> [--data b …] [--fresh]` | study root 由 `--study` 指定(默认 `~/studies/nmrforge_api_smoke`,可用环境变量 `NMRFORGE_API_STUDY` 覆盖);结构就是普通研究根:`records/reference.json`、`records/manifest.json`、`workflows/W0001/<条件>/peak_table_{parabolic,gaussian}.csv`、`run.json`;stdout 末尾一行 `RESULT_JSON {…}`(逐 run 的 `peak_tables`/`peak_localization`/`window`/`detection` + `summary` + `records`) |
| 真实引擎靶向校验 | `scripts/vm_validate_zero_fill.py`、`vm_validate_nus_indirect_equiv.py`、`vm_validate_phase_score.py`、`vm_sample_regression.py`(配合 `vm_sample_compare.py`) | 直接打印逐项指标(填零 SI、内存/后端等价性相对差、相位评分余量、主峰与水峰方向);结论回填到 ../API_CONTRACT.md |

真实引擎验收请**串行**跑:并发会争 CPU、互相覆盖日志,并报出与代码无关的失败。

### 科学验证(不在本软件范围内)

- 工程回归与真实引擎冒烟只能说明「链路能跑通、产物自洽、同一输入给同一结果」;
  它们**不能**说明处理结果在真实体系上「科学上正确」;
- 要回答后者需要:真值基准(合成基准或已知答案的体系)+ 你自己的判据 + 统计口径,
  并接受「算法在哪些体系上会偏」的结论 —— 这些由**使用者自己的分析**完成
  (本软件不做统计判定与科学结论,见 01-overview 的软件边界);
- 因此引用产物时请分开写三件事:① 用了哪套引擎行为(`compat` / `behavior_digest`),
  ② 处理流程是否被工程回归覆盖(本节上表),③ 科学结论是否成立(使用者)。

### 维护者做过的外部真值对照(证据,不是保证)

「不在本软件范围内」说的是**软件不替你下结论**,不是说这件事没做过。维护者用**公开数据**做过一层
外部真值对照,口径与数字都在 [真实数据实测证据](../evidence/real-data-comparison.md) §2:

| 环节 | 做法 |
| --- | --- |
| 数据 | 一套真实 2D 15N-1H HSQC 的原始 Bruker 数据(**公开条目 53374,可自行下载**) |
| 期望峰位 | 同一样品同一条件的**已发表沉积化学位移**(不提供给选峰,只作外部判据) |
| 匹配 | 一对一贪心最近优先 + 容差阶梯;标定**一次**全局参照后冻结 |
| 对照 | 同容差下把期望峰表逐峰独立平移(固定 seed、200 次)算偶然匹配背景 |

结果:紧容差(1H 0.01 / 15N 0.05 ppm)下**84.1%(90 / 107)的期望峰被一对一配上**(放宽容差到
0.02 / 0.10 ppm 是 93.5%),位置残差中位 0.0012 / 0.0164 ppm,而偶然背景只有 2.0%。紧容差在 15N
轴上比一个数据点还小(0.055 ppm/点),所以未配上的多数是**卡在门槛上**而不是旁边没有峰 —— 逐峰
距离写在匹配明细 CSV 里。这层对照只回答「自动处理能否复现外部真值」,**不**覆盖你的样品、你的参数
选择与你自己的科学结论 —— 引用产物时仍按上面三条分开写。

## 9.6 与主程序的关系:API 不引入自己的算法

- API 不做第二套处理:参考谱、组合定位、参数扫描调用的是主程序(桌面程序与命令行)同一套
  代码,产物由主程序产出 —— 主程序有效的优化过程在 API 上同样有效,**不需要单独证明**;
- API 层没有算法上的特殊设计:它只是把主程序的调用做得更灵活(批量、组合、断点续跑、留档与
  可机读产物);阈值、默认值、优化与 QC 口径一律沿用主程序,并版本化在 `compat` 里;
- 因此本接口文档只写「输入什么、输出什么、错误怎么报」;处理质量与优化效果的证据属于主程序
  (入口见 README),不在 API 文档里重复。
- 「处理程序效果如何」的证据在主程序一侧:与**已发表沉积化学位移**做外部真值对照(公开数据
  53374)见 `docs/evidence/real-data-comparison.md`;此外维护者另用**十几套暂无法公开的数据**验证过,
  自动处理都达到了与人工处理相当的优化效果 —— 这一条属**维护者自述**,无法仅凭本快照复算。

## 9.7 边界复核要点(常见误解)

- 「跑通了」≠「结果对」:9.5 的工程回归只能证明链路自洽、产物可追溯,不能证明结果在真实
  体系上科学正确;科学结论仍由使用者自己的分析给出。
- 「CI 绿了」≠「引擎绿了」:本仓库**没有任何**作业会调用 NMRPipe —— 引擎边界处处打桩,
  包括 `external-engine`(它只是把同一套测试搬到装有引擎的机器上再跑一遍)。引擎层面的
  结论来自你自己在装有 NMRPipe 的机器上跑的验收(`scripts/vm_test.sh`、
  `scripts/vm_api_smoke.py`、`scripts/vm_realdata_report.py`)。
- 「工具改了我的数据」≠「我的数据集坏了」:NUS 源级清理改写的是**项目自己那份 raw 副本**,
  同时留 `.bak` 并用 `os.replace` 打断与原件的链接;范围写在 README 的「已知边界」一节。
- 「没有覆盖率门槛」≠「没有测试纪律」:门禁是打标的测试套件、结构守卫
  (`test_qt_independence`、`test_ownership`、行为指纹与黄金向量)以及发布就绪检查;
  代码覆盖率百分比是刻意不设的门槛。另外 `pytest -m unit` 实测约 45 秒(主要是收集开销),
  不是「纯逻辑子集应该有的秒级」。
- 「公开日志只有一个快照提交」≠「项目没有历史」:公开历史为剔除内部材料与真实样品名而
  重启,版本历史看 release notes。
- 「无数字」≠「快」:基准里被跳过的四项是刻意不估,不是跑得飞快。
- 「转换记录对上了」≠「输入没被动过」:超过 8 MiB 之后,记录里的指纹退化为 `size + mtime_ns`
  (见 `core/data/raw_fingerprint.py`);它证明的是「raw 输入就是转换时那一份」,不是内容认证。
