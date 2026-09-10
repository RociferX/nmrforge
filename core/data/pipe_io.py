"""NMRPipe 谱文件读取。

NMRPipe 2D/3D 谱文件（.ft1/.ft2/.ft3）的复型数据以「第一轴实/虚交错」存储，
nmrglue 会把它读成第一轴翻倍的实型数组；本模块负责拆包还原复型。
（实测验证：nus3d_rc 平面 (268,112) 实型 → (134,112) 复型，拆包后 FT 出清晰峰。）
"""

from __future__ import annotations

from pathlib import Path

import numpy as np


def _unpack_interleaved(arr: np.ndarray) -> np.ndarray:
    """把第一轴实/虚交错数组拆包为复型（偶数行=实部，奇数行=虚部）。"""
    if arr.shape[0] % 2 != 0:
        raise ValueError("第一轴长度为奇数，不是交错复型存储")
    return arr[0::2] + 1j * arr[1::2]


def read_pipe_complex(path: Path | str) -> np.ndarray:
    """读取 NMRPipe 谱文件并拆包为复型数组。"""
    import nmrglue as ng

    _dic, arr = ng.pipe.read(str(path))
    return _unpack_interleaved(np.asarray(arr))
