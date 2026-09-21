"""NMRPipe spectrum file reading.

Complex data in NMRPipe 2D/3D spectrum files (.ft1/.ft2/.ft3) is stored with the real and
imaginary parts of the first axis interleaved, which makes nmrglue read it as a real array
with twice the length along the first axis; this module unpacks it back into complex.
(Verified: a nus3d_rc plane went from a (268,112) real array to a (134,112) complex one, and
the FT of the unpacked data shows clean peaks.)
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ui_support.i18n import tr


def _unpack_interleaved(arr: np.ndarray) -> np.ndarray:
    """Unpack the real/imaginary interleaved first axis into complex (even rows are the real
    part, odd rows the imaginary part)."""
    if arr.shape[0] % 2 != 0:
        raise ValueError(
            tr(
            "the first axis length is odd; the data is not interleaved complex "
            "storage",
        )
        )
    return arr[0::2] + 1j * arr[1::2]


def read_pipe_complex(path: Path | str) -> np.ndarray:
    """Read an NMRPipe spectrum file and unpack it into a complex array."""
    import nmrglue as ng

    _dic, arr = ng.pipe.read(str(path))
    return _unpack_interleaved(np.asarray(arr))
