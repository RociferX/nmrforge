# scripts/

独立命令行工具与回归脚本(不进入产品运行路径;旧 git 保留历史版本)。

## 协作与工具

- check_ownership.py:双 Agent 所有权检查(gui/backend/all)
- make_icon.py:生成 AppImage 图标 PNG

## 处理/优化 CLI(可选)

- smile_optimize.py:SMILE 参数网格优化 CLI(与 GUI smile 步骤同源)
- param_optimize.py:后处理参数优化 CLI(相位/基线,只重构一次)

## VM 回归/验证(Architect 用)

- vm_sample_make_nus.py / vm_sample_regression.py / vm_sample_noext.py /
  vm_sample_water_profile.py / vm_sample_viewer_check.py / vm_sample_compare.py
- vm_validate_phase_score.py / vm_validate_zero_fill.py

已删除(0.2.164,旧 git 可恢复):recon_phase_search.py、
vm_validate_optimize.py、vm_validate_recon_phase_equiv.py。
