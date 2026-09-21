# scripts/

Independent command line tool and regression script (does not enter the product running path; old git retains historical versions).

## Collaboration and tools

- check_ownership.py: ownership-boundary check (gui / backend / shared)
- Make_icon.py: Generate AppImage icon PNG

## Processing/optimisationCLI(optional)

- Smile_optimize.py:SMILE parameter grid scan CLI (same origin as GUI smile step;
  Output sorting table + top three scripts, candidate spectrum deleted after evaluation).
- Param_optimize.py: Post-processing parameter optimisation CLI(phase / baseline, only reconstruct once)

## Real-engine verification scripts

- vm_sample_make_nus.py / vm_sample_regression.py / vm_sample_noext.py /
  vm_sample_water_profile.py / vm_sample_viewer_check.py / vm_sample_compare.py
- vm_validate_phase_score.py / vm_validate_zero_fill.py
- Vm_validate_nus_indirect_equiv.py:NUS indirect dimension memory score equivalence retest
  (docs/manager/decisions.md D-2026-08-31)

One-time troubleshooting/diagnosis script (vm_100_*/vm_102_*/vm_check_*/vm_verify_*/vm_proj_*.
Etc., 2026-09-03 0.2.199-patch29fu) has been archived to archive/deprecated/scripts/ --.
Both the git history and archive directory can be reviewed and are no longer distributed with scripts/.

Deleted (0.2.164, old git can be restored): recon_phase_search.py.
vm_validate_optimize.py, vm_validate_recon_phase_equiv.py.
