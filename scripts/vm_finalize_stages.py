"""VM 轻量检查:finalize 链逐步落盘,确认各阶段流形状与复型结构(不跑 SMILE)。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import nmrglue as ng


def _dump(work: Path, name: str, script: str) -> None:
    out = work / name
    bin_dir = "/home/<lab-user>/pipe/nmrbin.linux235_64"
    full = (
        f"cd {work} && export PATH={bin_dir}:$PATH; "
        + script
        + f"| pipe2xyz -out {out.name} -x"
    )
    r = subprocess.run(full, shell=True, capture_output=True, text=True)
    err = (r.stderr or "").splitlines()
    warns = [l for l in err if "Warning" in l or "Error" in l]
    print(f"== {name} rc={r.returncode} ==")
    if warns:
        print("warns:", warns[:4])
    if out.is_file():
        dic, data = ng.pipe.read(str(out))
        arr = np.asarray(data)
        print(
            "  shape", arr.shape,
            "complex" if np.iscomplexobj(data) else "real",
        )
    else:
        print("  NO OUTPUT")



def main() -> int:
    work = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    _dump(
        work,
        "stage0.ft3",
        "xyz2pipe -in nus3d_rc/test%04d.ft1 -x \\\n",
    )
    _dump(
        work,
        "stage1.ft3",
        "xyz2pipe -in nus3d_rc/test%04d.ft1 -x \\\n"
        "| nmrPipe -fn ZF -size 128 \\\n"
        "| nmrPipe -fn FT \\\n"
        "| nmrPipe -fn PS -p0 0 -p1 0 \\\n",
    )
    _dump(
        work,
        "stage2.ft3",
        "xyz2pipe -in nus3d_rc/test%04d.ft1 -x \\\n"
        "| nmrPipe -fn ZF -size 128 \\\n"
        "| nmrPipe -fn FT \\\n"
        "| nmrPipe -fn PS -p0 0 -p1 0 \\\n"
        "| nmrPipe -fn TP \\\n",
    )
    _dump(
        work,
        "stage3.ft3",
        "xyz2pipe -in nus3d_rc/test%04d.ft1 -x \\\n"
        "| nmrPipe -fn ZF -size 128 \\\n"
        "| nmrPipe -fn FT \\\n"
        "| nmrPipe -fn PS -p0 0 -p1 0 \\\n"
        "| nmrPipe -fn TP \\\n"
        "| nmrPipe -fn ZF -size 256 \\\n"
        "| nmrPipe -fn FT -alt \\\n"
        "| nmrPipe -fn PS -p0 0 -p1 0 \\\n",
    )
    _dump(
        work,
        "stage4.ft3",
        "xyz2pipe -in nus3d_rc/test%04d.ft1 -x \\\n"
        "| nmrPipe -fn ZF -size 128 \\\n"
        "| nmrPipe -fn FT \\\n"
        "| nmrPipe -fn PS -p0 0 -p1 0 \\\n"
        "| nmrPipe -fn TP \\\n"
        "| nmrPipe -fn ZF -size 256 \\\n"
        "| nmrPipe -fn FT -alt \\\n"
        "| nmrPipe -fn PS -p0 0 -p1 0 \\\n"
        "| nmrPipe -fn TP \\\n"
        "| nmrPipe -fn ZTP \\\n",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
