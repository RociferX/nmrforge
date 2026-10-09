# 04 · 命令行参考(v1.1.1)

入口:`python -m nmrforge_api <命令> --study <研究根>`。
公共参数:`--study`(必填)、`--name`(新建研究名)、`--condition <A|B|…>`
(缺省 = 全部条件)。

## init — 建研究并导入数据集

```bash
python -m nmrforge_api init --study ~/studies/s1 --dataset ~/data/apo --condition A
python -m nmrforge_api init --study ~/studies/s1 --dataset ~/data/holo --condition B
python -m nmrforge_api init --study ~/studies/segments --segmented \
    --dataset ~/data/segment1 --dataset ~/data/segment2 --condition A
python -m nmrforge_api init --study ~/studies/s1            # 只看已登记条件
```

输出:研究根 + 条件 + 数据集摘要(ndim/核/采样/来源/raw_dir/文件数)。
同一条件标签不能重复登记(报错,不覆盖)。默认关闭 `--segmented`，此时至多传一个
`--dataset`；开启时必须重复给出至少两个 `--dataset`，它们按命令行顺序组成同一条件
的一份完整源段列表。路径不能重复，不会自动搜索或补齐段。reference/sweep 对已登记的
分段数据无需再次传 `--segmented`。

每个段必须是完整 Bruker 原始数据目录。逐段动力学实验、缺少 `nuslist` 的 NUS 数据会拒绝；
段的维数、核、有效 TD、谱宽、采样模式、采集轴布局、SFO 频率及载频必须一致。段可位于不同父目录。复用
现有条件时，所有源段及其顺序都必须一致；要换段或改顺序请使用新条件或新研究根，
`reference --force` 只重建参数参考，不更改源段绑定。版本号为 API v1.1.1 / 软件 1.0.5；
此处描述的多段工程行为尚无真实 NMRPipe/SMILE 引擎验证。

## reference — 参考工作流(每个条件一份)

```bash
python -m nmrforge_api reference --study ~/studies/s1
python -m nmrforge_api reference --study ~/studies/s1 --condition A --force
python -m nmrforge_api reference --study ~/studies/s1 --params auto.yaml
python -m nmrforge_api reference --study ~/studies/s1 --condition-params conditions.json
python -m nmrforge_api reference --study ~/studies/s1 --carrier-ppm F1=120.0 --carrier-ppm F2=4.7 --force
python -m nmrforge_api reference --study ~/studies/s3 --carrier-ppm F3=4.7 --force
```

`--params` 是自动流程的输入覆盖(YAML/JSON);`--carrier-ppm` 可重复指定逻辑 F 轴载频，
例如二维 `F1=120.0`、`F2=4.7`(单位 ppm);同一轴重复指定会报错。显式载频按轴覆盖
`--params` 中的公共值,`--condition-params` 再按轴覆盖公共载频。0 和负值有效；bool、
NaN/Inf、空映射、未知轴或超过数据维数的轴拒绝。未指定轴保留参考转换路径上的 CAR，
不改 raw `acqus`。Bruker `-AUTO` 成功时保留 `fid.com` 实际 CAR；仅 uniform AUTO 不可用或失败
而走内置 `bruk2pipe` fallback 时才按解析 `Dimension.o1p` 规则确定载频(详见 05)。载频是参考
输入指纹的一部分；更改后需 `--force` 重建，不能自动重建。组合/sweep 沿用参考载频，不能
通过组合表或 `base_overrides` 扫描它。`--phase-route` 显式指定相位
路线;`--force` 重建;`--direct-range HIGH_PPM LOW_PPM` 指定**直接维范围**
(ext_lo 高端 / ext_hi 低端)。不带 `--force` 时只复用完整规范化请求指纹一致的参考;
任一参数、FT、谱宽、范围或其他输入不同,或旧参考没有有效指纹,均报错并要求 `--force`,
不会自动重建。多条件先整体预检,不匹配时不会开始任何条件的后端处理。输出冻结谱/
参考脚本路径与 SHA-256、相位来源、采样方式、该条件是否支持参数组合。
`--condition-params` 接受 JSON 对象,键为条件标签,值为该条件覆盖参数,例如
`{"A":{"zero_fill":2},"B":{"sampling":{"ft_neg_f1":true}}}`;未知条件会报错。
参考模式每个条件独立生成峰表;外部 `--peak-table` 只应用到主条件,不会传播。

`--rebuild-peak-tables` 只重算参考峰表(用已有的冻结参考谱 + `reference.list`,不动谱与
峰身份,调用前后断言 SHA-256 不变),并**刷新记录里的 `software_version` / `software_commit`
与研究级聚合 `records/reference.json`**(2026-09-19 修:此前升级过的研究根仍写着旧版本号
与空 commit,聚合里还挂着旧峰表 SHA)。

## peaks — 参考峰表(身份 + 一张统一峰表)

```bash
python -m nmrforge_api peaks --study ~/studies/s1
python -m nmrforge_api peaks --study ~/studies/s1 --sigma 25 --max-peaks 60
python -m nmrforge_api peaks --study ~/studies/s1 --peak-table external.list
```

每个条件都在自己的参考谱上独立选峰并生成 `reference.list` 和
`reference_peak_table_parabolic.csv`;外部 `--peak-table` 只登记主条件,不传播到其他条件。
`R0001…` 是所属参考峰表的局部身份,不代表跨条件或参考/组合谱间存在对应关系。
组合表的 `reference_peak_id` 留空,本谱 `peak_id` 只在本谱有效。
峰定位只有三点抛物线一种
方法,2026-09-26 起二维高斯拟合算法已删除,`--localization` /
`--localize-peaks-gaussian` / `--localize-peaks-parabolic` /
`--gaussian-roi-f1-ppm` / `--gaussian-roi-f2-ppm` 选项一并取消)。输出峰表路径/
哈希/来源/峰数 + 峰表摘要与定位 QC。

`--sigma N` 是**选峰阈值**(噪声 σ 倍数,缺省 35σ):在参考峰表**尚未生成**时
指定 = 生成参考时选阈值;参考已冻结后再指定不同阈值会被拒绝(退出码 2,
报错说明「阈值已锁定在参考」),因为后续参数扰动只能沿用参考的阈值。

## sweep(= workflows)— 组合模式:按参数组合表批量执行

**`--reference` 必填**(组合模式必须显式指定参考):

```bash
python -m nmrforge_api sweep --study ~/studies/s1 \
    --reference ~/studies/s1 --combos design.csv
python -m nmrforge_api sweep --study ~/studies/s1 \
    --reference ~/studies/s1#B --grid grid.yaml
python -m nmrforge_api sweep --study ~/studies/s1 \
    --reference ~/studies/s1/study/reference/exp_001_d_001/reference.json \
    --combos design.csv --no-resume
python -m nmrforge_api sweep --study ~/studies/s1 \
    --reference ~/studies/s1 --combos design.csv \
    --localize-peaks truth_peaks.csv
```

参考模式 = `reference`(参考谱/脚本)+ `peaks`(参考峰表)两个命令;参考不存在时 `sweep` 会报错并提示先跑这两个命令。

`--study` 必须与 `--reference` 所属研究根相同。指定不同根会在写入前报错;当前不支持将
参考根与组合输出根分开。相同研究根下运行仍会正常写入 workflow 与 records,不应将参考视为
只读或用它隔离结果。

| 选项 | 含义 |
| --- | --- |
| `--reference` | **必填**:参考写法(`<研究根>` / `<研究根>#<条件>` / `reference.json`) |
| `--combos` | **用户参数组合表**(CSV/TSV/YAML/JSON,一行一个组合,原样按序执行) |
| `--grid` | 各轴候选值(YAML/JSON 的 `axes:`,接口展开全因子) |
| `--direct-range` | 直接维范围 `HIGH_PPM LOW_PPM`(覆盖本批 workflow 基值;与参考冻结范围不一致时报错,见下行) |
| `--allow-ext-override` | 允许本批 `--direct-range` 与参考冻结范围不一致(留 run 级警告码 `direct_range_override`);缺省不一致即报错,不静默换窗口 |
| `--max-runs` | 组合数上限(缺省 256) |
| `--localize-peaks` | 仅目标峰从检出整数格点做三点抛物线精修；非目标保留整数格点，方法记 `none`、定位 QC 为 NaN。CSV 至少含 `peak_id`，可带 `condition`；条件缺行默认报错 |
| `--edge-margin-ppm` | 人工覆盖选峰边缘排除宽度(ppm);缺省依据采集先验与原始边缘候选证据保守筛查,不设固定边缘遮罩 |
| `--no-resume` | 不跳过已完成 workflow(续跑指纹 schema 为 `nmrforge_api.resume.v4`) |

`--combos` 与 `--grid` 必须且只能给一个。组合表支持把显式布尔值作为扫描候选:
`sampling.ft_neg`、`sampling.ft_neg_f1`、`sampling.ft_neg_f2` 及兼容别名
`sampling.flip_f1` / `sampling.flip_f2`;全局 `ft_neg` 优先于逐轴值。参考相位固定,
`sampling.ft_alt` 仍锁定,不能作为组合轴。参考建立时也可在 `--params` 中用
`sweep_width_hz` 按逻辑轴显式覆盖谱宽(Hz),每个值须为正有限数;组合使用已转换 FID,
不得改变该谱宽。

每个组合 = 一个 `workflow_id`
(`W0001`…);对全部条件跑处理,再在**该组合自己的谱**上用参考锁定阈值独立选峰,
做三点抛物线精修出一张 `peak_table_parabolic.csv`(峰定位只有这一种方法;
`--localization`、`--localize-peaks-gaussian` / `--localize-peaks-parabolic` 与
`--gaussian-roi-*` 已于 2026-09-26 删除)+ 完整日志 + 参数三层
+ 版本。阈值键(`sigma_multiplier`/`min_snr`/`threshold_sigma`/
`detection.sigma_multiplier`)写进组合表会直接报错(阈值锁定在参考)。
`--direct-range` 与参考冻结范围不一致时默认**报错**(退出码 2):参考谱不会重建,
峰位与峰集却会随窗口变。确认要覆盖时显式加 `--allow-ext-override`,每条 run 的
`warnings` 会留 `direct_range_override`(参考谱仍不重建)。

输出:workflow 数、条件列表、状态计数、records 路径。所有结构化命令结果以 JSON 写到
stdout;进度、日志和错误写到 stderr,便于管道读取 JSON。

## report — 用已有记录重算汇总(不重跑处理)

```bash
python -m nmrforge_api report --study ~/studies/s1
```

重新拼装 `study/records/`(长表 `peak_table_parabolic.csv`/manifest/workflows/runs/measurement),
不调用后端;**并刷新 `records/reference.json` 参考聚合**(版本/提交/峰表 SHA 取磁盘现状,
2026-09-19)。输出 workflow 数与状态计数。

## status — 现状

```bash
python -m nmrforge_api status --study ~/studies/s1
```

输出:条件数据集、逐条件参考(脚本/谱哈希、峰数、峰表)、计划的 workflow 数与
ID、已记录 workflow 数、运行状态计数、records 目录。

## 退出码

- `0` 成功;
- `2` `SensitivityError`(参数/数据/参考/测量/组合表问题);错误消息由 CLI 输出到
  stderr(`Error: ...`),可通过退出码判定。命令行用法/参数解析错误也输出到 stderr。

## `compat`:行为兼容清单(不需要研究根)

```bash
python -m nmrforge_api compat
python -m nmrforge_api compat --out compat.json
python -m nmrforge_api compat --golden --workdir /tmp/nmrforge_golden
```

| 选项 | 含义 |
| --- | --- |
| `--out FILE` | 把清单写成 JSON 文件 |
| `--golden` | 顺带跑黄金向量(确定性合成谱)并与声明的哈希比对 |
| `--workdir DIR` | 黄金向量产物目录(缺省临时目录,跑完即弃) |

用途:跑 ensemble 前/后各取一次 `behavior_digest`(或直接用产物里的
`compat_level`),不等就中止或标注;只有 `behavior_changed` /
`contract_changed` 时才按 `affected` 决定重跑范围。字段说明见 03 的
「行为兼容清单」一节。
