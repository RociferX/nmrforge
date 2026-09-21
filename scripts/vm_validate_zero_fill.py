"""Real-engine retest: zero filling dynamic SI (zero_fill_plan, 0.2.44). Real data: - 2D uniform
sampleA: Stepped spectrum, check process.com's ZF row (direct dimension 2 x TD, indirect
dimension dynamic), spectrum shape/main peak/QC; - 3D NUS sampleB: reconstruct_nus, check
nus.com of ZF rows, spectrum shape/main peak. output: zero_fill_plan dimension-wise SI, script
ZF rows, spectrum index, with full sampling/Manual consistency."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import numpy as np


def _metrics(path: Path) -> dict:
    import nmrglue as ng

    from core.qc import spectrum_quality

    dic, data = ng.pipe.read(str(path))
    arr = np.asarray(data)
    q = spectrum_quality.evaluate(arr)
    idx = np.unravel_index(int(np.argmax(np.abs(arr))), arr.shape)
    return {
        "QC": round(q.score.overall, 1),
        "shape": list(arr.shape),
        "FDTRANSPOSED": int(dic.get("FDTRANSPOSED", 0) or 0),
        "main_idx": (int(idx[0]), int(idx[1])),
    }


def run_2d(dataset: Path, root: Path) -> None:
    from backend.nmrpipe_backend import NMRPipeBackend
    from core.project import ProjectManager
    from workflow.import_workflow import import_data
    from workflow.stepwise import generate_fid, generate_spectrum

    proj = root / "zf_2d"
    if proj.exists():
        shutil.rmtree(proj)
    manager = ProjectManager.create_project(proj, "zf_2d")
    entry = manager.create_experiment(title="ZF 2D")
    result = import_data(manager, entry.id, dataset)
    backend = NMRPipeBackend()
    generate_fid(manager, entry.id, result.data_id, backend)
    spec = generate_spectrum(manager, entry.id, result.data_id, backend)
    proc = proj / entry.id / result.data_id / "process"
    com = next(proc.glob("*_process.com"))
    zf_lines = [ln.strip() for ln in com.read_text().splitlines() if "ZF" in ln]
    print("[2D] process.com ZF lines:")
    for ln in zf_lines:
        print("   ", ln)
    print("[2D] Spectrum:", spec)
    print("[2D] Indicator:", _metrics(Path(spec)))
    manager.save()


def run_3d(dataset: Path, root: Path) -> None:
    from backend.nmrpipe_backend import NMRPipeBackend
    from core.data.bruker_reader import read_dataset

    exp = read_dataset(dataset)
    work = root / "zf_3d"
    work.mkdir(parents=True, exist_ok=True)
    backend = NMRPipeBackend(work_dir=str(work))
    resp = backend.reconstruct_nus(exp, {})
    if not resp.get("success"):
        print("[3D] SMILE Reconstruction failed:", resp.get("message"))
        return
    nus_com = work / f"{exp.dataset_id}_nus.com"
    zf_lines = [
        ln.strip() for ln in nus_com.read_text().splitlines() if "ZF" in ln
    ]
    print("[3D] nus.com ZF lines:")
    for ln in zf_lines:
        print("   ", ln)
    print("[3D] Spectrum:", resp.get("spectrum_path"))
    print("[3D] Indicators:", _metrics(Path(resp["spectrum_path"])))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("2d", "3d", "all"))
    parser.add_argument(
        "--dataset-2d", type=Path, default=Path("/home/<lab-user>/Desktop/data/sampleA")
    )
    parser.add_argument(
        "--dataset-3d", type=Path, default=Path("/home/<lab-user>/Desktop/data/sampleB")
    )
    parser.add_argument(
        "--root", type=Path, default=Path("outputs/zero-fill-validation"),
        help="Output directory (default: outputs/zero-fill-validation)",
    )
    opts = parser.parse_args(argv if argv is not None else sys.argv[1:])
    opts.root.mkdir(parents=True, exist_ok=True)
    if opts.mode in ("2d", "all"):
        run_2d(opts.dataset_2d, opts.root)
    if opts.mode in ("3d", "all"):
        run_3d(opts.dataset_3d, opts.root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
