# 示例

| 文件 | 说明 |
| --- | --- |
| `run_study.py` | 一步式:导入数据 → 参考谱 → 自动选峰 → 参数扫描 → 汇总 |
| `step_by_step.py` | 分步:逐段控制,打印每步产物与统计 |
| `measure_only.py` | 最小侵入:只借「参考峰表 + 峰位测量」,谱来自你自己的 pipeline |
| `grid.yaml` | 参数网格示例(命令行 `sweep --grid` 用,接口展开全因子) |
| `combos.csv` | 显式组合表示例(命令行 `sweep --combos` 用;正交/部分因子表直接放这里) |

运行前把脚本里的 `STUDY`、`DATA` 改成你的路径,或按脚本里的说明用环境变量覆盖:

```bash
export NMRFORGE_API_STUDY=~/studies/hsqc_params
export NMRFORGE_API_DATA=~/bruker_data/bmrXXXXX/1
~/NMRForge/nmrforge/bin/python run_study.py
```

真实运行需要 NMRPipe 环境;若只想看链路形状,可先用一个 fake 后端(见
NMRForge 仓库里的测试实现 `tests/test_nmrforge_api.py::_FakeSweepBackend`)。
