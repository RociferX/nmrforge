"""Element type of Bruker raw data (ser/fid): DTYPE + BYTORDA.

TopSpin convention (``##$DTYPE``):
    0 -> 32-bit integer (int32, by far the most common)
    1 -> 64-bit float (float64)
    2 -> 32-bit float (float32)
``##$BYTORDA``: 0 = little endian, 1 = big endian.

A module of its own (depending on nothing else in the project) so that the readers, the
backend and the VM tools share one definition; an unknown DTYPE is never guessed but
reported, leaving the handling to the caller (user 2026-09-11: "the ser file looks like
dynamic byte output, so it is not necessarily int32").
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np

from ui_support.i18n import tr

BRUKER_DTYPE_CODES: dict[int, str] = {0: "i4", 1: "f8", 2: "f4"}


class UnknownBrukerDtype(ValueError):
    """DTYPE is outside the TopSpin values we know (0/1/2) and must not be guessed."""


def _int_param(acqus: Mapping[str, Any] | None, key: str, default: int) -> int:
    try:
        return int((acqus or {}).get(key, default) or default)
    except (TypeError, ValueError):
        return default


def sample_dtype(acqus: Mapping[str, Any] | None) -> np.dtype:
    """Sample element type of ser/fid (int32/float64/float32 plus endianness).

    DTYPE defaults to 0 (int32); an unknown value raises `UnknownBrukerDtype` (it is never
    silently treated as int32).
    """
    code = _int_param(acqus, "DTYPE", 0)
    if code not in BRUKER_DTYPE_CODES:
        raise UnknownBrukerDtype(
            tr("Unknown Bruker DTYPE={p0}(Only supports 0=int32 / 1=float64 / 2=float32)", p0=code)
        )
    order = "<" if _int_param(acqus, "BYTORDA", 0) == 0 else ">"
    return np.dtype(order + BRUKER_DTYPE_CODES[code])


def sample_itemsize(acqus: Mapping[str, Any] | None) -> int:
    """Number of bytes of one sample value (the real or the imaginary component)."""
    return int(sample_dtype(acqus).itemsize)


def point_bytes(acqus: Mapping[str, Any] | None) -> int:
    """Number of bytes of one complex point (interleaved real/imaginary = 2 x sample bytes)."""
    return 2 * sample_itemsize(acqus)
