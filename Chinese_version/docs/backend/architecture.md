# Backend 架构

## 目录

- backend/:nmrpipe_backend.py(process/reconstruct_nus/finalize_nus/转换)、
  script_generator.py(确定性 .com)、bruker_workflow.py(fid.com patch)、
  memory_guard.py(0.2.112)、runtime.py(CshRuntime)、factory.py、
  base.py(ProcessingBackend Protocol,Shared)、config.py(默认参数)、
  nmrpipe_finder.py;
- workflow/:stepwise.py(三步接口)、phase_routes.py(unified_route)、
  memory_phase_search.py、import_workflow.py、pick_peaks.py、
  batch.py、smile_optimize.py、param_optimize.py、baseline_optimize.py、
  window_optimize.py、direct_diagnostics.py、manual.py、ucsf_export.py、
  optimization_report.py;
- core/:data(bruker_reader/nus_reader/pipe_io/internal_data_model[Shared])、
  experiment(分类器)、experiments(registry + presets 加载)、processing、
  planning、optimization、qc、peaks、project[Shared]、
  workspace.py[Shared]。

## 关键流

read_dataset → Experiment → convert_to_fid(保留 AUTO 的单文件/切片输出，
不改 acqu3s TD 强制形态)→ reconstruct_nus(SMILE,直接维 1×TD,内存护栏)→
finalize_nus(相位/基线/填零)→ 终谱;NMRPipe 语义只存在于 backend/ 与生成的
脚本。

## 接口

选峰轴映射、峰表输出与查看器共用 FDDIMORDER 逻辑身份；参考坐标重复核以 `nucleus:F轴`
区分。`noise.estimate` 给全局 sigma 与中位基线，检测/峰高/SNR 相对该基线，不改谱数组。
`peak_align` 仅估计匹配偏移；低质量/歧义约束跳过并留档，非有限坐标/非法容差明确失败。
`save_peaks(ndim=...)` 确保空 3D 表仍保留三列化学位移，独立 API H/N 专用表不猜重复核轴。

自动轴峰筛查单一来源 `core.peaks.axial.filter_axial_peaks`：Experiment 先验 + 当前谱
FDDIMORDER/ppm 轴 + 窄而对齐的边缘候选峰。最终谱不使用内部载频排除，不设置整带遮罩。
GUI 选峰只读小型采集参数和导入摘要，不能为筛查重读 ser/NUS；SMILE/研究组合复用已有
Experiment。缺元数据的独立 API 谱保留边缘峰；显式 ppm/点数边距属于人工覆盖。

人工谱图运行接受可选 `script_baselines`（编辑前原文），由 `script_audit.py` 生成差异，
RunRecord.params / snapshot / quality.json 留档，GUI 中间报告按记录展示，读取时不重跑处理。

独立 FID 质量入口 `run_fid_diagnostics_paths(repair=False)` 支持单文件与切片目录，按自然顺序
汇总直接子级 *.fid（去重、验证直接维长度）；只检测并给建议，不写诊断或审计记录。
流水线的 `run_direct_diagnostics` 修复、记录行为不受这个只读边界影响。

ProcessingBackend 返回稳定键(success/message/logs/spectrum_path/metrics/
effective_params);详见 docs/API_CONTRACT.md。
