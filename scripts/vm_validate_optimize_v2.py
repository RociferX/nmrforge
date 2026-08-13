"""Architect VM 验证 v2:真实数据完整自动优化(相位多尺度 + 嵌入基线/窗函数)。

走 stepwise.optimize_phase_brute_force(与 GUI/实际使用一致):
- 2D 均匀 sampleA:generate_spectrum(初始)→ optimize_phase_brute_force;
- 3D NUS sampleB:reconstruct_nus(初始)→ optimize_phase_brute_force(NUS 路径)。

输出:优化前后 QC/相位/基线分数、相位、基线配置、backend_runs、主峰。
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import numpy as np


def _metrics(path: Path) -> dict:
    import nmrglue as ng

    from core.qc import baseline_quality, phase_quality, spectrum_quality

    _dic, data = ng.pipe.read(str(path))
    arr = np.asarray(data)
    q = spectrum_quality.evaluate(arr)
    ph = phase_quality.evaluate(arr)
    bl = baseline_quality.evaluate(arr)
    idx = np.unravel_index(int(np.argmax(np.abs(arr))), arr.shape)
    return {
        "QC": round(q.score.overall, 1),
        "phase": round(ph.score, 2),
        "baseline": round(bl.score, 1),
        "shape": list(arr.shape),
        "main": (int(idx[0]), int(idx[1])),
    }


def run_2d(dataset: Path, root: Path) -> None:
    from backend.nmrpipe_backend import NMRPipeBackend
    from core.project import ProjectManager
    from workflow.import_workflow import import_data
    from workflow.stepwise import generate_spectrum, optimize_phase_brute_force

    proj = root / "v2_2d"
    if proj.exists():
        shutil.rmtree(proj)
    manager = ProjectManager.create_project(proj, "v2_2d")
    entry = manager.create_experiment(title="2D 完整优化")
    result = import_data(manager, entry.id, dataset)
    backend = NMRPipeBackend()
    spec = generate_spectrum(manager, entry.id, result.data_id, backend)
    print("[2D] 初始谱:", _metrics(Path(spec)))
    out = optimize_phase_brute_force(
        manager, entry.id, result.data_id, backend
    )
    print("[2D] 相位:", out["phase"], "backend_runs:", out["backend_runs"])
    print("[2D] 基线配置:", out.get("baseline", {}).get("config"))
    print("[2D] processing:", out.get("processing"))
    print("[2D] 优化后谱:", _metrics(Path(out["spectrum_path"])))
    manager.save()


def run_3d(dataset: Path, root: Path) -> None:
    from backend.nmrpipe_backend import NMRPipeBackend
    from core.data.bruker_reader import read_dataset
    from core.project import ProjectManager
    from workflow.import_workflow import import_data
    from workflow.stepwise import optimize_phase_brute_force

    proj = root / "v2_3d"
    if proj.exists():
        shutil.rmtree(proj)
    manager = ProjectManager.create_project(proj, "v2_3d")
    entry = manager.create_experiment(title="3D 完整优化")
    result = import_data(manager, entry.id, dataset)
    backend = NMRPipeBackend()
    from workflow.stepwise import generate_spectrum

    spec = generate_spectrum(manager, entry.id, result.data_id, backend)
    print("[3D] 初始谱:", _metrics(Path(spec)))
    out = optimize_phase_brute_force(
        manager, entry.id, result.data_id, backend
    )
    print("[3D] 相位:", out["phase"], "backend_runs:", out["backend_runs"])
    print("[3D] 基线配置:", out.get("baseline", {}).get("config"))
    print("[3D] 优化后谱:", _metrics(Path(out["spectrum_path"])))
    manager.save()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("2d", "3d", "all"))
    parser.add_argument("--dataset-2d", type=Path, default=Path("/home/<lab-user>/Desktop/data/sampleA"))
    parser.add_argument("--dataset-3d", type=Path, default=Path("/home/<lab-user>/Desktop/data/sampleB"))
    parser.add_argument("--root", type=Path, default=Path("/home/<lab-user>/val_opt"))
    opts = parser.parse_args(argv if argv is not None else sys.argv[1:])
    opts.root.mkdir(parents=True, exist_ok=True)
    if opts.mode in ("2d", "all"):
        run_2d(opts.dataset_2d, opts.root)
    if opts.mode in ("3d", "all"):
        run_3d(opts.dataset_3d, opts.root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
