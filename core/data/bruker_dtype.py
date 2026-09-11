"""Bruker 原始数据(ser/fid)的元素类型:DTYPE + BYTORDA。

TopSpin 约定(`##$DTYPE`):
    0 → 32 位整数(int32,最常用)
    1 → 64 位浮点(float64)
    2 → 32 位浮点(float32)
`##$BYTORDA`:0 = 小端,1 = 大端。

单独成模块(不依赖项目其它模块),供读取器、后端与 VM 工具共用同一份定义;
未知 DTYPE 不猜,直接报错由调用方决定怎么处理(用户 2026-09-11:
「ser 文件似乎是动态字节输出,所以不一定是 int32」)。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np

BRUKER_DTYPE_CODES: dict[int, str] = {0: "i4", 1: "f8", 2: "f4"}


class UnknownBrukerDtype(ValueError):
    """DTYPE 不在 TopSpin 已知取值(0/1/2)内,不能猜。"""


def _int_param(acqus: Mapping[str, Any] | None, key: str, default: int) -> int:
    try:
        return int((acqus or {}).get(key, default) or default)
    except (TypeError, ValueError):
        return default


def sample_dtype(acqus: Mapping[str, Any] | None) -> np.dtype:
    """ser/fid 的采样元素类型(int32/float64/float32 + 大小端)。

    DTYPE 缺省按 0(int32);未知值抛 `UnknownBrukerDtype`(不静默按 int32 处理)。
    """
    code = _int_param(acqus, "DTYPE", 0)
    if code not in BRUKER_DTYPE_CODES:
        raise UnknownBrukerDtype(
            f"未知的 Bruker DTYPE={code}(仅支持 0=int32 / 1=float64 / 2=float32)"
        )
    order = "<" if _int_param(acqus, "BYTORDA", 0) == 0 else ">"
    return np.dtype(order + BRUKER_DTYPE_CODES[code])


def sample_itemsize(acqus: Mapping[str, Any] | None) -> int:
    """单个采样值(实或虚分量)的字节数。"""
    return int(sample_dtype(acqus).itemsize)


def point_bytes(acqus: Mapping[str, Any] | None) -> int:
    """一个复数点的字节数(实虚交错 = 2 × 采样字节数)。"""
    return 2 * sample_itemsize(acqus)
