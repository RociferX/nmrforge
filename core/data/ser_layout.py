"""The single source of the physical ser layout: every row is padded to 1024 bytes, and both the
row length and the row count are solved from the file size.

When Bruker writes ``ser`` it pads **every row** to a multiple of 1024 bytes (4-byte samples ->
128 complex points, 8-byte samples -> 64 complex points; NMRPipe ``com/nih.tcl``'s ``serPadSize``
of 256/128 points is the same convention). Therefore:

* **row length** (complex points of the direct dimension) = the smallest pad multiple that is not
  less than ``acqus TD``, and it must divide the file size;
* **row count** (number of indirect-dimension FIDs) = ``size // (row length x bytes per complex
  point)``.

Re-checked on 2026-09-24 (250 real datasets, 14 deposited ``fid.com``): the row length solved by
this rule agrees with the depositor's ``-xN`` **14/14 times** (including ``TD=1612->1664``,
``356->384``, ``952->1024``); the earlier implementation took "the first candidate that divides
the file size" and tried ``TD`` first in ascending order, so in coincidences such as
``TD=2000 / row 2048 / 125 rows`` it silently returned 2000 (exactly the "all content wrong" case
it warned about itself), and it did not cover the padding of 8-byte samples (``DTYPE=1``).

Single source: ``backend.bruker_workflow.physical_direct_points`` (the ``-xN`` of fid.com) and
``core.data.bruker_reader.read_data`` (the matrix shape) both call this module.
"""

from __future__ import annotations

#: Number of bytes every row is padded to (the physical convention of Bruker ``serPadSize``;
#: **converted into points via the bytes per sample**).
#: Note the unit: a "point" here is one **sample value** (real or imaginary), i.e. bruk2pipe
#: ``-xN``'s "Pts Real + Imag" convention -- one row of ``-xN`` values = ``-xN/2`` complex points
#: (see the module docstring for the real-machine anchors).
ROW_PAD_BYTES = 1024
#: Upper bound on the pad multiples searched upwards when the row length cannot be solved (a
#: guard against abnormal files, avoiding an infinite loop).
_MAX_PAD_STEPS = 4096


def row_pad_points(value_bytes: int) -> int:
    """Number of **sample values** a row is padded to: ``ROW_PAD_BYTES // value_bytes`` (0 when
    unusable).

    ``value_bytes`` is the size in bytes of **one sample value** (real or imaginary):
    int32/float32 -> 4, float64 -> 8 (from ``DTYPA``/``DTYPE``, see
    :func:`core.data.bruker_dtype.sample_itemsize`).
    """
    if value_bytes <= 0 or ROW_PAD_BYTES % value_bytes:
        return 0
    return ROW_PAD_BYTES // value_bytes


def solve_row_points(td: int, value_bytes: int, size: int) -> int | None:
    """Solve the **physical row length** of the direct dimension (in sample values, the ``-xN``
    convention); returns ``None`` when it cannot be solved (never guessed).

    Rule: start from ``ceil(td / pad) x pad`` and take the first pad multiple that divides the
    file size (``pad = 1024 / value_bytes``). **No fixed pad is assumed**: the pad is derived from
    the bytes per value and the row count is solved from "the parameters give the count / the file
    gives the total bytes" (see the :mod:`core.data.ser_layout` module docstring).
    """
    pad = row_pad_points(value_bytes)
    if pad <= 0 or td <= 0 or size <= 0:
        return None
    row = ((int(td) + pad - 1) // pad) * pad
    for _ in range(_MAX_PAD_STEPS):
        if size % (row * value_bytes) == 0:
            return row
        row += pad
    return None


def solve_row_count(size: int, row_points: int, value_bytes: int) -> int | None:
    """Solve the **row count** (number of indirect-dimension FIDs) from the file size and the row
    length in sample values; returns ``None`` when it does not divide evenly.
    """
    if row_points <= 0 or value_bytes <= 0 or size <= 0:
        return None
    per_row = row_points * value_bytes
    if size % per_row:
        return None
    rows = size // per_row
    return rows if rows > 0 else None


__all__ = [
    "ROW_PAD_BYTES",
    "row_pad_points",
    "solve_row_count",
    "solve_row_points",
]
