"""VM 验证:任意终谱 → proj3D 投影 → 直接维迹线 → HT 搜索。
用法: python scripts/vm_ht_proj_run.py <ft3路径> [直接维核,默认1H]
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from backend.runtime import CshRuntime
from core.optimization.phase_consensus import search_direct_phase_real_ht


def _traces_from_proj(path: Path) -> np.ndarray:
    import nmrglue as ng

    _dic, data = ng.pipe.read(str(path))
    data = np.asarray(data)
    if np.iscomplexobj(data):
        data = data.real
    if data.ndim != 2:
        raise RuntimeError(f"投影应为 2D: {path} {data.shape}")
    return data.reshape(-1, data.shape[-1])


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    src = Path(sys.argv[1])
    direct_nuc = sys.argv[2] if len(sys.argv) > 2 else "1H"
    proj_dir = Path(f"/tmp/{src.stem}_proj_ht")
    proj_dir.mkdir(exist_ok=True)
    for stale in proj_dir.glob("*.dat"):
        stale.unlink()
    run = CshRuntime().run(
        [
            "/home/<lab-user>/pipe/com/proj3D.tcl",
            "-in", str(src), "-outDir", str(proj_dir),
            "-sum", "-noverb",
        ],
        timeout=600,
    )
    print("proj3D rc:", run.returncode)
    if run.returncode != 0:
        print(run.stderr or run.stdout)
        return 1
    dats = sorted(proj_dir.glob("*.dat"))
    print("投影文件:", [p.name for p in dats])
    rows: list[np.ndarray] = []
    for dat in dats:
        tr = _traces_from_proj(dat)
        print(dat.name, tr.shape)
        if direct_nuc in dat.stem.split("."):
            rows.append(tr)
    if not rows:
        print(f"未找到含 {direct_nuc} 的投影")
        return 1
    traces = np.concatenate(rows, axis=0)
    print("直接维投影迹线:", traces.shape)
    for mode in ("uniform", "mixed"):
        est = search_direct_phase_real_ht(traces, axis=-1, sign_mode=mode)
        print(f"sign_mode={mode} HT 搜索:", est)
    return 0


if __name__ == "__main__":
    sys.exit(main())
