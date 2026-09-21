# 示例

| 文件 | 内容 |
| --- | --- |
| `run_study.py` | 一步式:数据 + 组合表 → 参考 + 全部 workflow + records(支持 A/B) |
| `step_by_step.py` | 分步:open_study → add_dataset → build_reference → ensure_reference_peaks → plan_sweep → run_sweep → write_records |
| `measure_only.py` | 只用测量层:对已有谱按同一批参考峰出 parabolic / gaussian 两张统一峰表 |
| `combos.csv` | 显式组合表示例(每行一个 workflow) |
| `grid.yaml` | 轴网格示例(接口展开全因子) |

运行前请确认数据是解压后的 Bruker 目录;真机处理需要 NMRPipe。

```bash
python docs/external-api/examples/run_study.py \
    --study ~/studies/s1 --a ~/data/apo --b ~/data/holo \
    --combos docs/external-api/examples/combos.csv
```

输出(节选):workflow 列表、逐 workflow × 条件状态、两张峰表路径,以及
`study/records/` 下的清单与长表。
