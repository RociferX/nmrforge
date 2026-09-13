# 09 · v0.1 边界与扩展路径

## 9.1 当前支持 / 不支持

| 项目 | v0.1 | 说明 |
| --- | --- | --- |
| uniform 1D/2D/3D 数据 | ✅ 扫描 | 走 NMRPipe `process()` 路径;研究以 2D 为主 |
| NUS **2D** 数据 | ✅ 扫描 | 走 `reconstruct_nus()`(SMILE 重构),候选谱隔离输出;可扫 SMILE 参数 |
| NUS 3D 数据 | ⚠️ 只能建参考谱 | 扫描会抛 `SweepError`(原因见 9.2) |
| 3D 谱的峰位 | ✅ 可测 | 2D 峰表在 3D 谱上测量时,未参与测量的轴取最强平面 |
| 峰重叠/去卷积 | ❌ | 只做窗口内极值 + 抛物线 refine |
| 峰拟合(线宽/体积) | ⚠️ 2D 高斯已可用 | `refine="gaussian"`(仅 2D)给 2D 中心/σ/FWHM/幅度/基线;Lorentzian、体积、多峰分解仍请用你自己的流程 |
| 并行/集群调度 | ❌ | 串行 + 断点续跑;可分片(见 9.3) |
| 参数轴合法性校验 | 部分 | 仅校验组合数上限;键写错会被后端忽略,请对照参数清单 |
| 重复采集/重复处理的噪声贡献 | ❌ | 结果只反映处理参数引入的离散度 |

### 9.1b 峰定位方法(抛物线 / 2D 高斯)

- `parabolic`(默认):与既有选峰/测量完全一致,是**参考方法**;
- `gaussian`:**仅 2D**,在候选峰附近拟合不旋转、轴向可分离的高斯(ROI 按 ppm
  物理宽度换算点数),失败回退抛物线并记录原因;
- 两种方法对同一批 candidate 独立运行,可直接比较「算法带来的峰位差」;
- 逐峰诊断在 `run.json` 的 `measurements[].localization` 与峰表附件
  `<峰表>.localization.json`;汇总计数在 `records/measurement.json`。

## 9.2 NUS 的支持范围

**已支持:2D NUS**。做法与 uniform 一致,只是处理入口从 `process()` 换成
`reconstruct_nus()`(SMILE 重构):

- 参考谱仍由自动优化产生(`build_reference`);NUS 的相位记在运行参数里
  (`phases` 间接维 + 扁平 `direct_phase` 直接维),扫描时两处都锁定;
- 每个组合把候选谱写到 `work/_intermediate/<run_id>.ft2`、脚本写到
  `work/<run_id>.com`,**不覆盖**工作目录里的终谱(后端新增 `out_file`/
  `script_name` 语义,与 `process()` 一致);
- 候选模式下后端会跳过「显示层相位搜索/重渲」,避免用未锁定的相位重渲候选谱;
- 可扫的 SMILE 参数用顶层键:`nsigma`(别名 `nSigma`)、`thresh`、`nthread`、
  `smile_scaling`(见 [05-inputs-and-data.md](05-inputs-and-data.md) 的 NUS 参数表);
  接口会把驼峰别名归一成后端输入键,避免「参数写了但没生效」。

**仍未支持:3D NUS**。3D NUS 的终跑走切片流(`nus3d_*` 平面目录),候选隔离还
需要按平面目录分桶与独立的 finalize 输出命名,属于下一项工作(见 9.4)。

### VM 真机证据:2D NUS(2026-09-12)

数据:公开库 BMRB bmr6980 的 15N-1H HSQC 全采样数据,用 NMRForge 自带工具
合成 2D NUS(`scripts/vm_sample_make_nus.py`,复点网格 128、采样 32 点 = 25%)。
流程:分步 CLI 四步(四个独立进程):

```bash
python -m nmrforge_api init      --study DIR --dataset <NUS 目录>
python -m nmrforge_api reference --study DIR      # 真实 SMILE 重构
python -m nmrforge_api peaks     --study DIR      # 软件自动选峰
python -m nmrforge_api sweep     --study DIR --grid grid.yaml   # nsigma 3/5/7
```

结果:参考谱由统一路线 + SMILE 重构产生(综合质量分 92.4、相位=统一自动、
`sampling=nus`、`sweep_supported=true`);自动选峰 99 个(`peak_source=auto`,
峰表 SHA-256 入档);3/3 组合 success,每组合 99 个峰位全部测到;
**三个候选谱 SHA-256 互不相同**(参数真的生效)。

本次 3 组合的峰位不确定度(仅链路验证,不构成研究结论):
`Δδ_std` min 0.00011 / median 0.0021 / p90 0.037 / max 0.072 ppm;
逐核 σ:15N median 0.0089 / p90 0.185 / max 0.357 ppm,
1H median 0.00071 / p90 0.0092 / max 0.0125 ppm。

真机跑出的两个缺陷(均已修复,见 CHANGELOG):

1. **分步 CLI 跨进程看不到已登记的谱**:`build_reference` 只改内存未写
   `project.json`,下一进程的 `peaks` 报「谱图缺失」;现参考构建/自动选峰/
   一步式流程都会落盘项目状态。
2. **SMILE 参数键不一致**:后端输入键是小写 `nsigma`,而运行记录回写
   `nSigma`;扫描轴写 `nSigma` 时被静默忽略,三个组合跑出**同一张谱**
   (Δδ 全 0)。现在后端两者都接受,接口把驼峰别名归一成输入键。

### VM 真机证据:显式组合表 + 相位偏差轴(2026-09-12)

数据:BMRB bmr6980 的 15N-1H HSQC(uniform,真实 NMRPipe)。组合表 4 行:

```text
window.F1.off,zero_fill,phase_delta.F2.p0,baseline.F1.enabled
0.35,1,-5,false
0.35,2,5,true
0.45,1,5,false
0.45,2,-5,true
```

```bash
python -m nmrforge_api sweep --study DIR --combos design.csv
```

结果:4/4 组合 success,每个组合 152 个峰位全部测到;**相位偏差精确生效**:
F2 = 22.5°(-5°)与 32.5°(+5°),参考相位 27.5°,而 F1 保持参考的 172.5°;
窗函数/填零/基线开关均按表执行(候选谱 SHA-256 互不相同)。
本次 4 组合的峰位不确定度(仅链路验证):`Δδ_std` min 0.0008 / median 0.0013 /
p90 0.0064 / max 0.038 ppm;15N σ median 0.0031 ppm。

## 9.3 长扫描怎么切分

- 组合数上限默认 256(`max_runs`);超过请拆成多个研究根或分批网格;
- 同一研究根内可反复重跑,已成功的组合会跳过;
- 若要多机并行:按 `run_id` 分片没有接口内建支持,建议按**参数轴**拆分(各机器
  跑不同子网格、各自一个研究根),最后在分析侧合并 `peak_positions.csv`
  (同一参考谱与峰表保证可比)。

## 9.4 后续 roadmap(按需求排序)

| 优先级 | 项 | 交付形态 |
| --- | --- | --- |
| 高 | 3D NUS 参数扫描 | 切片流按 `run_id` 分目录 + finalize 输出隔离 |
| 中 | 峰拟合扩展到 Lorentzian/Voigt/多峰分解 | 2D Gaussian 已实现(`refine="gaussian"`);其余仍在路线图 |
| 中 | 参数轴校验 | 用 `param_schema()` 校验键与取值,报错而不是静默忽略 |
| 中 | 增量上报/进度文件 | 每组合更新 `records/progress.json`,便于外部监控 |
| 中 | 3D 平面显式指定 | 峰表带固定维取值时按平面测量 |
| 低 | 多数据集联合报告 | 跨数据集汇总 σ/Δδ 分布 |

需求请附 `records/manifest.json`(以及 `status` 输出),便于复现与定位。
