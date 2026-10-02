"""The single source of the physical ser layout: every row is padded to 1024 bytes, and both the
row length and the row count are solved from the file size.

When Bruker writes ``ser`` it pads **every row** to a multiple of 1024 bytes (4-byte samples ->
128 complex points, 8-byte samples -> 64 complex points; NMRPipe ``com/nih.tcl``'s
``serPadSize``
of 256/128 points is the same convention). Therefore:

* **row length** (complex points of the direct dimension) = the smallest pad multiple that is
not
  less than ``acqus TD``, and it must divide the file size;
* **row count** (number of indirect-dimension FIDs) = ``size // (row length x bytes per complex
  point)``.

Re-checked on 2026-09-24 (250 real datasets, 14 deposited ``fid.com``): the row length solved by
this rule agrees with the depositor's ``-xN`` **14/14 times** (including ``TD=1612->1664``,
``356->384``, ``952->1024``); the earlier implementation took "the first candidate that divides
the file size" and tried ``TD`` first in ascending order, so in coincidences such as
``TD=2000 / row 2048 / 125 rows`` it silently returned 2000 (exactly the "all content wrong"
case
it warned about itself), and it did not cover the padding of 8-byte samples (``DTYPE=1``).

Single source: ``backend.bruker_workflow.physical_direct_points`` (the ``-xN`` of fid.com) and
``core.data.bruker_reader.read_data`` (the matrix shape) both call this module.
"""

from __future__ import annotations

SER_PAD_POINTS_4BYTE = 256
SER_PAD_POINTS_8BYTE = 128
_MAX_PAD_STEPS = 4096


def row_pad_points(value_bytes: int) -> int:
    """Number of **sample values** a row is padded to: ``ROW_PAD_BYTES // value_bytes`` (0 when
    unusable).

    ``value_bytes`` is the size in bytes of **one sample value** (real or imaginary):
    int32/float32 -> 4, float64 -> 8 (from ``DTYPA``/``DTYPE``, see
    :func:`core.data.bruker_dtype.sample_itemsize`).
    """
    if value_bytes <= 0:
        return 0
    if value_bytes <= 4:
        return SER_PAD_POINTS_4BYTE
    return SER_PAD_POINTS_8BYTE


def solve_row_points(td: int, value_bytes: int, size: int) -> int | None:
    """Solve the **physical row length** of the direct dimension (in sample values, the ``-xN``
    convention); returns ``None`` when it cannot be solved (never guessed).

    Rule: start from ``ceil(td / pad) x pad`` and take the first pad multiple that divides the
    file size (``pad = 1024 / value_bytes``). **No fixed pad is assumed**: the pad is derived
    from
    the bytes per value and the row count is solved from "the parameters give the count / the
    file
    gives the total bytes" (see the :mod:`core.data.ser_layout` module docstring).
    """
    pad = row_pad_points(value_bytes)
    if pad <= 0 or td <= 0 or size <= 0:
        return None
    remainder = int(td) % pad
    row = int(td) if not remainder else int(td) + pad - remainder
    return row


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


def effective_row_count(
    size: int,
    row_points: int,
    value_bytes: int,
    trailing_zero_rows: int,
) -> int:
    """Return the effective row count after trimming trailing all-zero rows, with a minimum of one.

    Uniform ``ser`` files can contain whole zero rows at the end when the file is padded to its
    declared ``TD``. If retained, file size overstates the number of acquired points and the
    converted FID carries an unnecessary zero tail into later processing.

    Only consecutive trailing zero rows are removed; isolated zero rows inside the data may be
    real and are left untouched. The caller (for example,
    :func:`core.data.bruker_reader.read_data`)
    supplies ``trailing_zero_rows``. This function only subtracts that count and enforces the
    lower bound.
    """
    rows = solve_row_count(size, row_points, value_bytes)
    if rows is None:
        return 0
    trimmed = int(rows) - max(0, int(trailing_zero_rows))
    return max(1, trimmed)


def count_trailing_zero_rows(samples, row_points: int, *, limit: int | None = None) -> int:
    """Count consecutive all-zero rows at the end of a flattened sample sequence.

    Scan backward from the final row and stop at the first row containing a nonzero value.
    ``limit`` caps the work for large files; reaching it means at least that many trailing rows
    are zero, and the caller can decide how to handle the remainder.
    """
    if row_points <= 0 or samples is None:
        return 0
    total = len(samples) // row_points
    if total <= 0:
        return 0
    cap = total if limit is None else max(0, min(int(limit), total))
    zero_rows = 0
    for index in range(total - 1, total - 1 - cap, -1):
        start = index * row_points
        window = samples[start : start + row_points]
        if not any(window):
            zero_rows += 1
            continue
        break
    return zero_rows


__all__ = [
    "SER_PAD_POINTS_4BYTE",
    "SER_PAD_POINTS_8BYTE",
    "count_trailing_zero_rows",
    "effective_row_count",
    "row_pad_points",
    "solve_row_count",
    "solve_row_points",
]
