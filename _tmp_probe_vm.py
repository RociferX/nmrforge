# -*- coding: utf-8 -*-
"""VM 探针:修复后 2D 人工谱图路径."""
import os
import shutil
import tempfile
import yaml
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

raw_src = Path("/home/<lab-user>/Desktop/data/sampleA")
tmp = Path("/tmp/nf_manual_2d4")
if tmp.exists():
    shutil.rmtree(tmp)
tmp.mkdir(parents=True)
raw = tmp / "20"
shutil.copytree(raw_src, raw, ignore=shutil.ignore_patterns("*.ft2", "*.ucsf", "*.com"))

from core.project import ProjectManager

manager = ProjectManager.create_project(tmp / "proj", "demo")
entry = manager.create_experiment("HSQC")
data = manager.import_data(entry.id, str(raw))
manager.save()

from backend.factory import create_backend

cfg = yaml.safe_load(Path("/home/<lab-user>/NMRForge/config/nmrforge.yaml").read_text())
backend = create_backend(cfg)
work = manager.data_dir(entry.id, data.id, "process")
if hasattr(backend, "work_dir"):
    backend.work_dir = str(work)

from workflow.stepwise import generate_fid

fid = generate_fid(manager, entry.id, data.id, backend)
print("FID:", Path(fid).name, "size:", Path(fid).stat().st_size)

from workflow.manual import manual_scripts, run_manual_spectrum

scripts = manual_scripts(manager, entry.id, data.id)
key = "process.com"
content = scripts[key]
print("script in:", [l for l in content.splitlines() if "xyz2pipe -in" in l][0].strip())

try:
    spec = run_manual_spectrum(manager, entry.id, data.id, {key: content})
    print("MANUAL SPECTRUM OK:", spec, "exists:", Path(spec).exists())
except Exception as e:
    print("MANUAL FAIL:", type(e).__name__, repr(str(e))[:200])
