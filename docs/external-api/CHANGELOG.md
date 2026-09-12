# nmrforge_api 变更记录

版本口径:新增功能/新增字段保持同一 minor;破坏性改动升 minor 并给出迁移说明。

## 0.1(2026-09-12)

首个可用版本。公开面:

```text
run_parameter_study / open_study / add_dataset / dataset_info
build_reference / load_reference / set_reference_peaks / ensure_reference_peaks
sanitize_sweep_params / normalize_direct_phase / reference_phase
pick_reference_peaks / read_reference_peaks / measure_peak_positions / peak_coordinates
expand_grid / merge_overrides / plan_sweep / run_sweep / load_plan / load_runs
position_uncertainty / uncertainty_summary / write_records
```

CLI:`python -m nmrforge_api {init,reference,peaks,sweep,report,status}`。

要点:

- 参考谱、参考脚本、参考峰位全部由 NMRForge 自动优化/自动选峰产生;外部峰表
  为可选逃生口(`peaks=` / `--peak-table`);
- 同一 fid 只转换一次;参考相位(各轴 PS)锁定;候选谱不替换活动谱;
  逐组合 `run.json` 支持断点续跑;单组合失败不中断;
- 亚像素峰位测量(窗口极值 + 三点抛物线)与质量标记;
- 不确定度主指标 `Δδ_std = sqrt(Σ(w_n·σ_n)²)`(w(15N) 默认 0.2);
- `records/` 六个产物:manifest / sweep_plan / runs / peak_positions /
  uncertainty / uncertainty_summary;
- 不 import Qt;有「不加载 Qt」与断点续跑的专项测试。

同版本内新增/修复(发布前):

- **支持 2D NUS 扫描**:`reconstruct_nus` 增加 `out_file`/`script_name`
  (候选输出写 `_intermediate/`,不覆盖终谱),扫描按数据采样方式自动派发;
  参考相位锁定扩展到 NUS(间接维 `phases` + 直接维扁平 `direct_phase`);
  可扫 `nSigma`/`thresh`/`nthread`/`smile_scaling`。3D NUS 仍不支持。

- **相位锁定**:统一相位路线把相位记在 `phases`(各轴 PS),最初只读
  `direct_phase` 导致候选谱间接维相位回退默认值;现按 `reference_phase()`
  取全轴 PS 并传给后端 `direct_phase_override`。
- **参考峰表格式**:支持研究项目导出的 `peak_id,H_ppm,N_ppm,...` CSV
  (按表头自动判别),与 Poky `.list`、旧 `H_shift/N_shift` CSV 并存。
- **默认自动选峰**:新增 `ensure_reference_peaks()`,并把峰表来源/SHA-256/
  峰数/选峰参数写入 `reference.json` 与 `manifest.json`。
