# 弃用/一次性代码归档

> 创建:2026-09-03(master 0.2.199-补29fu)。归档内容全部保留 git 历史
> (git log/git show 可查原始提交),目录内仅供回查,不随产品分发、不参与
> 测试与构建。**禁止**将归档代码重新接入产品路径;如需恢复某段功能,
> 应先走 Proposal/决策流程并核对现行实现是否已取代它。

## 背景与规则

项目经历了大量迭代、功能回退与一次性 VM 排查。为避免废弃代码继续污染
主代码树(ruff 噪音、误导性引用、维护负担),按用户要求把「以前任务弃用
的代码」集中到本目录:

- 被 git revert 干净回退的实现不在此处(已从主树消失,查 git log);
- 用户明确要求保留的代码(如 joint_recheck_memory 与其测试)仍留在
  原位,不归档;
- 仍被现行代码引用的 Shared Contract/工具不归档,只清理明确的死分支;
- 移动一律使用 `git mv`,历史与 blame 可追溯。

## scripts/——一次性 VM 排查/诊断脚本与就地补丁工具(2026-09-03 归档)

原位置 `scripts/`,均为 2026-08-19~2026-09-03 各次相位优化/重构/轴序排查
过程中的一次性脚本(VM 上手工运行,产物不落库),含大量过期常量与断点式
调试内容,当前代码已不再生成其依赖的中间目录(nus3d_rc_r300 等)。按
用途分组:

- `vm_100_*` / `vm_101_*` / `vm_102_*`:data/100/101/102(3D NUS)相位、
  轴序、平面布局排查(补29do/补29dn 时期);
- `vm_check_*` / `vm_verify_*` / `vm_compare_*` / `vm_recon_*`:复型结构、
  step1/HT、重建一致性、直接维缓存核验;
- `vm_proj_*` / `vm_ht_*` / `vm_direct_*`:投影/HT/直接维验证;
- `vm_smile_*.com` / `vm_uniform_cplx.com` / `vm_ht_finalize.com`:当时的
  NMRPipe/csh 手写脚本(旧目录布局,已过时);
- `apply_cmp300.py` / `apply_pair_fix.py` / `apply_quad_fix.py` /
  `apply_verify_zf.py` / `fix_p1_clamp.py`:对上述诊断脚本或
  core/optimization/phase_search.py 的一次性就地补丁工具,补丁内容早已
  并入代码(如 fix_p1_clamp 对应 0.2.199-补29i 的 p1 钳回)。

### 仍在 scripts/ 受维护的脚本(未归档)

- 协作/工具:`check_ownership.py`、`make_icon.py`、`install_desktop.sh`、
  `setup_env.sh`;
- CLI:`smile_optimize.py`、`param_optimize.py`;
- VM 回归/验证:`vm_test.sh`、`vm_sample_*.py`(六件)、
  `vm_validate_phase_score.py`、`vm_validate_zero_fill.py`、
  `vm_validate_nus_indirect_equiv.py`(见 scripts/README.md)。

## 维护约定

- 新增归档:先确认代码已不再被任何生产路径/测试/受维护脚本引用,再用
  `git mv` 移入对应子目录,并在本 README 注明来源(版本/用途);
- 归档不改变 Shared Contract,不进入 ownership 检查的产品前缀;
- ruff/测试不扫本目录(如扫描,一次性脚本按历史参考对待,不必修复)。
