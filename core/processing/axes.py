"""Logical axis (F1/F2/F3) -> numpy axis index.

Two conventions:
- axis_index: the internal in-memory convention -- 2D (F1, F2), 3D (F1, F2, F3);
- file_axis_index: the layout of NMRPipe single-file/slice output (0.2.199-patch29,
  verified with a sampleB ft3 and hand-made sampleJ slices) -- 2D (F1, F2), 3D (F2, F1, F3).

For 3D the two conventions disagree on F1/F2: the production layout puts F2 (the fastest
indirect dimension) on the first axis. Reading final spectra, complex previews and other
backend output files therefore requires file_axis_index; the wrong one flips F1 and F2.
"""

from __future__ import annotations

from ui_support.i18n import tr

_INDEX = {"F1": 0, "F2": 1, "F3": 2}


_FILE_INDEX_3D = {"F2": 0, "F1": 1, "F3": 2}
_FILE_INDEX_2D = {"F1": 0, "F2": 1}


def axis_index(axis: str, ndim: int) -> int:
    """Internal in-memory convention: the numpy axis index of a logical axis in ndim data."""
    axis = axis.upper()
    index = _INDEX.get(axis)
    if index is None or index >= ndim:
        raise ValueError(tr("axis {p0} does not exist in {p1} Dimensional data", p0=axis, p1=ndim))
    return index


def file_axis_index(axis: str, ndim: int) -> int:
    """NMRPipe output-file layout (measured): the numpy axis index of a logical axis in the
    spectrum array."""
    axis = axis.upper()
    index = (_FILE_INDEX_3D if ndim >= 3 else _FILE_INDEX_2D).get(axis)
    if index is None or index >= ndim:
        raise ValueError(tr("axis {p0} does not exist in {p1} Dimensional data", p0=axis, p1=ndim))
    return index
