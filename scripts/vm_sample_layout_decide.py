"""sampleB 布局定案:直接维在重构堆叠的哪个轴(0.2.199)。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from core.data.pipe_io import read_pipe_complex


def main() -> int:
    import nmrglue as ng

    work = Path("/home/<lab-user>/Desktop/data/sampleB.nmrpipe")
    ps = sorted((work / "nus3d_rc").glob("test*.ft1"))
    arrays = [read_pipe_complex(p) for p in ps]
    stack = np.stack(arrays, axis=-1)  # (i0, i1, plane)
    print("recon stack:", stack.shape)
    # 终谱最强峰
    ft3s = sorted((work / "spectra").glob("*.ft3"))
    if not ft3s:
        ft3s = sorted(work.glob("*.ft3"))
    _dic, data = ng.pipe.read(str(ft3s[0]))
    fin = np.asarray(data, dtype=float)
    print("final:", ft3s[0].name, fin.shape)
    magf = np.abs(fin)
    t = int(np.argmax(magf))
    idx = np.unravel_index(t, magf.shape)
    print("final strongest peak:", idx)
    # 终谱直接轴:哪个轴的迹线是 1H 直接维(峰在轴中间、线型干净)
    # 用每个轴的迹线对称性粗判:直接维若未调相应为色散(对称性低)
    # 更直接:重构堆叠最强点沿各轴的迹线,看哪个轴峰位与终谱一致
    mags = np.abs(stack)
    t2 = int(np.argmax(mags))
    r0, r1, rk = np.unravel_index(t2, mags.shape)
    print(f"recon strongest: (i0={r0}, i1={r1}, k={rk})")
    for ax, name in ((0, "axis0"), (1, "axis1"), (2, "axis2/planes")):
        trace = np.take(stack, (r1, r0, r0), axis=ax) if ax == 1 else np.take(stack, (r0, r0, r0), axis=ax)
        # 沿 ax 取通过最强点的迹线
        if ax == 0:
            tr = stack[:, r1, rk]
        elif ax == 1:
            tr = stack[r0, :, rk]
        else:
            tr = stack[r0, r1, :]
        j = int(np.argmax(np.abs(tr)))
        ph = np.rad2deg(np.angle(tr))
        print(
            f"  {name}: 迹线峰@k={j} 峰相位={float(ph[j]):.1f}° "
            f"相位序列={[round(float(ph[q]), 1) for q in range(0, tr.size, max(tr.size // 8, 1))]}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
