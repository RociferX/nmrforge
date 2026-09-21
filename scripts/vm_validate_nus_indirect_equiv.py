"""NUS indirect dimension equivalence verification: memory rotate_real scoring vs candidate-by-
candidate backend finalize. Usage (VM): PYTHONPATH=$PWD nmrforge/bin/python \
scripts/vm_validate_nus_indirect_equiv.py \\ <project root> <exp_id> <data_id> [axis name,
default first indirect axis] Principle: same SMILE reconstruction On the plane (nus3d_rc, do not
rerun SMILE), A) memory path: finalize duplicate preview (preview axis PS(0,0) without adding
-di)+ rotate_real(p) = Re(F·exp(i·phase)) take real, phase_quality.evaluate score; B) back-end
path: finalize(preview axis PS(p,0) with -di) output real-type spectrum, with the same score.
Criterion: 12 coarse grid candidates bit-by-bit relative difference, score Spearman, top-match
consistent. 2026-08-31 VM measured (d_011, item 2/exp_003, axis F2): relative difference <=
1.7e-7, Spearman=1.0000, top-match consistent (330°) -> Equivalence passes and the memory score
remains."""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np

from core.qc import phase_quality
from workflow.memory_phase_search import rotate_real
from workflow.phase_routes import _axis_index, _read_complex_preview, _read_real_ft3

CANDIDATES = [0.0, 30.0, 60.0, 90.0, 120.0, 150.0,
              180.0, 210.0, 240.0, 270.0, 300.0, 330.0]


def main() -> int:
    if len(sys.argv) < 4:
        print(__doc__)
        return 2
    project_root, exp_id, data_id = sys.argv[1:4]
    axis_override = sys.argv[4] if len(sys.argv) > 4 else None

    from backend.nmrpipe_backend import NMRPipeBackend
    from core.project import ProjectManager
    from workflow.stepwise import _read_experiment, _work_dir

    manager = ProjectManager.open_project(project_root)
    exp = _read_experiment(manager, exp_id, data_id)
    proc_dir = _work_dir(manager, exp_id, data_id)
    backend = NMRPipeBackend()
    # The reconstruction plane is copied to the temporary directory, and the product does not
    # pollute the project working directory.
    work = Path(tempfile.mkdtemp(prefix="equiv_work_"))
    shutil.copytree(proc_dir / "nus3d_rc", work / "nus3d_rc")
    axes = [d.logical_axis for d in exp.dimensions]
    direct_axis = "F3" if exp.ndim >= 3 else "F2"
    axis = axis_override or [a for a in axes if a != direct_axis][0]
    ax = _axis_index(axis, exp.ndim)
    zf_phase = {"zero_fill": {a: {"mode": "auto"} for a in axes}}
    print(f"project={project_root} data={exp_id}/{data_id} "
          f"dataset={exp.dataset_id} ndim={exp.ndim} axes={axes} "
          f"axis={axis} ax={ax} planes={proc_dir / 'nus3d_rc'}", flush=True)

    preview = work / f"equiv_mem_{axis}.ft3"
    resp = backend.finalize_nus(
        exp,
        phases={},
        work_dir=work,
        params={**zf_phase, "preview_axis": axis},
        out_file=preview.name,
        script_name=f"equiv_mem_{axis}.com",
    )
    if not resp.get("success"):
        print("Memory clone preview failed:", resp.get("message"))
        return 2
    arr = _read_complex_preview(str(resp["spectrum_path"]), unpack_axis=ax)
    print(f"preview shape={arr.shape} complex={np.iscomplexobj(arr)}", flush=True)

    mem_scores: dict[float, float] = {}
    be_scores: dict[float, float] = {}
    max_rel_diff = 0.0
    for p in CANDIDATES:
        mem_real = rotate_real(arr, ax, p, 0.0)
        mem_scores[p] = float(phase_quality.evaluate(mem_real).score)
        be_file = work / f"equiv_be_{axis}_{int(p)}.ft3"
        resp2 = backend.finalize_nus(
            exp,
            phases={axis: (p, 0.0)},
            work_dir=work,
            params=zf_phase,
            out_file=be_file.name,
            script_name=f"equiv_be_{axis}_{int(p)}.com",
        )
        if not resp2.get("success"):
            print(f"p={p:g} Backend finalize failed:", resp2.get("message"))
            return 2
        be_real = _read_real_ft3(str(resp2["spectrum_path"]))
        if mem_real.shape == be_real.shape:
            scale = max(float(np.max(np.abs(mem_real))),
                        float(np.max(np.abs(be_real))), 1e-12)
            rel = float(np.max(np.abs(mem_real - be_real))) / scale
        else:
            rel = float("nan")
        max_rel_diff = max(max_rel_diff, rel)
        be_scores[p] = float(phase_quality.evaluate(be_real).score)
        print(
            f"p={p:g}: mem={mem_scores[p]:.3f} be={be_scores[p]:.3f} "
            f"max_rel_diff={rel:.3e} shape {mem_real.shape}/{be_real.shape}",
            flush=True,
        )

    keys = list(CANDIDATES)
    mem_v = np.array([mem_scores[k] for k in keys])
    be_v = np.array([be_scores[k] for k in keys])
    from scipy.stats import spearmanr

    rho, _pval = spearmanr(mem_v, be_v)
    top_mem = keys[int(np.argmax(mem_v))]
    top_be = keys[int(np.argmax(be_v))]
    print(
        f"axis={axis} candidates={len(keys)} spearman={rho:.4f} "
        f"top_mem={top_mem:g} top_be={top_be:g} max_rel_diff={max_rel_diff:.3e}",
        flush=True,
    )
    ok = (
        not np.isnan(rho)
        and rho >= 0.99
        and top_mem == top_be
        and max_rel_diff < 1e-4
    )
    print(f"EQUIVALENT={ok}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
