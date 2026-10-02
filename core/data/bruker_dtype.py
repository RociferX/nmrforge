"""Element type of Bruker raw data (ser/fid): DTYPA / DTYPE + BYTORDA.

**Real-instrument facts (re-checked 2026-09-24, 292 datasets)**: 287 of them only carry
``##$DTYPA`` and just 5 carry ``##$DTYPE`` -- the word size has to be decided by DTYPA
(same convention as NMRPipe ``com/nih.tcl``: ``DTYPA==2 -> serWordSize 8``), otherwise
data sampled with 8 bytes would be read as int32 (sizes and row lengths all wrong).

Field conventions:
    ``##$DTYPA``: 0/1 -> 4 bytes per sample (int32); 2 -> 8 bytes per sample (float64).
    ``##$DTYPE`` (authoritative when present): 0 -> int32; 1 -> float64; 2 -> float32.
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

#: ``##$DTYPA`` -> numpy dtype code (NMRPipe ``com/nih.tcl`` serWordSize convention:
#: 2 -> 8 bytes).
BRUKER_DTYPA_CODES: dict[int, str] = {0: "i4", 1: "i4", 2: "f8"}


class UnknownBrukerDtype(ValueError):
    """DTYPE is outside the TopSpin values we know (0/1/2) and must not be guessed."""


def _int_param(acqus: Mapping[str, Any] | None, key: str, default: int) -> int:
    try:
        return int((acqus or {}).get(key, default) or default)
    except (TypeError, ValueError):
        return default


def sample_dtype(acqus: Mapping[str, Any] | None) -> np.dtype:
    """Sample element type of ser/fid (int32/float64/float32 plus endianness).

    DTYPE is authoritative when present, otherwise DTYPA decides (most real datasets only
    carry DTYPA) and when neither is present int32 is assumed. An unknown value raises
    `UnknownBrukerDtype` (it is never silently treated as int32).
    """
    params = acqus or {}
    if params.get("DTYPE") not in (None, ""):
        code = _int_param(params, "DTYPE", 0)
        if code not in BRUKER_DTYPE_CODES:
            raise UnknownBrukerDtype(
                tr(
                    "Unknown Bruker DTYPE={p0}(Only supports 0=int32 / 1=float64 / 2=float32)",
                    p0=code,
                )
            )
        kind = BRUKER_DTYPE_CODES[code]
    elif params.get("DTYPA") not in (None, ""):
        code = _int_param(params, "DTYPA", 0)
        if code not in BRUKER_DTYPA_CODES:
            raise UnknownBrukerDtype(tr("Unknown Bruker DTYPA={p0}(0/1=int32, 2=float64)", p0=code))
        kind = BRUKER_DTYPA_CODES[code]
    else:
        kind = BRUKER_DTYPE_CODES[0]
    order = "<" if _int_param(params, "BYTORDA", 0) == 0 else ">"
    return np.dtype(order + kind)


def sample_itemsize(acqus: Mapping[str, Any] | None) -> int:
    """Number of bytes of one sample value (the real or the imaginary component)."""
    return int(sample_dtype(acqus).itemsize)


def point_bytes(acqus: Mapping[str, Any] | None) -> int:
    """Number of bytes of one complex point (interleaved real/imaginary = 2 x sample bytes)."""
    return 2 * sample_itemsize(acqus)
