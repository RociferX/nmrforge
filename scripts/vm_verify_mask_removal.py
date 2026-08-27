"""VM 轻量验证:fid.com mask 阶段移除(0.2.199-补23,不跑 SMILE)。"""

from __future__ import annotations

import sys
from pathlib import Path

from backend.bruker_workflow import patch_fid_com
from core.data.bruker_reader import read_dataset


def main() -> int:
    raw = Path("/home/<lab-user>/Desktop/sampleK")
    work = Path("/home/<lab-user>/Desktop/sampleK.nmrpipe")
    exp = read_dataset(raw)
    text = (work / "fid.com").read_text(encoding="utf-8", errors="replace")
    patched, warnings = patch_fid_com(text, exp)
    print("mask 阶段存在:", "nusExpand.tcl -mask" in text)
    print("mask 阶段移除后:", "nusExpand.tcl -mask" not in patched)
    print("ser_full 保留:", "ser_full" in patched)
    print("102.fid 输出保留:", "-out ./102.fid" in patched)
    for w in warnings:
        if "mask" in w:
            print("WARN:", w)
    return 0


if __name__ == "__main__":
    sys.exit(main())
