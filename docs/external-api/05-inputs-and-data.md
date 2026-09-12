# 05 · 输入与数据要求

## 5.1 数据必须是什么

**Bruker 原始数据目录**(含有 `acqus`),典型内容:

```text
bmrXXXXX/1/
  acqus  acqu2s  (acqu3s)     ← 采集参数(必有 acqus)
  ser  (或 fid)               ← 时域数据
  nuslist                     ← 仅 NUS 数据
  pdata/                      ← 已处理结果,本接口不使用
```

为什么必须是原始数据:本研究的自变量是**处理**参数,只有从时域重跑处理才有意义。
已处理谱(ft2/ft3/ucsf)无法回答这个问题。

导入时会做策略守卫(例如 Kinetics 实验直接拒绝),不会把不支持的数据静默收进来。

## 5.2 从公开库获取

常见来源:BMRB(含原始 Bruker 数据)、PDB 相关沉积、以及各实验室公开的
Bruker 原始目录。下载后:

1. 解压到工作目录(例如 `~/bruker_data/bmrXXXXX/1`);
2. 确认目录里直接有 `acqus`(有的压缩包会多套一层子目录);
3. 直接把它作为 `dataset=` / `--dataset` 传入——不需要先做任何处理。

快速校验(不需要 NMRForge):

```bash
ls ~/bruker_data/bmrXXXXX/1/acqus && echo OK
```

多段/重复叠加采集的目录容器也支持(走 NMRForge 的分段导入逻辑)。

## 5.3 参考峰位怎么来(默认:不用你准备)

默认情况下,**参考峰位由 NMRForge 在参考谱上自动选峰**:

```python
run_parameter_study(..., sigma_multiplier=None, max_peaks=0)   # 默认 35σ,取全部
```

- 阈值 `sigma_multiplier`:越小选得越多(更容易纳入弱峰/噪声);
- `max_peaks > 0`:只保留强度前 N 个峰(先去弱峰,再进入扫描);
- 结果峰表冻结为 `study/reference/<exp>_<data>/reference.list`,并记录
  SHA-256、峰数、来源 `auto` 与选峰参数。

**可选**:若你另有公开库指认表或自己整理的峰表,传 `peaks=`(或
`--peak-table`)即可。支持两种格式:

```text
# 1) Poky/Sparky .list(NMRForge 标准峰表)
Assignment  w1      w2     Data  Height  Volume
G1          119.03  5.012  0     120     0

# 2) 研究项目 CSV(公开库导出的常见格式,按表头自动判别)
peak_id,H_ppm,N_ppm,height,linewidth,volume
1:LEU10,8.211,122.733,0.0,0.0,0.0
```

两种来源都会在 `reference.json` / `manifest.json` 里留档(`peak_source` 为
`auto` 或 `external`)。**注意**:峰集不同会改变不确定度估计(实测同一数据上
两套峰集的中位 Δδ 可差数倍),比较不同研究时请固定峰集来源。

## 5.4 参数轴怎么写

`axes` 的键是**点号路径**,指向处理参数;值是候选列表。常用轴:

| 键 | 含义 | 取值示例 |
| --- | --- | --- |
| `zero_fill` | 间接维填零倍数(数字分辨率) | `[1, 2, 4]`(0=自动) |
| `points_per_line` | 间接维目标数字点距(每线宽点数) | `[2.0, 4.0]` |
| `linewidth_hz.F1` | 间接维估计线宽(Hz),影响自动填零 | `[10, 15, 20]` |
| `window.F1.off` | 间接维窗函数起始位置 | `[0.35, 0.45, 0.55]` |
| `window.F1.end` | 间接维窗函数结束位置 | `[0.90, 0.98]` |
| `window.F1.type` | 窗型:`sine_bell`/`sine_bell_squared`/`exp`/`gaussian`/`none` | `["sine_bell", "exp"]` |
| `baseline.F1.enabled` | 间接维基线开关(关=不加 POLY) | `[True, False]` |
| `baseline.F1.mode` | 间接维基线模式(`auto`→`POLY -auto`;`order`→`POLY -ord N`) | `["auto", "order"]` |
| `baseline.F1.order` | 仅 `mode="order"` 时生效的阶数 | `[1, 2, 3]` |
| `extract` / `ext_lo` / `ext_hi` | 直接维提取窗口开关与边界(ppm) | `[True]` / `["10.5"]` |
| `direct_poly_time` | 直接维 POLY `-time` 开关 | `[False, True]` |

**NUS 2D 专用(SMILE 重构)**:

| 键 | 含义 | 取值示例 |
| --- | --- | --- |
| `nsigma`(别名 `nSigma`) | SMILE 重构 nSigma(峰的稀疏/噪声门限) | `[3.0, 5.0, 7.0]` |
| `thresh` | SMILE 重构 thresh(峰阈值) | `[0.90, 0.95, 0.99]` |
| `nthread`(别名 `nThread`) | SMILE 线程数(受机器核数上限约束) | `[2, 4]` |
| `smile_scaling` | SMILE 幅度缩放开关 | `[True, False]` |

> 键名说明:后端输入约定是小写 `nsigma`,而运行记录/结果里回写的是
> `nSigma`;接口两者都接受并统一成输入键,所以写哪个都能生效。

> NUS 数据必须是 **2D**(3D NUS 扫描未开放);需要目录里有 `nuslist`。
> 参考谱与扫描共用同一份转换后的 fid,相位(间接维 `phases` + 直接维
> `direct_phase`)全部锁定在参考值。

说明:

- 轴上写 `F1` 是**逻辑维**(F1 = 间接, F2 = 直接;3D 时 F3 = 直接);
- 未列出的处理计划参数同样可以用点号路径覆盖(见
  `backend/script_generator.py::param_schema()` 的完整清单);
- **不要**把 `phases` / `sampling.auto_phase` / `phase_route` 放进网格:它们
  会破坏「相位锁定在参考值」的前提(接口不会阻止,但结果不可比);
- 组合数 = 各轴长度之积,超过 `max_runs`(默认 256)直接报错。长扫描请分批,
  同一研究根可以续跑。

## 5.5 资源与时长预期

- 参考谱:一次完整自动优化(含统一相位优化),2D HSQC 量级约 30 s;
- 每个组合:复用 fid + 锁定相位,约数秒(取决于数据规模与机器);
- 磁盘:每个组合保留一份脚本(几 KB)与一份候选谱;若谱很大,注意研究根所在
  磁盘余量(`study/runs/<run_id>/`);
- 内存:与 NMRForge 常规处理一致;3D/NUS 不在 v0.1 扫描范围。
