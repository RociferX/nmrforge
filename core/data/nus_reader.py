"""NUS sampling schedule reading (nuslist / ser judged together)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import numpy as np

from core.data.bruker_dtype import UnknownBrukerDtype, sample_dtype
from core.experiment.acquisition_mode_detector import hypercomplex_mult
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
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return points
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            points.append(tuple(int(tok) for tok in line.split()))
        except ValueError:
            continue
    return points


_SCHEDULE_MAX_BYTES = 64 * 1024

_SCHEDULE_MIN_ROWS = 2

_SCHEDULE_MAX_COLUMNS = 3


def schedule_columns_for_ndim(ndim: int) -> int | None:
    """Return the number of NUS schedule coordinate columns, or ``None`` for unsupported data."""
    value = int(ndim or 0)
    return value - 1 if value in (2, 3) else None


def schedule_grid_shape(experiment: Any, *, has_schedule: bool = False) -> tuple[int, ...]:
    """Return the complex-point grid addressed by an NUS schedule (one axis in 2D, two in 3D).

    Bruker ``TD`` and ``NusTD`` count time-domain values. For complex indirect modes such as
    States, States-TPPI and echo/antiecho, two time-domain components correspond to one schedule
    coordinate. Real modes such as QF, QSEQ and TPPI must not be halved. In 2D, declared
    ``NusTD`` is used when NUS metadata or a confirmed schedule supplies that context; ordinary
    uniform data ignores stale ``NusTD`` values.

    ``has_schedule`` is for callers that have found a valid schedule before it is installed on
    the experiment's sampling record.
    """
    ndim = int(getattr(experiment, "ndim", 0) or 0)
    if ndim == 2:
        grid, _mult, _direct = indirect_grid_2d(experiment, has_schedule=has_schedule)
        return (int(grid),) if grid > 0 else ()
    if ndim != 3:
        return ()
    params = getattr(experiment, "acquisition_parameters", {}) or {}
    dimensions = list(getattr(experiment, "dimensions", []) or [])
    shape: list[int] = []
    for index, name in ((1, "acqu2s"), (2, "acqu3s")):
        block = params.get(name) or {}
        try:
            declared = int(block.get("NusTD", 0) or block.get("TD", 0) or 0)
        except (TypeError, ValueError):
            declared = 0
        if declared <= 0 and len(dimensions) > index:
            declared = int(getattr(dimensions[index], "td", 0) or 0)
        try:
            fnmode = int(block.get("FnMODE", 0) or 0)
        except (TypeError, ValueError):
            fnmode = 0
        mult = hypercomplex_mult(fnmode)
        if declared <= 0 or declared % mult:
            return ()
        shape.append(declared // mult)
    return tuple(shape) if all(value > 0 for value in shape) else ()


def _schedule_shape_error(path: Path, *, expected_columns: int | None) -> str:
    """Return why a schedule has an invalid shape, or an empty string when it is valid.

    Reads only a bounded text file rather than loading an arbitrarily large file into memory.
    """
    try:
        size = path.stat().st_size
    except OSError as exc:
        return str(exc)
    if size <= 0:
        return tr("empty file")
    if size > _SCHEDULE_MAX_BYTES:
        return tr("too large to be a sampling schedule ({p0} bytes)", p0=size)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return str(exc)
    rows = 0
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        tokens = line.split()
        if len(tokens) > _SCHEDULE_MAX_COLUMNS:
            return tr("more than {p0} columns per line", p0=_SCHEDULE_MAX_COLUMNS)
        if expected_columns is not None and len(tokens) != expected_columns:
            return tr(
                "column count {p0} does not match the expected {p1}",
                p0=len(tokens),
                p1=expected_columns,
            )
        try:
            values = [int(tok) for tok in tokens]
        except ValueError:
            return tr("contains a non-integer token")
        if any(value < 0 for value in values):
            return tr("contains a negative index")
        rows += 1
    if rows < _SCHEDULE_MIN_ROWS:
        return tr("only {p0} index line(s)", p0=rows)
    return ""


def find_schedule_file(
    raw_dir: Path,
    *,
    acqus: Mapping[str, Any] | None = None,
    expected_columns: int | None = None,
    exclude: Iterable[Path] = (),
) -> tuple[Path | None, str]:
    """Find an NUS schedule in the raw directory using an explicit source.

    Accept only the standard ``nuslist`` filename or a file explicitly named by
    ``acqus.NUSLIST``. A candidate must also pass the shape check: a small text file containing
    one to three nonnegative integers per data line. Never scan the directory and guess;
    ordinary
    integer lists such as ``vclist`` could otherwise be mistaken for a schedule and cause a
    uniform dataset to be classified as NUS.

    Candidates are tried in priority order: the standard ``nuslist`` name first, then the name
    in ``acqus.NUSLIST``. The latter is accepted only if a file with that exact name exists.
    Failure is not an exception: return ``(None, reason)`` for the caller to handle.

    Parameters
    ----------
    raw_dir : Path
        Raw Bruker directory. Only this directory is checked; subdirectories are not searched.
    acqus : Mapping[str, Any], optional
        Parsed ``acqus`` parameters, used to read the ``NUSLIST`` filename. If omitted, that
        parameter is not consulted.
    expected_columns : int, optional
        Expected number of columns (one for 2D, two for 3D). When provided, only that shape is
        accepted.
    exclude : Iterable[Path], optional
        Paths to skip, such as candidates already known to be invalid.

    Returns
    -------
    tuple[Path | None, str]
        ``(schedule_path, explanation)``. The path is ``None`` when no schedule is accepted;
        the explanation can be written to a log.

    Raises
    ------
    - Does not raise: a missing or unreadable directory returns ``(None, reason)``.

    Side effects
    ------------
    Read-only: reads the directory listing and the first bounded portion of candidate files.

    Examples
    --------
        path, why = find_schedule_file(raw_dir, acqus=acqus, expected_columns=2)
        if path is not None:
            points = read_nuslist(path)
    """
    directory = Path(raw_dir)
    if not directory.is_dir():
        return None, tr("directory does not exist: {p0}", p0=directory)
    skip = {Path(item).resolve() for item in exclude}
    tried: list[str] = []

    def _accept(candidate: Path) -> tuple[Path | None, str]:
        if candidate.resolve() in skip:
            return None, ""
        reason = _schedule_shape_error(candidate, expected_columns=expected_columns)
        if reason:
            tried.append(f"{candidate.name}({reason})")
            return None, ""
        return candidate, ""

    standard = directory / "nuslist"
    if standard.is_file():
        found, _ = _accept(standard)
        if found is not None:
            return found, tr("standard filename nuslist")

    declared = ""
    if acqus:
        declared = str(acqus.get("NUSLIST") or "").strip().strip("<>").strip()
    if declared:
        declared_path = Path(declared)
        if declared_path.is_absolute() or declared_path.name != declared:
            tried.append(tr("the acqus NUSLIST value is not a local filename: {p0}", p0=declared))
        else:
            named = directory / declared_path.name
            if named.is_file():
                found, _ = _accept(named)
                if found is not None:
                    return found, tr("named by the acqus NUSLIST parameter: {p0}", p0=declared)

    detail = (
        "; ".join(tried[:2])
        if tried
        else tr("neither nuslist nor a file named by the acqus NUSLIST parameter exists")
    )
    return None, tr("no usable sampling schedule found ({p0})", p0=detail)


def _int_or_none(value: object) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def indirect_grid_2d(
    experiment: Any, *, has_schedule: bool = False,
) -> tuple[int, int, int]:
    """2D -> (indirect complex grid, hypercomplex components, direct points); 0 when not 2D.

    For NUS, prefer the declared ``acqu2s.NusTD`` grid and convert it using ``FnMODE``; ``TD``
    may describe only a compact acquisition. Traditional data ignores stale ``NusTD``. NUS
    semantics also survive a full-schedule downgrade to uniform or pre-detection schedule lookup.

    Parameters
    ----------
    experiment : Any
        An already loaded 2D dataset (reads TD and FnMODE from ``acqus``/``acqu2s``).
    has_schedule : bool
        The caller has confirmed a valid schedule, even if sampling has not yet been assigned.

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
            if str(getattr(getattr(dim, "role", ""), "value", "")).startswith("indirect")
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
    params = getattr(experiment, "acquisition_parameters", {}) or {}
    block = params.get("acqu2s") or {}
    sampling = getattr(experiment, "sampling", None)
    mode = getattr(sampling, "mode", "")
    is_nus = (
        has_schedule
        or str(getattr(mode, "value", mode)) == "nus"
        or _int_or_none((params.get("acqus") or {}).get("FnTYPE")) == 2
        or bool(getattr(sampling, "nus_list", None))
    )
    declared = _int_or_none(block.get("NusTD")) if is_nus else None
    if declared is None or declared <= 0:
        declared = int(getattr(indirect, "td", 0) or 0)
    fnmode = _int_or_none(block.get("FnMODE"))
    if fnmode is None:
        fnmode = _int_or_none(getattr(indirect, "acquisition_mode", ""))
    mult = hypercomplex_mult(fnmode if fnmode is not None else 0)
    grid = declared // mult if declared > 0 and declared % mult == 0 else 0
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
        "padding_rows": 0,
    }
    if grid_complex <= 0 or mult <= 0 or direct_points <= 0:
        result["kind"] = "bad_layout"
        result["detail"] = tr(
            "the indirect grid / hypercomplex components / direct points are inconsistent; the "
            "dense model cannot be "
            "judged",
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
        from core.data.ser_layout import solve_row_points

        use_physical_padding = any(
            key in (acqus or {}) for key in ("DTYPE", "DTYPA", "PARMODE", "BYTORDA")
        )
        padded_x_n = solve_row_points(x_n, int(dt.itemsize), size) if use_physical_padding else None
        if padded_x_n and size >= padded_x_n * int(dt.itemsize):
            padded_per_row = int(dt.itemsize) * int(padded_x_n)
            padded_rows = size // padded_per_row
            logical_rows = size // per_row if per_row and size % per_row == 0 else 0
            if padded_rows >= declared_rows or logical_rows < declared_rows:
                x_n = int(padded_x_n)
                per_row = padded_per_row
        if per_row <= 0 or size % per_row:
            result["kind"] = "bad_layout"
            result["detail"] = tr(
                'ser size {p0} is not "direct dimension {p1} x {p2} bytes"({p3}); layout '
                "unclear, so the dense model cannot be "
                "decided",
                p0=size,
                p1=x_n,
                p2=int(dt.itemsize),
                p3=dt.str[1:],
            )
            return result
        rows = size // per_row
        result["rows"] = int(rows)
        if rows < declared_rows:
            result["kind"] = "sparse"
            return result
        table = np.fromfile(src, dtype=dt).reshape(rows, x_n)
    elif fid_file is not None and Path(fid_file).is_file():
        result["source"] = "fid"
        import nmrglue as ng

        _dic, data = ng.pipe.read(str(fid_file))
        table = np.asarray(data)
        if table.ndim < 2:
            result["kind"] = "bad_layout"
            result["detail"] = tr(
                "the converted fid is not two-dimensional; the dense model cannot be judged",
            )
            return result
        rows = int(table.shape[0])
        result["rows"] = rows
        if rows < declared_rows:
            result["kind"] = "mismatch"
            return result
    else:
        return result

    if rows > declared_rows:
        # Bruker may pad the *indirect row count* to a block boundary as well as
        # padding inside each direct trace.  Those trailing all-zero rows are not
        # missing increments.  data/5 on the validation VM is a concrete example:
        # 850 declared/meaningful rows followed by 174 block-padding rows.
        tail = np.asarray(table[declared_rows:])
        if tail.size and bool(np.any(np.abs(tail) > 0.0)):
            result["kind"] = "mismatch"
            return result
        result["padding_rows"] = int(rows - declared_rows)
        table = np.asarray(table[:declared_rows])
        rows = declared_rows

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


def scan_whole_trace_zeros(
    raw_dir: Path,
    *,
    acqus: Mapping[str, Any] | None = None,
    direct_points: int,
    fid_file: Path | None = None,
) -> dict[str, Any]:
    """Use a whole-trace zero test to identify unmeasured increments in 2D or 3D data.

    Only fully zero traces count as unmeasured; the exact amount of end-of-row padding is not
    needed. Bruker may pad the tail of each direct-dimension FID for block alignment, and those
    zeros do not mean that an increment was not acquired. This function therefore:

    - Splits ``ser`` into rows using direct-dimension real points times the value size and
    floors
      the row count; trailing alignment bytes are discarded without an error.
    - Computes ``sum(abs(value))`` for each row; a zero sum marks a whole-trace zero.
    - Counts zero rows and reports contiguous blocks of nonzero rows, which can indicate compact
      sampling.

    It does not perform geometry checks, infer the active direct-dimension region, or compare
    the
    fraction of nonzero values within a row. Those checks require exact padding boundaries and
    are intentionally outside this policy.

    Parameters
    ----------
    raw_dir : Path
        Raw directory containing ``ser``. If ``ser`` is absent, ``fid_file`` may be used.
    acqus : Mapping[str, Any], optional
        Parsed ``acqus`` parameters used to select the dtype; defaults to a float64 probe.
    direct_points : int
        Direct-dimension real-point count (direct ``TD`` in 2D or F3 ``TD`` in 3D).
    fid_file : Path, optional
        Converted FID to scan when ``ser`` is absent.

    Returns
    -------
    dict[str, Any]
        Result fields include ``source``, ``rows``, ``zero_rows``, ``nonzero_rows``,
        ``remainder_bytes``, contiguous nonzero ``blocks`` as ``[(start, end), ...]``,
        ``kind`` (``whole_trace_scanned``, ``missing`` or ``bad_layout``), and ``detail``.

    Raises
    ------
    - Does not raise: missing files or invalid parameters are represented by ``kind`` and
      ``detail``.

    Side effects
    ------------
    Read-only: scans the zero pattern in ``ser`` or ``fid`` and changes no files.

    Examples
    --------
        scan = scan_whole_trace_zeros(raw_dir, acqus=acqus, direct_points=356)
        if scan["kind"] == "whole_trace_scanned" and scan["zero_rows"]:
            ...  # A whole zero trace means the grid is not fully sampled.
    """
    result: dict[str, Any] = {
        "kind": "missing",
        "source": "",
        "rows": 0,
        "zero_rows": 0,
        "nonzero_rows": 0,
        "remainder_bytes": 0,
        "blocks": [],
        "detail": "",
    }
    x_n = int(direct_points)
    if x_n <= 0:
        result["kind"] = "bad_layout"
        result["detail"] = tr(
            "the direct-dimension point count is illegal ({p0}); the zero pattern cannot be "
            "scanned",
            p0=direct_points,
        )
        return result
    try:
        dt = sample_dtype(acqus or {})
    except UnknownBrukerDtype as exc:
        result["kind"] = "bad_layout"
        result["detail"] = str(exc)
        return result
    per_row = int(dt.itemsize) * x_n
    if per_row <= 0:
        result["kind"] = "bad_layout"
        result["detail"] = tr("the row byte length is illegal; the zero pattern cannot be scanned")
        return result

    raw = Path(raw_dir)
    src = raw / "ser"
    if src.is_file():
        result["source"] = "ser"
        size = int(src.stat().st_size)
        from core.data.ser_layout import solve_row_points

        use_physical_padding = any(
            key in (acqus or {}) for key in ("DTYPE", "DTYPA", "PARMODE", "BYTORDA")
        )
        padded_x_n = solve_row_points(x_n, int(dt.itemsize), size) if use_physical_padding else None
        if padded_x_n and size >= padded_x_n * int(dt.itemsize):
            x_n = int(padded_x_n)
            per_row = int(dt.itemsize) * x_n
        rows = size // per_row
        result["remainder_bytes"] = int(size % per_row)
        if rows <= 0:
            result["kind"] = "bad_layout"
            result["detail"] = tr(
                "ser size {p0} is smaller than one row ({p1} bytes); the zero pattern cannot be "
                "scanned",
                p0=size,
                p1=per_row,
            )
            return result
        table = np.fromfile(src, dtype=dt, count=rows * x_n).reshape(rows, x_n)
    elif fid_file is not None and Path(fid_file).is_file():
        result["source"] = "fid"
        import nmrglue as ng

        _dic, data = ng.pipe.read(str(fid_file))
        table = np.asarray(data)
        if table.ndim != 2:
            result["kind"] = "bad_layout"
            result["detail"] = tr(
                "the converted fid is not two-dimensional; the zero pattern cannot be scanned",
            )
            return result
        rows = int(table.shape[0])
        if int(table.shape[1]) != x_n:
            result["kind"] = "bad_layout"
            result["detail"] = tr(
                "the converted fid has {p0} points per row but {p1} were expected",
                p0=int(table.shape[1]),
                p1=x_n,
            )
            return result
    else:
        return result

    energies = np.abs(table).sum(axis=1)
    zero_mask = energies <= 0.0
    result["rows"] = int(rows)
    result["zero_rows"] = int(zero_mask.sum())
    result["nonzero_rows"] = int(rows - result["zero_rows"])
    blocks: list[tuple[int, int]] = []
    for index in np.flatnonzero(~zero_mask):
        position = int(index)
        if blocks and position == blocks[-1][1] + 1:
            blocks[-1] = (blocks[-1][0], position)
        else:
            blocks.append((position, position))
    result["blocks"] = blocks
    result["kind"] = "whole_trace_scanned"
    return result


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
_AUTO_PROBE_FILES = ("acqus", "acqu2s", "acqu3s", "acqu", "acqu2", "acqu3", "ser")

#: resolve_tool("bruker", settings)`` → ``probe_auto_sampling(..., bruker_cmd=...)``。
_BRUKER_CANDIDATES = ("bruker",)

_configured_bruker: str = ""


def set_bruker_executable(path: str) -> None:
    """Register the local ``bruker`` executable path after the backend probes it.

    Parameters
    ----------
    path : str
        Absolute executable path; an empty string clears the registration.

    Side effects
    ------------
    Updates only this module's in-process state and writes no files.
    """
    global _configured_bruker
    _configured_bruker = str(path or "")


def configured_bruker_executable() -> str:
    """Return the registered ``bruker`` path, or an empty string if none is registered."""
    return _configured_bruker


def _find_bruker(explicit: str = "") -> str | None:
    """Find ``bruker`` in priority order: explicit path, registered path, csh, then ``PATH``.

    NMRPipe's ``com/`` directory is often absent from ``PATH`` and is exposed only after the
    login shell loads the NMRPipe setup. Do not guess machine-specific paths; return ``None`` if
    the executable cannot be found.

    This lookup protects NUS classification. If ``detect()`` cannot obtain AUTO geometry, it
    may fall back to ``acqus.NusAMOUNT``. That parameter was 100 for data with a measured 25%
    sampling rate, causing sparse datasets to be treated as fully sampled.
    """
    import os
    import shutil
    import subprocess

    candidates: list[str] = []
    for value in (explicit, _configured_bruker):
        if value and value.strip():
            candidates.append(value.strip())
    shell = shutil.which("tcsh") or shutil.which("csh")
    if shell is not None and not candidates:
        try:
            proc = subprocess.run(
                [shell, "-c", "if (-e ~/.cshrc) source ~/.cshrc; which bruker"],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=15,
                check=False,
                stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.TimeoutExpired):
            proc = None
        if proc is not None:
            for line in (proc.stdout or "").splitlines():
                line = line.strip()
                if "/" in line and "not found" not in line.lower():
                    candidates.append(line)
                    break
    candidates.append("bruker")
    for candidate in candidates:
        if os.sep in candidate:
            if Path(candidate).is_file():
                return candidate
        else:
            found = shutil.which(candidate)
            if found:
                return found
    return None


def parse_auto_sampling(text: str) -> dict[str, Any]:
    """Read indirect-dimension geometry from the ``fid.com`` text made by ``bruker -AUTO``.

    ``-yN``/``-zN`` are NMRPipe axis sizes and ``-yT``/``-zT`` are effective time-domain
    points. For a complex indirect dimension, ``T=N/2`` is normal hypercomplex encoding, not
    50% NUS. In 3D, multiplying two complex indirect dimensions can produce ``T/N=0.25``.
    Preserve those raw values and the geometric ratio, but callers must not treat ``fraction``
    as the NUS sampling rate. Compute that rate from valid schedule coordinates divided by the
    complex-point grid.

    Parameters
    ----------
    text : str
        Contents of ``fid.com``.

    Returns
    -------
    dict
        ``grid`` (product of N values), ``sampled`` (product of T values), and ``fraction``
        (the geometric T/N ratio). Unavailable values are ``0`` or ``0.0``.

    Side effects
    ------------
    None; this is a pure parser.
    """
    import re

    def flag(name: str) -> int:
        match = re.search(rf"-{name}\s+(\d+)", text)
        return int(match.group(1)) if match else 0

    y_n, z_n = flag("yN"), flag("zN")
    y_t, z_t = flag("yT"), flag("zT")
    grid = y_n * z_n if y_n and z_n else y_n
    if y_t and z_t:
        sampled = y_t * z_t
    else:
        sampled = y_t or z_t
    fraction = 0.0
    if grid > 0 and 0 < sampled <= grid:
        fraction = sampled / grid
    return {
        "grid": int(grid),
        "sampled": int(sampled),
        "fraction": float(fraction),
        "yN": y_n,
        "zN": z_n,
        "yT": y_t,
        "zT": z_t,
    }


def probe_auto_sampling(
    raw_dir: Path,
    *,
    bruker_bin: str | None = None,
    timeout: float = 120.0,
) -> dict[str, Any]:
    """Run ``bruker -AUTO`` in a temporary directory and remove it immediately afterward.

    ``bruker -AUTO`` writes ``fid.com`` to its current working directory. Running it directly
    in the raw-data directory would modify user data, so this function links the parameters and
    ``ser`` into a temporary directory, runs the command there, reads ``fid.com``, then removes
    the entire temporary directory. The raw directory remains read-only.

    Reading only ``acqus`` is insufficient: ``NusTD == TD`` describes the declared grid, while
    the actual sampled-point counts are reported by ``bruker -AUTO`` as ``-yT``/``-zT``.

    Parameters
    ----------
    raw_dir : Path
        Read-only Bruker dataset directory.
    bruker_bin : str | None
        Explicit ``bruker`` executable path. Callers should pass the resolved path from
        ``backend.environment_probe.resolve_tool("bruker", settings)`` (normally registered in
        ``bruker_path`` at first launch). When ``None``, search only ``PATH``; do not guess a
        machine-specific path.
    timeout : float
        Subprocess timeout in seconds.

    Returns
    -------
    dict
        Result from :func:`parse_auto_sampling`, with ``ok`` for success and ``detail`` for the
        failure reason or executable used.

    Raises
    ------
    - Does not raise: an unavailable executable, timeout, or nonzero exit returns ``ok=False``
      with a reason so the caller can use its existing fallback criterion.

    Side effects
    ------------
    Creates a temporary directory with ``tempfile.mkdtemp`` and removes it before returning;
    ``raw_dir`` is read-only.
    """
    import os
    import shutil
    import subprocess
    import tempfile

    directory = Path(raw_dir)
    result: dict[str, Any] = {
        "ok": False,
        "grid": 0,
        "sampled": 0,
        "fraction": 0.0,
        "detail": "",
    }
    executable = _find_bruker(bruker_bin or "")
    if executable is None:
        result["detail"] = tr("bruker -AUTO is not available")
        return result

    temp_dir: str | None = None
    try:
        temp_dir = tempfile.mkdtemp(prefix="nmrforge_auto_")
        linked = 0
        for name in _AUTO_PROBE_FILES:
            source = directory / name
            if source.exists():
                try:
                    os.symlink(source, Path(temp_dir) / name)
                    linked += 1
                except OSError:
                    shutil.copy2(source, Path(temp_dir) / name)
                    linked += 1
        if not linked:
            result["detail"] = tr("the dataset has no files that bruker -AUTO can read")
            return result
        try:
            completed = subprocess.run(
                [executable, "-AUTO"],
                cwd=temp_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            result["detail"] = tr("bruker -AUTO failed ({p0})", p0=type(exc).__name__)
            return result
        generated = Path(temp_dir) / "fid.com"
        if completed.returncode != 0 or not generated.is_file():
            result["detail"] = tr(
                "bruker -AUTO returned rc={p0} and wrote no fid.com",
                p0=completed.returncode,
            )
            return result
        parsed = parse_auto_sampling(generated.read_text(encoding="utf-8", errors="replace"))
        result.update(parsed)
        result["ok"] = parsed["grid"] > 0
        result["detail"] = executable
        return result
    finally:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
