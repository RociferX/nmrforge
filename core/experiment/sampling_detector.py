"""Sampling-mode detection (uniform / NUS / uncertain).

Combines nuslist, PULPROG, the number of sampling points, ser/ser_full, the acquisition
parameters, FnMODE and the actual data length; contradictory metadata returns uncertain (safe
mode: no forced processing, framework §6).
"""

from __future__ import annotations

from itertools import product
from pathlib import Path

from core.data.internal_data_model import (
    Experiment,
    Sampling,
    SamplingMode,
)
from core.data.nus_reader import (
    find_schedule_file,
    indirect_grid_2d,
    probe_auto_sampling,
    read_nuslist,
    scan_dense_2d,
    scan_whole_trace_zeros,
    schedule_columns_for_ndim,
    schedule_grid_shape,
)
from ui_support.i18n import tr

_MIN_PLAUSIBLE_SCHEDULE_FRACTION = 0.01


def full_sampling_evidence(
    experiment: Experiment,
    *,
    nus_list: list[tuple[int, ...]],
    has_nuslist: bool,
) -> str | None:
    """When NUS is declared, decide whether the data is "really fully sampled" -> evidence string;
    otherwise None.

    - ``nuslist`` has at least as many lines as the indirect complex grid -> the schedule covers
      the whole grid = full sampling;
    - no ``nuslist`` (2D) and ``ser``/fid is the "full grid + zero filling" dense model with no
      zero rows -> full sampling (every complex point was acquired).

    Judgement only, never a data change; the caller downgrades mode to uniform from it (user
    2026-09-14: full sampling should take the uniform route).
    """
    if has_nuslist:
        shape = schedule_grid_shape(experiment, has_schedule=True)
        if shape and _schedule_is_canonical_full_grid(experiment, nus_list):
            return tr(
                "actual full sampling: the sampling schedule covers the whole indirect "
                "complex-point grid {p0} "
                "dimension (labelled NUS, actually full sampling) -> "
                "uniform",
                p0="×".join(str(value) for value in shape),
            )
        return None
    ndim = int(getattr(experiment, "ndim", 0) or 0)
    planned_rows = (
        _declared_td_rows(experiment)
        if _fntype_state(experiment) == "traditional"
        else _planned_indirect_rows(experiment)
    )
    if planned_rows <= 0:
        return None
    if ndim == 2:
        _actual_grid, mult, direct_points = indirect_grid_2d(experiment)
        if mult <= 0 or planned_rows % mult:
            return None
        planned_grid = planned_rows // mult
        scan = scan_dense_2d(
            experiment.source_path,
            acqus=(experiment.acquisition_parameters or {}).get("acqus", {}),
            grid_complex=planned_grid,
            mult=mult,
            direct_points=direct_points,
        )
        if scan["kind"] != "full":
            return None
        return tr(
            "actual full sampling: {p0} holds all {p1} rows with no zero rows (labelled NUS, "
            "actually full sampling; grid {p2} covered) -> "
            "uniform",
            p0=scan["source"],
            p1=scan["rows"],
            p2=planned_grid,
        )
    if ndim == 3:
        direct = getattr(experiment, "direct_dimension", None)
        direct_points = int(getattr(direct, "td", 0) or 0)
        if direct_points <= 0:
            return None
        try:
            scan = scan_whole_trace_zeros(
                Path(experiment.source_path),
                acqus=(experiment.acquisition_parameters or {}).get("acqus", {}),
                direct_points=direct_points,
            )
        except OSError:
            return None
        blocks = list(scan.get("blocks") or [])
        prefix_is_full_grid = bool(
            scan.get("kind") == "whole_trace_scanned"
            and int(scan.get("nonzero_rows") or 0) == planned_rows
            and len(blocks) == 1
            and tuple(blocks[0]) == (0, planned_rows - 1)
        )
        if prefix_is_full_grid:
            return tr(
                "actual full sampling: the first {p0} traces cover the complete 3D indirect "
                "grid; the remaining {p1} zero trace(s) are block padding -> uniform",
                p0=planned_rows,
                p1=int(scan.get("zero_rows") or 0),
            )
    return None


def _int_param(block: dict, key: str, default: int) -> int:
    value = block.get(key, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _planned_indirect_rows(experiment: Experiment) -> int:
    """Return the planned indirect-dimension trace count, including real components.

    ``TD`` is often the compact length actually written or converted, while ``NusTD`` may
    retain the full planned grid. When they differ, use the larger value to verify full
    sampling; otherwise a small acquired subset with a much larger declared grid could be
    mistaken for a complete uniform dataset. In 3D, multiplying the two indirect dimensions
    includes the real-component expansion for States and echo/antiecho modes.
    """
    ndim = int(getattr(experiment, "ndim", 0) or 0)
    if ndim not in (2, 3):
        return 0
    params = experiment.acquisition_parameters or {}
    rows = 1
    for name in ("acqu2s", "acqu3s")[: ndim - 1]:
        block = params.get(name, {}) or {}
        td = max(_int_param(block, "TD", 0), 0)
        nus_td = max(_int_param(block, "NusTD", 0), 0)
        size = max(td, nus_td)
        if size <= 0:
            return 0
        rows *= size
    return rows


def _declared_td_rows(experiment: Experiment) -> int:
    """Return the traditional declared raw trace count from the indirect-dimension ``TD`` values."""
    ndim = int(getattr(experiment, "ndim", 0) or 0)
    if ndim not in (2, 3):
        return 0
    params = experiment.acquisition_parameters or {}
    rows = 1
    for name in ("acqu2s", "acqu3s")[: ndim - 1]:
        td = max(_int_param(params.get(name, {}) or {}, "TD", 0), 0)
        if td <= 0:
            return 0
        rows *= td
    return rows


def _fntype_state(experiment: Experiment) -> str:
    """Return the explicit Bruker ``FnTYPE`` state: ``traditional``, ``nus`` or ``unknown``."""
    params = experiment.acquisition_parameters or {}
    values = [
        block.get("FnTYPE")
        for block in [params.get("acqus", {})]
        + [params.get(name, {}) for name in ("acqu2s", "acqu3s")]
        if isinstance(block, dict) and block.get("FnTYPE") is not None
    ]
    for value in values:
        text = str(value).strip().lower().replace("-", "_").replace(" ", "_")
        if text in {"2", "nus", "non_uniform_sampling", "nonuniform_sampling"}:
            return "nus"
    for value in values:
        text = str(value).strip().lower().replace("-", "_").replace(" ", "_")
        if text in {"0", "traditional", "traditional_planes"}:
            return "traditional"
    return "unknown"


def detect(
    experiment: Experiment,
    *,
    auto_sampling: dict | None = None,
    allow_auto_probe: bool = True,
) -> Sampling:
    """Return the sampling mode (including evidence and confidence).

    Parameters
    ----------
    experiment : Experiment
        An already loaded dataset (needs ``source_path`` and ``acquisition_parameters``).

    Returns
    -------
    Sampling
        ``mode`` (uniform/nus/uncertain), ``nus_list``, ``sampling_fraction``,
        ``schedule_type``, ``confidence`` and ``evidence`` (the grounds for the decision, kept
        entry by entry).

    Raises
    ------
    - never raises: contradictory metadata returns ``uncertain`` (safe mode) instead of guessing
      the sampling mode.

    Side effects
    ------------
    Read-only: it may read ``nuslist`` and the ``ser`` header to decide, and changes no file.

    Examples
    --------
        sampling = detect(experiment)
        if sampling.schedule_type == "full_sampling":
            ...  # NUS was declared but the data is fully sampled -> downgraded to uniform
    """
    params = experiment.acquisition_parameters
    acqus = params.get("acqus", {})
    evidence: list[str] = []

    auto = auto_sampling
    if auto is None and allow_auto_probe:
        auto = probe_auto_sampling(Path(experiment.source_path))
    if auto is None:
        auto = {}
    if auto.get("ok") and auto.get("grid"):
        evidence.append(
            tr(
                "bruker -AUTO conversion geometry: N={p0}, T={p1} (T/N={p2}; this ratio "
                "includes real/complex encoding and is not used as the NUS sampling fraction)",
                p0=int(auto["grid"]),
                p1=int(auto["sampled"]),
                p2=f"{float(auto['fraction']):.4f}",
            )
        )

    expected_columns = schedule_columns_for_ndim(int(getattr(experiment, "ndim", 0)))
    if expected_columns is None:
        nuslist_path, schedule_source = (
            None,
            tr("NUS schedules are only supported for 2D and 3D datasets"),
        )
    else:
        nuslist_path, schedule_source = find_schedule_file(
            Path(experiment.source_path),
            acqus=acqus,
            expected_columns=expected_columns,
        )
    has_nuslist = nuslist_path is not None
    if has_nuslist:
        evidence.append(
            tr("sampling schedule: {p0} ({p1})", p0=nuslist_path.name, p1=schedule_source)
        )

    nus_amount = _int_param(acqus, "NusAMOUNT", 100)
    blocks = [acqus] + [params[name] for name in ("acqu2s", "acqu3s") if name in params]
    nus_markers = [
        f"{name}:NusT2={_int_param(block, 'NusT2', 0)}"
        f":NusTD={_int_param(block, 'NusTD', 0)}"
        f":NusJSP={_int_param(block, 'NusJSP', 0)}"
        for block, name in zip(blocks, ["acqus", "acqu2s", "acqu3s"])
        if _int_param(block, "NusT2", 0) > 0
        or _int_param(block, "NusTD", 0) > 0
        or _int_param(block, "NusJSP", 0) > 0
    ]
    fntype_state = _fntype_state(experiment)
    explicit_nus_fntype = fntype_state == "nus"
    declared_schedule_name = str(acqus.get("NUSLIST", "") or "").strip()
    named_legacy_schedule = declared_schedule_name.lower() not in {
        "",
        "automatic",
        "none",
        "<automatic>",
    }
    residual_nus_markers = bool(nus_markers)
    if residual_nus_markers:
        evidence.append(" ".join(nus_markers) + f" NusAMOUNT={nus_amount}")
    if fntype_state == "traditional":
        evidence.append(
            tr(
                "FnTYPE=0 explicitly selects traditional acquisition; dormant Nus* values alone "
                "do not enable NUS"
            )
        )
    elif explicit_nus_fntype:
        evidence.append(tr("FnTYPE=2 explicitly enables non-uniform sampling"))

    if has_nuslist:
        nus_list = read_nuslist(nuslist_path)
        full_evidence = full_sampling_evidence(experiment, nus_list=nus_list, has_nuslist=True)
        if full_evidence is not None:
            evidence.append(full_evidence)
            return Sampling(
                mode=SamplingMode.UNIFORM,
                sampling_fraction=1.0,
                schedule_type="full_sampling",
                confidence=0.9,
                evidence=evidence,
                schedule_file=nuslist_path.name,
                schedule_source=schedule_source,
            )
        if _schedule_covers_full_grid(experiment, nus_list):
            evidence.append(
                tr(
                    "the sampling schedule covers the full grid but is not in canonical grid "
                    "order; the data must still be reordered with the schedule before ordinary FT"
                )
            )
        if nus_amount >= 100:
            evidence.append(
                tr(
                    "NusAMOUNT={p0} says full sampling but {p1} lists only {p2} of {p3} "
                    "complex-grid point(s); the valid schedule is trusted over the parameter",
                    p0=nus_amount,
                    p1=nuslist_path.name,
                    p2=len(nus_list),
                    p3=_schedule_full_grid(experiment),
                )
            )
        #
        declared = max(nus_amount, 1) / 100.0
        grid = _schedule_full_grid(experiment)
        schedule_fraction = min(len(nus_list) / grid, 1.0) if grid and nus_list else 0.0
        if (
            nus_amount >= 100
            and 0.0 < schedule_fraction < 1.0
            and schedule_fraction >= _MIN_PLAUSIBLE_SCHEDULE_FRACTION
        ):
            fraction = schedule_fraction
        else:
            fraction = declared
        return Sampling(
            mode=SamplingMode.NUS,
            nus_list=nus_list,
            sampling_fraction=fraction,
            schedule_type="nuslist",
            confidence=0.98,
            evidence=evidence,
            schedule_file=nuslist_path.name,
            schedule_source=schedule_source,
        )

    ndim = int(getattr(experiment, "ndim", 0) or 0)
    zero_scan = _whole_trace_zero_evidence(experiment, acqus)
    compact_3d_nus_evidence = bool(
        ndim == 3
        and residual_nus_markers
        and zero_scan is not None
        and zero_scan[1] > 0
        and zero_scan[4]
    )
    nus_params = bool(
        explicit_nus_fntype
        or compact_3d_nus_evidence
        or (named_legacy_schedule and residual_nus_markers)
        or (fntype_state == "unknown" and residual_nus_markers)
    )
    if zero_scan is not None and zero_scan[1] > 0:
        scanned, zero_rows, nonzero_rows, block_count, trailing_only = zero_scan
        if trailing_only:
            evidence.append(
                tr(
                    "{p0}: {p1} non-zero trace(s) form one compact prefix, followed by {p2} "
                    "whole-trace zero row(s) of trailing padding",
                    p0=scanned,
                    p1=nonzero_rows,
                    p2=zero_rows,
                )
            )
        else:
            evidence.append(
                tr(
                    "{p0}: {p1} row(s), {p2} whole-trace zero row(s), {p3} sampled row(s) "
                    "in {p4} contiguous block(s)",
                    p0=scanned,
                    p1=nonzero_rows + zero_rows,
                    p2=zero_rows,
                    p3=nonzero_rows,
                    p4=block_count,
                )
            )

    if zero_scan is not None and zero_scan[1] > 0 and not zero_scan[4]:
        evidence.append(
            tr(
                "the sampling positions are not recoverable from the data (a schedule file is "
                "needed to reconstruct)"
            )
        )
        return Sampling(
            mode=SamplingMode.NUS,
            nus_list=[],
            sampling_fraction=0.0,
            schedule_type="zero_trace",
            confidence=0.9,
            evidence=evidence,
            schedule_source=schedule_source,
        )

    allow_dense_full_sampling = not nus_params
    if ndim in (2, 3) and allow_dense_full_sampling:
        dense_evidence = full_sampling_evidence(experiment, nus_list=[], has_nuslist=False)
        if dense_evidence is not None:
            evidence.append(dense_evidence)
            return Sampling(
                mode=SamplingMode.UNIFORM,
                sampling_fraction=1.0,
                schedule_type="full_sampling",
                confidence=0.9,
                evidence=evidence,
                schedule_source=schedule_source,
            )

    if nus_params:
        #
        if ndim == 3:
            if zero_scan is not None and zero_scan[1] > 0 and zero_scan[4]:
                evidence.append(
                    tr(
                        "the 3D ser contains a compact non-zero prefix plus trailing zero padding, "
                        "and NUS parameter markers are enabled; without the sampling schedule, "
                        "the two-dimensional trace order cannot be recovered"
                    )
                )
            else:
                evidence.append(
                    tr(
                        "3D NUS parameter markers are present but no sampling schedule was found; "
                        "even when the trace count equals the full grid, the two-dimensional "
                        "sampling order cannot be recovered"
                    )
                )
        else:
            evidence.append(
                tr(
                    "NUS parameter markers present but no sampling schedule was found; the "
                    "sampling positions are unknown"
                )
            )
        return Sampling(
            mode=SamplingMode.NUS,
            nus_list=[],
            sampling_fraction=max(nus_amount, 1) / 100.0,
            schedule_type="params",
            confidence=0.9 if ndim == 3 and zero_scan is not None and zero_scan[1] else 0.6,
            evidence=evidence,
            schedule_source=schedule_source,
        )

    return Sampling(
        mode=SamplingMode.UNIFORM,
        sampling_fraction=1.0,
        confidence=0.95,
        evidence=evidence
        + [
            tr(
                "No NUS evidence (no sampling schedule, Nus* parameter not enabled, and no "
                "whole-trace zero row)"
            )
        ],
        schedule_source=schedule_source,
    )


def _schedule_full_grid(experiment: Experiment) -> int:
    """Return the full indirect-dimension complex-point grid size, or zero if unavailable."""
    sizes = schedule_grid_shape(experiment, has_schedule=True)
    if not sizes:
        return 0
    product = 1
    for size in sizes:
        product *= size
    return max(product, 0)


def _schedule_covers_full_grid(experiment: Experiment, nus_list: list[tuple[int, ...]]) -> bool:
    """Return whether a schedule covers the entire grid and is therefore redundant for sampling."""
    shape = schedule_grid_shape(experiment, has_schedule=True)
    grid = _schedule_full_grid(experiment)
    if grid <= 0 or not nus_list or any(len(point) != len(shape) for point in nus_list):
        return False
    unique = set(nus_list)
    if len(unique) != grid:
        return False
    zero_based = all(
        all(0 <= int(point[index]) < shape[index] for index in range(len(shape)))
        for point in unique
    )
    one_based = all(
        all(1 <= int(point[index]) <= shape[index] for index in range(len(shape)))
        for point in unique
    )
    return zero_based or one_based


def _schedule_is_canonical_full_grid(
    experiment: Experiment, nus_list: list[tuple[int, ...]]
) -> bool:
    """Return whether a full schedule is already in ordinary grid order and can use uniform FT."""
    shape = schedule_grid_shape(experiment, has_schedule=True)
    if not shape or not _schedule_covers_full_grid(experiment, nus_list):
        return False
    expected_zero = list(product(*(range(size) for size in shape)))
    points = [tuple(int(value) for value in point) for point in nus_list]
    if points == expected_zero:
        return True
    expected_one = [tuple(value + 1 for value in point) for point in expected_zero]
    return points == expected_one


def _whole_trace_zero_evidence(
    experiment: Experiment, acqus: dict
) -> tuple[str, int, int, int, bool] | None:
    """Scan for whole-trace zero rows and mark consecutive trailing zero rows as padding
    candidates.

    The direct-dimension real-point count comes from F3 ``td``. In a 3D ``ser`` file, each row
    is one direct-dimension acquisition, so an entirely zero row indicates an unmeasured
    increment.
    """
    dimensions = list(getattr(experiment, "dimensions", []) or [])
    direct = next(
        (
            dim
            for dim in dimensions
            if str(getattr(getattr(dim, "role", ""), "value", "")).startswith("direct")
        ),
        None,
    )
    if direct is None:
        return None
    direct_points = int(getattr(direct, "td", 0) or 0)
    if direct_points <= 0:
        return None
    try:
        scan = scan_whole_trace_zeros(
            Path(experiment.source_path),
            acqus=acqus,
            direct_points=direct_points,
        )
    except OSError:
        return None
    if scan["kind"] != "whole_trace_scanned":
        return None
    blocks = list(scan["blocks"])
    zero_rows = int(scan["zero_rows"])
    nonzero_rows = int(scan["nonzero_rows"])
    declared_rows = (
        _declared_td_rows(experiment)
        if _fntype_state(experiment) == "traditional"
        else _planned_indirect_rows(experiment)
    )
    trailing_only = bool(
        declared_rows > 0
        and nonzero_rows >= declared_rows
        and zero_rows > 0
        and len(blocks) == 1
        and tuple(blocks[0]) == (0, nonzero_rows - 1)
    )
    return (
        str(scan["source"]),
        zero_rows,
        nonzero_rows,
        len(blocks),
        trailing_only,
    )
