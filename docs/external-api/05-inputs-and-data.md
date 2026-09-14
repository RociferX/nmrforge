# 05 · 输入:数据、条件与参数组合表(v0.2)

## 5.1 原始数据

- **Bruker 原始数据集目录**(下载/解压后含 `acqus`/`ser`);压缩包与已处理格式
  不支持,报错信息会说明;
- 导入只做链接 raw + 写 metadata + 登记 import 运行;不做转换/处理;
- 采样方式:uniform 任意维、**2D NUS**(SMILE 重构)、3D NUS 目前只支持建参考;
- **满采样优先**:标注 NUS 但实际满采样(`nuslist` 覆盖全格,或 2D `ser` 全格
  无零行)→ 按 uniform 处理,理由见 `reference.sampling_evidence`;

## 5.2 条件(A/B…)

```python
run_parameter_study(root, datasets={"A": "…/apo", "B": "…/holo"}, combos=[...])
# 或分步:
add_dataset(session, "…/apo", condition="A")
add_dataset(session, "…/holo", condition="B")
```

- 条件标签必须唯一(重复报错,不覆盖);缺省自动分配 A/B/C…;
- 每个条件各有一份**参考**(相位/噪声来自该条件自身数据);
- 峰身份、用户参数组合**全条件共享**:同一个 workflow 对 A/B 用同一份
  `parameters_requested`,输出各自峰值表。

## 5.3 参数组合表(用户定义)

两种入口(必须且只能给一个):

1. **显式组合表**(推荐,设计由外部决定,接口不做设计决策):

```csv
window.F1.off,zero_fill,phase_delta.F2.p0
0.35,1,-5
0.45,2,5
```

```python
run_parameter_study(root, datasets={"A": path}, combos=load_combo_table("design.csv"))
```

2. **轴网格**(便捷全因子展开):

```yaml
axes:
  zero_fill: [1, 2, 4]
  "window.F1.off": [0.35, 0.45, 0.55]
max_runs: 128
```

组合数上限 `max_runs`(缺省 256);组合顺序 = 表顺序 = workflow 编号顺序。
**空单元格 / `null` = 该行不指定该参数**(沿用参考基底),不是「覆盖成空值」。

## 5.4 参数键

### 处理参数(处理后端直接读取)

| 键 | 含义 |
| --- | --- |
| `zero_fill` | 填零倍数(只改点距,不改物理峰位) |
| `window.<轴>.off/end` | 窗函数参数(`window.F1.off` 等) |
| `baseline` | 基线校正(`{enabled, mode: auto|order, order, axes}`) |
| `ext_lo`/`ext_hi`/`extract` | 提取窗口(确定性参数,一般不必进网格) |
| `points_per_line` | 目标点距/线宽点数(确定性参数) |
| `linewidth_hz` | 各核线宽(Hz),影响物理宽度换算的缺省 |
| `sampling.auto_phase` 等 | 采样/相位开关(锁定键,直接写会报错) |

### 相位轴(保留前缀)

- `phase_delta.<轴>.p0|p1`:**相对参考相位的偏差**(如人工识别偏差 ±5°);
- `phase.<轴>.p0|p1`:**绝对相位值**;
- 直接写 `phases`/`direct_phase` 会报错(会破坏相位锁定语义)——请用上面两种。

### NUS(SMILE)参数

| 键 | 含义 |
| --- | --- |
| `nsigma`(别名 `nSigma`) | SMILE 阈值倍数;不写则由采样率自动分档 |
| `thresh` | SMILE 阈值 |
| `nthread`(别名 `nThread`) | 线程数 |
| `smile_scaling` / `smile_report` | 缩放/报告开关 |
| `nuslist_file` / `nuslist_count` / `timeout_s` / `fid_noise*` | 确定性/策略参数(只提示) |

自动分档时**实际使用**的 `nsigma`/`thresh` 会写进
`parameters_resolved.smile`(requested = `auto(smile_tier)`,actual = 实际值)。

## 5.5 参考峰表(可选输入)

```python
run_parameter_study(..., peaks="library.list")   # 或 peak_id,H_ppm,N_ppm CSV
```

- 缺省不用给:软件自动选峰并建立峰身份 `R0001…`;
- 给了外部峰表:作为主条件的峰身份表冻结(`peak_source="external"`),其他条件
  沿用同一身份;
- 接受格式:Poky/Sparky `.list`、NMRForge 旧 CSV、研究项目
  `peak_id,H_ppm,N_ppm,height,linewidth,volume`。

## 5.6 参数原则(软件不越界)

- **严格按用户参数运行**:该条件的基底 = 参考运行的有效参数,组合表只覆盖它
  显式指定的键;软件不自行修改用户指定参数;
- **不自动生成研究参数空间**:`combos=` 原样执行,`axes` 只是便捷展开;
- 参数非法给 **error 或 warning**:锁定键报错、确定性/未知键写 `plan.notes`;
- 一切影响结果的参数都必须可追溯:`parameters_requested` →
  `parameters_used` → `parameters_resolved`(自动参数实际结果)。

## 5.9 按维指定参数(组合表)

组合表/网格键支持**点号路径**,因此两个维度(以及 3D 的第三维)可以分别指定;
`parameters_requested` 保留用户原样的键,`parameters_used` 是合并后的逐轴结构。

| 处理环节 | 逐轴写法(示例) | 语义 |
| --- | --- | --- |
| 窗函数 | `window.F1.off`、`window.F2.off`、`window.F1.type` | 每个逻辑维一套(类型/端值) |
| 基线 | `baseline.F1.enabled`、`baseline.F2.mode`、`baseline.F1.order` | 每维开关/模式/阶数 |
| 填零 | `zero_fill.F1=2`、`zero_fill.F1.size=512`、`zero_fill.F1.mode=none` | 裸标量 = **k×TD**(与全局 `zero_fill=k` 同义);显式 SI 用 `.size` |
| 线宽(Hz) | `linewidth_hz.F1=12`、`linewidth_hz.F2=9` | 每维线宽:影响自动填零目标与物理宽度换算 |
| 目标数字分辨率 | `points_per_line.F1=4`、`points_per_line.F2=2` | 每维“每线宽点数”(自动 SI 的目标) |
| 相位 | `phase.F1.p0`、`phase_delta.F2.p0` | 逐轴绝对相位 / 相对参考的偏差 |
| 采样开关 | `sampling.*` | 锁定键(相位锁定语义),写进组合表会报错 |

```csv
window.F1.off,window.F2.off,zero_fill.F1,baseline.F2.enabled,points_per_line.F1
0.35,0.45,2,false,4
0.45,0.45,4,true,2
```

- 高斯拟合预算(config `peaks.localization`,2026-09-14):`gaussian_roi_max_points`
  (每轴半宽点数上限,默认 48)、`gaussian_max_nfev`(单峰求值上限,默认 200);
  两者只影响**成本**,触发截断时会留档;
- 直接维范围 `ext_lo`/`ext_hi` 只作用于**直接维**;3D 数据请用 `window.F3.*` 等
  逐轴键(若该轴是直接维);
- 参考层的逐轴参数(参考谱定义)用参考模式的 `params=`/`direct_range=` 指定,
  组合表里的键只覆盖**该组合**;
- 未知轴的键(如 `window.F9.off`)不会报错,但也不会生效:请对照上表核对轴名。


## 5.8 直接维范围(可由外部指定)

直接维提取窗口用 **ppm** 指定,与 NMRPipe `EXT -x1/-xn` 及 config
`processing.ext_lo/ext_hi` 同序:

- `ext_lo` = 直接维**高端**(较大 ppm,对应 `EXT -x1`);
- `ext_hi` = 直接维**低端**(较小 ppm,对应 `EXT -xn`)。

三种写法(等价,后者覆盖前者):

```python
run_reference_study(root, dataset, direct_range=(10.5, 6.5))       # (high, low)
run_reference_study(root, dataset, direct_range=(6.5, 10.5))       # 反序:自动换回
run_reference_study(root, dataset, ext_lo="10.5", ext_hi="6.5")    # 显式
```

```bash
python -m nmrforge_api reference --study ~/studies/s1 --direct-range 10.5 6.5
python -m nmrforge_api sweep --study ~/studies/s1 --reference ~/studies/s1 \
    --combos design.csv --direct-range 9 7
```

- **参考模式**:范围是参考谱的定义之一;与已建参考不一致时会**重建参考谱并重测
  两张参考峰表**(日志说明),`force=True` 无条件重建;
- **组合模式**:`direct_range=` 覆盖**本批 workflow 的基值**(参考谱不重建),
  逐组合还可用 `ext_lo`/`ext_hi` 再覆盖(`plan.notes` 会说明口径);
- 留档:参考记录 `params.ext_lo/ext_hi`;每条 `run.json` 的
  `parameters_resolved.direct_range`(`ext_lo`/`ext_hi` + `source`:
  `reference_or_base` / `combo`);
- 非法输入(只给一端、两端相同、非数值)直接报 `SweepError`;
- 也在 config/`params` 里生效:`params={"ext_lo": "10.5", "ext_hi": "6.5"}` 会被
  解析成同一份范围并统一留档。


## 5.7 选峰阈值(生成参考时可选,随后锁定)

参考峰表的选峰阈值 = **噪声 σ 倍数**(`sigma_multiplier`,内部同时作为
`min_snr` 传给检测)。缺省 35σ(既有默认,行为不变);**在生成参考时可以
由外部指定**:

```python
pick_reference_peaks(session, sigma_multiplier=20)          # 生成参考峰表
ensure_reference_peaks(session, reference, sigma_multiplier=20)
run_parameter_study(root, datasets=..., combos=..., sigma_multiplier=20)
```

```bash
python -m nmrforge_api peaks --study ~/studies/s1 --sigma 20
```

- **阈值是参考定义的一部分**:参考峰表一旦冻结,后续所有 workflow/参数扰动
  只能沿用参考的阈值;此时再给**不同**阈值会直接报 `ReferenceError`(CLI
  退出码 2),不会悄悄重选峰;
- 与参考一致(或与参考默认 35σ 一致)的阈值可以显式给 → 复用,不重复选峰;
- 想换阈值属于**重建参考**:显式 `force=True`,或删掉该条件的
  `study/reference/<key>/` 后重跑参考;
- 实际用量落档:`reference.json.peak_params.sigma_multiplier`(生成参考时
  选定的值)、`previous_sigma_multiplier`(force 重建时的上一版)、
  `detection.sigma_multiplier` 与 `detection.threshold_source`
  (`user` / `default(35sigma)`);每条 workflow 记录另记
  `parameters_resolved.peak_picking_threshold`(`locked_to_reference=true`);
- 阈值过高导致选不出峰 → 明确报错(不静默产出空峰表);
- 阈值写进 workflow 参数组合表 → 直接报错(`SweepError`),提示应改在生成参考
  时指定。
