# 命令行参考

处理命令行在 `nmrforge_api` 包里,驱动的就是 GUI 用的同一套引擎:

```bash
python -m nmrforge_api --help
```

请在 v1.0.0 的源码检出里、完成可编辑安装之后使用命令行;见
[installation.md](installation.md)。

## 子命令

| 子命令 | 用途 |
| --- | --- |
| `init` | 创建一个 study,并登记某个条件的 Bruker 数据集。 |
| `reference` | 生成并冻结参考谱、参考脚本与参考峰表。 |
| `peaks` | 在参考谱上选峰,或登记一张外部峰表。 |
| `sweep`(别名 `workflows`) | 针对冻结的参考跑参数组合。 |
| `report` | 从既有记录重算汇总,不重新处理任何东西。 |
| `status` | 打印当前 study 的状态。 |

## 典型顺序

```bash
python -m nmrforge_api init      --study ./study --dataset /path/to/bruker/dataset
python -m nmrforge_api reference --study ./study
python -m nmrforge_api peaks     --study ./study
python -m nmrforge_api sweep     --study ./study --grid grid.yaml --reference study/reference.json
python -m nmrforge_api report    --study ./study
python -m nmrforge_api status    --study ./study
```

## `sweep` 选项

```text
--study STUDY                     研究根目录
--name NAME                       新建研究时的名称
--condition CONDITION             条件标签(A/B/...);缺省跑全部条件
--grid GRID                       参数网格,支持 YAML/JSON(笛卡尔展开)
--reference REFERENCE             要对照的参考,例如 study 或 study#condition
--combos COMBOS                   显式组合表(CSV/TSV/YAML/JSON)
--direct-range HIGH_PPM LOW_PPM   直接维窗,施加在 workflow 默认值之上
--max-runs MAX_RUNS               限制执行的 workflow 数量
--localization {parabolic,gaussian,both}
                                  组合所用的峰定位方法
--edge-margin-ppm EDGE_MARGIN_PPM 选峰的边缘排除量
--gaussian-roi-f1-ppm / --gaussian-roi-f2-ppm
                                  高斯拟合 ROI 半径(间接维 / 直接维,ppm)
--no-resume                       忽略此前已完成的 workflow 并重跑
```

`grid.yaml` 示例:

```yaml
zero_fill: [1, 2, 4]
window.F1.off: [0.35, 0.45, 0.55]
```

## 峰定位方法

| 取值 | 含义 |
| --- | --- |
| `parabolic` | 默认。三点抛物线精修,适用于任意维度。 |
| `gaussian` | 2D 高斯拟合;对非 2D 谱会以显式错误拒绝。 |
| `both` | 两种都跑,两张峰表都保留。 |

## 采样判定是硬门槛

对采样判定为 `uncertain` 的数据集,`sweep` 拒绝继续。请先解决元数据冲突:
uniform/NUS 判错会改变网格里每一个参数的含义,所以这刻意不提供命令行开关来绕过。

## 完整参考

- [external-api/04-cli-reference.md](external-api/04-cli-reference.md) —— 全部选项,
  包括条件、组合表与断点续跑。
- [external-api/06-outputs-and-records.md](external-api/06-outputs-and-records.md) ——
  各个子命令写了什么。
- [external-api/10-troubleshooting.md](external-api/10-troubleshooting.md) —— 命令行相关错误。
