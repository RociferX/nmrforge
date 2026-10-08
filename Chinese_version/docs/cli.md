# 命令行参考

处理命令行在 `nmrforge_api` 包里,驱动的就是 GUI 用的同一套引擎:

```bash
python -m nmrforge_api --help
```

当前命令行属于 API v1.1；请在含该契约的源码检出里完成可编辑安装后使用，
本轮源码软件版本为 1.0.4，已发布 AppImage 仍为 1.0.2；见
[installation.md](installation.md)。

## 子命令

| 子命令 | 用途 |
| --- | --- |
| `init` | 创建一个 study,并登记某个条件的 Bruker 数据集；可显式登记同条件的有序分段。 |
| `reference` | 生成并冻结参考谱、参考脚本与参考峰表。 |
| `peaks` | 在参考谱上选峰,或登记一张外部峰表。 |
| `sweep`(别名 `workflows`) | 针对冻结的参考跑参数组合。 |
| `report` | 从既有记录重算汇总,不重新处理任何东西。 |
| `status` | 打印当前 study 的状态。 |

## 典型顺序

```bash
python -m nmrforge_api init      --study ./study --dataset /path/to/bruker/dataset
python -m nmrforge_api init      --study ./study-segmented --segmented \
    --dataset /path/to/segment1 --dataset /path/to/segment2 --condition A
python -m nmrforge_api reference --study ./study
python -m nmrforge_api peaks     --study ./study
python -m nmrforge_api sweep     --study ./study --grid grid.yaml --reference ./study
python -m nmrforge_api report    --study ./study
python -m nmrforge_api status    --study ./study
```

`init` 默认接受至多一个 `--dataset`；加 `--segmented` 后需重复提供至少两个完整原始
Bruker 目录，按参数顺序组成一个条件。reference/sweep 重开已登记的数据时无需再次传该标志。
分段模式及校验边界见[完整 CLI 参考](external-api/04-cli-reference.md)。

## `sweep` 选项

参考请求只在完整处理输入与缓存一致时复用；参数变化或旧输入指纹缺失需
`reference --force` 显式重建。`--rebuild-peak-tables` 只重算抛物线峰表，不重跑参考谱。
参考构建可重复指定 `--carrier-ppm F1=118.0` 等逻辑轴 CAR（ppm）；不指定时
保留 AUTO 转换结果或使用已解析的 Bruker 载频。详见[CLI 完整参考](external-api/04-cli-reference.md)。
不同 `--study` / `--reference` 根目录目前不支持，会显式报错，不向只读参考根写入结果。

```text
--study STUDY                     研究根目录
--name NAME                       新建研究时的名称
--condition CONDITION             条件标签(A/B/...);缺省跑全部条件
--grid GRID                       参数网格,支持 YAML/JSON(笛卡尔展开)
--reference REFERENCE             要对照的参考,例如 study 或 study#condition
--combos COMBOS                   显式组合表(CSV/TSV/YAML/JSON)
--direct-range HIGH_PPM LOW_PPM   直接维窗,施加在 workflow 默认值之上
--allow-ext-override              允许直接维窗与参考冻结范围不一致
--max-runs MAX_RUNS               限制执行的 workflow 数量
--localize-peaks CSV              只精修该 CSV(含 peak_id 列)里的峰
--edge-margin-ppm EDGE_MARGIN_PPM 选峰的边缘排除量
--no-resume                       忽略此前已完成的 workflow 并重跑
```

`grid.yaml` 示例:

```yaml
zero_fill: [1, 2, 4]
window.F1.off: [0.35, 0.45, 0.55]
```

## 峰定位方法

峰定位只有三点抛物线一种方法:每个 workflow 出一张 `peak_table_parabolic.csv`。

| 取值 | 含义 |
| --- | --- |
| `parabolic` | 唯一方法。三点抛物线精修,适用于任意维度。 |

二维高斯拟合算法与其 CLI 表面(`--localization`、`--localize-peaks-gaussian`、
`--localize-peaks-parabolic`、`--gaussian-roi-*`)已于 2026-09-26(用户需求⑦)
整体删除;`localization` 只接受 `"parabolic"`,`"gaussian"`/`"both"` 报
`SweepError`。想限定只精修部分峰,用 `--localize-peaks`(普通 CSV)。

## 采样判定是硬门槛

对采样判定为 `uncertain` 的数据集,`sweep` 拒绝继续。请先解决元数据冲突:
uniform/NUS 判错会改变网格里每一个参数的含义,所以这刻意不提供命令行开关来绕过。

## 完整参考

- [external-api/04-cli-reference.md](external-api/04-cli-reference.md) —— 全部选项,
  包括条件、组合表与断点续跑。
- [external-api/06-outputs-and-records.md](external-api/06-outputs-and-records.md) ——
  各个子命令写了什么。
- [external-api/10-troubleshooting.md](external-api/10-troubleshooting.md) —— 命令行相关错误。
