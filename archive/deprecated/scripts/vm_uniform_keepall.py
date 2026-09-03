"""VM 验证:uniform sampleF 经 app backend keep_complex_all 全复谱 + 逐维搜索。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import nmrglue as ng

from backend.nmrpipe_backend import NMRPipeBackend
from core.data.bruker_reader import read_dataset
from workflow.phase_routes import _read_complex_preview


def main() -> int:
    raw = Path("/home/<lab-user>/Desktop/sampleF")
    work = Path("/home/<lab-user>/Desktop/sampleF.nmrpipe")
    exp = read_dataset(raw)
    backend = NMRPipeBackend(work_dir=str(work))
    from core.planning.method_selector import select_method

    plan = select_method(exp)
    resp = backend.process(
        exp,
        plan,
        params={"keep_complex_all": True, "zero_fill": 1},
        out_file="3_keepall.ft2",
        script_name="3_keepall.com",
    )
    print("process ok:", resp.get("success"), resp.get("message", ""))
    p = resp.get("spectrum_path")
    dic, rawd = ng.pipe.read(str(p))
    arr = np.asarray(rawd)
    print("read:", arr.shape, "complex:", np.iscomplexobj(rawd))
    print("FDF1QUADFLAG:", dic.get("FDF1QUADFLAG"),
          "FDF2QUADFLAG:", dic.get("FDF2QUADFLAG"))
    cplx = _read_complex_preview(str(p), unpack_axis=1)
    print("preview cplx:", cplx.shape, np.iscomplexobj(cplx))
    return 0


if __name__ == "__main__":
    sys.exit(main())
