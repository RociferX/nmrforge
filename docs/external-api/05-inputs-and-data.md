# 05 · 输入:数据、条件与参数组合表(v0.2)

## 5.1 原始数据

- **Bruker 原始数据集目录**(下载/解压后含 `acqus`/`ser`);压缩包与已处理格式
  不支持,报错信息会说明;
- 导入只做链接 raw + 写 metadata + 登记 import 运行;不做转换/处理;
- 采样方式:uniform 任意维、**2D NUS**(SMILE 重构)、3D NUS 目前只支持建参考。

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

## 5.7 选峰阈值(可由外部指定)

参考峰表的选峰阈值 = **噪声 σ 倍数**(`sigma_multiplier`,内部同时作为
`min_snr` 传给检测)。缺省 35σ(既有默认,行为不变),也可由外部指定:

```python
pick_reference_peaks(session, sigma_multiplier=20)          # 选峰并冻结
ensure_reference_peaks(session, reference, sigma_multiplier=20)
run_parameter_study(root, datasets=..., combos=..., sigma_multiplier=20)
```

```bash
python -m nmrforge_api peaks --study ~/studies/s1 --sigma 20
```

- 给了阈值就**按该阈值选峰**;与研究里已冻结的阈值不同时,会自动**重新选峰**
  (旧行为是不管给什么都复用默认阈值冻结下来的峰表,外部指定等于没生效);
- 不给阈值 → 沿用上次(或默认 35σ);阈值相同 → 复用,不重复选峰;
- 实际用量落档:`reference.json.peak_params.sigma_multiplier`(外部给的)、
  `previous_sigma_multiplier`(上一版)、`detection.sigma_multiplier` +
  `detection.threshold_source`(`user` / `default(35sigma)`);
- 阈值过高导致选不出峰 → 明确报错(不静默产出空峰表);
- 阈值是**参考峰表建立时**的参数,不是 workflow 处理参数:写进参数组合表
  会在 `plan.notes` 里提示应改在选峰步骤指定。
