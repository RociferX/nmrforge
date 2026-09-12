# 10 · 排查与常见问题

## 安装/导入

**`ModuleNotFoundError: No module named 'nmrforge_api'`**
接口包是新增顶层包,editable 安装需要刷新:

```bash
~/NMRForge/nmrforge/bin/pip install -e ~/NMRForge
```

若用系统 python,同样重跑 `pip install -e <NMRForge 路径>`。

**`import nmrforge_api` 报 Qt 相关错误**
不应该发生:接口不 import Qt(有专项测试守护)。若出现,说明环境里的 `nmrforge_api`
不是本仓库的版本,检查 `python -c "import nmrforge_api; print(nmrforge_api.__file__)"`。

## 数据与导入

**`DatasetError: 无法识别为 Bruker 原始数据集`**
目录里缺 `acqus`(常见于压缩包多套了一层目录),或拿到的是已处理谱。

```bash
ls <data_dir>/acqus
```

**`DatasetError: 导入失败: KineticsUnsupportedError`**
动力学实验被策略守卫拒绝(产品决策:不支持导入),换数据集。

## 参考谱/参考脚本

**`ReferenceError: 参考运行没有留下可用的处理脚本`**
参考运行没有产出脚本文件。检查 `study/work/` 是否有 `*.com`,以及日志里是否有
NMRPipe 报错;必要时 `--force` 重建参考谱。

**`未找到 nmrPipe（csh: which nmrPipe）`**
处理需要 NMRPipe 且通过 csh 提供环境(通常写在 `~/.cshrc`):

```bash
csh -c 'source ~/.cshrc; which nmrPipe'
```

也可在 NMRForge 设置里显式指定 NMRPipe 路径(`backend.nmrpipe.path`)。

**记录里没有 `nmrpipe` / `smile` 版本**
接口只在解析到 NMRPipe 安装目录时探测一次版本;探测失败(不可执行/超时)不影响
运行,记录里就没有该项。其它版本(NMRForge/Python/numpy/…)始终记录。

## 选峰

**自动选峰数量异常(太多/太少)**
默认阈值 35σ。

- 太多(含噪声/弱峰):提高阈值或 `max_peaks` 限强峰:
  `run_parameter_study(..., sigma_multiplier=50, max_peaks=80)`;
- 太少:降低阈值(如 25),或确认实验类型识别是否正确(峰符号规则依赖模板)。

**想固定峰集做对照**
先用一次自动选峰,然后用 `set_reference_peaks(session, 你的.list,
source="external")` 登记;之后 `ensure_reference_peaks` 会复用已有峰表。

## 扫描

**`SweepError: v0.1 的参数扫描只支持 uniform 数据`**
NUS 数据超出了 v0.1 范围(见第 9 节)。

**`SweepError: 参数组合 N 个超过上限 max_runs=256`**
减小网格或提高上限;长扫描建议分批(同一研究根可续跑)。

**某些组合 `status=failed`**
看该组合的 `run.json`:

- `message`:失败原因(后端返回);
- `logs_tail`:NMRPipe 日志尾部(最多 40 行);
- 常见原因:参数取值非法(窗函数 off/end 组合、填零过大)、内存不足、NMRPipe
  报 data in Frequency Domain 等。
修正后重跑同一命令即可(成功的组合会跳过;要重算失败组合,把它的
`study/runs/<run_id>/` 目录删掉再跑,或整轮加 `resume=False`/`--no-resume`)。

**结果与预期不符?先看 `phase_locked`**
应为 `true`。若为 `false`(参考运行没记录相位),说明参考相位没有被锁定,
组合间差异会混入相位差——请重建参考谱(`--force`)或检查运行日志。

## 峰位测量

**大量 `window_edge=true`**
窗口太小或该峰位置本身随参数移动较远。加大 `window_pts`(如 5)重跑测量阶段
(可用最小侵入接法只重跑测量,不必重跑 NMRPipe)。

**大量 `out_of_range=true`**
参考峰位落在候选谱范围外:通常是被扫参数改变了提取窗口(`ext_lo`/`ext_hi`)
或谱宽。请固定提取窗口,或换一组峰。

**`found=false`**
该组合上窗口内无有效数据(峰消失/被裁掉)。该组合不进入该峰的统计
(`missing_runs` 会 +1)。

**怀疑测量精度**
接口自带数值守卫测试:对已知 +1.25 点平移,测量误差 < 0.2 点;若怀疑实现,
可运行 `pytest tests/test_nmrforge_api.py -k subpoint`。

## 运行与性能

**耗时估算**
参考谱(含统一相位优化)2D HSQC 约 30 s;每个组合约数秒(复用 fid、锁定相位)。
3D 数据更慢,但 v0.1 不做 3D/NUS 扫描。

**中断后怎么继续**
重跑同一命令即可(默认跳过已成功组合)。若换了网格,请换研究根。

**磁盘占用**
每个组合保留候选谱副本(`study/runs/<run_id>/spectrum.ft2`)。谱很大时注意余量;
不需要候选谱时可在分析后自行删除,但那样就无法复算该组合。

**日志里的 UCSF 提示**
`pipe2ucsf` 缺失只影响参考谱的 Sparky 导出,不影响研究流程。

## 报问题时请附上

1. `python -m nmrforge_api status --study DIR` 的输出;
2. `study/records/manifest.json`;
3. 失败组合的 `study/runs/<run_id>/run.json`(含 `logs_tail`);
4. NMRForge 版本与 NMRPipe 版本(manifest 里有)。
