# 08 · 向下游分析程序交接(v0.2)

本软件止于「谱 + 峰表 + 处理记录」。**CSP、robustness、统计推断与显著性判断
属于你的独立分析程序**,请按下述契约读取产物。

## 8.1 交接面

| 你的分析需要 | 读什么 |
| --- | --- |
| 每个参数组合的峰位(两种算法) | `study/records/peak_table_parabolic.csv` / `peak_table_gaussian.csv`(长表),或逐 workflow 的 `study/workflows/<id>/<条件>/peak_table_*.csv` |
| 峰身份 | 参考峰表:`reference_peak_id`(R0001…);**组合峰表的匹配在你这侧**(组合模式独立选峰,表里 `reference_peak_id`/`assignment` 留空,靠 `H_ppm`/`N_ppm` 等匹配) |
| 条件 A/B | `condition` / `dataset` 列(或按目录/条件分组) |
| 参数与自动参数实际值 | `workflows/<id>/workflow.json` 与 `runs.json` 的 `parameters_requested`/`parameters_used`/`parameters_resolved` |
| 峰的可用性 | `SNR`、`fit_success`、`boundary_hit`、`fallback`(组合模式峰表只含检出的峰;参考峰表另带 `detected=false` 的保留行) |
| 参考基准 | `records/manifest.json` 的 `references`(脚本/谱/两张峰表哈希)与 `peak_identity` |
| 复算与引用 | 脚本/谱 SHA-256、`grid_sha256`、`versions`、完整 `log.txt` |

## 8.2 最小读取示例(只读,不计算)

```python
import csv
from pathlib import Path

records = Path("~/studies/hsqc_params/study/records").expanduser()

def load(method: str) -> list[dict]:
    with (records / f"peak_table_{method}.csv").open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))

parabolic = load("parabolic")      # 列: workflow_id, condition, peak_id,
gaussian = load("gaussian")        #     reference_peak_id, H_ppm, N_ppm, intensity, ...

# 例:取条件 A 的所有组合峰位(组合模式:峰表已只含检出的峰)
values = [
    (row["workflow_id"], float(row["N_ppm"]))
    for row in parabolic
    if row["condition"] == "A"
]
print(len(values), values[:3])
```

> 上面只做**读取与筛选**。σ、Δδ、robustness、显著性检验请在你的分析代码里
> 按自己的统计假设实现(样本量、分布、缺失峰的处理都要写明)。若只想快速
> 自检,可复用测试/检测辅助 `nmrforge_api.uncertainty`(处理链不调用它,
> 也不出现在 records 里)。

## 8.3 建议的处理约定

1. **峰对齐**:组合模式的峰表**没有** `reference_peak_id`(独立选峰)——按
   `H_ppm`/`N_ppm` 容差把各组合的峰匹配回参考峰表的 `reference_peak_id`
   (或你自己的指认),并明确记录匹配容差、未匹配峰与峰数;
2. **算法选择**:parabolic 与 gaussian 是两套独立观测;比较两者差异时,
   Gaussian 侧应排除 `fit_success=false`(或把它们作为缺失处理),
   并保留 `fallback_reason` 作为审计线索;
3. **权重/缺失**:某组合没匹配到某参考峰时**不要静默删除**——在分析里明确标注
   (缺失机制可能与被扫参数相关;参考峰表里的 `detected=false` 行同样保留);
4. **可复算**:分析产物里附上 `records/manifest.json` 里的脚本/谱哈希与
   `grid_sha256`,以及所用软件的 `versions`;
5. **不要回写研究根**:分析结果请落在你自己的目录(软件产物是执行记录,
   分析不应改写它们)。

## 8.4 分片与并行(可选)

组合数上限 `max_runs`(缺省 256);要并行请按**参数轴**拆分(各机器跑不同子
网格、各自一个研究根),最后在分析侧按键合并长表——同一参考与峰身份保证可比。
要点:分片时把 `records/manifest.json` 一起归档,便于核对是否同一参考。
