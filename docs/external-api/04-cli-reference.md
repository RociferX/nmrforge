# 04 · 命令行参考(v0.2)

入口:`python -m nmrforge_api <命令> --study <研究根>`。
公共参数:`--study`(必填)、`--name`(新建研究名)、`--condition <A|B|…>`
(缺省 = 全部条件)。

## init — 建研究并导入数据集

```bash
python -m nmrforge_api init --study ~/studies/s1 --dataset ~/data/apo --condition A
python -m nmrforge_api init --study ~/studies/s1 --dataset ~/data/holo --condition B
python -m nmrforge_api init --study ~/studies/s1            # 只看已登记条件
```

输出:研究根 + 条件 + 数据集摘要(ndim/核/采样/来源/raw_dir/文件数)。
同一条件标签不能绑定两份数据(报错,不覆盖)。

## reference — 参考工作流(每个条件一份)

```bash
python -m nmrforge_api reference --study ~/studies/s1
python -m nmrforge_api reference --study ~/studies/s1 --condition A --force
python -m nmrforge_api reference --study ~/studies/s1 --params auto.yaml
```

`--params` 是自动流程的输入覆盖(YAML/JSON);`--phase-route` 显式指定相位
路线;`--force` 重建。输出冻结谱/参考脚本路径与 SHA-256、相位来源、采样方式、
该条件是否支持参数组合。

## peaks — 参考峰表(身份 + 两张统一峰表)

```bash
python -m nmrforge_api peaks --study ~/studies/s1
python -m nmrforge_api peaks --study ~/studies/s1 --sigma 25 --max-peaks 60
python -m nmrforge_api peaks --study ~/studies/s1 --peak-table external.list
python -m nmrforge_api peaks --study ~/studies/s1 --localization gaussian \
    --gaussian-roi-f1-ppm 1.5 --gaussian-roi-f2-ppm 0.25
```

主条件自动选峰(或登记外部峰表)建立 `reference.list`;其他条件共享同一峰身份;
随后写两张参考峰表。输出峰表路径/哈希/来源/峰数 + 两张表的摘要与定位 QC。

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
    --combos design.csv --window-ppm 0.5 --no-resume
```

参考模式 = `reference`(参考谱/脚本)+ `peaks`(两张参考峰表)两个命令;参考不存在时 `sweep` 会报错并提示先跑这两个命令。

| 选项 | 含义 |
| --- | --- |
| `--reference` | **必填**:参考写法(`<研究根>` / `<研究根>#<条件>` / `reference.json`) |
| `--combos` | **用户参数组合表**(CSV/TSV/YAML/JSON,一行一个组合,原样按序执行) |
| `--grid` | 各轴候选值(YAML/JSON 的 `axes:`,接口展开全因子) |
| `--max-runs` | 组合数上限(缺省 256) |
| `--window-ppm` | 峰位搜索窗口半径(ppm;缺省 1.5×核素线宽) |
| `--window-pts` | 窗口半径(点数,跨分辨率不可比,不推荐) |
| `--gaussian-roi-f1-ppm` / `--gaussian-roi-f2-ppm` | 高斯 ROI 物理半径(ppm) |
| `--no-resume` | 不跳过已完成 workflow |

`--combos` 与 `--grid` 必须且只能给一个。每个组合 = 一个 `workflow_id`
(`W0001`…);对全部条件跑处理,再对同一张谱跑两种定位,输出两张峰表 + 完整
日志 + 参数三层 + 版本。输出:workflow 数、条件列表、状态计数、records 路径。

## report — 用已有记录重算汇总(不重跑处理)

```bash
python -m nmrforge_api report --study ~/studies/s1
```

重新拼装 `study/records/`(长表/manifest/workflows/runs/measurement),
不调用后端。输出 workflow 数与状态计数。

## status — 现状

```bash
python -m nmrforge_api status --study ~/studies/s1
```

输出:条件数据集、逐条件参考(脚本/谱哈希、峰数、峰表)、计划的 workflow 数与
ID、已记录 workflow 数、运行状态计数、records 目录。

## 退出码

- `0` 成功;
- `2` `SensitivityError`(参数/数据/参考/测量/组合表问题),错误写入 stderr
  (`错误: ...`),可被脚本判定。
