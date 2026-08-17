# 相位优化两条途径(简单/进阶)设计记录

> 状态:实现与独立测试已完成,尚未接入现有 backend/stepwise 生产编排。
> 日期:2026-08-16。

## 1. 目标

把 NMR 相位优化统一成两条可选途径,都尽量贴合人工 nmrDraw 的工作方式:

- 人工做法:先正常处理一遍,打开谱图,逐维看一维谱,在显示层调相位使峰呈
  吸收形,记录每维 (p0, p1);同时肉眼判断基线是否平、峰是否圆润,决定
  POLY 和填零;把这些参数填回脚本重新生成,得到良谱。
- 自动做法:直接模仿以上顺序。关键事实是 nmrDraw 显示的谱是实型谱,一维
  谱可调相位是因为它对实型谱做希尔伯特变换重建了虚部,旋转后取实部观察。
  因此自动流程也在实型谱上做逐维希尔伯特显示层调相,而不是重新制造复型
  中间谱。

## 2. 两条途径

### 2.1 简单途径(新方法,所有谱型统一)

思路:先跑一遍拿谱,再在显示层评估,再重跑一遍。

1. 第一遍:uniform 调 `backend.process`,NUS 调 `backend.reconstruct_nus`,
   都关闭直接维搜索 / 显示层搜索,得到实型终谱。
2. 显示层评估:读终谱,对每个维度:
   - 希尔伯特变换重建该维虚部;
   - 选信号峰;
   - 跨峰相位集中度拟合 p1;
   - 锚定峰圆均值拟合 p0;
   - 用对称性 + 正峰约束评分验证;
   - 同时评估基线(两端/中部均值)和峰圆润度(最强峰半高宽)。
3. 第二遍:把每维 (p0, p1) 填回脚本:
   - uniform:所有维度写入 `process` 的 direct_phase_override;
   - NUS:直接维写回 `reconstruct_nus` 的 direct_phase_override,间接维写回
     `finalize_nus` 的 phases。
4. 得到良谱。后端运行次数:uniform 2 次;NUS 3 次(SMILE 两次 + finalize
   一次)。

特点:成本低、可重复、贴近熟练人工;质量上限受希尔伯特重建和对称性评分
与真实后端 PS 之间的约定偏移影响,需要真实数据校准。

### 2.2 进阶途径(旧方法 + NUS 混合)

思路:uniform 仍用旧方法全维度重跑后端优化;NUS 因为直接维重跑 SMILE
代价大,直接维走简单途径的显示层,间接维走逐候选后端优化。

- uniform:委托现有 `workflow.phase_optimize.optimize_phase_sequential`,
  所有维度逐候选跑后端评分(几十次 backend)。
- NUS 混合流程(`workflow.display_hybrid_optimize.optimize_nus_hybrid`):
  1. 第一遍 SMILE(直接维 PS(0,0))得到实型终谱;
  2. 终谱直接维做希尔伯特显示层调相,记录 (p0, p1);
  3. 用记录的相位重跑 SMILE,得到带正确直接维相位的复型平面;
  4. 以这些复型平面为起点,对每个间接维逐候选跑 finalize 并评分,选最优;
  5. 用全部最优相位跑最后一次 finalize,生成良谱。

特点:uniform 保留「真实谱 + 客观评分」的系统性搜索;NUS 在直接维无法
几十次重跑 SMILE 的约束下,把显示层估计作为直接维相位来源,间接维仍用
真实后端候选优化。

## 3. 详细实现

### 3.1 显示层引擎

文件:`core/optimization/display_phase_engine.py`。

数据对象:

- `AxisPhaseEstimate(axis, p0, p1, score, windows)`
- `BaselineEstimate(axis, mode, order, score)`
- `FillEstimate(axis, fwhm_points, suggested, ratio)`

核心函数:

- `analytic_axis(real, axis)`:沿指定轴做希尔伯特变换,返回解析信号。
- `search_axis_phase(real, axis)`:
  - `_row_peaks`:一条 1D 谱取 top-K 局部峰(噪声/阈值过滤);
  - `_p1_concentration`:候选 p1 去掉斜坡后各峰单位向量圆均值模最大,
    粗网格 + 两级细化,得到信号斜坡 p1;
  - p0:用最强迹线锚定,移除 p1 斜坡后峰相位加权圆均值取负,±180 用
    对称性评分正峰消歧;
  - `_anchor_score`:在锚定迹线的峰窗口内做显示层旋转,取实部,用
    `_symmetry_score` 评分;
  - 局部 ±10°/±5° 细化,p1 与零相位分差小于 1 时归零;
  - 门控:`|p1| > 20°` 归零(单峰窗口对 p1 天然不敏感,防止噪声斜坡)。
- `assess_baseline(real, axis)`:取两端 8% 与中部 8% 均值,归一化
  slope/curvature/offset;curvature > 0.05 判 order 2,slope > 0.05 判
  order 1,否则 auto(关闭基线)。
- `assess_fill(real, axis)`:其它维 max 投影得到最强峰,计算半高宽 FWHM;
  FWHM < 目标点数(默认 4)建议填零。
- `inspect_spectrum(real, axes=None)`:一次性逐维返回
  `{"phases", "baselines", "fills"}`。

### 3.2 NUS 混合编排

文件:`workflow/display_hybrid_optimize.py`。

- `direct_axis_from_header(header, nucleus)`:按 FDF1/FDF2/FDF3 标签匹配
  直接维核素,得到谱数组轴索引。
- `estimate_direct_phase(spectrum_path, experiment)`:读终谱,在直接维上
  调 `search_axis_phase`,返回 (p0, p1, score)。
- `optimize_nus_hybrid(experiment, backend, *, p0_values, p1_values,
  score_fn, work_dir, base_params)`:
  1. `backend.reconstruct_nus`(direct/display search 关闭);
  2. `estimate_direct_phase`;
  3. `backend.reconstruct_nus`(direct_phase_override 为估计相位);
  4. 对每个间接维逐候选 `backend.finalize_nus` + `score_fn` 评分;
  5. 最终 `backend.finalize_nus` 一次。
  返回 `phases`、`backend_runs`、`candidates_scored`、`spectrum_path`、
  `direct_phase`、`logs`。

### 3.3 途径分派

文件:`workflow/phase_routes.py`。

- `axis_to_logical(experiment, axis)`:谱数组轴索引映射到 F1/F2/F3 逻辑轴。
- `estimate_all_axes(spectrum_path, experiment)`:读实型谱,逐维显示层估计,
  返回 `{逻辑轴: (p0, p1)}`。
- `simple_route(experiment, backend, ...)`:所有谱型两遍流程(见 2.1)。
- `advanced_route(experiment, backend, ...)`:
  - NUS → `optimize_nus_hybrid`;
  - uniform → `workflow.phase_optimize.optimize_phase_sequential`。

## 3.4 显示层相位应用原则(0.2.102)

- 显示层虚部重建、相位旋转、取实部,优先直接调用 nmrPipe 函数
  (HT / PS -ht),不自己用 numpy/scipy 模拟;只有能严格证明 numpy 与
  nmrPipe 逐位等价时才可替换,否则以 nmrPipe 函数为准。
- 每一维的 HT 符号/轴必须对照该维生成脚本中的 FT/TP/EXT 变换,逐维
  转置到管道轴后再 HT;镜像 Hilbert(-ps90-180)按该维频率方向选择。
- 候选显示谱用 nmrPipe PS -p0 -p1 -ht -di 生成,再复用进阶版固定迹线
  净吸收评分,避免 numpy 模拟旋转引入残差。

- 峰位锁定差异:进阶版在基础谱固定锁定(真实后端候选谱峰位稳定);简单版
  在 PS -ht 候选显示谱上动态锁定,因为 HT 重建的谱峰位随相位变化,固定
  锁定会评分失真。这是除虚部来源外当前唯一实现差异,已明确记录。

## 4. 关键约定与门控

- 显示层相位是「校正相位」:对解析信号乘
  `exp(i·(p0 + p1·k/(n-1)))` 后峰呈吸收形;脚本回填时 p0/p1 作为 PS 的
  校正值。
- 希尔伯特重建是实型谱的虚部估计,不是原始 FID 的真实虚部;与 nmrPipe PS
  的真实频域旋转约定可能带系统偏移,需要在 sampleI、sampleB 上做一次显示层
  相位 ↔ PS 相位标定。
- p1 在单峰窗口区分度弱,当前门控 |p1| > 20° 归零;如果保留 p1,必须返回
  「p1 是否可信」而不是盲目写入脚本。
- 基线/填零评估目前在引擎中独立可用,尚未在 simple_route 中写入
  baseline/zero_fill 参数(下一步落地)。

## 5. 测试

- `tests/test_display_phase_engine.py`:希尔伯特逐维相位恢复、近零相位、
  基线阶数、窄峰填零建议。
- `tests/test_display_hybrid_optimize.py`:NUS 混合流程的调用顺序、直接维
  override、间接维候选选择、后端次数。
- `tests/test_phase_routes.py`:simple/advanced 分派、uniform 两遍、
  NUS 直接维 override + 间接维 finalize、进阶 uniform 委托旧优化器。

## 6. 待办(接入生产)

- 把 simple_route / advanced_route 接进 stepwise 或 AutoProcessor 的
  生成谱图步骤;
- 真实数据校准显示层相位与 PS 相位约定;
- 把 assess_baseline / assess_fill 的结果映射为 baseline / zero_fill
  参数并写回重生成;
- NUS 混合路径确认复型平面复用,避免第二次 reconstruct 之外再触发 SMILE。


## 7. 最终统一方案(2026-08-17,替代简单/进阶分派)

结论:不再分简单/进阶两条途径,统一为一种途径;显示层虚部必须是
FID 复型 FT 后的真实虚部,不再用 HT 重建。

### 流程

1. 第一遍:每个维度只做 FT(直接维保留 EXT 窗口),所有 PS 都不加 -di;
   按轴把该维作为管道轴单独 `pipe2xyz` 输出复型文件:
   - 直接维:`-x`;
   - 2D 间接维 F1:`-y`;
   - 3D F2:`-y`,F1:`-z`;
   - NUS 间接维直接复用 SMILE recon 复型平面(nus2d/recon.ft1 /
     nus3d_rc/test%04d.ft1)。
2. 显示层调相:读取这些真实复型文件,在内存做频域旋转取实部,用
   固定迹线净吸收评分逐维搜 (p0,p1);零额外后端;NUS 间接维做 ±90°
   消歧即可。
3. 最后完整重跑:窗函数、填零、基线、各维 PS(填入相位)、EXT、-di,
   一次生成良谱。

### 关键原则

- 后端只跑两次:第一遍出复型数据,最后一遍完整处理;调相在内存。
- 所有维度都保留真实虚部,不使用 nmrPipe HT / scipy hilbert。
- 删除 simple/advanced 分派;`params["phase_route"]="none"` 保留为
  旧暴力路径的逃生口。
- NUS SMILE 能否接受复型直接维输入需 VM 实验确认后,再改 stage1 的
  `-di`;在此之前 NUS 直接维暂保持实型并待实验。

### 状态

- uniform 直接维保留真实虚部已落地(sampleI F2 300°/F1 95°,残差 ≤10°);
- 逐维复型输出、NUS 复型输入、统一分派、删除 HT 路径为待办。
