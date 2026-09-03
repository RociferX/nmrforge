"""VM 验证:旧切片工作目录重转能否产出单文件(0.2.199,不跑 SMILE)。"""

from __future__ import annotations

import sys
from pathlib import Path

from backend.nmrpipe_backend import NMRPipeBackend
from core.data.bruker_reader import read_dataset


def main() -> int:
    raw = Path("/home/<lab-user>/Desktop/data/sampleC")
    work = Path("/home/<lab-user>/Desktop/data/sampleC.nmrpipe")
    exp = read_dataset(raw)
    print("dataset_id:", exp.dataset_id)
    print("single fid 存在:", (work / f"{exp.dataset_id}.fid").is_file())
    print("旧切片存在:", (work / "fid").is_dir())
    backend = NMRPipeBackend(work_dir=str(work))
    resp = backend.convert_to_fid(exp, str(raw))
    print("success:", resp.get("success"), "|", resp.get("message"))
    for line in resp.get("logs", []):
        if "mask" in line or "102" in line.lower() or "out" in line.lower():
            print("LOG:", line)
    fid = work / f"{exp.dataset_id}.fid"
    print("转换后单文件存在:", fid.is_file())
    if fid.is_file():
        print("大小:", fid.stat().st_size)
    return 0


if __name__ == "__main__":
    sys.exit(main())
