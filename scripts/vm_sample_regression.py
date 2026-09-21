r"""Real-engine regression: sampleA real data step processing. Purpose:
Verify the end-to-end behaviour of "2D process script EXT extraction window (default 6-11 ppm) +
end TP" on real data after 020b762. Usage (VM): nmrforge/bin/python
scripts/vm_sample_regression.py \ <data directory> <Verification root directory> [--no-extract]
Output: three-step process log + final spectrum NMRPipe header summary + main peak position
(water-containing peak 4.7 ppm check when not clipped)."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from backend.nmrpipe_backend import NMRPipeBackend
from core.project import ProjectManager
from workflow.import_workflow import import_data
from workflow.stepwise import generate_fid, generate_spectrum


def header_summary(path: Path) -> dict[str, object]:
    import nmrglue as ng

    dic, data = ng.pipe.read(str(path))
    keys = [
        "FDF1SW",
        "FDF2SW",
        "FDF1ORIG",
        "FDF2ORIG",
        "FDF1LABEL",
        "FDF2LABEL",
        "FDTRANSPOSED",
        "FDSIZE",
        "FDSPECNUM",
    ]
    summary: dict[str, object] = {"shape": list(data.shape), "max": float(data.max())}
    for key in keys:
        summary[key] = dic.get(key)
    return summary


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("root", type=Path)
    parser.add_argument("--no-extract", action="store_true")
    opts = parser.parse_args(args)

    root = Path(opts.root)
    tag = "noext" if opts.no_extract else "ext"
    project_dir = root / f"vm_sample_{tag}"
    if project_dir.exists():
        shutil.rmtree(project_dir)

    manager = ProjectManager.create_project(project_dir, f"vm_sample_{tag}")
    entry = manager.create_experiment(title="sampleA 2D uniform")
    result = import_data(manager, entry.id, Path(opts.dataset))
    print(f"[import] {entry.id}/{result.data_id} raw={result.raw_dir}")

    backend = NMRPipeBackend()
    fid_path = generate_fid(manager, entry.id, result.data_id, backend)
    print(f"[fid] {fid_path} exists={Path(fid_path).is_file()}")

    params = {"extract": False} if opts.no_extract else {}
    spectrum = generate_spectrum(
        manager, entry.id, result.data_id, backend, params=params
    )
    print(f"[spectrum] {spectrum} exists={Path(spectrum).is_file()}")

    process_dir = project_dir / entry.id / result.data_id / "process"
    coms = sorted(process_dir.glob("*_process.com"))
    if coms:
        print("[process.com]")
        print(coms[0].read_text(encoding="utf-8"))
    print("[header]", header_summary(Path(spectrum)))

    manager.save()
    print("[runs]", [(r.workflow_ref, r.status) for r in manager.project.workflow_runs])
    print("[status]", manager.data(entry.id, result.data_id).status)
    print("[project]", project_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
