# Backend 架构

## 目录

- backend/:nmrpipe_backend.py(process/reconstruct_nus/finalize_nus/转换)、
  script_generator.py(确定性 .com)、bruker_workflow.py(fid.com patch)、
  memory_guard.py(0.2.112)、runtime.py(CshRuntime)、factory.py、
  base.py(ProcessingBackend Protocol,Shared)、config.py(默认参数)、
  native_backend.py、nmrpipe_finder.py;
- workflow/:stepwise.py(三步接口)、phase_routes.py(unified_route)、
  phase_optimize.py、memory_phase_search.py、import_workflow.py、
  pick_peaks.py、analyze.py、batch.py、engine.py、smile_optimize.py、
  param_optimize.py、baseline_optimize.py、manual.py;
- core/:data(bruker_reader/nus_reader/pipe_io/internal_data_model[Shared])、
  experiment(分类器)、experiments(registry + presets 加载)、processing、
  planning、optimization、qc、reporting、peaks、project[Shared]、
  workspace.py[Shared]。

## 关键流

read_dataset → Experiment → convert_to_fid(3D NUS:acqu3s TD 副本 → 切片
fid/test%03d.fid)→ reconstruct_nus(SMILE,直接维 1×TD,内存护栏)→
finalize_nus(相位/基线/填零)→ 终谱;NMRPipe 语义只存在于 backend/ 与生成的
脚本。

## 接口

ProcessingBackend 返回稳定键(success/message/logs/spectrum_path/metrics/
effective_params);详见 docs/API_CONTRACT.md。
