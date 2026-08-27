"""VM 验证:终谱 28.ft3 → proj3D.tcl 投影 → 含直接维两平面抽迹线 →
HT(nmrPipe 约定)逐条调相 + 统计;对照 numpy 投影路径与记录相位 (0,0)。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from core.optimization.phase_consensus import search_direct_phase_real_ht


def _traces_from_proj(path: Path, direct_last: bool = True) -> np.ndarray:
    import nmrglue as ng

    dic, data = ng.pipe.read(str(path))
    data = np.asarray(data)
    if np.iscomplexobj(data):
        data = data.real  # 纯实终谱投影
    if data.ndim != 2:
        raise RuntimeError(f"投影应为 2D: {path} {data.shape}")
    # nmrglue 轴序反转:直接维在最后一轴
    return data.reshape(-1, data.shape[-1])


def main() -> int:
    import subprocess

    src = Path("/home/<lab-user>/Desktop/data/sampleB.nmrpipe/28.ft3")
    proj_dir = Path("/tmp/28_proj_ht")
    proj_dir.mkdir(exist_ok=True)
    for stale in proj_dir.glob("*.dat"):
        stale.unlink()
    run = subprocess.run(
        [
            "/home/<lab-user>/pipe/com/proj3D.tcl",
            "-in", str(src), "-outDir", str(proj_dir),
            "-sum", "-noverb",
        ],
        capture_output=True, text=True, timeout=600,
    )
    print("proj3D rc:", run.returncode)
    if run.returncode != 0:
        print(run.stderr)
        return 1
    dats = sorted(proj_dir.glob("*.dat"))
    print("投影文件:", [p.name for p in dats])
    rows: list[np.ndarray] = []
    for dat in dats:
        print(dat.name, _traces_from_proj(dat).shape)
        if "1H" in dat.stem.split("."):
            rows.append(_traces_from_proj(dat))
    if not rows:
        print("未找到含 1H(直接维)的投影")
        return 1
    traces = np.concatenate(rows, axis=0)
    print("直接维投影迹线:", traces.shape)
    est = search_direct_phase_real_ht(traces, axis=-1, sign_mode="uniform")
    print("投影文件 HT 搜索:", est)

    # 对照:numpy 直接投影路径(全实谱)
    import nmrglue as ng

    _dic, arr = ng.pipe.read(str(src))
    arr = np.asarray(arr)
    if np.iscomplexobj(arr):
        arr = arr.real
    print("28.ft3 numpy shape:", arr.shape)
    est2 = search_direct_phase_real_ht(arr, axis=-1, sign_mode="uniform")
    print("numpy 投影 HT 搜索:", est2)
    print("记录直接维相位: (0, 0)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
