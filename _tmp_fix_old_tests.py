# -*- coding: utf-8 -*-
"""更新旧人工测试:谱名按 data_id(d_001),不再用 raw 目录名."""
from pathlib import Path

# test_manual.py
p = Path("tests/test_manual.py")
t = p.read_text(encoding="utf-8")
count = t.count('_FakeRuntime(spectrum_name=f"{raw.name}.ft2")')
assert count == 5, f"manual runtime count={count}"
t = t.replace(
    '_FakeRuntime(spectrum_name=f"{raw.name}.ft2")',
    '_FakeRuntime(spectrum_name="d_001.ft2")',
)
p.write_text(t, encoding="utf-8")
print("updated test_manual.py")

# test_manual_workflow.py
p = Path("tests/test_manual_workflow.py")
t = p.read_text(encoding="utf-8")
old = '    runtime = FakeRuntime(Path(f"{experiment.dataset_id}.ft2"))\n'
assert t.count(old) == 1, "workflow runtime anchor"
t = t.replace(old, '    runtime = FakeRuntime(Path("d_001.ft2"))\n')
# fid 文件也用 d_001
old = '    (work / f"{experiment.dataset_id}.fid").write_bytes(b"FID")\n'
assert t.count(old) == 1, "workflow fid anchor"
t = t.replace(old, '    (work / "d_001.fid").write_bytes(b"FID")\n')
p.write_text(t, encoding="utf-8")
print("updated test_manual_workflow.py")
print("ALL DONE")
