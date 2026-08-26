"""VM 验证:sampleC 当前代码自动路径的终跑脚本 POLY 与人工脚本对照。"""

from __future__ import annotations

import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from backend.config import load_config
from backend.factory import create_backend
from core.project import ProjectManager
from workflow.import_workflow import import_data
from workflow.stepwise import generate_fid, generate_spectrum

SOURCE = pathlib.Path.home() / "Desktop" / "data" / "sample" / "30"


def main() -> int:
    root = pathlib.Path(tempfile.mkdtemp(prefix="nmr_sample30_"))
    mgr = ProjectManager.create_project(root, "sample30")
    entry = mgr.create_experiment(title="sample30")
    res = import_data(mgr, entry.id, SOURCE, copy=True)
    mgr.save()
    backend = create_backend(load_config())
    generate_fid(mgr, res.experiment_id, res.data_id, backend)
    generate_spectrum(
        mgr,
        res.experiment_id,
        res.data_id,
        backend,
        progress=print,
    )
    work = mgr.data_dir(res.experiment_id, res.data_id, "process")
    final = work / f"{res.data_id}_nus.com"
    print("== 终跑脚本 POLY 行 ==")
    for i, ln in enumerate(final.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if "POLY" in ln:
            print(f"{i}: {ln.strip()}")
    print("== 人工脚本 smile.com POLY 行 ==")
    manual = SOURCE / "smile.com"
    for i, ln in enumerate(manual.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if "POLY" in ln:
            print(f"{i}: {ln.strip()}")
    print("sampleC 验证完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
