"""VM 轻量检查:对 nus3d_rc 流做 ZF 128+FT,看哪个轴被 FT(ZF 只到 128 的轴即被 FT)。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import nmrglue as ng


def main() -> int:
    work = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    bin_dir = "/home/<lab-user>/pipe/nmrbin.linux235_64"
    out = work / "probe_minft.ft3"
    script = (
        f"cd {work} && export PATH={bin_dir}:$PATH; "
        "xyz2pipe -in nus3d_rc/test%04d.ft1 -x \\\n"
        "| nmrPipe -fn ZF -size 128 \\\n"
        "| nmrPipe -fn FT -verb \\\n"
        f"| pipe2xyz -out {out.name} -x"
    )
    r = subprocess.run(script, shell=True, capture_output=True, text=True)
    print("rc:", r.returncode)
    err = r.stderr or ""
    print("stderr head:", err[:1200])
    if out.is_file():
        dic, data = ng.pipe.read(str(out))
        arr = np.asarray(data)
        print(
            "probe_minft shape", arr.shape,
            "complex" if np.iscomplexobj(data) else "real",
        )
        out.unlink()
    return 0


if __name__ == "__main__":
    sys.exit(main())
