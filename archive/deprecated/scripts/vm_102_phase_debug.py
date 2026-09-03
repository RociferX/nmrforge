"""sampleK SMILE 相位调试(0.2.199):直接维填零 1024 + 显示层相位搜索。

用法(VM):
    PYTHONPATH=$HOME/NMRForge python scripts/vm_102_phase_debug.py

清掉 phase.json/重构中间产物强制全流程,输出日志、phase.json 与终谱。
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

from backend.nmrpipe_backend import NMRPipeBackend
from core.data.bruker_reader import read_dataset


def main() -> int:
    raw = Path("/home/<lab-user>/Desktop/sampleK")
    work = Path("/home/<lab-user>/Desktop/sampleK.nmrpipe")
    for stale in ("phase.json", "nus3d_rc", "nus3d_rc_ph", "nus3d_1"):
        p = work / stale
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        elif p.is_file():
            p.unlink()
    exp = read_dataset(raw)
    print(
        f"experiment: {exp.dataset_id} {exp.ndim}D "
        f"{exp.sampling.mode.value} td={[d.td for d in exp.dimensions]}"
    )
    params = {
        "ext_lo": "9.0",
        "ext_hi": "7.0",
        "nthread": 6,
        "zero_fill": {"F3": {"mode": "size", "size": 1024}},
        "display_phase_search": True,
        "direct_phase_search": True,
        "timeout_s": 1800,
    }
    backend = NMRPipeBackend(work_dir=str(work))
    health = backend.health_check()
    print(f"health: ok={health.get('ok')} {health.get('message')}")
    if not health.get("ok"):
        return 1
    t0 = time.time()
    resp = backend.reconstruct_nus(exp, params)
    print(f"elapsed: {time.time() - t0:.1f}s")
    print(f"success: {resp.get('success')} | {resp.get('message')}")
    for line in resp.get("logs", []):
        print("LOG:", line)
    phase_file = work / "phase.json"
    if phase_file.is_file():
        print("PHASE.JSON:", phase_file.read_text(encoding="utf-8").strip())
    print("spectrum:", resp.get("spectrum_path"))
    return 0 if resp.get("success") else 2


if __name__ == "__main__":
    sys.exit(main())
