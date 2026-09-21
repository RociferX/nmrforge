# 10 · 排查(v0.2)

## 10.1 常见错误与处理

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `DatasetError: 无法识别为 Bruker 原始数据集` | 传了压缩包/已处理格式 | 解压出含 `acqus`(2D 还需 `acqu2s`)与 `ser` 的目录 |
| `DatasetError: 条件标签 'A' 已被 … 占用` | 同一条件标签绑了两份数据 | 换标签(B/C…)或另建研究根 |
| `ReferenceError: 参考谱产物缺失,请重建(force=True)` | `study/reference/<key>/` 被移动/删除 | 删掉该目录或 `build_reference(..., force=True)` |
| `ReferenceError: 非主条件的参考峰身份需要主条件先选峰` | 只给 B 选峰,主条件 A 还没选 | 先对主条件(A)调用 `ensure_reference_peaks` |
| `MeasurementError: Gaussian peak fitting is currently supported only for 2D spectra.` | 非 2D 直接调用 `measure_peak_positions(refine="gaussian")` | 参数组合执行里不报错(写 fallback 的 Gaussian 表);直接调用请用 `refine="parabolic"` |
| `SweepError: …超过上限 max_runs` | 组合数超限 | 减网格/显式提高 `max_runs`,或分批 |
| `SweepError: 网格里的 'phases'/'direct_phase' 会破坏相位锁定` | 直接写相位字典 | 改用 `phase_delta.<轴>.p0|p1` 或 `phase.<轴>.p0|p1` |
| `SweepError: 当前只支持 2D NUS 参数组合` | 3D NUS | 先只建参考;组合执行待 roadmap |
| `plan.notes` 里出现「不在后端读取的参数清单内」 | 键名拼错 | 对照 05 的键表;notes 只是提示,不会让运行失败 |

## 10.2 状态是 `success_with_warning` 怎么办

看 `run.json.warnings` 的 `code` 与 `peaks` 列表(以及 `log.txt` 的
`--- warnings ---` 段):

| 码 | 处理建议 |
| --- | --- |
| `peak_count_zero` | 该组合在锁定阈值下一个峰都没检出:确认该组合的谱没坏,或重建参考改阈值 |
| `gaussian_fallback` | 看 `fallback_reason`(roi_too_small / not_converged / center_at_boundary / sigma_at_bound …);调大 ROI 或接受抛物线回退 |
| `gaussian_boundary_hit` | 峰太宽/太窄或 ROI 不合适;调 ROI 半径 |
| (已移除) | `peak_not_detected` / `peak_window_edge` / `peak_out_of_range` / `window_points_fallback`:2026-09-14 起组合模式独立选峰,不再产出 |

## 10.3 断点续跑与重跑

成功运行只有在执行指纹一致时才会复用。指纹包括条件数据集、参数组合与实际
参数、锁定相位、参考脚本/谱/峰表哈希,以及**锁定阈值、精修方式
(`localization`)与选峰边距**、Gaussian ROI 选项；旧版无指纹记录或任一输入
改变都会安全重跑。缩短组合表后，活动计划
之外的旧 `Wxxxx` 目录可以保留作历史，但不会再进入当前汇总记录。

- 已 `success`/`success_with_warning` 的 workflow × 条件会被跳过;
- 想重跑某个组合:删掉 `study/workflows/<id>/` 下该条件目录(或整个 `<id>/`)
  再跑同一 plan;
- 想重跑参考:`build_reference(..., force=True)` 或删 `study/reference/<key>/`;
- 换了参数表:重新 `plan_sweep`(会得到新的 `grid_sha256`);同一研究根内
  重新跑会覆盖相应 workflow 目录(`W0001…` 按表序编号)。

## 10.4 找不到东西时

```bash
python -m nmrforge_api status --study <root>     # 条件/参考/workflow 概览
cat <root>/study/workflows/W0001/workflow.json   # 组合级记录
cat <root>/study/workflows/W0001/A/log.txt       # 该条件完整日志
cat <root>/study/records/manifest.json           # 全局清单与边界声明
```

报告问题请附:研究根路径、`records/manifest.json`、涉及的 `run.json` 与
`log.txt`、以及 `python -m nmrforge_api status` 输出。
