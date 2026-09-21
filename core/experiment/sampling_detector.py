"""Sampling-mode detection (uniform / NUS / uncertain).

Combines nuslist, PULPROG, the number of sampling points, ser/ser_full, the acquisition
parameters, FnMODE and the actual data length; contradictory metadata returns uncertain (safe
mode: no forced processing, framework §6).
"""

from __future__ import annotations

from core.data.internal_data_model import (
    Experiment,
    Sampling,
    SamplingMode,
)
from core.data.nus_reader import (
    indirect_grid_2d,
    read_nuslist,
    scan_dense_2d,
)
from ui_support.i18n import tr


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
    grid, mult, direct_points = indirect_grid_2d(experiment)
    if grid <= 0:
        return None
    if has_nuslist:
        # a 2D nuslist should carry a single indirect coordinate. Both 0-based and 1-based
        # spellings are supported, but every line must be valid and the set of unique coordinates
        # must cover the whole grid exactly.
        if not nus_list or any(len(point) != 1 for point in nus_list):
            return None
        coordinates = [int(point[0]) for point in nus_list]
        unique = set(coordinates)
        zero_based = set(range(grid))
        one_based = set(range(1, grid + 1))
        valid_full_grid = (
            len(coordinates) == len(unique)
            and (unique == zero_based or unique == one_based)
        )
        if valid_full_grid:
            return (
                tr(
                    "actual full sampling: nuslist covers the whole grid {p0} for the indirect "
                    "dimension (labelled NUS, actually full sampling) -> "
                    "uniform",
                    p0=grid,
                )
            )
        return None
    if int(experiment.ndim) != 2:
        return None
    scan = scan_dense_2d(
        experiment.source_path,
        acqus=(experiment.acquisition_parameters or {}).get("acqus", {}),
        grid_complex=grid,
        mult=mult,
        direct_points=direct_points,
    )
    if scan["kind"] == "full":
        return (
            tr(
                "actual full sampling: {p0} holds all {p1} rows with no zero rows (labelled NUS, "
                "actually full sampling; grid {p2} covered) -> "
                "uniform",
                p0=scan['source'],
                p1=scan['rows'],
                p2=grid,
            )
        )
    return None




def _int_param(block: dict, key: str, default: int) -> int:
    value = block.get(key, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def detect(experiment: Experiment) -> Sampling:
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

    nuslist_path = experiment.source_path / "nuslist"
    has_nuslist = nuslist_path.is_file()
    if has_nuslist:
        evidence.append("dataset contains nuslist")

    nus_amount = _int_param(acqus, "NusAMOUNT", 100)
    blocks = [acqus] + [
        params[name] for name in ("acqu2s", "acqu3s") if name in params
    ]
    nus_markers = [
        f"{name}:NusT2={_int_param(block, 'NusT2', 0)}"
        f":NusTD={_int_param(block, 'NusTD', 0)}"
        f":NusJSP={_int_param(block, 'NusJSP', 0)}"
        for block, name in zip(blocks, ["acqus", "acqu2s", "acqu3s"])
        if _int_param(block, "NusT2", 0) > 0
        or _int_param(block, "NusTD", 0) > 0
        or _int_param(block, "NusJSP", 0) > 0
    ]
    nus_params = bool(nus_markers) and nus_amount < 100
    if nus_params:
        evidence.append(" ".join(nus_markers) + f" NusAMOUNT={nus_amount}")

    if has_nuslist and nus_params and nus_amount >= 100:
        evidence.append(tr("nuslist conflicts with NusAMOUNT>=100, enter safe mode"))
        return Sampling(mode=SamplingMode.UNCERTAIN, confidence=0.5, evidence=evidence)

    if has_nuslist or nus_params:
        fraction = max(nus_amount, 1) / 100.0
        nus_list = read_nuslist(nuslist_path) if has_nuslist else []
        # 2026-09-14 (user): NUS declared but really fully sampled -> process as uniform
        # (evidence kept)
        full_evidence = full_sampling_evidence(
            experiment, nus_list=nus_list, has_nuslist=has_nuslist
        )
        if full_evidence is not None:
            evidence.append(full_evidence)
            return Sampling(
                mode=SamplingMode.UNIFORM,
                sampling_fraction=1.0,
                schedule_type="full_sampling",
                confidence=0.9,
                evidence=evidence,
            )
        return Sampling(
            mode=SamplingMode.NUS,
            nus_list=nus_list,
            sampling_fraction=fraction,
            schedule_type="nuslist" if has_nuslist else "params",
            confidence=0.98 if has_nuslist else 0.9,
            evidence=evidence,
        )

    return Sampling(
        mode=SamplingMode.UNIFORM,
        sampling_fraction=1.0,
        confidence=0.95,
        evidence=evidence + [tr("No NUS tag (no nuslist and Nus* parameter not enabled)")],
    )
