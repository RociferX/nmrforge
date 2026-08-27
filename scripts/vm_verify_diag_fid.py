"""VM 轻量验证:诊断读取单文件 aq2D fid(0.2.199-补25,不跑 SMILE)。"""

from __future__ import annotations

import sys
from pathlib import Path

from workflow.direct_diagnostics import _collect_fid_paths, _read_fid_raw


def main() -> int:
    from core.data.bruker_reader import read_dataset

    raw = Path("/home/<lab-user>/Desktop/sampleK")
    work = Path("/home/<lab-user>/Desktop/sampleK.nmrpipe")
    exp = read_dataset(raw)
    paths = _collect_fid_paths(work, exp)
    print("fid paths:", [str(p) for p in paths])
    for p in paths:
        res = _read_fid_raw(p)
        if res is None:
            print(f"{p.name}: 解析失败")
        else:
            arr, fdsize, specnum, header = res
            print(
                f"{p.name}: shape={arr.shape} fdsize={fdsize} "
                f"nrows={specnum} header={header}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
