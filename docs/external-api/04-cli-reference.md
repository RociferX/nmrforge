# 04 · 命令行参考

```bash
python -m nmrforge_api <命令> [参数]
```

所有命令都接受 `--study DIR`(研究根)与 `--name NAME`(新建研究时的项目名)。
输出为 JSON(便于 `jq`/脚本消费)。退出码:`0` 成功;`2` 接口异常
(`SensitivityError` 及其子类,打印为 `错误: …`);参数错误也是 `2`。

## init · 建研究并导入数据集

```bash
python -m nmrforge_api init --study DIR --dataset BRUKER_DIR [--title T]
```

`--dataset` 省略时只建/打开研究并打印当前数据集。导入只做 raw 链接 +
metadata + 导入运行记录:不转换、不处理。

## reference · 自动优化并冻结参考谱/脚本

```bash
python -m nmrforge_api reference --study DIR [--params params.yaml] \
    [--phase-route unified] [--force]
```

- `--params`:YAML/JSON,参考处理的输入参数(如 `ext_lo`、`window` 初值);
- `--force`:丢弃已有参考谱重建;
- 输出含 `spectrum`、`script`、`script_sha256`、`phase_route`、`sampling`、
  `sweep_supported`。

## peaks · 参考峰表(默认软件自动选峰)

```bash
python -m nmrforge_api peaks --study DIR [--sigma 35] [--max-peaks 100] [--force]
python -m nmrforge_api peaks --study DIR --peak-table external.list   # 可选:外部峰表
```

输出含 `peak_table`、`sha256`、`source`(`auto`/`external`)、`params`、`count`。
不需要外部峰表;`--peak-table` 只是逃生口(公开库/已指认表,同样冻结留档)。

## sweep · 按网格扫描

```bash
# 入口一:轴网格(接口展开全因子)
python -m nmrforge_api sweep --study DIR --grid grid.yaml \
    [--max-runs 256] [--window-pts 3] [--csp-n-weight 0.2] [--no-resume]

# 入口二:外部给定的组合表(正交表/部分因子/D-optimal/LHS/手挑,原样执行)
python -m nmrforge_api sweep --study DIR --combos design.csv [--max-runs 256]
```

`--grid` 与 `--combos` 必须且只能给一个。组合表格式:CSV/TSV(首行表头 =
轴键)或 YAML/JSON(组合列表,或 `combos: [...]`)。

网格文件(YAML;JSON 是 YAML 子集,可直接使用):

```yaml
axes:
  zero_fill: [1, 2, 4]
  "window.F1.off": [0.35, 0.45, 0.55]
  "baseline.F1.order": [1, 2]
max_runs: 128          # 可选;也可用命令行 --max-runs
base_params:           # 可选;缺省用参考运行的有效参数
  points_per_line: 4.0
```

- 键支持点号路径,值是该参数的候选列表;组合数 = 各轴长度之积;
- 组合数超过上限直接报错(不静默截断),提示减小网格或提高 `max_runs`;
- 每个组合单独跑一次处理;失败组合记入结果但不中断整轮;
- `--no-resume` 重算已有组合(默认跳过已成功的)。

## report · 只重算汇总(不重跑处理)

```bash
python -m nmrforge_api report --study DIR [--csp-n-weight 0.2]
```

用已有 `runs/*/run.json` 重算逐峰 σ/Δδ 与数据集级汇总,并重写 `records/`。
换 `csp_n_weight` 口径时用这个命令,避免重跑 NMRPipe。

## status · 打印研究现状

```bash
python -m nmrforge_api status --study DIR
```

输出数据集、参考谱(含哈希)、组合数与运行统计(成功/失败)、`records/` 路径。

## 典型集群用法

```bash
#!/bin/bash
set -e
STUDY=$1
GRID=$2
DATA=$3
python -m nmrforge_api init      --study "$STUDY" --dataset "$DATA"
python -m nmrforge_api reference --study "$STUDY"
python -m nmrforge_api peaks     --study "$STUDY"
python -m nmrforge_api sweep     --study "$STUDY" --grid "$GRID"
```

长任务建议 `nohup … &` 后台运行并轮询 `status`:每个组合成功即落盘,被抢占后
重跑同一命令即可继续。

## 与 Python API 的对应

| CLI | Python |
| --- | --- |
| `init` | `open_study` + `add_dataset` |
| `reference` | `build_reference` |
| `peaks` | `ensure_reference_peaks` / `set_reference_peaks` |
| `sweep` | `plan_sweep`(`axes=` 全因子 / `combos=` 外部组合表)+ `run_sweep` + `position_uncertainty` + `write_records` |
| `report` | `load_plan` + `load_runs` + 汇总 + `write_records` |
| `status` | `load_reference` / `load_plan` / `load_runs` |
