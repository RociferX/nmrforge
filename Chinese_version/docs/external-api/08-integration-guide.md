# 08 · 向使用者分析程序交接(v1.1)

本软件止于「谱 + 峰表 + 处理记录」。**统计推断与显著性判断
属于你的独立分析程序**,请按下述契约读取产物。

## 8.1 交接面

| 你的分析需要 | 读什么 |
| --- | --- |
| 每个参数组合的峰位 | `study/records/peak_table_parabolic.csv`(唯一的组合长表),或逐 workflow 的 `study/workflows/<id>/<条件>/peak_table_parabolic.csv` |
| 峰身份 | 参考峰表:`reference_peak_id`(R0001…);**组合峰表的匹配在你这侧**(组合模式独立选峰,表里 `reference_peak_id`/`assignment` 留空；API 不建立跨谱对应) |
| 条件 A/B | `condition` / `dataset` 列(或按目录/条件分组) |
| 参数与自动参数实际值 | `workflows/<id>/workflow.json` 与 `runs.json` 的 `parameters_requested`/`parameters_used`/`parameters_resolved` |
| 峰的可用性 | `SNR`、`fit_success`、`boundary_hit`、`fallback`(组合模式峰表只含检出的峰;参考峰表另带 `detected=false` 的保留行) |
| 参考基准 | `records/manifest.json` 的 `references`(脚本/谱/参考峰表哈希)与 `peak_identity` |
| 复算与引用 | 脚本/谱 SHA-256、`grid_sha256`、`versions`、完整 `log.txt` |

## 8.2 最小读取示例(只读,不计算)

```python
import csv
from pathlib import Path

records = Path("~/studies/hsqc_params/study/records").expanduser()

with (records / "peak_table_parabolic.csv").open(encoding="utf-8") as fh:
    rows = list(csv.DictReader(fh))   # 列: workflow_id, condition, peak_id,
                                      #     reference_peak_id, H_ppm, N_ppm, ...

# 例:取条件 A 的所有组合峰位(组合模式:峰表已只含检出的峰)
values = [
    (row["workflow_id"], float(row["N_ppm"]))
    for row in rows
    if row["condition"] == "A"
]
print(len(values), values[:3])
```

> 上面只做**读取与筛选**。先在下游分析中确定如何跨谱匹配峰、处理未匹配峰和
> QC 字段，再按明确的统计假设完成统计推断与科学结论检验。`nmrforge_api.uncertainty`
> 不属于两步处理接口，不能将独立谱中的本地 `peak_id` 直接当成对应峰输入。

## 8.3 建议的处理约定

1. **峰对应**:组合模式的峰表**没有** `reference_peak_id`(独立选峰),本谱 `peak_id`
   也不能跨谱连接。跨谱对应需由下游分析建立并记录所用证据、阈值和不确定性；
   仅凭 H/N 坐标匹配时须检查峰拥挤、重叠和歧义，不能视为 API 已自动追踪峰;
2. **定位口径**:`localization_method` 为 `parabolic`;负峰的独立检测 QC 可能出现
   `fit_success=false` 或 FWHM 为 NaN，这是已知问题，不能单凭这些字段判定峰位无效。
   若沿用旧研究根里残留的
   `gaussian` 峰表,请当作历史产物另行标注,不要和新表混用;
3. **权重/缺失**:某组合没匹配到某参考峰时**不要静默删除**——在分析里明确标注
   (缺失机制可能与被扫参数相关;参考峰表里的 `detected=false` 行同样保留);
4. **可复算**:分析产物里附上 `records/manifest.json` 里的脚本/谱哈希与
   `grid_sha256`,以及所用软件的 `versions`;
5. **不要回写研究根**:分析结果请落在你自己的目录(软件产物是执行记录,
   分析不应改写它们)。

## 8.4 分片与并行(可选)

组合数上限 `max_runs`(缺省 256);要并行请按**参数轴**拆分(各机器跑不同子
网格、各自一个研究根),最后在分析侧按键合并长表。合并前须核对参考，并由下游
分析处理不同谱间的峰对应；参数与参考记录本身不保证峰身份可比。多条件参考峰集
限制及统一峰表的 3D 坐标限制见 [09-limitations-and-roadmap.md](09-limitations-and-roadmap.md) §9.8。
要点:分片时把 `records/manifest.json` 一起归档,便于核对是否同一参考。
