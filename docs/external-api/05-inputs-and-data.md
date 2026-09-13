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
| `phase_delta.F2.p0` | **相位识别偏差**(相对参考相位,度) | `[-5, 0, 5]` |
| `phase_delta.F1.p0` | 间接维相位偏差(度) | `[-5, 5]` |
| `phase.F2.p0` | 相位绝对值(不想相对参考时用) | `[12, 17, 22]` |

**确定性 / 策略参数(一般不进网格)**——接口会执行,但会在 `plan.notes` 里提示:

| 键 | 为什么不建议进网格 |
| --- | --- |
| `extract` / `ext_lo` / `ext_hi` | 改了会**换峰集**(峰被裁掉/越界),不是位置不确定度的自由度 |
| `points_per_line` | 数字点距目标,由填零与线宽决定的分辨率口径 |
| `direct_poly_time` | 直接维 POLY `-time` 策略开关(首遍/终跑约定) |
| `nuslist_file` / `nuslist_count` / `timeout_s` | 输入与运行参数,不是处理方法 |
| `fid_noise` / `fid_noise_seed` | 专门用于「重复性/去伪峰」实验,不是常规扫描轴 |
| `keep_direct_complex` / `keep_complex_all` | 特殊用途开关 |

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
- **相位**:不要直接写 `phases` / `direct_phase`(接口会直接报错)——相位自由度
  用 `phase_delta.<轴>.p0|p1`(相对参考,如 ±5°)或 `phase.<轴>.p0|p1`(绝对值);
- `sampling.auto_phase` / `phase_route` 同样不能进网格(由参考运行决定);
- 组合数 = 各轴长度之积(用 `axes` 时),超过 `max_runs`(默认 256)直接报错;
  长扫描请分批,同一研究根可以续跑。

## 5.5 扫描参数口径(精确定义)

一次扫描由三件事唯一确定:**基底、轴、合并**。

1. **基底(base)** = 参考运行记录的有效参数(`reference.sweep_params`):自动优化
   选出的 `window` / `baseline` / `zero_fill` / `linewidth_hz` / `points_per_line`、
   提取窗口(`extract` / `ext_lo` / `ext_hi`)、NUS 的 SMILE 参数等。
   接口先剥掉**运行期派生键**(不参与扫描):
   `phase_route`、`preview_axis`、`projections`、`backend_runs`、`diagnostics`、
   `fill`、`nus`、`final_ext_lo`、`final_ext_hi`、`segment_shift_hz`。
   → **没写进网格的参数,一律保持参考值**。
2. **轴** = 二选一:
   - `axes`:点号键 → 候选值列表,接口展开**全因子**(便捷路径);
   - `combos`:**外部给定的组合表**——正交表/部分因子/D-optimal/LHS/手挑都行,
     接口**原样按表序执行,不做任何设计决策**;
3. **合并** = 每个组合在基底上做**深合并**(`window.F1.off` 只覆盖 `F1.off`,
   同层其它键保留),基底本身不被修改。

### 设计由外部决定(接口只提供入口与核对)

组合规模可能很大,所以**用什么设计由你们决定**,接口负责接收、校验、执行、留档:

```python
from nmrforge_api import load_combo_table, plan_sweep, design_diagnostics

combos = load_combo_table("design.csv")   # CSV/TSV/YAML/JSON:一行一个组合
plan = plan_sweep(reference, combos=combos)   # design="explicit",按表序执行
print(plan.diagnostics)   # 水平计数 / 重复行 / 成对相关最大|r| / 缺失水平
```

- 外部工具(pyDOE2、Taguchi 正交表、LHS、D-optimal、手写表)只要能导出
  「一行一个组合」的 CSV/TSV 或 YAML/JSON 列表即可直接跑;
- 也可以先用 `axes` 全因子,再把 `plan.combos` 导出(`write_combo_table`)去筛选;
- `design_diagnostics` 只做**信息性核对**(水平均衡、重复、两两因子相关),
  不替你做设计决策;`grid_sha256` 让两次扫描可比对;
- 显式组合表同样受 `max_runs` 上限与键校验约束。

### 峰位窗口与边距:物理宽度(ppm)口径

选峰的边缘排除、峰位测量的搜索窗口都是**物理量**,缺省按该轴核素线宽定义,
运行时按当前谱的点距换算成点数:

| 量 | 缺省物理宽度 | 换算 |
| --- | --- | --- |
| 选峰轴峰排除边距(第 0 轴上下) | 3 × 该轴核素线宽(Hz) 折算 ppm | 按参考谱点距 |
| 峰位搜索窗口半径 | 1.5 × 该轴核素线宽(Hz) 折算 ppm | 逐组合按候选谱点距 |

线宽(Hz)取自 `config/nmrforge.yaml` 的 `processing.linewidth_hz`(与自动填零
用的是同一张表),因此口径一致、可改。换算:`points = round(width_ppm /
ppm_per_point)`,实现见 `core/peaks/axis_units.py`。

```python
from nmrforge_api import measure_peak_positions, window_points_by_axis
from workflow.pick_peaks import read_spectrum_axes

axes = read_spectrum_axes(spectrum)
print(window_points_by_axis(axes))            # 缺省:1.5×线宽
print(window_points_by_axis(axes, window_ppm=0.5))   # 显式物理半径
measure_peak_positions(spectrum, peaks, window_ppm=0.5, axes=axes)
```

为什么要这样:填零 k 倍只让网格变密(点距 1/k)。同一个「5 点」在 1× 与 4×
下覆盖的 ppm 宽度差 4 倍,峰集与测量窗口口径就会随处理参数漂移——而填零正是
研究的自变量之一。**结构性点数**(局部极大 3 点邻域、抛物线 ±1 点模板)不换算:
它们必须等于网格步长本身。

逃生口:`edge_margin_ppm=` / `edge_margin_points=`(`pick_peaks`)、
`window_ppm=` / `window_pts=`(`measure_peak_positions`、`run_sweep`、
`run_parameter_study`、CLI `sweep --window-ppm/--window-pts`)。
每次换算的实际值(点数 + 等效 ppm + 点距 + 来源)都会留档:`run.json` 的
`window`、`records/measurement.json`、`manifest.json` 的 `measurement`,
以及选峰运行参数里的 `detection`。

### 峰定位方法:抛物线 / 2D 高斯拟合

**检测**与**定位**是两步、彼此独立(检测阈值/噪声估计/符号模式一律不变):

```text
peak detection(阈值/局部极大) → candidate 整数格极大值
        ↓ peak localization
  ├── parabolic(默认,既有 3 点抛物线顶点;行为完全不变)
  └── gaussian(2D 不旋转高斯拟合;仅 2D)
```

高斯模型 ``I(x,y) = B + A·exp(-(x-x0)²/2σx² - (y-y0)²/2σy²)``,x=F2(直接)、
y=F1(间接);初值中心取**抛物线结果**(同一 candidate),`scipy.optimize.
least_squares` 带 bounds 求解。ROI 是**物理半径(ppm)**,按当前谱点距换算点数
——与窗口/边距同一口径,填零不改变 ROI 覆盖的 ppm 范围。

| 口径 | 默认 | 覆盖方式 |
| --- | --- | --- |
| 方法 | `parabolic` | config `peaks.localization.method` / `localization_method=` / `refine=` |
| 高斯 ROI F1(间接) | 1.5 ppm | config `peaks.localization.gaussian_roi_f1_ppm` / `roi_f1_ppm=` |
| 高斯 ROI F2(直接) | 0.25 ppm | config `peaks.localization.gaussian_roi_f2_ppm` / `roi_f2_ppm=` |

失败(ROI 太小/数据不足/NaN/平坦区/不收敛/中心或 σ 撞边界)一律:
``fit_success=false`` + ``fallback_reason``,**回退抛物线**并在记录里写明
``requested_method=gaussian`` / ``actual_method=parabolic``(不静默)。非 2D 谱
调用高斯会直接报错 ``Gaussian peak fitting is currently supported only for 2D
spectra.``(GUI 对应项在非 2D 上禁用)。

留档位置:峰表附件 ``data/peaks/<exp>-<data>.list.localization.json``(逐峰
中心/σ/FWHM/幅度/基线/RMSE/边界/原因)、`WorkflowRun.params["localization"]`
(方法 + ROI + 成功/回退计数),研究接口侧另见 `measurement.json`。

### 网格语义

- 用 `axes` 时组合数 = 各轴长度之积,顺序 = 轴书写顺序 × 值列表顺序(笛卡尔积);
  用 `combos` 时按表序执行,顺序即表格行序;
- 超过 `max_runs`(默认 256)直接报错,不静默截断;
- `grid_sha256` = 组合列表的规范 JSON 哈希(键排序),写入 `sweep_plan.json` 与
  `manifest.json`,可核对两次扫描是否同一网格。

### 真正生效的参数键(必须与后端输入约定一致)

**uniform(`process()` 读取)**

| 键 | 说明 |
| --- | --- |
| `extract` / `ext_lo` / `ext_hi` | 直接维提取窗口开关与边界(ppm) |
| `window` | 逐轴窗函数:`{轴: {type, off, end, pow, c, lb, g1, g2}}` |
| `baseline` | 逐轴基线:`{轴: {enabled, mode("auto"/"order"), order}}` |
| `zero_fill` | `0`=全自动;`k≥1`=间接维固定 k×TD;或 `{轴: {mode, size}}` |
| `linewidth_hz` / `points_per_line` | 间接维自动填零的目标线宽/数字点距 |
| `sampling` | `ft_neg` / `ft_alt` / `flip_f1` |
| `direct_poly_time` | 直接维 POLY `-time` 开关 |
| `keep_direct_complex` / `keep_complex_all` | 保留复型的特殊用途开关 |

**2D NUS(`reconstruct_nus()` 读取)**

| 键 | 说明 |
| --- | --- |
| `nsigma`(别名 `nSigma`) / `thresh` | SMILE 重构门限(接口会把别名归一成输入键) |
| `nthread` / `smile_scaling` / `smile_report` | SMILE 线程、缩放、报告 |
| `extract` / `ext_lo` / `ext_hi` / `window` / `baseline` / `zero_fill` | 同 uniform |
| `linewidth_hz` / `points_per_line` | 同 uniform |
| `direct_poly_time` / `sampling`(`ft_neg`/`ft_alt`/`flip_f1`) | 同 uniform |
| `nuslist_file` / `nuslist_count` | 采样表与采样点数(一般不改) |
| `fid_noise` / `fid_noise_seed` | 注入噪声重复性实验(特殊用途) |
| `timeout_s` | 单次重构超时(秒) |

### 锁定(直接写会报错)

| 键 | 原因 | 正确做法 |
| --- | --- | --- |
| `phases` / `direct_phase` | 直接覆盖会破坏参考相位基准 | 用 `phase_delta.<轴>.p0\|p1`(偏差)或 `phase.<轴>.p0\|p1`(绝对值) |
| `sampling.auto_phase` / `phase_route` | 由参考运行/接口决定 | — |
| `light_phase_search` / `display_phase_search` | 仅影响参考构建;候选模式强制跳过重渲 | — |

> **写错的键不会报错**(后端按缺省值处理),因此判断「参数是否真的生效」最可靠的
> 办法是看候选谱的 SHA-256 是否不同(或看 `delta_std` 是否为 0)。例如 SMILE 的
> `nsigma` 写成后端不认的键时,三个组合会跑出同一张谱、Δδ 全为 0。

## 5.6 资源与时长预期

- 参考谱:一次完整自动优化(含统一相位优化),2D HSQC 量级约 30 s;
- 每个组合:复用 fid + 锁定相位,约数秒(取决于数据规模与机器);
- 磁盘:每个组合保留一份脚本(几 KB)与一份候选谱;若谱很大,注意研究根所在
  磁盘余量(`study/runs/<run_id>/`);
- 内存:与 NMRForge 常规处理一致;3D/NUS 不在 v0.1 扫描范围。
