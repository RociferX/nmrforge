# 09 · 边界与扩展路径(v0.2)

## 9.1 支持矩阵

| 项目 | v0.2 | 说明 |
| --- | --- | --- |
| uniform 1D/2D/3D 数据 | ✅ 组合执行 | 走 NMRPipe `process()`;研究以 2D 为主 |
| NUS **2D** 数据 | ✅ 组合执行 | 走 `reconstruct_nus()`(SMILE),候选谱隔离输出;可扫 SMILE 参数 |
| NUS 3D 数据 | ⚠️ 只能建参考谱 | 组合执行会抛 `SweepError`(见 9.2) |
| 参考工作流(1 脚本 + 2 峰表) | ✅ | 参考只作基准,不声称全局最优 |
| workflow_id 批量执行 | ✅ | `W0001…`;两条件 A/B 同参数同峰身份 |
| 峰定位:parabolic / 2D gaussian | ✅ | 同一 candidate 两法都跑,两张同结构峰表 |
| 多条件(A/B) | ✅ | 每条件一份参考;峰身份与用户参数共享 |
| 峰重叠/去卷积 | ❌ | 只做窗口内极值 + 抛物线/单峰高斯 |
| Lorentzian / Voigt / 多峰分解 | ❌ | 路线图项 |
| 并行/集群调度 | ❌ | 串行 + 断点续跑;按参数轴分片(见 8.4) |
| 参数轴合法性校验 | 部分 | 锁定键报错、确定性/未知键提示;键名有效性以 notes 提示为主 |
| CSP / robustness / 统计 / 显著性 | ❌ **(不属本软件)** | 由下游独立分析程序从统一峰表计算 |

## 9.2 NUS 的支持范围

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
