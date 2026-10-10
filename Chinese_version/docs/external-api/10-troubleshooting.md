# 10 · 排查(v1.1.1)

## 10.1 常见错误与处理

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `DatasetError: 无法识别为 Bruker 原始数据集` | 传了压缩包/已处理格式 | 解压出含 `acqus`(2D 还需 `acqu2s`)与 `ser` 的目录 |
| `DatasetError: 条件标签 'A' 已被 … 占用` | 同一条件标签绑了两份数据 | 换标签(B/C…)或另建研究根 |
| `ReferenceError: 参考谱产物缺失,请重建(force=True)` | `study/reference/<key>/` 被移动/删除 | 删掉该目录或 `build_reference(..., force=True)` |
| 不支持的峰位精修方法报 `MeasurementError` 或 `SweepError` | `refine` 或 `localization` 使用了不支持的方法 | `measure_peak_positions` 使用 `refine="parabolic"`(默认)或 `refine="none"`；组合模式使用 `localization="parabolic"`，不支持的方法不会自动替换 |
| 组合提示参考FID缺失、损坏或不一致 | 冻结FID、来源指纹、转换证据或请求参数不匹配 | 用`force=True` / `reference --force`重建参考；组合不会现场重转或合并 |
| `SweepError: …超过上限 max_runs` | 组合数超限 | 减网格/显式提高 `max_runs`,或分批 |
| `SweepError: 网格里的 'phases'/'direct_phase' 会破坏相位锁定` | 直接写相位字典 | 改用 `phase_delta.<轴>.p0\|p1` 或 `phase.<轴>.p0\|p1` |
| `SweepError: 当前只支持 2D NUS 参数组合` | 3D NUS | 3D NUS 可建立参考，不能运行参数组合 |
| `plan.notes` 里出现「不在后端读取的参数清单内」 | 键名拼错 | 对照 05 的键表;notes 只是提示,不会让运行失败 |

## 10.2 状态是 `success_with_warning` 怎么办

看 `run.json.warnings` 的 `code` 与 `peaks` 列表(以及 `log.txt` 的
`--- warnings ---` 段):

| 码 | 处理建议 |
| --- | --- |
| `peak_count_zero` | 该组合在锁定阈值下一个峰都没检出:确认该组合的谱没坏,或重建参考改阈值 |
| `boundary_hit` | 有峰的三点抛物线顶点贴在 ±0.5 点边界(真峰顶可能落在三点模板之外):核对谱/窗口,或接受该定位并看 `n_boundary_hit` 计数 |
| `duplicate_localization` | 同表出现同坐标(ppm 1e-6)的重复行:独立记录可能因存储点分辨率或亚格点精修落在同一坐标;下游不要把重复行当成两个独立观测 |
| `direct_range_override` | 本批用了 `--allow-ext-override`/`allow_ext_override=True`,脚本直接维范围与参考冻结范围不一致:确认这是有意为之 |

## 10.3 断点续跑与重跑

成功运行只有在执行指纹一致时才会复用。指纹包括条件数据集、参数组合与实际
参数、锁定相位、参考脚本/谱/峰表哈希,以及**锁定阈值、定位方式
(`localization="parabolic"`)与选峰边距**、目标峰列表。缺少指纹或任一输入改变时，该 run
不会复用。缩短组合表后，活动计划之外的 `Wxxxx` 目录不会进入当前汇总记录。

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
