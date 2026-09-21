r"""Real-engine regression: render extract=False process.com and run (direction control). Purpose:
Verify that the water peak 4.7 ppm is a vertical vertical line on the 1H full-width (unclipped)
spectrum (1H is on the F2 horizontal axis, height ppm is on the left; 15N is on the F1 vertical
axis, height ppm is on the bottom). Usage (VM): nmrforge/bin/python
scripts/vm_sample_noext.py \ <project root> <exp_id> <data_id>."""

from __future__ import annotations

import sys
from pathlib import Path

from backend.runtime import CshRuntime
from backend.script_generator import generate_process_script
from core.data.bruker_reader import read_dataset
from core.planning.method_selector import select_method
from core.project import ProjectManager


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 3:
        print("Usage: vm_sample_noext.py <project root> <exp_id> <data_id>")
        return 1
    root, exp_id, data_id = args
    manager = ProjectManager.open_project(Path(root))
    entry = manager.project.experiment(exp_id)
    if entry is None:
        print(f"experiment does not exist: {exp_id}")
        return 1
    data = manager.data(exp_id, data_id)
    raw_dir = Path(data.raw_dir)
    if not raw_dir.is_absolute():
        raw_dir = manager.root / raw_dir
    experiment = read_dataset(raw_dir)
    plan = select_method(experiment)

    process_dir = manager.data_dir(exp_id, data_id, "process")
    fid = process_dir / "raw.fid"
    if not fid.is_file():
        print(f"Missing fid: {fid}")
        return 1
    script = generate_process_script(
        experiment, plan, in_file="raw.fid", out_file="raw_noext.ft2",
        extract=False,
    )
    com = process_dir / "raw_noext_process.com"
    com.write_text(script, encoding="utf-8", newline="\n")
    print(com.read_text(encoding="utf-8"))
    result = CshRuntime().run(["csh", com.name], cwd=str(process_dir), timeout=7200)
    print(f"rc={result.returncode}")
    out = process_dir / "raw_noext.ft2"
    print(f"out={out} exists={out.is_file()} size={out.stat().st_size if out.is_file() else 0}")
    return 0 if result.returncode == 0 and out.is_file() else 1


if __name__ == "__main__":
    raise SystemExit(main())
