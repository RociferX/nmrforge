"""NUS sampling schedule reading (nuslist / ser judged together)."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np

from core.data.bruker_dtype import UnknownBrukerDtype, sample_dtype
from ui_support.i18n import tr


def read_nuslist(path: Path) -> list[tuple[int, ...]]:
    """Read the sampling points of an nuslist (several integer indices per line; comments and
    blank lines are skipped).

    Parameters
    ----------
    path : Path
        Path of the ``nuslist`` file.

    Returns
    -------
    list[tuple[int, ...]]
        One sampling coordinate per line (one or more integers; blank lines skipped).

    Raises
    ------
    - never raises: a missing or unreadable file yields an empty list (the caller then takes
      the safe branch).

    Side effects
    ------------
    Reads a file only.

    Examples
    --------
        points = read_nuslist(raw_dir / "nuslist")
    """
    points: list[tuple[int, ...]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            points.append(tuple(int(tok) for tok in line.split()))
        except ValueError:
            continue
    return points


#: FnMODE -> number of hypercomplex components (2 for States/TPPI/States-TPPI/Echo-Antiecho,
#: 1 for QF)
_SUPERCOMPLEX_FNMODES = (0, 1, 2, 4, 5, 6)


def _int_or_none(value: object) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def indirect_grid_2d(experiment: Any) -> tuple[int, int, int]:
    """2D -> (indirect complex grid, hypercomplex components, direct points); 0 when not 2D.

    Matches the 2D rule of ``backend.script_generator.effective_td`` (indirect complex points
    = TD // hypercomplex components) but **does not depend on sampling.mode** -- after a
    full-sampling downgrade the mode is already uniform and the decision still has to use the
    complex grid (user 2026-09-14: full sampling goes the uniform route).

    Parameters
    ----------
    experiment : Any
        An already loaded 2D dataset (reads TD and FnMODE from ``acqus``/``acqu2s``).

    Returns
    -------
    tuple[int, int, int]
        ``(grid, mult, direct_points)``: number of complex points in the indirect grid, rows
        per complex point (1 or 2) and the number of direct points.

    Raises
    ------
    - never raises: missing parameters fall back to the known defaults and the caller judges
      together with the evidence.

    Side effects
    ------------
    Pure computation (only reads the experiment object).

    Examples
    --------
        grid, mult, direct = indirect_grid_2d(experiment)
    """
    if int(getattr(experiment, "ndim", 0)) != 2:
        return 0, 0, 0
    dimensions = list(getattr(experiment, "dimensions", []) or [])
    if len(dimensions) < 2:
        return 0, 0, 0
    indirect = next(
        (
            dim
            for dim in dimensions
            if str(getattr(getattr(dim, "role", ""), "value", "")).startswith(
                "indirect"
            )
        ),
        None,
    )
    direct = next(
        (
            dim
            for dim in dimensions
            if str(getattr(getattr(dim, "role", ""), "value", "")).startswith("direct")
        ),
        None,
    )
    if indirect is None or direct is None:
        return 0, 0, 0
    fnmode = _int_or_none(getattr(indirect, "acquisition_mode", ""))
    mult = 2 if fnmode in _SUPERCOMPLEX_FNMODES else 1
    grid = int(getattr(indirect, "td", 0) or 0) // mult
    return max(grid, 0), mult, max(int(getattr(direct, "td", 0) or 0), 0)



def scan_dense_2d(
    raw_dir: Path,
    *,
    acqus: Mapping[str, Any] | None = None,
    grid_complex: int,
    mult: int,
    direct_points: int,
    fid_file: Path | None = None,
) -> dict[str, Any]:
    """Scan the dense 2D "full grid + zero filling" model -> the complex points really sampled
    (shared by the front end and the backend).

    Returns ``{kind, points, rows, declared_rows, source, dtype, detail}``:

    - ``full``    every full-grid row is present and **no row is zero** -> this is really full
      sampling (even when NUS is declared);
    - ``partial`` every full-grid row is present but some are zero -> the sampled subset
      recovered from the zero pattern;
    - ``sparse``  fewer rows than the declared full grid -> a sparse file whose sampling
      positions are unknown;
    - ``mismatch`` the row count differs from the declared full grid -> metadata and file
      disagree;
    - ``all_zero`` / ``unknown_dtype`` / ``bad_layout`` / ``missing``.

    It only reads the data and takes no processing decision: whether to treat the data as
    uniform is decided by ``sampling_detector`` and the backend from this result (user
    2026-09-14: full sampling must take the uniform route).

    Parameters
    ----------
    raw_dir : Path
        The Bruker raw directory holding ``ser``.
    acqus : Mapping[str, Any], optional
        An already parsed ``acqus`` (re-read by default; pass it explicitly to avoid parsing
        twice).
    grid_complex : int
        Number of complex points in the indirect grid (given by :func:`indirect_grid_2d`).
    mult : int
        Rows per complex point (1 or 2).
    direct_points : int
        Number of direct points.
    fid_file : Path, optional
        Name the file to scan explicitly (``ser`` -> ``ser_full`` by default).

    Returns
    -------
    dict[str, Any]
        ``kind`` (``full`` full sampling / ``dense`` dense subset / ``sparse`` / ``mismatch``),
        ``rows``, ``points`` (the non-zero complex coordinates), ``source`` and ``reason``.

    Raises
    ------
    - never raises: a row count or type that does not fit is expressed as ``kind`` plus
      ``reason`` and the caller decides how to fall back.

    Side effects
    ------------
    Read-only: it scans the file header and the zero pattern on demand and never rewrites the
    raw data.

    Examples
    --------
        scan = scan_dense_2d(raw_dir, grid_complex=128, mult=2, direct_points=2048)
        if scan["kind"] == "full":
            ...  # really full sampling
    """
    raw = Path(raw_dir)
    declared_rows = int(mult) * int(grid_complex)
    result: dict[str, Any] = {
        "kind": "missing",
        "points": [],
        "rows": 0,
        "declared_rows": declared_rows,
        "source": "",
        "dtype": "",
        "detail": "",
    }
    if grid_complex <= 0 or mult <= 0 or direct_points <= 0:
        result["kind"] = "bad_layout"
        result["detail"] = (
            tr(
            "the indirect grid / hypercomplex components / direct points are inconsistent; the "
            "dense model cannot be "
            "judged",
        )
        )
        return result
    try:
        dt = sample_dtype(acqus)
    except UnknownBrukerDtype as exc:
        result["kind"] = "unknown_dtype"
        result["detail"] = str(exc)
        return result
    result["dtype"] = str(dt.str[1:])
    x_n = int(direct_points)
    per_row = int(dt.itemsize) * x_n
    src = raw / "ser"
    if src.is_file():
        result["source"] = "ser"
        size = int(src.stat().st_size)
        if per_row <= 0 or size % per_row:
            result["kind"] = "bad_layout"
            result["detail"] = (
                tr(
                    "ser size {p0} is not \"direct dimension {p1} x {p2} bytes\"({p3}); layout "
                    "unclear, so the dense model cannot be "
                    "decided",
                    p0=size,
                    p1=x_n,
                    p2=int(dt.itemsize),
                    p3=dt.str[1:],
                )
            )
            return result
        rows = size // per_row
        result["rows"] = int(rows)
        if rows < declared_rows:
            result["kind"] = "sparse"
            return result
        if rows > declared_rows:
            result["kind"] = "mismatch"
            return result
        table = np.fromfile(src, dtype=dt).reshape(rows, x_n)
    elif fid_file is not None and Path(fid_file).is_file():
        result["source"] = "fid"
        import nmrglue as ng

        _dic, data = ng.pipe.read(str(fid_file))
        table = np.asarray(data)
        if table.ndim < 2:
            result["kind"] = "bad_layout"
            result["detail"] = (
                tr(
                "the converted fid is not two-dimensional; the dense model cannot be "
                "judged",
            )
            )
            return result
        rows = int(table.shape[0])
        result["rows"] = rows
        if rows != declared_rows:
            result["kind"] = "mismatch"
            return result
    else:
        return result

    energies = np.abs(table).sum(axis=1)
    points: list[int] = []
    for k in range(int(grid_complex)):
        lo, hi = int(mult) * k, int(mult) * k + int(mult)
        if lo >= rows:
            break
        if any(float(energies[i]) > 0.0 for i in range(lo, min(hi, rows))):
            points.append(k)
    result["points"] = points
    if not points:
        result["kind"] = "all_zero"
        return result
    result["kind"] = "full" if len(points) == int(grid_complex) else "partial"
    return result
