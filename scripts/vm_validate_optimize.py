"""Architect VM 验证:2D/3D 真实数据自动优化有效性(相位/基线)。

覆盖:
- 2D 均匀(sampleA):步骤化出谱 → 基线优化(baseline_optimize)→
  相位逐维暴力(optimize_phase_sequential,直接维+间接维)→ 对比指标;
- 3D NUS(sampleB):SMILE 重构 → 基线优化 → 相位优化(NUS 路径:
  finalize_nus 逐间接维候选,不重跑 SMILE)→ 对比指标。

输出:优化前后 QC/相位分数/基线分数/主峰 + backend_runs。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np


def _load(path: Path):
    import nmrglue as ng

    dic, data = ng.pipe.read(str(path))
    return dic, data


def _metrics(path: Path) -> dict:
    from core.qc import baseline_quality, phase_quality, spectrum_quality

    _dic, data = _load(path)
    arr = np.asarray(data)
    q = spectrum_quality.evaluate(arr)
    ph = phase_quality.evaluate(arr)
    bl = baseline_quality.evaluate(arr)
    idx = np.unravel_index(int(np.argmax(np.abs(arr))), arr.shape)
    return {
        "QC": round(q.score.overall, 1),
        "phase_score": round(ph.score, 2),
        "baseline_score": round(bl.score, 1),
        "shape": list(arr.shape),
        "main_idx": (int(idx[0]), int(idx[1])),
    }


def run_2d(dataset: Path, work_root: Path) -> None:
    from backend.nmrpipe_backend import NMRPipeBackend
    from core.data.bruker_reader import read_dataset
    from core.project import ProjectManager
    from workflow.baseline_optimize import optimize_baseline
    from workflow.phase_optimize import optimize_phase_sequential
    from workflow.stepwise import generate_fid, generate_spectrum

    root = work_root / "opt2d"
    if root.exists():
        import shutil

        shutil.rmtree(root)
    manager = ProjectManager.create_project(root, "opt2d")
    entry = manager.create_experiment(title="2D 优化验证")
    from workflow.import_workflow import import_data

    result = import_data(manager, entry.id, dataset)
    backend = NMRPipeBackend()
    generate_fid(manager, entry.id, result.data_id, backend)
    spec = generate_spectrum(manager, entry.id, result.data_id, backend)
    print(f"[2D] 初始谱: {spec}")
    print("  指标:", _metrics(Path(spec)))

    exp = read_dataset(root / entry.id / result.data_id / "raw")
    bl = optimize_baseline(exp, spec)
    print(f"[2D] 基线优化: {bl.logs}")
    print("  基线配置:", bl.baseline)

    ph = optimize_phase_sequential(exp, backend)
    print(f"[2D] 相位优化: phases={ph.phases} backend_runs={ph.backend_runs}")
    print("  优化后谱:", ph.spectrum_path)
    print("  指标:", _metrics(Path(ph.spectrum_path)))
    manager.save()


def run_3d(dataset: Path, work_root: Path) -> None:
    from backend.nmrpipe_backend import NMRPipeBackend
    from core.data.bruker_reader import read_dataset
    from workflow.baseline_optimize import optimize_baseline
    from workflow.phase_optimize import optimize_phase_sequential

    exp = read_dataset(dataset)
    backend = NMRPipeBackend(work_dir=str(work_root / "opt3d"))
    resp = backend.reconstruct_nus(exp, {})
    if not resp.get("success"):
        print(f"[3D] SMILE 重构失败: {resp.get('message')}")
        return
    spec = Path(resp["spectrum_path"])
    print(f"[3D] 初始谱: {spec}")
    print("  指标:", _metrics(spec))

    bl = optimize_baseline(exp, spec)
    print(f"[3D] 基线优化: {bl.logs}")
    print("  基线配置:", bl.baseline)

    ph = optimize_phase_sequential(exp, backend, work_dir=str(work_root / "opt3d"))
    print(f"[3D] 相位优化: phases={ph.phases} backend_runs={ph.backend_runs}")
    print("  优化后谱:", ph.spectrum_path)
    print("  指标:", _metrics(Path(ph.spectrum_path)))


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
