"""sampleK 最强信号直接维迹线检查(0.2.199):线型/相位扫描。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from core.data.pipe_io import read_pipe_complex


def _sym(win: np.ndarray) -> float:
    win = np.asarray(win, dtype=float)
    if win.size < 2:
        return 0.0
    left = win[: len(win) // 2]
    right = win[len(win) - len(left):][::-1]
    return float(np.mean((left + right) ** 2 / (2 * (left**2 + right**2) + 1e-12)))


def main() -> int:
    import nmrglue as ng

    work = Path("/home/<lab-user>/Desktop/sampleK.nmrpipe")
    planes = sorted((work / "nus3d_rc").glob("test*.ft1"))
    arrays = [read_pipe_complex(p) for p in planes]
    stack = np.stack(arrays, axis=-1)  # (i0, i1, k=直接维)
    # 终谱最强峰(217,397,71)对应的重构点:用之前验证的相关点
    _dic, data = ng.pipe.read(str(work / "102.ft3"))
    fin = np.asarray(data, dtype=float)
    tr = fin[217, 397, :]
    trn = tr - tr.mean()
    trn /= np.linalg.norm(trn)
    best = (-1, 0, 0)
    for i0 in range(0, stack.shape[0], 2):
        for i1 in range(0, stack.shape[1], 2):
            v = np.real(stack[i0, i1, :])
            v = v - v.mean()
            nv = np.linalg.norm(v)
            if nv < 1e-9:
                continue
            c = float(np.dot(trn, v / nv))
            if c > best[0]:
                best = (c, i0, i1)
    c, i0, i1 = best
    print(f"最佳相关: corr={c:.3f} (i0={i0}, i1={i1})")
    trace = stack[i0, i1, :]
    n = trace.size
    k = np.arange(n, dtype=float)
    print("直接迹线: |峰|@", int(np.argmax(np.abs(trace))),
          " 峰相位(未校正):", round(float(np.rad2deg(np.angle(trace[int(np.argmax(np.abs(trace)))]))), 1))
    # 扫描 p0/p1 使窗口对称性最高
    j = int(np.argmax(np.abs(trace)))
    half = 12
    best_ph = (-1, 0.0, 0.0)
    for p0 in np.arange(0.0, 360.0, 5.0):
        for p1 in np.arange(-90.0, 91.0, 15.0):
            rot = trace * np.exp(1j * np.deg2rad(p0 + p1 * k / max(n - 1, 1)))
            re = np.real(rot)
            win = re[max(0, j - half) : j + half + 1]
            s = _sym(win)
            if s > best_ph[0]:
                best_ph = (s, p0, p1)
    s, p0b, p1b = best_ph
    print(f"最优相位: p0={p0b:.0f}° p1={p1b:.0f}° 窗口对称={s:.3f}")
    # 峰值处的复相位序列(前 20 点)看是否线性
    ph = np.rad2deg(np.angle(trace))
    print("迹线峰相位序列(每 8 点):",
          [round(float(ph[q]), 1) for q in range(0, n, 8)])
    return 0


if __name__ == "__main__":
    sys.exit(main())
