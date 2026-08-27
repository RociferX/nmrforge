"""sampleK unified 全流程实跑(0.2.199-补18 复型频域相位搜索)。"""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

from backend.nmrpipe_backend import NMRPipeBackend
from core.data.bruker_reader import read_dataset


def main() -> int:
    from workflow.phase_routes import unified_route

    raw = Path("/home/<lab-user>/Desktop/sampleK")
    work = Path("/home/<lab-user>/Desktop/sampleK.nmrpipe")
    # 清相位缓存与旧预览,强制重新搜索
    for stale in ("phase.json", "102_direct_preview.ft3"):
        p = work / stale
        if p.is_file():
            p.unlink()
    exp = read_dataset(raw)
    # 0.2.199-补22:不强制填零,保持原有默认逻辑(NUS 直接维 1×TD)
    params = {
        "ext_lo": "9.0",
        "ext_hi": "7.0",
        "nthread": 6,
    }
    backend = NMRPipeBackend(work_dir=str(work))
    t0 = time.time()

    def progress(msg: str) -> None:
        print(f"[{time.time() - t0:6.1f}s] {msg}", flush=True)

    result = unified_route(
        exp, backend, plan=None, work_dir=str(work),
        base_params=params, progress=progress,
    )
    print(f"elapsed: {time.time() - t0:.1f}s")
    print(f"success: {result.get('success')} | {result.get('message')}")
    for line in result.get("logs", []):
        print("LOG:", line)
    phase_file = work / "phase.json"
    if phase_file.is_file():
        print("PHASE.JSON:", phase_file.read_text(encoding="utf-8").strip())
    print("spectrum:", result.get("spectrum_path"))
    return 0 if result.get("success") else 2


if __name__ == "__main__":
    sys.exit(main())
