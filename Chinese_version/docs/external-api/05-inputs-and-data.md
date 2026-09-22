# 05 · 输入:数据、条件与参数组合表(v1.0)

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
| `window.<轴>.type` + `off/end/pow/c/lb/g1/g2` | 窗函数:**必须成对给**——`type=none/off` 时该轴不插窗行,子参数会被忽略(API 直接报错);`sine_bell`(`off/end/pow/c`)、`sine_bell_squared`、`gaussian`(`g1/g2`)、`exp`(`lb`) |
| `baseline` | 基线校正(`{enabled, mode: auto|order, order, axes}`):`mode=order` 渲染 `POLY -ord N -auto`(NMRPipe `-auto` 自动挑基线点,2026-09-16 起真实生效);`mode=auto` 渲染 `POLY -auto`;`mode≠order` 时 `order` 被忽略(API 提示) |
| `reference_optimize` | **仅测试/复现/审计**用的参考优化开关(见 §5.10);真实实验不要使用 |
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
  的**参考峰表**沿用同一身份(组合模式的峰表不跟踪它——组合峰表独立选峰、
  `reference_peak_id` 留空);
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
  (每轴半宽点数上限,**默认 0 = 不限制**,结果与旧版一致;设 48 等正值可提速,
  但细网格上会改结果并逐峰留档)、`gaussian_max_nfev`(单峰求值上限,默认 200);
- 直接维范围 `ext_lo`/`ext_hi` 只作用于**直接维**;3D 数据请用 `window.F3.*` 等
  逐轴键(若该轴是直接维);
- 参考层的逐轴参数(参考谱定义)用参考模式的 `params=`/`direct_range=` 指定,
  组合表里的键只覆盖**该组合**;
- 未知轴的键(如 `window.F9.off`)不会报错,但也不会生效:请对照上表核对轴名;
- **窗型与窗参数必须成对**:该轴有效 `type=none/off` 时写 `window.<轴>.off/end/…`
  会被 `SweepError` 拒绝(真机实例:参考窗型选到 none 后,`window.F1.off`
  全程没有渲染出任何窗函数行);基底没有 `type` 时给提示(会按默认 sine_bell 渲染)。

## 5.10 参考优化开关(**仅测试/复现/审计;真实实验不可用**)

> ⚠️ **真实实验请保持默认(参考自动优化)**。下面这些开关会关掉/限定参考阶段的
> 自动优化,使参考不再“自动优化生成”;一旦使用,必须在处理记录与论文方法里
> 明确写出“参考未做自动优化/优化被限定”,否则参考的合法性不成立。

```python
run_reference_study(
    root, dataset,
    params={
        "reference_optimize": {
            "baseline": "off",            # off / auto / {"grid": [["off",0],["auto",1],["order",2],["order",3]]}
            "window": "off",              # off / auto / {"direct_candidates": [...], "indirect_candidates": [...]}
        },
        "baseline": {"F1": {"enabled": False}},   # 关掉优化时,这份配置被终跑直接使用
        "window": {"F1": {"type": "sine_bell", "off": 0.45, "end": 0.98}},
    },
)
```

- `baseline="off"` / `window="off"`:跳过对应优化器,参考终跑直接用你给的
  `baseline` / `window` 配置;`{"grid": …}` / `{…_candidates: …}` 只限定候选集合,
  仍由评分挑最优;
- 开关原样落档在 `reference.json.params.reference_optimize`(可审计),**不会**
  进入组合基底(sweep_params);
- 组合模式(参数扰动阶段)本来就逐组合显式控制窗/基线,不需要这个开关。


## 5.8 直接维范围(可由外部指定)

直接维提取窗口用 **ppm** 指定,与 NMRPipe `EXT -x1/-xn` 及 config
`processing.ext_lo/ext_hi` 同序:

- `ext_lo` = 直接维**高端**(较大 ppm,对应 `EXT -x1`);
- `ext_hi` = 直接维**低端**(较小 ppm,对应 `EXT -xn`)。

三种写法可组合,优先级为 `params` < `direct_range` < 显式 `ext_lo/ext_hi`:

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
- **组合模式**:`direct_range=` 写入本批 `base_overrides`(参考谱不重建),每个
  条件仍先使用自己的参考有效参数,逐组合还可用 `ext_lo`/`ext_hi` 最后覆盖
  (`plan.notes` 会说明口径);覆盖值与参考冻结范围**不一致时默认报错**——必须显式
  `allow_ext_override=True`(CLI `--allow-ext-override`)才放行,放行后每条 run 留
  `direct_range_override` 警告码(2026-09-19 P1-4);
- 留档:参考记录 `params.ext_lo/ext_hi` 与 `direct_range`(`ext_lo`/`ext_hi`/`unit`/
  `source`,`source` ∈ `explicit|params|default`;`default` = 没给范围、用后端/配置
  缺省值,附 `warning`);每条 `run.json` 的
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
  `parameters_resolved.detection`(`source="reference(locked)"`、实际 σ、边距、
  噪声 σ、精修方法列表;`independent=true`、`reference_matching="external"`);
- 阈值过高导致选不出峰 → 明确报错(不静默产出空峰表);
- 阈值写进 workflow 参数组合表 → 直接报错(`SweepError`),提示「要改阈值请重建
  参考」;
- 组合模式**没有** `max_peaks`:该组合在锁定阈值下检出多少峰就是多少峰。

## 5.11 限定峰的定位(targeted localization,2026-09-19)

`localization` 的作用范围默认是该谱的**全部检出峰**。批量 ensemble 的成本集中在
阈值受限的相关噪声峰上(实测:合成谱 431 个检出峰里只有约 75 个对应真值,
Gaussian 回退率约 69%,同一批 5 行 parabolic 36 s、加 Gaussian 约 13 min),因此
提供一等公民入口,只精修指定峰:

```python
run_combination_study(f"{root}#A", combos=..., localization="gaussian",
                      localize_peaks="truth_peaks.csv")
run_sweep(session, plan, localization="both", localize_peaks=[1, 5, 9])
detect_and_localize(spectrum, method="gaussian", targets=(1, 5, 9))
```

```bash
python -m nmrforge_api sweep --study ~/studies/s1 --reference ~/studies/s1 \
    --combos design.csv --localization gaussian --localize-peaks truth_peaks.csv
```

组合表里可以逐行指定(**优先级最高**;相对路径按**组合表所在目录**解析):

```csv
zero_fill,localization,localization.targets
1,gaussian,truth_peaks.csv
2,gaussian,
```

目标列表 CSV 至少一列 `peak_id`(可另带 `reference_peak_id` 供留档;单列文本、
每行一个序号也接受;重复 id 去重并保留首次出现顺序)。语义:

- **检出与峰集完全不受影响**:目标列表只决定「哪些峰参与该方法的精修」——
  选峰、行数、`peak_id` 编号一律不变;
- 未列入目标的峰**保留在表里**,位置取检出阶段的三点抛物线估计(即该行实际用的
  方法);该方法的 QC 列(`fit_success`/`FWHM_*`/`fit_rmse`/`boundary_hit`)写
  `NaN`(没做拟合,**不是**失败),`fallback` 为 false;
- 逐峰失败照常记录(`fit_success=false` + `fallback_reason`),**不会**换候选
  重新拟合;`n_fallback` 只统计真正做过拟合的峰;
- 留档:`run.json.parameters_resolved.detection.localization_targets` =
  `{scope, source, path, sha256, n_targets, peak_ids, reference_peak_ids?}`
  (风格同 `direct_range.source`);`peak_localization.<method>` 另有
  `localization_scope`(`all`/`subset`)、`n_targeted`、`n_skipped`;
- 断点续跑指纹包含**解析后**的目标列表(路径 + SHA-256 + id):换了目标列表、甚至
  只改了同一路径 CSV 的内容,也会重跑而不会复用旧 run;
- **报错**而不是静默退化成全谱。静态错误(空列表 / 文件不存在 / 缺 `peak_id` 列)
  在进入处理前就抛 `SweepError`(整轮中止);未知 `peak_id` 只能按谱判断,落在该
  (workflow, 条件) 上:该 run 标 `failed`、`message` 写明「目标峰列表里有未检出的
  peak_id…该谱检出 N 个峰,peak_id 范围 1..N」(`peak_id` 是**逐谱**序号,每个
  workflow × 条件一张自己的谱),不会静默忽略、也不会换峰;
- 不给目标 = 现在的全谱行为(`scope=all`),对既有研究根与记录零影响。

## 5.12 逐方法目标键(按方法分别限定,2026-09-20)

`both` 模式最常用的组合是「parabolic 全谱 + 只对指定目标峰做 gaussian」:

```python
run_combination_study(f"{root}#A", combos=..., localization="both",
                      localize_peaks={"gaussian": "truth_peaks.csv"})
```

```bash
python -m nmrforge_api sweep --study ~/studies/s1 --reference ~/studies/s1 \
    --combos design.csv --localization both \
    --localize-peaks-gaussian truth_peaks.csv
```

组合表里逐行写(`localization.targets.<方法>`;相对路径仍按组合表目录解析):

```csv
zero_fill,localization,localization.targets.gaussian,localization.targets.parabolic
1,both,truth_peaks.csv,
2,both,,""
```

语义与单列写法完全一致(**检出不变、未列入的峰保留、默认不变**),补充三条:

- 优先级:`localization.targets.<方法>` > `localization.targets.all`(或 `all`/`*`/
  `both` 键)> `localization.targets` > 调用参数 `localize_peaks=`;逐方法键只覆盖
  该方法,其余方法沿用调用参数;
- 显式写空串 = 该方法**不限定**(与「没给」区分:没给会继承方法无关那份);
- 留档:`parameters_resolved.detection.localization_targets` 顶层保持方法无关口径
  (`scope` 在逐方法不一致时写 `mixed`),逐方法明细在 `by_method.<方法>`(含该方法
  自己的 `n_skipped`);`peak_localization.<method>` 的计数照旧按方法给。

## 5.13 条件粒度(按 (workflow, 条件) 限定,2026-09-20)

A/B 是两张不同的谱,**检出峰集不同** → 同一个组合行在两个条件下的目标峰序号不同,
一份清单服务不了两个条件。目标 CSV 因此可以带一列 `condition`:

```csv
condition,peak_id
A,12
A,37
B,9
B,41
```

- 有 `condition` 列 → 每个条件只取 `condition == 自己` 的行;`peak_id` 按**该条件
  自己的谱**校验(未知 id 照常报错,不静默忽略);
- 没有该列 → 与不带条件粒度时**逐位一致**(整批共用),留档写 `by_condition: "all"`;
- 某条件在文件里**没有任何行** → **处理前**报错(默认 `on_missing="error"`);
  要放行必须显式声明 `on_missing="all"`(该条件不限定 = 全谱精修)或
  `on_missing="none"`(该条件不做任何精修),策略写进留档;
- 出现**不属于该研究**的条件名 → 报错(不静默忽略);
- 空文件 / 缺 `peak_id` 列 → 沿用既有报错口径。

`on_missing` 写在映射写法里(用例参数或逐行组合表):

```python
run_combination_study(f"{root}", combos=..., localization="gaussian",
                      localize_peaks={"path": "targets.csv", "on_missing": "none"})
```

**一个条件一份文件**(映射写法;CLI 保持只收单个文件,用 `condition` 列即可服务
A/B,不必为每个条件再跑一次 sweep):

```python
run_combination_study(f"{root}", combos=..., localization="gaussian",
                      localize_peaks={"A": "a.csv", "B": "b.csv"})
run_combination_study(f"{root}", combos=..., localization="gaussian",
                      localize_peaks={"default": "all_conditions.csv",
                                      "by_condition": {"A": "a.csv"}})
```

组合表里同样能写(CSV 单元格用花括号写法,相对路径仍按组合表目录解析):

```csv
zero_fill,localization,localization.targets.gaussian
1,gaussian,"{A: a.csv, B: b.csv}"
```

留档:`run.json.parameters_resolved.detection.localization_targets` 顶层保留
`path`/`sha256`(整文件)与本 run 实际生效的 `peak_ids`/`n_targets`/`n_skipped`,
按条件写时另有 `condition`/`on_missing`,新增 `by_condition`(逐条件:
`peak_ids`/`n_targets`/`line_ranges` 行号范围/`path` + `sha256` 来源文件/`from`);
整批共用时 `by_condition` 写 `"all"`。`peak_localization.<method>.n_targeted` /
`n_skipped` **仍是逐 run 口径**。断点续跑指纹带**解析后的逐条件清单**,且只带本
条件那一份:改 A 的行不会让 B 重跑(映射写法各自文件另带自己的 SHA-256)。指纹
载荷形状变了 → 既有断点缓存会失效**一次**并重算一遍同样数字(数值不变)。
