# 09 · v0.1 边界与扩展路径

## 9.1 当前支持 / 不支持

| 项目 | v0.1 | 说明 |
| --- | --- | --- |
| uniform 1D/2D/3D 数据 | ✅ 扫描 | 走 NMRPipe `process()` 路径;研究以 2D 为主 |
| NUS 数据 | ⚠️ 只能建参考谱 | 扫描会抛 `SweepError`(原因见下) |
| 3D 谱的峰位 | ✅ 可测 | 2D 峰表在 3D 谱上测量时,未参与测量的轴取最强平面 |
| 峰重叠/去卷积 | ❌ | 只做窗口内极值 + 抛物线 refine |
| 峰拟合(线宽/体积) | ❌ | 需要线宽误差请用你自己的拟合流程 |
| 并行/集群调度 | ❌ | 串行 + 断点续跑;可分片(见 9.3) |
| 参数轴合法性校验 | 部分 | 仅校验组合数上限;键写错会被后端忽略,请对照参数清单 |
| 重复采集/重复处理的噪声贡献 | ❌ | 结果只反映处理参数引入的离散度 |

## 9.2 为什么 NUS 扫描暂不支持

NUS 的终谱由 `reconstruct_nus`(SMILE 重构)产生,该入口目前:

- 不支持把候选谱写到独立文件名/目录(会覆盖工作目录里的终谱);
- SMILE 参数(nSigma/threshold/nthread 等)与处理参数在同一段脚本里,需要先给
  `reconstruct_nus` 加 `out_file`/`script_name` 隔离,才能做到「候选谱不替换
  参考谱 + 每组合可留档」。

扩展路径(在 NMRForge 侧,约一天量级):

1. `backend/nmrpipe_backend.reconstruct_nus` 增加 `out_file` / `script_name` 参数,
   与 `process()` 语义一致;
2. `nmrforge_api/sweep.py` 放宽 uniform 限制,按 `experiment.sampling.mode` 选择
   `process()` 或 `reconstruct_nus()`;
3. 参数轴加 `nus.nsigma` / `nus.thresh` / `nus.nthread` 等(点号键天然支持)。

在扩展落地前,若你需要 NUS/SMILE 结论,建议:

- 用本接口 NUS 数据的参考谱(参考优化仍然可用)作为基准;
- 在 NMRForge GUI 的「SMILE 优化」入口做参数扫描(2D NUS),再把候选谱交给
  `measure_peak_positions()` 测量峰位。

## 9.3 长扫描怎么切分

- 组合数上限默认 256(`max_runs`);超过请拆成多个研究根或分批网格;
- 同一研究根内可反复重跑,已成功的组合会跳过;
- 若要多机并行:按 `run_id` 分片没有接口内建支持,建议按**参数轴**拆分(各机器
  跑不同子网格、各自一个研究根),最后在分析侧合并 `peak_positions.csv`
  (同一参考谱与峰表保证可比)。

## 9.4 后续 roadmap(按需求排序)

| 优先级 | 项 | 交付形态 |
| --- | --- | --- |
| 高 | NUS/SMILE 参数扫描 | 后端候选输出隔离 + 扫描支持 + 文档 |
| 高 | 峰拟合(2D Lorentzian/Gaussian) | `measure_peak_positions(fit=...)` 或独立函数 |
| 中 | 参数轴校验 | 用 `param_schema()` 校验键与取值,报错而不是静默忽略 |
| 中 | 增量上报/进度文件 | 每组合更新 `records/progress.json`,便于外部监控 |
| 中 | 3D 平面显式指定 | 峰表带固定维取值时按平面测量 |
| 低 | 多数据集联合报告 | 跨数据集汇总 σ/Δδ 分布 |

需求请附 `records/manifest.json`(以及 `status` 输出),便于复现与定位。
