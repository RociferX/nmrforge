"""VM 验证:单文件/切片输入选择(0.2.199-补27,不跑 SMILE)。"""

from __future__ import annotations

import sys
from pathlib import Path

from backend.nmrpipe_backend import _slice_in_file


def main() -> int:
    cases = (
        ("sampleK", Path("/home/<lab-user>/Desktop/sampleK.nmrpipe"), "102"),
        ("sampleB", Path("/home/<lab-user>/Desktop/data/sampleB.nmrpipe"), "28"),
        ("sampleC", Path("/home/<lab-user>/Desktop/data/sampleC.nmrpipe"), "30"),
    )
    for name, work, dsid in cases:
        fid_file = work / f"{dsid}.fid"
        slice_in = _slice_in_file(work / "fid", dsid)
        if fid_file.is_file():
            in_file = fid_file.name
        elif slice_in:
            in_file = slice_in
        else:
            in_file = "(无)"
        print(
            f"{name}: 单文件={fid_file.is_file()} 切片={bool(slice_in)} "
            f"→ in_file={in_file}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
