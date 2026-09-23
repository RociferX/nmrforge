"""NMRPipe backend: bruker -AUTO conversion + NMRPipe pipeline + NUS SMILE
reconstruction (Linux/csh).

NMRPipe semantics live only in this layer (backend/) and in the generated scripts;
upper layers call in through the ProcessingBackend protocol.
Lookup path: the csh environment ``source ~/.cshrc; which nmrPipe`` comes first (user
requirement); a bin directory may also be given explicitly.

Important design (verified on real data, 0.2.199-patch16 single-file):
- All four routes (auto/manual x single dataset/segmented) always write a single-file
  fid.com named {dataset_id}.fid (bruker -AUTO emits the full NusTD grid with
  -aq2D Complex, matching the lab fid.com); acqu3s TD is no longer corrected (with
  TD=1 bruker likewise writes a single full-grid fid, verified on sampleK); 3D NUS
  slices come from the direct-dimension step of SMILE script step1
  (nus3d_1/test%04d.ft1);
- Multi-segment experiments (one experiment split into several datasets, e.g.
  61/63/65/67): follow the lab scripts (1stfid.com + 2ndAdd.com) - each segment
  writes one converted file, addNMR merges them pairwise in the time domain into
  merged/{dataset_id}.fid, then a single SMILE pass reconstructs; an optional
  per-segment frequency shift (-rs Hz, guards against field drift) is supported. Since
  2026-09-23 multi-segment conversions also **check the inter-part field drift
  automatically** (reference = part 1, criterion |d| > 1.5 Hz; ppm is recorded only): when a part
  is over the threshold its PS -rs is inserted before MULT -c in fid.com and the part is
  re-converted, and the residual is re-checked before merging (see
  workflow/field_drift.py).
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from backend.base import BackendCapabilities
from backend.bruker_workflow import (
    apply_fid_com_overrides,
    patch_fid_com,
    patch_nus_expand_count,
    physical_direct_points,
)
from backend.config import (
    resolve_ext_hi,
    resolve_ext_lo,
    resolve_nthread,
    resolve_points_per_line,
)
from backend.memory_disk import INTERMEDIATE_SUBDIR
from backend.nmrpipe_finder import find_nmrpipe_bin, find_tool
from backend.nmrpipe_version import register_nmrpipe_versions
from backend.runtime import CshRuntime, cancel_requested
from backend.script_generator import (
    DEFAULT_POINTS_PER_LINE,
    _as_bool,
    effective_td,
    expand_baseline,
    generate_2d_nus_script,
    generate_3d_nus_script,
    generate_convert_script,
    generate_nus_finalize_script,
    generate_preview_script,
    generate_process_script,
    select_smile_params,
    zero_fill_plan,
    zero_fill_report,
)
from core.audit.qc_audit import QcAction, QcAuditLog
from core.data.internal_data_model import Experiment, SamplingMode
from core.data.nus_reader import read_nuslist
from core.data.raw_fingerprint import raw_dir_fingerprint
from core.optimization.phase_search import (
    direct_ft_traces,
    dominant_absorption_ratio,
    orient_dominant_positive,
    search_direct_phase_on_spectrum,
    search_direct_spectrum_phase,
)
from core.planning.processing_plan import ProcessingPlan
from core.project.manager import atomic_write_text
from ui_support.i18n import tr
from workflow.field_drift import (
    DRIFT_HZ_MIN,
    DRIFT_PPM_THRESHOLD,
    MAX_ROUNDS,
    detect_group_drift,
    direct_axis_hz,
    insert_ps_shift,
    read_field_drift_record,
    write_field_drift_record,
)


def _slice_candidates(directory: Path, dataset_id: str) -> list[Path]:
    """Slice fid candidates in a directory: prefer the new {dataset_id}*.fid naming,
    keeping the legacy test*.fid compatible."""
    if not directory.is_dir():
        return []
    new_style = sorted(directory.glob(f"{dataset_id}*.fid"))
    legacy = sorted(directory.glob("test*.fid"))
    seen = {p.name for p in new_style}
    return new_style + [p for p in legacy if p.name not in seen]


def _slice_in_file(directory: Path, dataset_id: str) -> str | None:
    """in_file pattern of the slice stream in a directory (fid/test%03d.fid or
    fid/{dataset_id}%03d.fid)."""
    slices = _slice_candidates(directory, dataset_id)
    if not slices:
        return None
    new_style = any(p.name.startswith(dataset_id) for p in slices)
    return f"fid/{dataset_id}%03d.fid" if new_style else "fid/test%03d.fid"


def _nus_grid_bounds(experiment: Experiment) -> list[int] | None:
    """Upper bound of the NUS nuslist indices.

    2D: the single nuslist column holds F1 complex-point indices, bounded by the
    NUS grid td[1] (e.g. nus20_25 indexes up to 126 with a grid of 128, so
    NusTD//2 must not be used);
    3D: the nuslist columns hold complex-point indices, bounded by NusTD//2
    (cc indexes F2 up to 84 with NusTD 170).
    """
    td = effective_td(experiment)
    if experiment.ndim == 2 and len(td) > 1:
        return [int(td[1])]
    if experiment.ndim >= 3 and len(td) > 2:
        return [int(td[1]) // 2, int(td[2]) // 2]
    return None


def _validate_nus_points(
    points: list[tuple[int, ...]],
    experiment: Experiment,
) -> tuple[list[tuple[int, ...]], list[tuple[int, ...]], dict[tuple[int, ...], list[str]]]:
    """Bad-point check for NUS sampling points: out-of-range plus duplicated
    points. Returns (valid points, bad points, reasons per bad point)."""
    from collections import Counter

    counts = Counter(points)
    bounds = _nus_grid_bounds(experiment)
    valid: list[tuple[int, ...]] = []
    bad: list[tuple[int, ...]] = []
    reasons: dict[tuple[int, ...], list[str]] = {}
    seen: set[tuple[int, ...]] = set()
    for raw_point in points:
        point = tuple(raw_point)
        oob = bounds is not None and any(
            point[i] >= bounds[i] for i in range(min(len(point), len(bounds)))
        )
        dup = counts[point] > 1
        if oob or dup:
            if point not in bad:
                bad.append(point)
                r: list[str] = []
                if oob:
                    r.append(tr("out of bounds(grid {p0})", p0=bounds))
                if dup:
                    r.append(tr("repeat({p0})", p0=counts[point]))
                reasons[point] = r
            continue
        if point in seen:
            continue
        seen.add(point)
        valid.append(point)
    return valid, bad, reasons


def _ser_point_layout(
    experiment: Experiment, data_size: int, n_rows: int
) -> tuple[int, int, int] | None:
    """Derive the ser layout from the sampling parameters; returns (bytes per point,
    bytes per vector, redundancy).

    The ser byte layout follows the sampling parameters (0.2.195): one vector is the
    serPadSize-padded direct-dimension complex point count x 2 x word size (nusExpand:
    word size 8 -> 128-aligned, word size 4 -> 256-aligned); each sampling point
    carries a redundancy count of vectors (NS repeats, -avg averaging) = ser size /
    point count / bytes per vector, and must divide exactly. Returns None when it
    cannot be determined (missing parameters or no exact division) -- the caller then
    falls back to clearing the FID instead of deleting whole blocks that might be
    misaligned.
    """
    td = effective_td(experiment)
    if not td:
        return None
    direct_td = int(td[0])
    per_point = data_size // n_rows if n_rows else 0
    for word_bytes, ser_pad in ((8, 128), (4, 256)):
        padded = ((direct_td + ser_pad - 1) // ser_pad) * ser_pad
        vec_bytes = (padded // 2) * 2 * word_bytes
        if vec_bytes <= 0 or per_point % vec_bytes != 0:
            continue
        redundancy = per_point // vec_bytes
        if redundancy >= 1 and per_point * n_rows == data_size:
            return per_point, vec_bytes, redundancy
    return None


def apply_final_ext_params(params: dict[str, Any]) -> str | None:
    """Map the "final-run direct-dimension range" (final_ext_* + apply_ext_to_opt) onto
    ext_lo/ext_hi.

    0.2.199-patch29hz-fix17 (user): SMILE optimisation/sweeps call `reconstruct_nus`
    directly (bypassing unified_route), while the range parameters live in the GUI
    side as `final_ext_lo`/`final_ext_hi`; without this mapping the code falls back to
    the default wide window (10.5-6.5), the memory guard sizes against that wide
    window, and a narrow range that was set explicitly still reports "not enough
    memory" (measured on the VM with sampleJ: the default window peaks at about
    22692MB > 0.85 x available, so the direct dimension was zero-filled
    automatically; with the range mapped no downgrade happens).

    Semantics: the template must match the final-run script (user 2026-09-11: "use
    the final script as the template and only change SMILE parameters"), so this
    **ignores** `apply_ext_to_opt` -- that switch only governs the first
    reconstruction/phase search of the unified route; an explicit `ext_lo`/`ext_hi`
    wins. The change is in place on params; returns a log line (None when unmapped).
    """
    lo = str(params.get("final_ext_lo", "") or "").strip()
    hi = str(params.get("final_ext_hi", "") or "").strip()
    if not lo and not hi:
        return None
    mapped: list[str] = []
    if lo and not str(params.get("ext_lo", "") or "").strip():
        params["ext_lo"] = lo
        mapped.append(f"ext_lo={lo}")
    if hi and not str(params.get("ext_hi", "") or "").strip():
        params["ext_hi"] = hi
        mapped.append(f"ext_hi={hi}")
    if not mapped:
        return None
    return (
        tr(
        "direct dimension range: final run range has been used for optimisation/refactoring "
        "(",
    )
    ) + ", ".join(mapped) + ")"


def direct_phase_override(params: dict[str, Any]) -> tuple[float, float] | None:
    """Direct-dimension phase override: an explicit `direct_phase_override` wins,
    otherwise the `direct_phase` from the run record is used.

    0.2.199-patch29hz-fix18 (user): the final run stores the direct-dimension phase
    in the run parameters under the `direct_phase` key, while the script templates
    read `direct_phase_override` -- not honouring that key makes the SMILE
    optimisation template fall back to PS(0,0) (out of step with the final-run
    script, which breaks "use the final script as the template and only change
    SMILE parameters"). Returns None when both keys are missing or malformed (the
    usual search then runs).
    """
    raw = params.get("direct_phase_override")
    if raw is None:
        raw = params.get("direct_phase")
    if raw is None:
        return None
    try:
        return (float(raw[0]), float(raw[1]))
    except (TypeError, ValueError, IndexError):
        return None


def _nus_grid_from_points(
    points: list[tuple[int, ...]],
) -> list[int] | None:
    """Derive the grid from the actual nuslist sampling range once bad points are
    removed (max+1 per dimension).

    2D single column -> [f1_grid]; 3D two columns -> [f2_grid, f1_grid].
    """
    if not points:
        return None
    n_cols = len(points[0])
    if n_cols == 1:
        return [max(p[0] for p in points) + 1]
    if n_cols == 2:
        return [max(p[0] for p in points) + 1, max(p[1] for p in points) + 1]
    return None


def _record_source_clean(
    audit: QcAuditLog,
    experiment: Experiment,
    raw_dirs: list[Path],
    valid_count: int,
    bad_points: list[tuple[int, ...]],
    removed: bool,
) -> None:
    """Record "bad points deleted at the source", an automatic data change
    (Phase 10 structured audit).

    A record is written only when the sampling schedule really holds bad points;
    ``removed=False`` (missing ser, or not divisible by row) means the raw data was
    **not** changed and the caller falls back to clearing the FID, a path recorded
    separately by ``_zero_bad_point_fid``.
    """
    if not bad_points:
        return
    detail = [list(point) for point in bad_points[:8]]
    audit.record(
        QcAction(
            issue_detected=tr(
                "bad point in the NUS sampling table (out of range / duplicate "
                "coordinate)",
            ),
            location=",".join(str(Path(d) / "ser") + " + nuslist" for d in raw_dirs),
            detection_rule=tr("_validate_nus_points(nuslist versus experiment sampling grid)"),
            action_taken=(
                tr("removed_from_source_ser_and_nuslist")
                if removed
                else tr("reported_only")
            ),
            before_state={
                "sampling_points": int(valid_count) + len(bad_points),
                "bad_points": len(bad_points),
            },
            after_state={"sampling_points": int(valid_count)},
            extra={
                "dataset_id": experiment.dataset_id,
                "bad_points_sample": detail,
                "bad_points_truncated": len(bad_points) > len(detail),
                "source_removed": bool(removed),
            },
        )
    )


def _apply_nus_grid_after_clean(
    experiment: Experiment,
    points: list[tuple[int, ...]],
    *,
    audit: QcAuditLog | None = None,
) -> list[str]:
    """Update NusTD to the actual sampling range after bad points are removed
    (0.2.197).

    Cross-validation (parameter correction) used the static NusTD and thereby put
    back the grid adjusted after the bad points were cleared, leaving the fid.com
    grid inconsistent with the cleaned data; here the acqu2s/acqu3s NusTD is shrunk
    to the actual range (never enlarged), keeping _effective_td, the fid.com
    parameter correction, the nusExpand grid and the reconstruction consistent.
    Returns log lines.
    """
    grid = _nus_grid_from_points(points)
    if not grid:
        return []
    logs: list[str] = []
    params = experiment.acquisition_parameters
    if len(grid) == 1:
        block = params.setdefault("acqu2s", {})
        old = int(block.get("NusTD", 0) or 0)
        new = grid[0]
        if old and 0 < new < old:
            block["NusTD"] = new
            logs.append(
                tr(
                "Grid adjustment after sampling bad point removal: acqu2s NusTD {p0} → "
                "{p1}",
                p0=old,
                p1=new,
            ))
            if audit is not None:
                audit.record(
                    QcAction(
                        issue_detected=(
                            tr(
                            "After the sampling bad point is removed, the declared NusTD is "
                            "inconsistent with the actual sampling "
                            "range",
                        )
                        ),
                        location="acqu2s.NusTD",
                        detection_rule=(
                            tr(
                            "_nus_grid_from_points(nuslist after cleaning) = max+1 per "
                            "dimension",
                        )
                        ),
                        action_taken="nus_td_shrunk",
                        before_state={"NusTD": old},
                        after_state={"NusTD": new},
                        extra={"axis": "acqu2s", "dataset_id": experiment.dataset_id},
                    )
                )
    elif len(grid) == 2:
        for key, g in (("acqu2s", grid[0]), ("acqu3s", grid[1])):
            block = params.setdefault(key, {})
            old = int(block.get("NusTD", 0) or 0)
            new = 2 * g
            if old and 0 < new < old:
                block["NusTD"] = new
                logs.append(
                    tr(
                    "Grid adjustment after sampling bad point removal: {p0} NusTD {p1} → "
                    "{p2}",
                    p0=key,
                    p1=old,
                    p2=new,
                ))
                if audit is not None:
                    audit.record(
                        QcAction(
                            issue_detected=(
                                tr(
                                    "After the sampling bad point is removed, the declared NusTD "
                                    "is inconsistent with the actual sampling "
                                    "range",
                                )
                            ),
                            location=f"{key}.NusTD",
                            detection_rule=(
                                tr(
                                    "_nus_grid_from_points(cleaned nuslist) = 2 x max+1 per "
                                    "dimension",
                                )
                            ),
                            action_taken="nus_td_shrunk",
                            before_state={"NusTD": old},
                            after_state={"NusTD": new},
                            extra={"axis": key, "dataset_id": experiment.dataset_id},
                        )
                    )
    return logs


def zf_summary(plan: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Zero-fill plan summary (used in WorkflowRun params): {axis: {"mode",
    "size"}}."""
    return {
        axis: {"mode": str(cfg.get("mode", "auto")), "size": cfg.get("size")}
        for axis, cfg in plan.items()
    }

def _effective_params_base(
    extract: bool,
    ext_lo: str,
    ext_hi: str,
    zf_plan: dict[str, Any],
    baseline: dict[str, Any] | None,
    linewidth_hz: dict[str, float] | None,
    points_per_line: float,
    sampling: dict[str, Any] | None,
) -> dict[str, Any]:
    """Base effective_params keys shared by process/reconstruct_nus (0.2.46)."""
    return {
        "extract": extract,
        "ext_lo": ext_lo,
        "ext_hi": ext_hi,
        "zero_fill": zf_summary(zf_plan),
        "baseline": baseline,
        "linewidth_hz": linewidth_hz,
        "points_per_line": points_per_line,
        "sampling": dict(sampling),
    }


def _segment_kind_info(segments: list[Path | str]) -> tuple[str | None, list[str]]:
    """Identify the multi-segment container type and build the explanation log
    (0.2.199-patch29cv).

    - repeat_uniform / repeat_nus: repeated experiments co-added -- each segment's
      FID is summed point by point in the time domain with TopSpin fidadd
      semantics (co-addition, addNMR does not normalise), which raises the SNR; in
      NUS the segments share sampling points, so they are co-added on the same
      grid and reconstructed once.
    - segmented_nus: NUS segments -- the sampling points are complementary, so the
      nuslists are merged to complete the grid and reconstructed once.
    A failed classification returns (None, [warning]) and does not block FID
    generation (the caller still merges by segment).
    """
    from core.data.bruker_reader import classify_segment_kind

    try:
        kind = classify_segment_kind(list(segments))
    except Exception as exc:  # noqa: BLE001 - a failed classification must not block the conversion
        return None, [(
            tr(
            "⚠ Multi-segment type recognition failed ({p0}), merged according to "
            "segmented",
            p0=exc,
        )
        )]
    label = {
        "repeat_uniform": tr("repeated experiment (uniform, identical parameter)"),
        "repeat_nus": tr("repeated experiment (NUS, identical sampling points)"),
        "segmented_nus": tr("segmented (NUS, complementary sampling points)"),
    }.get(kind, kind)
    if kind == "repeat_uniform":
        detail = (
            tr(
                "identified as {p0}: {p1} FID parts follow the TopSpin fidadd convention: "
                "point-by-point summation in the time domain (co-addition; addNMR does not "
                "normalise) to raise the "
                "SNR",
                p0=label,
                p1=len(segments),
            )
        )
    elif kind == "repeat_nus":
        detail = (
            tr(
                "identified as {p0}: all parts share the same sampling points, so they are summed "
                "on one grid and reconstructed once (TopSpin fidadd semantics; addNMR does not "
                "normalise)",
                p0=label,
            )
        )
    elif kind == "segmented_nus":
        detail = (
            tr(
                "identified as {p0}: the sampling points of the parts are complementary, so the "
                "nuslists are merged to complete the grid and reconstructed in one "
                "pass",
                p0=label,
            )
        )
    else:
        detail = tr("Multi-segment type: {p0}", p0=label)
    return kind, [detail]


@dataclass


class NMRPipeBackend:
    """NMRPipe implementation (Linux: bruker -AUTO + fid.com + NMRPipe pipeline,
    SMILE, multi-segment merge)."""

    nmrpipe_bin: str = ""
    work_dir: str = ""
    capabilities: BackendCapabilities = field(
        default_factory=lambda: BackendCapabilities(provider="nmrpipe")
    )

    def _bin_dir(self) -> Path | None:
        bin_dir = find_nmrpipe_bin(self.nmrpipe_bin)
        if bin_dir is not None:
            # PROV-009: probe the version once the install directory is resolved so
            # WorkflowRun can record provenance; a failed probe does not affect
            # processing (see backend/nmrpipe_version.py).
            register_nmrpipe_versions(bin_dir)
        return bin_dir

    def _recover_dense_2d_nus(
        self,
        raw_dir: Path,
        experiment: Experiment,
        logs: list[str],
        fid_file: Path | None = None,
    ) -> list[int] | None:
        """2D without a sampling schedule: decide whether this is the dense
        "full grid + zero fill" model, and recover the real sampling points.

        User 2026-09-11: any subset of dense sampling must be detected; only data
        that truly has no sampling schedule is reported as missing.
        - row count == declared grid rows -> the whole grid is present, so the
          sampling points are the non-zero rows of the zero pattern (any subset),
          returned directly as complex-point indices;
        - row count < declared grid -> only the sampled rows are left and their
          positions are unknown (truly sparse) -> return None;
        - row count > declared grid / parameters unreadable -> metadata and file
          disagree -> return None.
        When ser is missing the converted fid is used instead (which also keeps
        every grid row).
        """
        from core.data.nus_reader import indirect_grid_2d, scan_dense_2d

        acqus = (experiment.acquisition_parameters or {}).get("acqus", {})
        # Note: effective_td can no longer be used -- fully sampled data is already
        # downgraded to uniform when read, and effective_td only folds complex
        # points when mode=NUS; a mode-independent grid is used here.
        grid_complex, mult, x_n = indirect_grid_2d(experiment)
        rows_declared = mult * grid_complex
        if grid_complex <= 0 or mult <= 0 or x_n <= 0:
            logs.append(
                tr(
                    "2D NUS Judgment: The grid parameter (direct dimension TD/ indirect dimension "
                    "grid) is illegal and the dense model cannot be "
                    "determined",
                )
            )
            return None
        scan = scan_dense_2d(
            raw_dir,
            acqus=acqus,
            grid_complex=grid_complex,
            mult=mult,
            direct_points=x_n,
            fid_file=fid_file,
        )
        kind = str(scan["kind"])
        rows = int(scan["rows"])
        if kind in ("bad_layout", "unknown_dtype"):
            logs.append(tr("2D NUS Judgment:{p0}", p0=scan['detail']))
            return None
        if kind == "missing":
            logs.append(
                tr(
                "2D NUS Determination: There is neither ser nor converted fid, so dense model "
                "cannot be determined",
            ))
            return None
        if kind == "sparse":
            logs.append(
                tr(
                    "2D NUS Judgment:{p0} only {p1} row < declare grid {p2} Row -> sparse file "
                    "(sampling location agnostic), requires nuslist sampling "
                    "schedule",
                    p0=scan['source'],
                    p1=rows,
                    p2=rows_declared,
                )
            )
            return None
        if kind == "mismatch":
            logs.append(
                tr(
                    "2D NUS judgment: {p0} {p1} rows != the declared grid of {p2} rows -> the "
                    "metadata disagrees with the file, cannot "
                    "decide",
                    p0=scan['source'],
                    p1=rows,
                    p2=rows_declared,
                )
            )
            return None
        if kind == "all_zero":
            logs.append(
                tr(
                "2D NUS Judgment: full grid data is all zero, sampling point cannot be "
                "restored",
            ))
            return None
        points = [int(point) for point in scan["points"]]
        if kind == "full":
            logs.append(
                tr(
                    "2D NUS judgment: the full grid has {p0} rows and no zero rows (NusAMOUNT "
                    "marks NUS, but the data are fully sampled) -> reconstructing from the "
                    "full-sampling table ({p1} complex "
                    "points)",
                    p0=rows,
                    p1=grid_complex,
                )
            )
        else:
            logs.append(
                tr(
                    "2D NUS Determination: dense model (full grid {p0}/{p1} rows, {p2}), "
                    "recovering from the zero pattern {p3}/{p4} sampled complex "
                    "points({p5:.1f}%)",
                    p0=rows,
                    p1=rows_declared,
                    p2=scan['dtype'],
                    p3=len(points),
                    p4=grid_complex,
                    p5=100.0 * len(points) / grid_complex,
                )
            )
        return points

    def _work_path(self, experiment: Experiment) -> Path:
        raw = Path(experiment.source_path)
        if self.work_dir:
            return Path(self.work_dir)
        return raw.parent / f"{experiment.dataset_id}.nmrpipe"

    def health_check(self) -> dict[str, Any]:
        bin_dir = self._bin_dir()
        if bin_dir is None:
            return {
                "ok": False,
                "nmrpipe": None,
                "bruker": None,
                "message": tr("nmrPipe not found (csh: source ~/.cshrc; which nmrPipe)"),
            }
        bruker = find_tool("bruker", bin_dir)
        return {
            "ok": True,
            "nmrpipe": str(bin_dir / "nmrPipe"),
            "bruker": str(bruker) if bruker else "",
            "message": tr("Find nmrPipe: {p0}", p0=bin_dir / 'nmrPipe'),
        }

    def process(
        self,
        experiment: Experiment,
        plan: ProcessingPlan,
        *,
        params: dict[str, Any] | None = None,
        direct_phase_search: bool = True,
        direct_phase_override: dict[str, tuple[float, float]] | None = None,
        progress: Callable[[str], None] | None = None,
        out_file: str | None = None,
        script_name: str | None = None,
    ) -> dict[str, Any]:
        """Uniform sampling: conversion (including multi-segment merging) plus the
        NMRPipe pipeline (for NUS use reconstruct_nus).

        A non-empty direct_phase_override skips the phase search and writes PS with
        the given phase directly (the reference/selected phase is written back for
        production; for the unified in-memory search see
        workflow.memory_phase_search).
        """
        if experiment.sampling.mode is SamplingMode.NUS:
            return {
                "success": False,
                "message": tr("NUS Please call reconstruct_nus (SMILE) for data"),
                "logs": [],
            }
        bin_dir = self._bin_dir()
        if bin_dir is None:
            return {
                "success": False,
                "message": tr("nmrPipe not found (csh: which nmrPipe)"),
                "logs": [],
            }
        runtime = CshRuntime()
        raw = Path(experiment.source_path)
        work = self._work_path(experiment)
        work.mkdir(parents=True, exist_ok=True)
        logs: list[str] = []

        def _progress(msg: str) -> None:
            if progress is not None:
                progress(msg)

        converted = True
        # 0.2.165: only a real conversion emits "starting fid conversion"; when a
        # converted product is reused, "reusing converted fid" is emitted instead, so
        # repeated process calls (preview/joint/candidate) do not keep showing a
        # misleading conversion progress
        if experiment.segments:
            merged_in = self._merged_fid_in(
                work, experiment.dataset_id
            )
            merged_ready = merged_in is not None
            in_file = merged_in or f"merged/{experiment.dataset_id}.fid"
            if (
                merged_ready
                and not (params or {}).get("segment_shift_hz")
                and self._converted_fid_is_current(
                    work,
                    experiment.dataset_id,
                    [Path(seg) for seg in experiment.segments],
                    logs,
                    require_field_drift=True,
                )
            ):
                _progress(tr("Reuse converted fid (skip conversion)"))
            else:
                _progress(tr("Start converting fid"))
                # 0.2.166: segment shift and NUS alignment (with segment_shift_hz a
                # fresh conversion is mandatory)
                shifts = [
                    float(v)
                    for v in (params or {}).get("segment_shift_hz", [])
                ]
                converted, convert_logs = self._convert_segments(
                    runtime, experiment, work, shifts
                )
                logs += convert_logs
                if converted:
                    self._record_conversion(
                        work,
                        experiment.dataset_id,
                        [Path(seg) for seg in experiment.segments],
                        logs,
                    )
        else:
            in_file = f"{experiment.dataset_id}.fid"
            if (work / in_file).is_file() and self._converted_fid_is_current(
                work, experiment.dataset_id, raw, logs
            ):
                _progress(tr("Reuse converted fid (skip conversion)"))
            else:
                _progress(tr("Start converting fid"))
                converted, convert_logs = self._convert(runtime, experiment, raw, work)
                logs += convert_logs
                # 0.2.81: bruker slice-style output (fid/test%03d.fid, when the 3D
                # TD is right); process also runs through the slice stream (as NUS
                # does)
                if not (work / in_file).is_file():
                    slice_dir = work / "fid"
                    slice_in = _slice_in_file(slice_dir, experiment.dataset_id)
                    if slice_in:
                        in_file = slice_in
                        logs.append(tr(
                            "Use bruker sliced fid ({p0}, streaming "
                            "processing)",
                            p0=slice_in,
                        ))
                self._record_conversion(work, experiment.dataset_id, raw, logs)
        if not converted:
            return {"success": False, "message": (
                tr(
                "Bruker -> NMRPipe conversion "
                "failed",
            )
            ), "logs": logs}
        proc_params = dict(params or {})
        sampling = proc_params.get("sampling") or {}
        # sampling.auto_phase=False -> switch off the automatic direct-dimension
        # phase (PS keeps the plan default 0/0)
        # 0.2.88: the check moved ahead of the search (it used to be set after the
        # search, so automatic phase could not actually be switched off)
        # 0.2.167: only effective when connecting straight through the route=none
        # escape hatch; the unified automatic path decides from the experiment
        # type, presets processing_hints.auto_phase (see phase_routes)
        if sampling.get("auto_phase") is False:
            direct_phase_search = False
        direct_phase: dict[str, tuple[float, float]] | None = None
        # 0.2.106: per-dimension complex preview mode -- only the preview_axis PS
        # omits -di (other axes add -di with the phase fixed by
        # direct_phase_override), zeros stay zero; preview data keeps PS(0,0) and
        # the direct-dimension phase search is switched off
        preview_axis = proc_params.get("preview_axis")
        if preview_axis:
            direct_phase_search = False
        # 0.2.160: the first complex preview pass adds no POLY -time (it would bias
        # the direct-dimension phase search); POLY -time goes only into the full
        # final script (direct_poly_time in params_final)
        direct_poly_time = _as_bool(proc_params.get("direct_poly_time", False))
        if preview_axis:
            direct_poly_time = False
        if direct_phase_override:
            direct_phase = dict(direct_phase_override)
            logs.append(tr("direct dimension phase coverage: {p0}", p0=direct_phase))
        elif direct_phase_search:
            _progress(
                tr("Start phase optimisation (1D)") if experiment.ndim == 1
                else tr("Start phase optimisation (direct dimension)")
            )
            phase_inputs: Path | list[Path]
            if experiment.segments:
                phase_inputs = work / "seg_001" / f"{experiment.dataset_id}.fid"
            else:
                slice_dir = work / "fid"
                slices = (
                    sorted(slice_dir.glob("test*.fid"))
                    if slice_dir.is_dir()
                    else []
                )
                if slices:
                    phase_inputs = slices
                    logs.append(
                        tr("Sliced fid: direct dimension phase search {p0} slices", p0=len(slices))
                    )
                else:
                    phase_inputs = work / f"{experiment.dataset_id}.fid"
            if isinstance(phase_inputs, Path) and not phase_inputs.is_file():
                logs.append(tr(
                    "direct-dimension phase search: no fid/slice found, keeping "
                    "p0=p1=0",
                ))
            else:
                p0, p1 = self._search_direct_phase(
                    work,
                    phase_inputs,
                    logs,
                    is_nus=False,
                    is_1d=(experiment.ndim == 1),
                )
                direct_axis = "F2" if experiment.ndim <= 2 else "F3"
                direct_phase = {direct_axis: (p0, p1)}
                if experiment.ndim == 1:
                    _progress(tr(
                        "Complete 1D phase optimisation: p0={p0:g}° "
                        "p1={p1:g}°",
                        p0=p0,
                        p1=p1,
                    ))
                else:
                    _progress(
                        tr(
                            "Complete phase optimisation (direct dimension {p0} p0={p1:g}° "
                            "p1={p2:g}°)",
                            p0=direct_axis,
                            p1=p0,
                            p2=p1,
                        )
                    )
        extract = _as_bool(proc_params.get("extract", experiment.ndim > 1))
        ext_lo = resolve_ext_lo(proc_params.get("ext_lo"))
        ext_hi = resolve_ext_hi(proc_params.get("ext_hi"))
        baseline = expand_baseline(experiment, proc_params.get("baseline"))
        window = proc_params.get("window")
        zero_fill = proc_params.get("zero_fill")
        linewidth_hz = proc_params.get("linewidth_hz")
        points_per_line = resolve_points_per_line(
            proc_params.get("points_per_line")
        )
        zf_plan = zero_fill_plan(
            experiment,
            zero_fill,
            linewidth_hz=linewidth_hz,
            points_per_line=points_per_line,
        )
        logs += zero_fill_report(zf_plan)
        processed, process_logs, spectrum = self._process(
            runtime,
            experiment,
            plan,
            work,
            in_file=in_file,
            direct_phase=direct_phase,
            baseline=baseline,
            window=window,
            zero_fill=zf_plan,
            linewidth_hz=linewidth_hz,
            points_per_line=points_per_line,
            extract=extract,
            ext_lo=ext_lo,
            ext_hi=ext_hi,
            sampling=sampling,
            progress=progress,
            out_file=out_file,
            script_name=script_name,
            keep_direct_complex=_as_bool(proc_params.get("keep_direct_complex", False)),
            keep_complex_all=_as_bool(proc_params.get("keep_complex_all", False)),
            preview_axis=preview_axis,
            direct_poly_time=direct_poly_time,
        )
        logs += process_logs
        if not processed:
            return {"success": False, "message": tr("NMRPipe processing failed"), "logs": logs}
        _progress(tr("final spectrum is in place"))
        return {
            "success": True,
            "message": tr("NMRPipe processed successfully"),
            "spectrum_path": str(spectrum),
            "logs": logs,
            "effective_params": {
                **_effective_params_base(
                    extract,
                    ext_lo,
                    ext_hi,
                    zf_plan,
                    baseline,
                    linewidth_hz,
                    points_per_line,
                    sampling,
                ),
                "window": window,
                "direct_phase": direct_phase,
                "direct_poly_time": direct_poly_time,
            },
        }

    def convert_to_fid(
        self,
        experiment: Experiment,
        data_dir: Path | str,
        progress: Callable[[str], None] | None = None,
        fid_com_overrides: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Standalone stage: bruker -AUTO/fid.com converts raw data into an NMRPipe
        fid (no spectrum is produced).

        fid_com_overrides: manual-route parameter overrides (0.2.163-patch13),
        applied segment by segment for segmented data; conversion/slicing/merging/
        bad-point cleanup still follow the automatic path.
        Returns the stable keys {success, fid_path, message, logs}
        (API_CONTRACT 8.3); the stepwise "generate FID" flow calls this, and
        process/reconstruct_nus reuses its result.
        """
        bin_dir = self._bin_dir()
        if bin_dir is None:
            return {
                "success": False,
                "message": tr("nmrPipe not found (csh: which nmrPipe)"),
                "logs": [],
            }
        runtime = CshRuntime()
        raw = Path(data_dir)
        work = self._work_path(experiment)
        work.mkdir(parents=True, exist_ok=True)
        logs: list[str] = []

        # 0.2.199-patch29dz (user): report missing key input files right away so
        # bruker cannot hang or stall
        def _missing_input(seg_dir: Path) -> str:
            if not (seg_dir / "acqus").is_file():
                return f"{seg_dir}/acqus"
            has_ser = (seg_dir / "ser").is_file()
            # 0.2.199-patch29gj-fix: a 1D Bruker data file is fid (a file), not ser/
            # or a fid directory
            has_fid_file = (seg_dir / "fid").is_file()
            has_fid_dir = (
                any((seg_dir / "fid").glob("*.fid"))
                if (seg_dir / "fid").is_dir()
                else False
            )
            if not has_ser and not has_fid_file and not has_fid_dir:
                return f"{seg_dir}/fid" if experiment.ndim == 1 else f"{seg_dir}/ser"
            return ""

        if experiment.segments:
            for _seg in [Path(s) for s in experiment.segments]:
                _miss = _missing_input(_seg)
                if _miss:
                    return {
                        "success": False,
                        "message": tr("Missing input file: {p0}", p0=_miss),
                        "logs": logs,
                    }
        else:
            _miss = _missing_input(raw)
            if _miss:
                return {
                    "success": False,
                    "message": tr("Missing input file: {p0}", p0=_miss),
                    "logs": logs,
                }
        segment_kind: str | None = None
        if progress is not None:
            progress(tr("Start converting fid"))
        if experiment.segments:
            # 0.2.199-patch29cv: identify repeated-experiment co-addition/segmentation
            # and note the TopSpin fidadd time-domain sum in the log; a failed
            # classification does not block the conversion
            segment_kind, kind_logs = _segment_kind_info(experiment.segments)
            logs += kind_logs
            # 0.2.124: bad points are deleted from the source ser/nuslist with a
            # backup kept (user request)
            audit = QcAuditLog(work)
            _count, _bad, source_removed = self._clean_source_nus(
                experiment, [Path(s) for s in experiment.segments], logs
            )
            _record_source_clean(
                audit, experiment, list(experiment.segments), _count, _bad, source_removed
            )
            if _bad and source_removed:
                # 0.2.197/0.2.199: segmented data likewise adjusts the grid to the
                # actual range of the cleaned, merged nuslist, so cross-validation
                # (parameter correction) no longer restores each segment grid to the
                # static NusTD
                merged_points: list[tuple[int, ...]] = []
                for seg in experiment.segments:
                    merged_points += [
                        tuple(p) for p in read_nuslist(Path(seg) / "nuslist")
                    ]
                logs += _apply_nus_grid_after_clean(
                    experiment, merged_points, audit=audit
                )
            converted, convert_logs = self._convert_segments(
                runtime, experiment, work, [], fid_com_overrides=fid_com_overrides
            )
            logs += convert_logs
            merged_in = self._merged_fid_in(
                work, experiment.dataset_id
            )
            if merged_in and merged_in.endswith(".fid"):
                fid_path = work / merged_in
            elif merged_in:
                fid_path = work / "merged" / "fid"
            else:
                fid_path = work / "merged"
            if converted:
                # 2026-09-23: the "generate FID" step records the conversion provenance
                # (including the field drift check) right away - "generate spectrum" can then
                # reuse it instead of converting a second time because a record is missing
                self._record_conversion(
                    work,
                    experiment.dataset_id,
                    [Path(s) for s in experiment.segments],
                    logs,
                )
                _count, bad_points = self._write_merged_nuslist(
                    work, experiment.segments, experiment, logs
                )
                if bad_points:
                    # when source-level deletion is not possible, fall back to
                    # clearing the generated FID
                    self._zero_bad_point_fid(
                        work,
                        bad_points,
                        logs,
                        dataset_id=experiment.dataset_id,
                        audit=audit,
                    )
        else:
            converted, convert_logs = self._convert(
                runtime, experiment, raw, work, fid_com_overrides=fid_com_overrides
            )
            logs += convert_logs
            fid_path = self._converted_fid_path(work, experiment.dataset_id)
        if not converted:
            return {
                "success": False,
                "message": tr("Bruker -> NMRPipe conversion failed"),
                "logs": logs,
            }
        logs.append(f"fid → {fid_path}")
        return {
            "success": True,
            "fid_path": str(fid_path),
            "message": tr("Conversion completed"),
            "logs": logs,
            "effective_params": {
                "dataset_id": experiment.dataset_id,
                "ndim": experiment.ndim,
                "segments": len(experiment.segments),
                "segment_kind": segment_kind,
                "work_dir": str(work),
                "fid_path": str(fid_path),
            },
        }

    def reconstruct_nus(
        self,
        experiment: Experiment,
        params: dict[str, Any] | None = None,
        progress: Callable[[str], None] | None = None,
        script_only: bool = False,
        out_file: str | None = None,
        script_name: str | None = None,
    ) -> dict[str, Any]:
        """NUS data: native bruker conversion (single or merged segments) plus
        SMILE reconstruction of the final spectrum.

        With script_only=True only the script text is generated and returned
        (0.2.199-patch29hz-fix3: the SMILE parameter sweep needs the script text
        first to decide how to run it); NMRPipe is not executed.

        out_file / script_name (2026-09-12, parameter sensitivity interface): when
        given explicitly they are treated as "candidate output" -- the script goes
        to ``work/<script_name>`` and the spectrum to
        ``work/_intermediate/<out_file>`` (the same convention as ``process()``),
        without overwriting the final spectrum in the working directory; the
        display-layer phase search/re-render is skipped in that case (candidates
        must lock the phase with ``params['direct_phase']``). The default
        behaviour is unchanged.
        """
        params = dict(params or {})
        # 0.2.199-patch29hz-fix17: the GUI passes the final-run range (final_ext_*),
        # mapped here onto ext_lo/ext_hi so SMILE optimisation/sweeps use the same
        # window as the final run (otherwise the default wide window returns and
        # "not enough memory" is reported spuriously)
        _ext_note = apply_final_ext_params(params)
        if experiment.sampling.mode is not SamplingMode.NUS:
            return {"success": False, "message": (
                tr(
                "For non-NUS data, please use "
                "process()",
            )
            ), "logs": []}
        bin_dir = self._bin_dir()
        if bin_dir is None:
            return {
                "success": False,
                "message": tr("nmrPipe not found (csh: which nmrPipe)"),
                "logs": [],
            }
        runtime = CshRuntime()
        raw = Path(experiment.source_path)
        work = self._work_path(experiment)
        work.mkdir(parents=True, exist_ok=True)
        logs: list[str] = []
        if _ext_note:
            logs.append(_ext_note)

        if experiment.segments:
            # 0.2.124: bad points are deleted from the source ser/nuslist with a
            # backup kept (user request)
            audit = QcAuditLog(work)
            _count, _bad, source_removed = self._clean_source_nus(
                experiment, [Path(s) for s in experiment.segments], logs
            )
            _record_source_clean(
                audit, experiment, list(experiment.segments), _count, _bad, source_removed
            )
            if _bad and source_removed:
                # 0.2.197: after bad points are removed the NusTD follows the actual
                # range of the cleaned nuslist, and cross-validation (parameter
                # correction) uses the adjusted value instead of restoring it
                merged_points: list[tuple[int, ...]] = []
                for seg in experiment.segments:
                    merged_points += [
                        tuple(p) for p in read_nuslist(Path(seg) / "nuslist")
                    ]
                logs += _apply_nus_grid_after_clean(
                    experiment, merged_points, audit=audit
                )
            merged_in = self._merged_fid_in(
                work, experiment.dataset_id
            )
            # 2026-09-22: reuse goes through the raw input fingerprint (see
            # _converted_fid_is_current) on every path. Source-level cleaning that
            # rewrites or empties raw always changes the fingerprint, so the old merged
            # product goes stale by itself -- no separate "cleaned this run" event flag
            # is needed, and the criterion is identical to process()'s segment branch.
            merged_ready = (
                merged_in is not None
                and (work / "nuslist").is_file()
                and not params.get("segment_shift_hz")  # a shift forces a re-conversion
                and self._converted_fid_is_current(
                    work,
                    experiment.dataset_id,
                    [Path(s) for s in experiment.segments],
                    logs,
                    require_field_drift=True,
                )
            )
            if not merged_ready:
                # 0.2.199-patch29cv: this path converts/merges on its own and notes
                # the multi-segment type as well
                _kind, kind_logs = _segment_kind_info(experiment.segments)
                logs += kind_logs
                shifts = [float(v) for v in params.get("segment_shift_hz", [])]
                converted, convert_logs = self._convert_segments(
                    runtime, experiment, work, shifts
                )
                logs += convert_logs
                if not converted:
                    return {
                        "success": False,
                        "message": tr("multi-segment NUS conversion/merge failed"),
                        "logs": logs,
                    }
                nuslist_count, bad_points = self._write_merged_nuslist(
                    work, experiment.segments, experiment, logs
                )
                if bad_points:
                    # when source-level deletion is not possible, fall back to
                    # clearing the generated FID
                    self._zero_bad_point_fid(
                        work,
                        bad_points,
                        logs,
                        dataset_id=experiment.dataset_id,
                        audit=audit,
                    )
                self._record_conversion(
                    work,
                    experiment.dataset_id,
                    [Path(s) for s in experiment.segments],
                    logs,
                )
            else:
                logs.append(tr("Reuse merged fid ({p0}; skipping conversion/merge)", p0=merged_in))
                nuslist_count = len(
                    (work / "nuslist").read_text(encoding="utf-8").splitlines()
                )
            in_file = self._merged_fid_in(
                work, experiment.dataset_id
            ) or f"merged/{experiment.dataset_id}.fid"
        else:
            # 0.2.124: bad points are deleted from the source ser/nuslist with a
            # backup kept (user request); this runs before the conversion
            audit = QcAuditLog(work)
            nuslist_count, bad_points, source_removed = self._clean_source_nus(
                experiment, [raw], logs
            )
            _record_source_clean(
                audit, experiment, [raw], nuslist_count, bad_points, source_removed
            )
            if bad_points and source_removed:
                # 0.2.197: after bad points are removed the NusTD follows the actual
                # range of the cleaned nuslist, and cross-validation (parameter
                # correction) uses the adjusted value instead of restoring it
                cleaned = [tuple(p) for p in read_nuslist(raw / "nuslist")]
                logs += _apply_nus_grid_after_clean(
                    experiment, cleaned, audit=audit
                )
            fid_file = work / f"{experiment.dataset_id}.fid"
            # 2026-09-22: reuse goes through the raw input fingerprint -- the old product
            # is dropped and re-converted only when the fingerprint changed (source-level
            # cleaning rewrote or emptied raw) or when the product itself is incomplete
            # (empty fid / size mismatch with the record / unreadable record). Projects
            # without a record are still reused, with an "unverified" note.
            if fid_file.is_file() and not self._converted_fid_is_current(
                work, experiment.dataset_id, raw, logs
            ):
                fid_file.unlink()
                stale_slice = work / "fid"
                if stale_slice.is_dir():
                    shutil.rmtree(stale_slice)
            raw_nuslist = raw / "nuslist"
            recovered_2d: list[int] | None = None
            if not raw_nuslist.is_file() and experiment.ndim == 2:
                # 0.2.199-patch29hz-fix12 (user): decide before converting -- running a
                # truly sparse file whose sampling positions are unknowable as dense
                # data hangs during conversion (measured: nmrPipe -fn MULT at 100%
                # CPU)
                recovered_2d = self._recover_dense_2d_nus(
                    raw, experiment, logs, fid_file=fid_file
                )
                if recovered_2d is None:
                    return {
                        "success": False,
                        "message": (
                            tr(
                                "missing nuslist sampling table: this 2D data set is not the "
                                "full-grid+zero-fill dense model, so the sampling positions cannot "
                                "be recovered; please provide "
                                "nuslist",
                            )
                        ),
                        "logs": logs,
                    }
            if not fid_file.is_file():
                converted, convert_logs = self._convert(runtime, experiment, raw, work)
                logs += convert_logs
                if not converted:
                    return {
                        "success": False,
                        "message": tr("NUS Conversion failed (bruker native recognition failed)"),
                        "logs": logs,
                    }
                self._record_conversion(work, experiment.dataset_id, raw, logs)
            if raw_nuslist.is_file():
                shutil.copy2(raw_nuslist, work / "nuslist")
                # Safety net: re-validate the working nuslist (0 bad points expected
                # once the source has been cleaned)
                nuslist_count, _leftover = self._clean_work_nuslist(
                    work, experiment, logs
                )
            elif recovered_2d is not None:
                # 0.2.199-patch29hz-fix12 (user): 2D data without a sampling schedule
                # but shaped as "full grid + zero fill" -- the sampling points are
                # recovered straight from the zero pattern (any subset, no longer
                # assuming "the first N complex points") and written to work/nuslist
                # for SMILE; the slice stream is still bypassed (2D is single-file).
                (work / "nuslist").write_text(
                    "\n".join(str(p) for p in recovered_2d) + "\n",
                    encoding="utf-8",
                )
                nuslist_count = len(recovered_2d)
                logs.append(
                    tr(
                        "2D NUS: No sampling schedule, restored from full grid zero mode {p0} "
                        "sampling complex "
                        "points",
                        p0=nuslist_count,
                    )
                )
            else:
                return {"success": False, "message": (
                    tr(
                    "Missing nuslist sampling "
                    "schedule",
                )
                ), "logs": logs}
            # 0.2.199-patch27: work with bruker's automatic output -- prefer the
            # single file and fall back to the slice stream (fid/test%03d.fid or
            # fid/{dataset_id}%03d.fid) when there is none; the nus script xyz2pipe
            # supports both inputs
            slice_dir = work / "fid"
            slice_in = _slice_in_file(slice_dir, experiment.dataset_id)
            if fid_file.is_file():
                in_file = fid_file.name
            elif slice_in:
                in_file = slice_in
                logs.append(tr("Use bruker sliced fid ({p0}, streaming processing)", p0=slice_in))
            else:
                in_file = fid_file.name  # fallback: conversion should have produced one of them
                logs.append(
                    tr(
                    "fid not found (neither a single file nor slices), the final run will "
                    "fail",
                )
                )
            if bad_points and not source_removed:
                # when source-level deletion is not possible (missing ser or a size
                # mismatch), fall back to clearing the generated FID
                self._zero_bad_point_fid(
                    work, bad_points, logs, in_file=in_file,
                    dataset_id=experiment.dataset_id,
                )

        direct_p0, direct_p1 = 0.0, 0.0
        override = direct_phase_override(params)
        if override is not None:
            direct_p0, direct_p1 = override
            logs.append(tr(
                "direct dimension phase coverage: p0={p0:g} "
                "p1={p1:g}",
                p0=direct_p0,
                p1=direct_p1,
            ))
        sampling = params.get("sampling") or {}
        direct_phase_search = bool(params.get("direct_phase_search", True))
        # 0.2.95: display-layer phase search (nmrDraw idea, on by default) -- scores
        # rotational symmetry in the frequency domain on the reconstructed final
        # spectrum; when enabled it skips NU-DFT/lightweight (unreliable/experimental)
        display_phase_search = bool(params.get("display_phase_search", True))
        light_phase = bool(params.get("light_phase_search", False))
        if sampling.get("auto_phase") is False:
            direct_phase_search = False
        # 0.2.94: lightweight SMILE phase search (experimental, off by default -- on
        # the VM reconstruction artefacts biased the fixed-trace score optimum:
        # 16/32-point subsampling -> F2 off by 55 deg)
        if (
            direct_phase_search
            and not display_phase_search
            and direct_phase_override(params) is None
            and light_phase
        ):
            light_result = self._light_phase_search(
                experiment,
                work,
                in_file,
                runtime,
                logs,
                params,
                nuslist_count=int(params.get("nuslist_count") or nuslist_count),
                sampling=sampling,
            )
            if light_result is not None:
                direct_p0, direct_p1 = light_result
                direct_phase_search = False
            else:
                logs.append(tr("Lightweight SMILE phase search failed, fallback NU-DFT"))
        if (
            direct_phase_search
            and not display_phase_search
            and not light_phase
            and direct_phase_override(params) is None
        ):
            phase_inputs: Path | list[Path]
            if experiment.segments:
                phase_inputs = work / "seg_001" / f"{experiment.dataset_id}.fid"
            else:
                slice_dir = work / "fid"
                slices = (
                    sorted(slice_dir.glob("test*.fid"))
                    if slice_dir.is_dir()
                    else []
                )
                if slices:
                    phase_inputs = slices
                    logs.append(
                        tr("Sliced fid: direct dimension phase search {p0} slices", p0=len(slices))
                    )
                else:
                    phase_inputs = work / f"{experiment.dataset_id}.fid"
            if isinstance(phase_inputs, Path) and not phase_inputs.is_file():
                logs.append(tr(
                    "direct-dimension phase search: no fid/slice found, keeping "
                    "p0=p1=0",
                ))
            else:
                _td = effective_td(experiment)
                direct_p0, direct_p1 = self._search_direct_phase(
                    work,
                    phase_inputs,
                    logs,
                    is_nus=True,
                    n_f1=int(_td[1]) if len(_td) > 1 else 0,
                    n_f2=int(_td[2]) if len(_td) > 2 else 1,
                    is_1d=(experiment.ndim == 1),
                )

        td = effective_td(experiment)
        if experiment.ndim >= 3:
            grid = max(int(td[1]) * int(td[2]), 1)
            ext = "ft3"
            script_fn = generate_3d_nus_script
        else:
            grid = max(int(td[1]), 1)
            ext = "ft2"
            script_fn = generate_2d_nus_script
        fraction = nuslist_count / grid if grid else 0.0
        tier_nsigma, tier_thresh = select_smile_params(fraction)
        # Parameter-key compatibility (2026-09-12): callers pass the lowercase
        # nsigma (smile_optimize/sweep scripts) while the run record writes back
        # effective_params' nSigma; both are accepted -- otherwise "use the reference
        # run parameters as the sweep basis" silently drops nSigma and different
        # combinations produce the same spectrum (found on real hardware through the
        # parameter sensitivity interface).
        _nsigma_raw = params.get("nsigma")
        if _nsigma_raw is None:
            _nsigma_raw = params.get("nSigma")
        nsigma = float(_nsigma_raw if _nsigma_raw is not None else tier_nsigma)
        thresh = float(params.get("thresh", tier_thresh))
        smile_scaling = bool(params.get("smile_scaling", True))
        smile_report = int(params.get("smile_report", 1))
        nthread = resolve_nthread(params.get("nthread"))
        # 0.2.113: threads are no longer limited by the grid -- the sampleM incident
        # came from direct-dimension memory (no slice stream / too much
        # direct-dimension zero fill), which the 0.2.112 memory guard now backstops
        ext_lo = resolve_ext_lo(params.get("ext_lo"))
        ext_hi = resolve_ext_hi(params.get("ext_hi"))
        extract = _as_bool(params.get("extract", True))
        baseline = expand_baseline(experiment, params.get("baseline"))
        zero_fill = params.get("zero_fill")
        linewidth_hz = params.get("linewidth_hz")
        points_per_line = resolve_points_per_line(params.get("points_per_line"))
        zf_plan = zero_fill_plan(
            experiment,
            zero_fill,
            linewidth_hz=linewidth_hz,
            points_per_line=points_per_line,
        )
        logs += zero_fill_report(zf_plan)
        # 0.2.112: memory guard -- SMILE peak estimate (0.2.199-patch15, aligned
        # with what SMILE reports itself: direct-dimension points x the product of
        # the indirect-dimension iterative FT sizes x 16B); above the limit the
        # direct-dimension zero fill is first reduced to 1x TD, and if it is still
        # too large an error is raised
        from backend.memory_guard import (
            MEM_SAFETY,
            available_memory_mb,
            direct_points_after_ext,
            estimate_smile_peak_mb,
        )

        direct_axis = (
            experiment.dimensions[0].logical_axis
            if experiment.dimensions
            else ""
        )
        zf_direct = int((zf_plan.get(direct_axis) or {}).get("size") or td[0])
        direct_pts = direct_points_after_ext(experiment, zf_direct, ext_lo, ext_hi)
        peak_mb = estimate_smile_peak_mb(
            experiment.ndim, direct_pts, td[1:]
        )
        avail_mb = available_memory_mb()
        if peak_mb > avail_mb * MEM_SAFETY:
            td0 = max(int(td[0]), 1)
            one_x = 1 << (td0 - 1).bit_length()  # next_pow2 of 1x TD
            if zf_direct > one_x:
                logs.append(
                    tr(
                        "Memory guard: peak value approx. {p0:.0f}MB > Available {p1}MB×{p2:.2f}, "
                        "direct dimension zero filling is reduced to 1 x "
                        "TD({p3}→{p4})",
                        p0=peak_mb,
                        p1=avail_mb,
                        p2=MEM_SAFETY,
                        p3=zf_direct,
                        p4=one_x,
                    )
                )
                if progress is not None:
                    progress(
                        tr(
                        "Out of memory: direct dimension zero filling has been reduced to 1 x TD "
                        "to reduce SMILE "
                        "memory",
                    ))
                zf_plan[direct_axis] = {
                    "mode": "size",
                    "size": one_x,
                    "note": tr("Memory guard: direct dimension zero filling reduced to 1 x TD"),
                }
                direct_pts = direct_points_after_ext(
                    experiment, one_x, ext_lo, ext_hi
                )
                peak_mb = estimate_smile_peak_mb(
                    experiment.ndim, direct_pts, td[1:]
                )
            if peak_mb > avail_mb * MEM_SAFETY:
                import math

                needed_gb = math.ceil(peak_mb / 1024.0)
                return {
                    "success": False,
                    "message": (
                        tr(
                            "The current memory cannot process this spectrum (about {p0} MB "
                            "available, SMILE peak about {p1:.0f} MB); please provide at least "
                            "{p2} GB. You can also narrow the direct-dimension range and turn on "
                            "\"apply this range to the optimisation\" (the narrower the "
                            "direct-dimension window, the lower the SMILE peak "
                            "memory)",
                            p0=avail_mb,
                            p1=peak_mb,
                            p2=needed_gb,
                        )
                    ),
                    "logs": logs,
                }
        # 0.2.199-patch14: set SMILE -maxMem from the currently available memory
        # (the same budget as the guard, so estimation error or concurrent use
        # cannot push the peak past the limit)
        max_mem_gb = max(avail_mb * MEM_SAFETY / 1024.0, 1.0)
        logs.append(
            tr(
                "SMILE Memory limit (-maxMem): {p0:.1f} GB({p1} MB "
                "available)",
                p0=max_mem_gb,
                p1=avail_mb,
            )
        )
        # 0.2.96: display-layer phase search (1x SMILE, no extra backend) -- the
        # main reconstruction runs with PS(0,0) (or the cached phase); afterwards the
        # symmetry is scored on the complex recon plane, and a final step rotates the
        # phase onto recon and cheaply re-renders stage-2 (no SMILE)
        smile_phase = (direct_p0, direct_p1)
        run_display_search = False
        if (
            display_phase_search
            and direct_phase_search
            and direct_phase_override(params) is None
        ):
            if (work / "phase.json").is_file():
                try:
                    data = json.loads(
                        (work / "phase.json").read_text(encoding="utf-8")
                    )
                    if data.get("version") == 2:
                        smile_phase = (float(data["p0"]), float(data["p1"]))
                        logs.append(
                            tr(
                                "direct dimension phase (cache): p0={p0:g} "
                                "p1={p1:g}",
                                p0=smile_phase[0],
                                p1=smile_phase[1],
                            )
                        )
                except (OSError, TypeError, ValueError, KeyError):
                    pass
            else:
                smile_phase = (0.0, 0.0)
                run_display_search = True
                logs.append(
                    tr(
                        "Display layer phase search: main reconstruction PS(0,0), symmetry score "
                        "after reconstruction",
                    )
                )

        # 0.2.162: SMILE optimisation stability -- a temporary copy of the input fid
        # gets a little injected noise so that repeated reconstructions with identical
        # parameters differ between runs (used to reject spurious peaks); it affects
        # only this reconstruction and is deleted afterwards
        fid_noise = float(params.get("fid_noise", 0.0) or 0.0)
        noise_seed = int(params.get("fid_noise_seed", 0) or 0)
        if fid_noise > 0:
            noisy_in = self._make_noisy_fid_input(
                work, in_file, fid_noise, noise_seed, logs
            )
            if noisy_in is not None:
                in_file = noisy_in
        candidate = out_file not in (None, "")
        if candidate:
            # Candidate output (API sweep): written into _intermediate/ following the
            # same convention as process(), which lets the ramdisk take over and keeps
            # the final spectrum untouched
            render_dir = work / INTERMEDIATE_SUBDIR
            render_dir.mkdir(parents=True, exist_ok=True)
            out_file = f"{INTERMEDIATE_SUBDIR}/{out_file}"
        else:
            out_file = f"{experiment.dataset_id}.{ext}"
        if candidate and run_display_search:
            logs.append(
                tr(
                    "candidate output mode: skip the display-layer phase search and re-render (the "
                    "phase must be locked by direct_phase, otherwise the candidate spectrum phase "
                    "disagrees with the "
                    "reference)",
                )
            )
            run_display_search = False
        script = script_fn(
            experiment,
            in_file=in_file,
            nuslist=str(
                params.get("nuslist_file")
                or ("nuslist" if (work / "nuslist").is_file() else "")
            ),
            out_file=out_file,
            nthread=nthread,
            nuslist_count=int(params.get("nuslist_count") or nuslist_count),
            ext_lo=ext_lo,
            ext_hi=ext_hi,
            nsigma=nsigma,
            thresh=thresh,
            smile_scaling=smile_scaling,
            smile_report=smile_report,
            max_mem=max_mem_gb,
            direct_phase=smile_phase,
            phases=params.get("phases"),
            window=params.get("window"),
            extract=extract,
            baseline=baseline,
            zero_fill=zf_plan,
            linewidth_hz=linewidth_hz,
            points_per_line=points_per_line,
            sampling=sampling,
            direct_poly_time=bool(params.get("direct_poly_time", False)),
        )
        nus_com = work / (script_name or f"{experiment.dataset_id}_nus.com")
        nus_com.write_text(script, encoding="utf-8", newline="\n")
        if script_only:
            return {
                "success": True,
                "message": tr("Generate only script (script_only)"),
                "logs": logs,
                "script": script,
                "script_path": str(nus_com),
                "work_dir": str(work),
            }
        logs.append(
            tr(
                "SMILE Refactor ({p0} sampling points, {p1:.1f}%,1H {p2}-{p3}ppm, nSigma={p4:g} "
                "thresh={p5:g})",
                p0=nuslist_count,
                p1=fraction * 100,
                p2=ext_lo,
                p3=ext_hi,
                p4=nsigma,
                p5=thresh,
            )
        )
        timeout = float(params.get("timeout_s", 3600))
        # Note: do not layer nice inside the tcsh -c wrapper (measured: it makes the
        # tcsh script hang and spin after finishing)
        if progress is not None:
            progress(tr("Start SMILE refactoring"))
        run_result = runtime.run(
            ["csh", nus_com.name],
            cwd=str(work),
            timeout=timeout,
            on_line=(lambda line: progress(line) if progress else None),
        )
        logs.append(f"nus.com: rc={run_result.returncode}")
        if fid_noise > 0:
            shutil.rmtree(work / f".smile_noise_{noise_seed}", ignore_errors=True)
        # 0.2.199-patch11: a SMILE internal error (e.g. an unapodised direct
        # dimension) can still leave rc=0 in a csh pipeline, so the output is checked
        # explicitly to avoid treating a failed reconstruction as a spectrum
        smile_err = "SMILE Error" in (
            (run_result.stdout or "") + (run_result.stderr or "")
        )
        if smile_err:
            logs.append(
                tr(
                    "SMILE internal error detected (direct dimension not windowed or bad input "
                    "state), reconstruction "
                    "failed",
                )
            )
            return {
                "success": False,
                "message": (
                    tr(
                    "SMILE Reconstruction failed (internal error: direct dimension needs to be "
                    "windowed)",
                )
                ),
                "logs": logs,
            }
        spectrum = work / out_file
        if (
            run_result.returncode != 0
            or not spectrum.is_file()
            or spectrum.stat().st_size == 0
        ):
            return {
                "success": False,
                "message": tr("SMILE reconstruction failed / nothing written to {p0}", p0=out_file),
                "logs": logs,
            }
        try:
            atomic_write_text(
                (work / ".nus_params.json"),
                json.dumps(params, ensure_ascii=False, indent=2),
            )
        except OSError:
            pass  # a failed parameter-fingerprint write does not affect the reconstruction
        logs.append(tr("final spectrum -> {p0}", p0=spectrum))
        # 0.2.96: the final step fills in the phase (display-layer search + recon
        # rotation + cheap finalize re-render)
        if run_display_search:
            est = self._display_phase_search(experiment, work, logs)
            if est is not None and est[2] >= 30.0:
                p0, p1, score = est
                logs.append(
                    tr(
                        "Display layer phase: F2=({p0:g}, {p1:g}) "
                        "score={p2:.2f}",
                        p0=p0,
                        p1=p1,
                        p2=score,
                    )
                )
                # 0.2.199-patch22: relaxed the p1 zeroing guard (20 -> 170; genuinely
                # large p1 occurs at high field)
                if abs(p1) > 170.0:
                    logs.append(
                        tr(
                            "Display layer phase p1={p0:g}° Abnormal amplitude (>170°), reset to "
                            "zero",
                            p0=p1,
                        )
                    )
                    p1 = 0.0
                atomic_write_text(
                    (work / "phase.json"),
                    json.dumps(
                        {
                            "version": 2,
                            "source": "display_recon",
                            "p0": p0,
                            "p1": p1,
                            "score": score,
                        },
                        indent=2,
                    ),
                )
                if (
                    abs(((p0 + 180.0) % 360.0) - 180.0) > 2.0
                    or abs(p1) > 2.0
                ):
                    if self._apply_direct_phase(
                        experiment, work, p0, p1, logs
                    ):
                        logs.append(
                            tr(
                                "final spectrum has been re-rendered according to the display "
                                "layer phase (stage-2 re-run, no "
                                "SMILE)",
                            )
                        )
            else:
                logs.append(
                    tr(
                    "The display layer phase has insufficient confidence or the search failed. "
                    "Keep the default "
                    "phase",
                ))
        if progress is not None:
            progress(tr("SMILE refactoring completed; final spectrum in place"))
        return {
            "success": True,
            "message": tr("SMILE Reconstruction successful"),
            "spectrum_path": str(spectrum),
            "logs": logs,
            "effective_params": {
                **_effective_params_base(
                    extract,
                    ext_lo,
                    ext_hi,
                    zf_plan,
                    baseline,
                    linewidth_hz,
                    points_per_line,
                    sampling,
                ),
                "nSigma": nsigma,
                "thresh": thresh,
                "smile_scaling": smile_scaling,
                "smile_report": smile_report,
                "nthread": nthread,
                "direct_phase": [direct_p0, direct_p1],
                "linewidth_hz": linewidth_hz,
                "points_per_line": points_per_line,
                "sampling": dict(sampling),
            },
        }

    def _make_noisy_fid_input(
        self, work: Path, in_file: str, noise_scale: float, seed: int, logs: list[str]
    ) -> str | None:
        """Copy the input fid (single file or slices) into a temporary directory and
        inject Gaussian noise.

        Returns the new in_file relative path; None on failure (the main flow is
        unaffected). SMILE is a deterministic algorithm, so identical parameters
        give bit-identical reconstructions; injecting a little measurement noise
        makes repeated reconstructions differ, which lets the SMILE optimisation
        reject spurious peaks through peak stability (0.2.162)."""
        import numpy as np

        rng = np.random.default_rng(seed)
        tmp = work / f".smile_noise_{seed}"
        if in_file.startswith("fid/"):
            src_dir = work / "fid"
            if not src_dir.is_dir():
                return None
            try:
                tmp.mkdir(parents=True, exist_ok=True)
                for src in sorted(src_dir.glob("test*.fid")):
                    self._write_noisy_fid(src, tmp / src.name, noise_scale, rng)
                return f".smile_noise_{seed}/test%03d.fid"
            except Exception as exc:  # noqa: BLE001
                logs.append(tr("fid noise injection (slicing) failed: {p0}", p0=exc))
                return None
        src = work / in_file
        if not src.is_file():
            return None
        try:
            tmp.mkdir(parents=True, exist_ok=True)
            target = tmp / src.name
            self._write_noisy_fid(src, target, noise_scale, rng)
            return f".smile_noise_{seed}/{src.name}"
        except Exception as exc:  # noqa: BLE001
            logs.append(tr("fid noise injection (single file) failed: {p0}", p0=exc))
            return None

    @staticmethod
    def _write_noisy_fid(
        src: Path, target: Path, noise_scale: float, rng: Any
    ) -> None:
        """Read by the nmrPipe fid byte layout, inject noise, write the copy back
        (real/imaginary blocks)."""
        import nmrglue as ng
        import numpy as np

        raw = src.read_bytes()
        dic, data = ng.pipe.read(str(src))
        arr = np.asarray(data).astype(np.complex64)
        fdsize = int(float(dic["FDSIZE"]))
        specnum = int(float(dic["FDSPECNUM"]))
        header_len = next(
            (
                header
                for header in (512, 1024, 2048)
                if len(raw) == header + specnum * fdsize * 8
            ),
            None,
        )
        if header_len is None or arr.shape != (specnum, fdsize):
            raise ValueError(tr("fid layout cannot be parsed"))
        noisy = arr.copy()
        for row in range(specnum):
            sigma = float(np.std(np.imag(arr[row]))) if fdsize > 1 else 0.0
            if sigma <= 0:
                continue
            noisy[row] = arr[row] + rng.normal(
                0.0, sigma * noise_scale, fdsize
            ) + 1j * rng.normal(0.0, sigma * noise_scale, fdsize)
        out = bytearray(raw[:header_len])
        for row in range(specnum):
            re = np.ascontiguousarray(noisy[row].real, dtype="<f4")
            im = np.ascontiguousarray(noisy[row].imag, dtype="<f4")
            out += re.tobytes() + im.tobytes()
        target.write_bytes(bytes(out))

    def smile_scan(
        self,
        experiment: Experiment,
        params: dict[str, Any] | None = None,
        combos: list[dict[str, Any]] | None = None,
        *,
        work_dir: Path | str,
        evaluate: Callable[[str], dict[str, Any]] | None = None,
        progress: Callable[[int, int, str], None] | None = None,
        delete_spectra: bool = True,
        holdout_ratio: float = 0.0,
    ) -> dict[str, Any]:
        """SMILE parameter sweep: the direct dimension runs once, then every
        candidate is evaluated and deleted (0.2.199-patch29hz-fix3).

        The final-run script is the template and only the SMILE parameters change:
        (1) run the direct-dimension part once to obtain the slice files; (2) run
        "SMILE + indirect dimension" once per parameter set to obtain a final
        spectrum; (3) evaluate(path) collects the metrics; (4) delete that spectrum
        immediately. Candidates are named individually, never overwrite each other,
        and leave the final-run scripts under process/ untouched. Returns
        {success, message, logs, candidates}.
        """
        from backend.script_generator import (
            build_2d_direct_only_script,
            rename_nus_scan_output,
            split_nus_script,
        )

        runtime = CshRuntime()
        combos = list(combos or [])
        base = dict(params or {})
        # 0.2.199-patch29hz-fix11: the holdout set is for **scoring** only -- the
        # script handed to the ranking table / re-runs must be the fully sampled
        # baseline (base_prod), otherwise the Rank1 script would reference
        # nuslist_train, which exists only in the sweep directory (measured on the
        # VM: re-running by Rank1 gave rc=2, sampling schedule missing)
        base_prod = dict(base)
        holdout_applied = False
        scan_dir = Path(work_dir)
        scan_dir.mkdir(parents=True, exist_ok=True)
        logs: list[str] = []
        holdout_file = ""
        # 3D uses (k0, k1); 2D uses the single-column complex-point index (k,)
        # (0.2.199-patch29hz-fix10)
        holdout_coords: list[tuple[int, ...]] = []
        old_work_dir = self.work_dir
        self.work_dir = str(scan_dir)
        if holdout_ratio and float(holdout_ratio) > 0:
            # Option A (0.2.199-patch29hz-fix5): hold out part of the **acquired**
            # sampling points and reconstruct from the remaining ones only; the
            # held-out points give a data-consistency residual (no fully sampled
            # reference needed). The nuslist in the sweep directory is produced or
            # copied in by reconstruct_nus; dense 2D has no sampling-schedule file,
            # so one script-generation run writes it out first
            # (0.2.199-patch29hz-fix10)
            def _find_nuslist() -> Path | None:
                for _cand in (
                    Path(str(experiment.source_path)) / "nuslist",
                    scan_dir / "nuslist",
                ):
                    if _cand.is_file():
                        return _cand
                return None

            src = _find_nuslist()
            if src is None:
                self.reconstruct_nus(experiment, dict(base), script_only=True)
                src = _find_nuslist()
            src = src or (scan_dir / "nuslist")
            if src.is_file():
                lines = [
                    ln.strip()
                    for ln in src.read_text(encoding="utf-8").splitlines()
                    if ln.strip() and not ln.strip().startswith("#")
                ]
                step = max(2, int(round(1.0 / float(holdout_ratio))))
                holdout = [ln for i, ln in enumerate(lines) if i % step == step - 1]
                train = [ln for i, ln in enumerate(lines) if i % step != step - 1]
                if train and holdout:
                    train_path = scan_dir / "nuslist_train"
                    holdout_path = scan_dir / "nuslist_holdout"
                    train_path.write_text("\n".join(train) + "\n", encoding="utf-8")
                    holdout_path.write_text("\n".join(holdout) + "\n", encoding="utf-8")
                    holdout_file = str(holdout_path)
                    base["nuslist_file"] = "nuslist_train"
                    base["nuslist_count"] = len(train)
                    holdout_applied = True
                    logs.append(
                        tr(
                            "Leave sampling point: train={p0} "
                            "holdout={p1}",
                            p0=len(train),
                            p1=len(holdout),
                        )
                    )
                    # A 2D nuslist has one column (complex-point indices), 3D has
                    # two; both are accepted and the column count decides how they are
                    # used later (0.2.199-patch29hz-fix10)
                    holdout_coords = [
                        tuple(int(v) for v in ln.split()[:2])
                        for ln in holdout
                        if ln.split()
                    ]
        try:
            scripts: list[str] = []
            prod_scripts: list[str] = []
            for combo in combos:
                resp = self.reconstruct_nus(
                    experiment, {**base, **combo}, script_only=True
                )
                if not resp.get("success") or not resp.get("script"):
                    return {
                        "success": False,
                        "message": str(resp.get("message", tr("Unable to generate scan script"))),
                        "logs": logs + list(resp.get("logs", [])),
                        "candidates": [],
                    }
                scripts.append(str(resp["script"]))
                logs.extend(str(line) for line in resp.get("logs", []))
                if holdout_applied:
                    prod = self.reconstruct_nus(
                        experiment, {**base_prod, **combo}, script_only=True
                    )
                    prod_scripts.append(
                        str(prod.get("script") or resp["script"])
                    )
                else:
                    prod_scripts.append(str(resp["script"]))
            prefix, _ = split_nus_script(scripts[0])
            # No slice split point (2D single-file script) -> fallback: run the whole
            # script per combination, each writing its own output name
            split_available = bool(prefix)
            timeout = float(base.get("timeout_s", 7200))
            # 2D holdout residual (0.2.199-patch29hz-fix10): 2D has no slice stream,
            # so the direct-dimension part cannot be split off; instead the pipeline
            # is run once "up to the direct dimension" to archive the SMILE input
            direct_2d_file = ""
            if holdout_file and experiment.ndim == 2 and scripts:
                direct_script = build_2d_direct_only_script(scripts[0])
                if not direct_script:
                    # fix24 (issue 7): do not skip silently -- the holdout_* columns
                    # of the ranking table would stay empty
                    logs.append(
                        tr(
                            "2D hold-out residual: cannot extract the direct-dimension block from "
                            "the final script (not a TP row before SMILE); skipping the hold-out "
                            "metric",
                        )
                    )
                if direct_script:
                    task = scan_dir / "step1_direct2d.com"
                    task.write_text(
                        direct_script, encoding="utf-8", newline="\n"
                    )
                    run0 = runtime.run(
                        ["csh", task.name], cwd=str(scan_dir), timeout=timeout
                    )
                    logs.append(tr("step1 2D direct dimension: rc={p0}", p0=run0.returncode))
                    if run0.returncode == 0:
                        direct_2d_file = str(scan_dir / "nus2d" / "direct.ft1")
            if split_available:
                step1 = scan_dir / "step1_direct.com"
                step1.write_text(prefix, encoding="utf-8", newline="\n")
                if progress is not None:
                    progress(0, len(combos), tr(
                        "direct dimension processing (generating "
                        "slices)..",
                    ))
                run1 = runtime.run(
                    ["csh", step1.name], cwd=str(scan_dir), timeout=timeout
                )
                logs.append(tr("step1 direct dimension: rc={p0}", p0=run1.returncode))
                if run1.returncode != 0:
                    return {
                        "success": False,
                        "message": tr(
                            "direct dimension processing failed "
                            "(rc={p0})",
                            p0=run1.returncode,
                        ),
                        "logs": logs,
                        "candidates": [],
                    }
            out_ext = {1: "ft1", 2: "ft2"}.get(experiment.ndim, "ft3")
            candidates: list[dict[str, Any]] = []
            # Option A (0.2.199-patch29hz-fix6): data-consistency residual of the
            # held-out sampling points.
            # fix23 (user): the caller passes holdout_ratio only under the
            # "consistency first" criterion, in which case the candidates are
            # reconstructed with the holdout (train) script and the measured and
            # reconstructed values are compared at the held-out points here.
            # The index mapping was established from real-data correlation
            # (scale=1.0/offset=0):
            #   held-out (k0,k1) -> in-plane [k1, k0] (plane = direct-dimension
            #   points, the two axes = indirect dimensions)
            if holdout_file and holdout_coords:
                def _holdout_residual() -> dict[str, float]:
                    import nmrglue as ng
                    import numpy as np

                    meas: list[complex] = []
                    pred: list[complex] = []
                    units = 0
                    if experiment.ndim >= 3:
                        acq_dir, rc_dir, name = (
                            scan_dir / "nus3d_1",
                            scan_dir / "nus3d_rc",
                            "test%04d.ft1",
                        )
                        # 2026-09-22: the plane list follows the slices this run actually
                        # produced instead of a fixed 1..1202 / step 40 (a spectrum of
                        # another size used to leave the hold-out metric silently empty).
                        available = sorted(
                            int(path.stem[4:])
                            for path in acq_dir.glob("test*.ft1")
                            if path.stem[4:].isdigit()
                        )
                        step = max(1, len(available) // 30)
                        planes = available[::step][:30]
                        if not planes:
                            logs.append(
                                tr(
                                    "Hold-out residual: no plane was found under {p0}; "
                                    "the hold-out metric is skipped",
                                    p0=acq_dir.name,
                                )
                            )
                        units = 0
                        for p in planes:
                            fa = acq_dir / (name % p)
                            fr = rc_dir / (name % p)
                            if not (fa.is_file() and fr.is_file()):
                                continue
                            units += 1
                            _da, A = ng.pipe.read(str(fa))
                            _dr, R = ng.pipe.read(str(fr))
                            A = np.asarray(A)
                            R = np.asarray(R)
                            for (k0, k1) in holdout_coords:
                                if not (
                                    0 <= k1 < A.shape[0]
                                    and 0 <= k0 < A.shape[1]
                                    and 0 <= k1 < R.shape[0]
                                    and 0 <= k0 < R.shape[1]
                                ):
                                    continue
                                meas.append(complex(A[k1, k0]))
                                pred.append(complex(R[k1, k0]))
                    elif direct_2d_file:
                        # 2D: no slice stream -> the measured mapping between the
                        # direct-dimension archive array and the SMILE output is
                        # "complex point k -> row 2k (real) / 2k+1 (imaginary) <->
                        # column k of recon"
                        _dd, A2 = ng.pipe.read(direct_2d_file)
                        _dr2, R2 = ng.pipe.read(
                            str(scan_dir / "nus2d" / "recon.ft1")
                        )
                        A2 = np.asarray(A2)
                        R2 = np.asarray(R2)
                        for (k,) in holdout_coords:
                            if 2 * k + 1 >= A2.shape[0] or k >= R2.shape[1]:
                                continue
                            m_vec = A2[2 * k] + 1j * A2[2 * k + 1]
                            p_vec = R2[:, k]
                            n = min(m_vec.shape[0], p_vec.shape[0])
                            meas.extend(complex(v) for v in m_vec[:n])
                            pred.extend(complex(v) for v in p_vec[:n])
                            units += 1
                    else:
                        return {}
                    if len(meas) < 10:
                        return {}
                    m = np.array(meas)
                    q = np.array(pred)
                    scale = float(np.sqrt(np.mean(np.abs(m) ** 2))) or 1.0
                    resid = np.abs(q - m) / scale
                    denom = float(np.linalg.norm(m) * np.linalg.norm(q))
                    corr = (
                        float(abs(np.vdot(m, q)) / denom) if denom else 0.0
                    )
                    return {
                        "holdout_rmse": round(float(np.median(resid)), 4),
                        "holdout_rmse_p90": round(float(np.percentile(resid, 90)), 4),
                        "holdout_corr": round(corr, 4),
                        "holdout_points": len(meas),
                        "holdout_planes": units,
                    }

            def _scan_suffix(script_text: str, out_name: str) -> str:
                """Turn a candidate script into the suffix used for this run (the
                slice form takes only the SMILE + indirect-dimension part)."""
                part = (
                    split_nus_script(script_text)[1]
                    if split_available
                    else script_text
                )
                return rename_nus_scan_output(part, out_name)

            for index, (combo, script) in enumerate(
                zip(combos, scripts), start=1
            ):
                tag = f"cand{index:02d}"
                if progress is not None:
                    progress(index, len(combos), tr(
                        "scanning {p0}/{p1}: "
                        "{p2}",
                        p0=index,
                        p1=len(combos),
                        p2=combo,
                    ))
                metrics: dict[str, Any] = {}
                _smile_log = scan_dir / "smile.log"

                # The caller decides how to run from the **ranking criterion** (user
                # 2026-09-11: "pick a full run or a holdout according to the ranking
                # method you need, instead of running twice"):
                #   net true peaks first -> the incoming script is the fully sampled
                #   one (peak count = final-spectrum criterion);
                #   consistency first -> the incoming script carries nuslist_train
                #   (the held-out points take no part in the reconstruction).
                task = scan_dir / f"step2_{tag}.com"
                task.write_text(
                    _scan_suffix(script, f"{tag}.{out_ext}"),
                    encoding="utf-8",
                    newline="\n",
                )
                # SMILE rewrites smile.log every round (measured on the VM: after three
                # consecutive rounds the file held only the 1024 lines of the last
                # one); the old log is deleted first so a failing round cannot read the
                # previous round's metrics (fix10)
                _smile_log.unlink(missing_ok=True)
                run2 = runtime.run(
                    ["csh", task.name], cwd=str(scan_dir), timeout=timeout
                )
                spectrum = scan_dir / f"{tag}.{out_ext}"
                ok = (
                    run2.returncode == 0
                    and spectrum.is_file()
                    and spectrum.stat().st_size > 0
                )
                # 0.2.199-patch29hz-fix6: SMILE's per-plane RMS report -> goodness of
                # fit for the training points (median of FINAL/INITIAL; no
                # plane-to-grid mapping needed, so it is comparable across parameters)
                try:
                    _tail = _smile_log.read_text(encoding="utf-8", errors="ignore")
                    # Handle NMRPipe builds that append: keep the last run block only
                    _marker = _tail.rfind("SMILE Version")
                    if _marker > 0:
                        _tail = _tail[_marker:]
                    _ratios: list[float] = []
                    for _line in _tail.splitlines():
                        if "INITIAL_RMS" not in _line or "FINAL_RMS" not in _line:
                            continue
                        try:
                            _ini = float(_line.split("INITIAL_RMS")[1].split()[0])
                            _fin = float(_line.split("FINAL_RMS")[1].split()[0])
                        except (IndexError, ValueError):
                            continue
                        if _ini > 0:
                            _ratios.append(_fin / _ini)
                    if _ratios:
                        _ratios.sort()
                        metrics["smile_rms_ratio"] = round(
                            _ratios[len(_ratios) // 2], 4
                        )
                        metrics["smile_planes"] = len(_ratios)
                except OSError:
                    pass
                if ok and evaluate is not None:
                    try:
                        metrics.update(dict(evaluate(str(spectrum)) or {}))
                    except Exception as exc:  # noqa: BLE001 - one failed combo must not stop the sweep
                        metrics = {"error": str(exc)}
                elif not ok:
                    metrics = {"error": tr("Refactoring failed (rc={p0})", p0=run2.returncode)}
                # Only the holdout criterion (consistency first) counts here:
                # compare measured vs reconstructed at the held-out points
                if holdout_file and holdout_coords:
                    try:
                        metrics.update(_holdout_residual())
                    except Exception as exc:  # noqa: BLE001 - a failed residual must not stop the sweep
                        logs.append(tr("Leave residual calculation failed: {p0}", p0=exc))
                if delete_spectra:
                    spectrum.unlink(missing_ok=True)
                logs.append(
                    tr(
                        "{p0}: rc={p1} ({p2}) "
                        "indicator={p3}",
                        p0=tag,
                        p1=run2.returncode,
                        p2='set aside rebuild' if holdout_file else (
                        'Full '
                        'sampling rebuild'
                    ),
                        p3=metrics,
                    )
                )

                candidates.append(
                    {
                        "index": index,
                        "params": dict(combo),
                        "metrics": metrics,
                        "script": prod_scripts[index - 1],
                        "ok": bool(ok),
                    }
                )

            if holdout_file:
                logs.append(
                    tr(
                    "scan run mode: hold-out reconstruction (consistency wording; peak count and "
                    "quality score come from the hold-out "
                    "reconstruction)",
                )
                )
            else:
                logs.append(
                    tr(
                    "scan run mode: full-sampling reconstruction (net true-peak wording; peak "
                    "count and quality score come from the final "
                    "spectrum)",
                )
                )
            n_ok = sum(1 for entry in candidates if entry.get("ok"))
            if not candidates:
                logs.append(tr("The scan produced no candidate; nothing to rank"))
            elif not n_ok:
                logs.append(
                    tr(
                        "Every one of the {p0} scan candidates failed; no ranking is "
                        "written and no rank-1 script is promoted",
                        p0=len(candidates),
                    )
                )
            return {
                "success": bool(n_ok),
                "message": (
                    tr("Finish {p0} group scan", p0=len(candidates))
                    if n_ok
                    else tr(
                        "SMILE scan failed: {p0} candidate(s), none succeeded",
                        p0=len(candidates),
                    )
                ),
                "logs": logs,
                "candidates": candidates,
                "n_ok": n_ok,
                "n_failed": len(candidates) - n_ok,
                "scan_dir": str(scan_dir),
                "holdout_file": holdout_file,
            }
        finally:
            self.work_dir = old_work_dir

    def finalize_nus(
        self,
        experiment: Experiment,
        *,
        phases: dict[str, tuple[float, float]] | None = None,
        work_dir: Path | str | None = None,
        timeout: float = 1800.0,
        baseline: dict[str, dict[str, Any]] | None = None,
        params: dict[str, Any] | None = None,
        sampling: dict[str, Any] | None = None,
        out_file: str | None = None,
        script_name: str | None = None,
        planes: str | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        """Finish the indirect-dimension FT from the SMILE reconstruction planes
        (per-dimension phase candidates, no SMILE re-run).

        phases: {axis -> (p0, p1)}, 0 by default; used by the per-dimension phase
        optimisation (the user's plan).
        planes: reconstruction-plane input override (default nus3d_rc/test%04d.ft1
        or nus2d/recon.ft1; the display-layer phase fill uses a copy under
        nus3d_rc_ph/).
        window: {axis -> apodisation config}; no apodisation is inserted by
        default (it goes before the indirect-dimension FT).
        """
        bin_dir = self._bin_dir()
        if bin_dir is None:
            return {
                "success": False,
                "message": tr("nmrPipe not found (csh: which nmrPipe)"),
                "logs": [],
            }
        work = Path(work_dir) if work_dir else self._work_path(experiment)
        if planes is None:
            planes = (
                "nus3d_rc/test%04d.ft1"
                if experiment.ndim >= 3
                else "nus2d/recon.ft1"
            )
        if experiment.ndim >= 3:
            if not (work / "nus3d_rc").is_dir():
                return {
                    "success": False,
                    "message": tr("Missing reconstruction plane nus3d_rc: {p0}", p0=work),
                    "logs": [],
                }
        else:
            if not (work / "nus2d" / "recon.ft1").is_file():
                return {
                    "success": False,
                    "message": tr("Missing reconstruction plane nus2d/recon.ft1: {p0}", p0=work),
                    "logs": [],
                }
        out_ext = {1: "ft1", 2: "ft2"}.get(experiment.ndim, "ft3")
        # 0.2.199-patch29ez: an explicit out_file means intermediate rendering
        # (preview/joint/candidate), written uniformly under work/_intermediate;
        # the final-run default name stays where it is.
        _render_out = out_file not in (None, "")
        out_file = out_file or f"{experiment.dataset_id}.{out_ext}"
        if _render_out:
            _render_dir = work / INTERMEDIATE_SUBDIR
            _render_dir.mkdir(parents=True, exist_ok=True)
            out_file = f"{INTERMEDIATE_SUBDIR}/{out_file}"
        zf_params = dict(params or {})
        zf_plan = zero_fill_plan(
            experiment,
            zf_params.get("zero_fill"),
            linewidth_hz=zf_params.get("linewidth_hz"),
            points_per_line=resolve_points_per_line(
                zf_params.get("points_per_line")
            ),
        )
        script = generate_nus_finalize_script(
            experiment,
            planes=planes,
            out_file=out_file,
            phases=phases,
            baseline=baseline,
            zero_fill=zf_plan,
            sampling=sampling,
            preview_axis=zf_params.get("preview_axis"),
            window=zf_params.get("window"),
            keep_complex=bool(zf_params.get("keep_complex")),
        )
        finalize_com = work / (
            script_name or f"{experiment.dataset_id}_finalize.com"
        )
        finalize_com.write_text(script, encoding="utf-8", newline="\n")
        runtime = CshRuntime()
        result = runtime.run(
            ["csh", finalize_com.name], cwd=str(work), timeout=timeout
        )
        logs = zero_fill_report(zf_plan) + [f"finalize.com: rc={result.returncode}"]
        spectrum = work / out_file
        if (
            result.returncode != 0
            or not spectrum.is_file()
            or spectrum.stat().st_size == 0
        ):
            return {
                "success": False,
                "message": tr("finalize failed / nothing written to {p0}", p0=out_file),
                "logs": logs,
            }
        logs.append(tr("spectrum -> {p0}", p0=spectrum))
        return {
            "success": True,
            "spectrum_path": str(spectrum),
            "message": tr("finalize complete"),
            "logs": logs,
        }

    def project_3d(
        self,
        spectrum_path: Path | str,
        out_dir: Path | str,
        *,
        prefix: str = "proj",
        timeout: float = 900,
        labels: list[str] | None = None,
    ) -> dict[str, dict[str, object]]:
        """Use NMRPipe's own proj3D.tcl to build the three 2D projections of a 3D
        final spectrum (summed along each axis).

        0.2.133 correct usage: hand the 3D spectrum straight to proj3D.tcl, without
        pre-splitting planes or rewriting any output header (proj3D names its
        outputs ``{nucA}.{nucB}.dat`` from the axis labels by itself, and those
        headers are correct under NMRPipe semantics, verified with showhdr).
        Returns:
          {"paths": {"nucA-nucB": path, ...},
           "labels": {"nucA-nucB": nucleus of the fixed axis, ...},
           "nuclei": {"nucA-nucB": [nucA, nucB], ...}};
        the fixed axis nucleus is the one of the source spectrum's three FDF
        labels that is not among the plane's two nuclei (e.g. the 13C-15N plane
        fixes 1H). The nucleus order in the output file names is proj3D's X.Y axis
        order, and nothing is inferred beyond that reading of the characters. A
        failed projection raises ToolError (the caller downgrades it and spectrum
        generation continues).
        """
        import nmrglue as ng

        from backend.nmrpipe_finder import find_tool
        from backend.runtime import ToolError

        runtime = CshRuntime()
        src = Path(spectrum_path)
        dest = Path(out_dir)
        dest.mkdir(parents=True, exist_ok=True)
        proj3d = find_tool("proj3D.tcl", self._bin_dir())
        if proj3d is None:
            raise ToolError(tr("proj3D.tcl(NMRPipe projection tool) not found"))
        dic, _ = ng.pipe.read(str(src))
        if labels is None:
            labels = [
                str(dic.get(k, "") or "")
                for k in ("FDF1LABEL", "FDF2LABEL", "FDF3LABEL")
            ]
        # Clear stale .dat files, keeping only this run's output
        for stale in dest.glob("*.dat"):
            stale.unlink(missing_ok=True)
        run = runtime.run(
            [
                str(proj3d),
                "-in",
                str(src),
                "-outDir",
                str(dest.resolve()),
                "-sum",
                "-noverb",
            ],
            timeout=timeout,
        )
        if run.returncode != 0:
            # 0.2.199-patch29w: duplicate labels (HNN has two 15N) leave proj3D
            # unable to pick an axis by label (measured: bad axis name Y), so fall
            # back to the numpy in-memory projection
            return self._project_3d_numpy(
                str(src), dest, prefix=prefix, labels=labels
            )
        dat_files = sorted(dest.glob("*.dat"))
        outputs: dict[str, str] = {}
        nuclei: dict[str, list[str]] = {}
        fixed: dict[str, str] = {}
        for dat in dat_files:
            parts = dat.stem.split(".")
            if len(parts) != 2 or not all(parts):
                continue
            n1, n2 = parts[0], parts[1]
            key = f"{n1}-{n2}"
            outputs[key] = str(dat)
            nuclei[key] = [n1, n2]
            fixed[key] = next(
                (str(lbl or "") for lbl in labels if str(lbl or "") not in (n1, n2)),
                "",
            )
        if len(outputs) != 3:
            raise ToolError(
                tr(
                    "proj3D output parsing exception (expected 3 *.dat, actual {p0}): "
                    "{p1}",
                    p0=len(outputs),
                    p1=[p.name for p in dat_files],
                )
            )
        return {"paths": outputs, "labels": fixed, "nuclei": nuclei}

    def _project_3d_numpy(
        self,
        spectrum_path: str,
        out_dir: Path,
        *,
        prefix: str = "proj",
        labels: list[str] | None = None,
    ) -> dict[str, dict[str, object]]:
        """numpy in-memory projection fallback (0.2.199-patch29w): repeated nucleus
        labels such as HNN.

        proj3D.tcl picks axes by label and two 15N cannot be told apart (measured:
        bad axis name Y); here the final spectrum is read and summed along the
        storage axes (axis0=F2, axis1=F1, axis2=F3) into three 2D projection FDFs
        named {prefix}_F{n}.ft2 (the fixed logical axis), which the GUI's
        _proj_F{n} parses. The data orientation (b,a) matches the _proj_F{n}
        convention of the GUI's _load_projection_ft2 (axis0=b, axis1=a). The axis
        parameters are copied from the source FDF block (the GUI prefers the axes
        of the loaded 3D spectrum's nuclei; the file header is only a fallback).
        """
        import nmrglue as ng
        from nmrglue.fileio import pipe as ngpipe

        from backend.runtime import ToolError

        dic, data = ng.pipe.read(spectrum_path)
        data = np.asarray(data)
        if np.iscomplexobj(data):
            data = data.real
        if data.ndim != 3:
            raise ToolError(
                tr(
                    "Projection requires 3D spectrum: {p0} "
                    "shape={p1}",
                    p0=spectrum_path,
                    p1=data.shape,
                )
            )

        def _src_fdf(axis_idx: int) -> str:
            order = [int(v) for v in dic.get("FDDIMORDER") or []]
            if len(order) >= 3:
                dim = order[2 - axis_idx]
                if 1 <= dim <= 4:
                    return f"FDF{dim}"
            return f"FDF{axis_idx + 1}"

        def _write(path: Path, plane: np.ndarray, fdf0: str, fdf1: str) -> None:
            # Keep every FDF1/2/3 key of the source (nmrglue dic2fdata needs the
            # full 512-byte header) and override only FDF1/FDF2 with the plane's two
            # axes; readers ignore FDF3 when FDDIMCOUNT=2
            out: dict[str, object] = dict(dic)
            out["FDDIMCOUNT"] = 2
            out["FDSIZE"] = float(plane.shape[1])
            out["FDSPECNUM"] = float(plane.shape[0])
            out["FDQUADFLAG"] = 1
            out["FDF1QUADFLAG"] = 1
            out["FDF2QUADFLAG"] = 1
            out["FDDIMORDER"] = [2.0, 1.0]
            for out_pref, src_pref, size in (
                ("FDF1", fdf1, plane.shape[1]),
                ("FDF2", fdf0, plane.shape[0]),
            ):
                for k, v in dic.items():
                    if str(k).startswith(src_pref):
                        out[out_pref + str(k)[len(src_pref):]] = v
                out[out_pref + "SIZE"] = float(size)
            ngpipe.write(
                str(path),
                out,
                np.ascontiguousarray(plane, dtype=np.float32),
                overwrite=True,
            )

        out_dir.mkdir(parents=True, exist_ok=True)
        # Storage axis order (F2, F1, F3): the three projections fix F1/F2/F3
        p23 = out_dir / f"{prefix}_F1.ft2"  # F2-F3 plane, F1 fixed
        p13 = out_dir / f"{prefix}_F2.ft2"  # F1-F3 plane, F2 fixed
        p12 = out_dir / f"{prefix}_F3.ft2"  # F1-F2 plane, F3 fixed
        _write(p23, data.sum(axis=1), _src_fdf(0), _src_fdf(2))
        _write(p13, data.sum(axis=0), _src_fdf(1), _src_fdf(2))
        _write(p12, data.sum(axis=2).T, _src_fdf(1), _src_fdf(0))
        return {
            "paths": {"F1": str(p23), "F2": str(p13), "F3": str(p12)},
            "labels": {"F1": "", "F2": "", "F3": ""},
            "nuclei": {"F1": None, "F2": None, "F3": None},
            "numpy_fallback": True,
        }


    @staticmethod
    def _converted_fid_path(work: Path, dataset_id: str) -> Path:
        """Converted fid path: prefer the single file, keep work/fid/ slices
        compatible."""
        single = work / f"{dataset_id}.fid"
        if single.is_file():
            return single
        slice_dir = work / "fid"
        if _slice_candidates(slice_dir, dataset_id):
            return slice_dir
        return single

    @staticmethod
    def _merged_fid_in(work: Path, dataset_id: str) -> str | None:
        """in_file of the merged segments: the single file first, otherwise the
        slice stream (0.2.199-patch28)."""
        if (work / "merged" / f"{dataset_id}.fid").is_file():
            return f"merged/{dataset_id}.fid"
        slice_dir = work / "merged" / "fid"
        slice_in = _slice_in_file(slice_dir, dataset_id)
        if slice_in:
            return f"merged/{slice_in}"
        return None

    def _conversion_record_path(self, work: Path, dataset_id: str) -> Path:
        """Path of the conversion record: next to the fid, holding the raw fingerprint."""
        return work / f"{dataset_id}.fid.conversion.json"

    def _record_conversion(
        self,
        work: Path,
        dataset_id: str,
        raw_dirs: Any,
        logs: list[str],
    ) -> None:
        """Record the raw fingerprint, the fid size and the inter-part drift check (for reuse)."""
        payload: dict[str, Any] = {
            "raw_fingerprint": raw_dir_fingerprint(raw_dirs),
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        # 2026-09-23: the inter-part field drift result is archived with the conversion
        # provenance (see _correct_group_drift)
        drift = read_field_drift_record(work)
        if drift is not None:
            payload["field_drift"] = drift
        fid = work / f"{dataset_id}.fid"
        if fid.is_file():
            payload["fid_size"] = fid.stat().st_size
        try:
            atomic_write_text(
                self._conversion_record_path(work, dataset_id),
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            )
        except OSError as exc:
            logs.append(tr("Could not record the conversion provenance: {p0}", p0=exc))

    def _converted_fid_is_current(
        self,
        work: Path,
        dataset_id: str,
        raw_dirs: Any,
        logs: list[str],
        *,
        require_field_drift: bool = False,
    ) -> bool:
        """Whether an already converted fid may be reused (2026-09-22 review: a stale or
        incomplete product must not be reused silently).

        - record present but the raw input fingerprint changed (for example the source-level
          NUS cleanup rewrote ser) -> convert again;
        - record missing (older project) -> still reuse, but log that nothing was verified;
        - record unreadable / fid empty / fid size not matching the record -> convert again;
        - with ``require_field_drift`` (multi-part) a record without an inter-part drift result
          -> convert again (since 2026-09-23 a multi-part conversion must leave a drift
          conclusion behind; older records are re-run once).
        """
        fid = work / f"{dataset_id}.fid"
        if fid.is_file() and fid.stat().st_size == 0:
            logs.append(tr("Converted fid {p0} is empty; converting again", p0=fid.name))
            return False
        record = self._conversion_record_path(work, dataset_id)
        if not record.is_file():
            logs.append(
                tr(
                    "Reuse converted fid (no conversion record; the raw data is not "
                    "verified)"
                )
            )
            return True
        try:
            data = json.loads(record.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logs.append(
                tr("Conversion record {p0} is unreadable; converting again", p0=record.name)
            )
            return False
        if require_field_drift and "field_drift" not in data:
            logs.append(tr(
                "The conversion record has no inter-part field drift check; converting again",
            ))
            return False
        recorded = str(data.get("raw_fingerprint") or "")
        if recorded and recorded != raw_dir_fingerprint(raw_dirs):
            logs.append(tr("Raw data changed since the conversion; converting again"))
            return False
        size = data.get("fid_size")
        if isinstance(size, int) and fid.is_file() and fid.stat().st_size != size:
            logs.append(
                tr("Converted fid {p0} is incomplete; converting again", p0=fid.name)
            )
            return False
        logs.append(tr("Reuse converted fid (skip conversion)"))
        return True

    def _finalize_converted_fid(
        self,
        raw_dir: Path,
        dest_work: Path,
        dataset_id: str,
        logs: list[str],
        ndim: int = 3,
    ) -> bool:
        """Put the bruker conversion product in place: the single file
        {dataset_id}.fid (0.2.199-patch16 unification).

        patch_fid_com has already rewritten the fid.com output name to
        {dataset_id}.fid (0.2.163-patch13); test.fid/test*.fid stay supported
        only for legacy data or a manual rename. The old slice-style fid/*.fid (a
        product of the acqu3s TD correction era) is kept for compatibility only;
        both forms have been accepted since 0.2.80, and the slice form is kept
        under work/fid/ for streaming.
        """
        source = raw_dir / f"{dataset_id}.fid"
        if not source.is_file():
            source = raw_dir / "test.fid"  # legacy name (fid.com edited back by hand)
        if source.is_file():
            shutil.move(str(source), dest_work / f"{dataset_id}.fid")
            logs.append(tr("{p0}.fid is in place ({p1})", p0=dataset_id, p1=raw_dir.name))
            return True
        slice_dir = raw_dir / "fid"
        slices = _slice_candidates(slice_dir, dataset_id)
        if not slices:
            return False
        if ndim == 2:
            # 0.2.199-patch29hz-fix11 (user: "2D should not have a slice stream,
            # mind the compatibility"): 2D holds a single plane -- even if bruker
            # writes its output into fid/ (with %03d and the like in the name), that
            # is still **one** file and is handled as a single file; fid.com is left
            # alone (always follow -AUTO).
            real = [p for p in slices if p.is_file() and p.stat().st_size > 0]
            if len(real) == 1:
                dest = dest_work / f"{dataset_id}.fid"
                shutil.move(str(real[0]), dest)
                logs.append(
                    tr(
                        "2D single plane output {p0} → {p1}(Processed as a single file, 2D does "
                        "not use slicing "
                        "flow)",
                        p0=real[0].name,
                        p1=dest.name,
                    )
                )
                return True
        dest_slice = dest_work / "fid"
        # 2026-09-22: copy into a staging directory and rename it into place, so a
        # half-written slice stream is never treated as a usable product
        staging = dest_work / "fid.copying"
        if staging.exists():
            shutil.rmtree(staging)
        shutil.copytree(slice_dir, staging)
        if dest_slice.exists():
            shutil.rmtree(dest_slice)
        os.replace(staging, dest_slice)
        logs.append(tr("sliced fid -> {p0}/ ({p1} slices)", p0=dest_slice.name, p1=len(slices)))
        return True

    def _convert_dir(
        self,
        runtime: CshRuntime,
        experiment: Experiment,
        raw_dir: Path,
        dest_work: Path,
        is_nus: bool,
        logs: list[str],
        fid_com_overrides: dict[str, str] | None = None,
    ) -> bool:
        """In raw_dir: bruker -AUTO -> fid.com -> place into dest_work -> patch ->
        run (the script lives in the work directory and its relative paths resolve
        with the conversion directory as cwd) -> product (the single file
        {dataset_id}.fid, 0.2.199-patch16 unification; the old slice form is put in
        place for compatibility).

        fid_com_overrides: manual-route parameter overrides (0.2.163-patch13) --
        the parameters a user changed on the reference segment's fid.com are
        applied segment by segment, while conversion/merging remain guaranteed by
        this layer. acqu3s TD is no longer corrected: bruker -AUTO writes a single
        full-grid fid straight from the NusTD grid with -aq2D Complex (verified on
        sampleK with acqu3s TD=1), and 3D slices come from the direct-dimension
        step of SMILE script step1. The multi-segment path writes one file per
        segment and merges them with addNMR. If bruker fails, only uniform
        sampling falls back to bruk2pipe. ser_full is cleaned up after the
        conversion (it can be regenerated).
        """
        convert_dir = raw_dir
        if not (convert_dir / "acqus").is_file():
            logs.append(tr("Missing input file: {p0}/acqus", p0=convert_dir))
            return False
        try:
            raw_fid = convert_dir / "fid.com"
            fid_com = dest_work / "fid.com"
            bruker_ok = False
            bruker = find_tool("bruker", self._bin_dir())
            if bruker is not None:
                result = runtime.run(
                    ["bruker", "-AUTO"], cwd=str(convert_dir), timeout=120
                )
                logs.append(f"bruker -AUTO ({convert_dir.name}): rc={result.returncode}")
                if result.returncode == 0 and raw_fid.is_file():
                    # 0.2.91: fid.com goes into process/ (dest_work); raw no longer
                    # keeps a generated script
                    if raw_fid != fid_com:
                        fid_com.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(raw_fid), str(fid_com))
                    text = fid_com.read_text(encoding="utf-8", errors="replace")
                    patched, corrections = patch_fid_com(
                        text, experiment, data_dir=convert_dir
                    )
                    if fid_com_overrides:
                        # Manual tuning overrides parameters only; the output name
                        # and structure remain guaranteed by the backend
                        patched, override_corrections = apply_fid_com_overrides(
                            patched, fid_com_overrides
                        )
                        corrections += override_corrections
                    if is_nus:
                        nuslist_path = convert_dir / "nuslist"
                        if nuslist_path.is_file():
                            nuslist_count = len(read_nuslist(nuslist_path))
                            patched, nus_corrections = patch_nus_expand_count(
                                patched, nuslist_count
                            )
                            corrections += nus_corrections
                    for correction in corrections:
                        logs.append(tr("parameter correction: {p0}", p0=correction))
                    # LF line endings are mandatory: CRLF breaks csh's \ line
                    # continuation. The script lives in the work directory and its
                    # internal relative paths (./ser) resolve with the conversion
                    # directory as cwd
                    fid_com.write_text(patched, encoding="utf-8", newline="\n")
                    # 0.2.199-patch29gk: an invalid direct-dimension TD (0) makes
                    # bruk2pipe convert forever, so abort explicitly instead of
                    # hanging (same policy as the missing-file case in patch29dz).
                    if experiment.dimensions and experiment.dimensions[0].td == 0:
                        logs.append(
                            tr(
                                "direct dimension TD=0(acqus/acqu does not provide valid TD), "
                                "abort the conversion to avoid "
                                "stuck",
                            )
                        )
                        return False
                    run_result = runtime.run(
                        ["csh", str(fid_com)], cwd=str(convert_dir), timeout=900
                    )
                    logs.append(f"fid.com: rc={run_result.returncode}")
                    bruker_ok = run_result.returncode == 0
            if not bruker_ok:
                if is_nus:
                    return False
                logs.append(tr("fallback: use the built-in bruk2pipe parameter conversion"))
                script = generate_convert_script(
                    experiment,
                    direct_points=physical_direct_points(experiment, convert_dir),
                )
                convert_script = dest_work / f"{experiment.dataset_id}_convert.com"
                convert_script.write_text(script, encoding="utf-8", newline="\n")
                run_result = runtime.run(
                    ["csh", str(convert_script)], cwd=str(convert_dir), timeout=600
                )
                logs.append(f"convert.com: rc={run_result.returncode}")
                if run_result.returncode != 0:
                    return False
            if not self._finalize_converted_fid(
                convert_dir, dest_work, experiment.dataset_id, logs,
                ndim=experiment.ndim,
            ):
                return False
            # SMILE needs only nuslist; intermediate products such as
            # ser_full/mask.fid/mask/ are deleted to save space (mask/ is the sampling
            # mask written by nusExpand.tcl -mask in fid.com and can be regenerated)
            for stale in ("ser_full", "mask.fid"):
                stale_path = convert_dir / stale
                if stale_path.is_file():
                    stale_path.unlink()
            mask_dir = convert_dir / "mask"
            if mask_dir.is_dir():
                shutil.rmtree(mask_dir, ignore_errors=True)
            return True
        finally:
            pass  # 0.2.199-patch16: there is no staging copy left to clean up

    def _light_phase_search(
        self,
        experiment: Experiment,
        work: Path,
        in_file: str,
        runtime: Any,
        logs: list[str],
        params: dict[str, Any],
        *,
        nuslist_count: int,
        sampling: dict[str, Any],
    ) -> tuple[float, float] | None:
        """Lightweight SMILE phase search (0.2.94, the user's plan): a subsampled
        nuslist plus PS(0,0) gives a sub-second lightweight reconstruction, and
        the existing fixed-trace score then estimates the direct-dimension
        (p0, p1) on that lightweight final spectrum.

        Single and multiple files are handled alike: the lightweight subdirectory
        work/light/ holds the subsampled nuslist (SMILE reads the default file
        nuslist when -sample None is given) and the conversion products are reused
        through symbolic links. On success phase.json is written (v2,
        source=phase_only_recon) and (p0, p1) returned; on failure None is returned
        (the caller keeps the NU-DFT result or (0,0)).
        """
        phase_file = work / "phase.json"
        if phase_file.is_file():
            try:
                data = json.loads(phase_file.read_text(encoding="utf-8"))
                if data.get("version") == 2:
                    logs.append(
                        tr(
                            "direct dimension phase (cache): p0={p0:g} "
                            "p1={p1:g}",
                            p0=data['p0'],
                            p1=data['p1'],
                        )
                    )
                    return float(data["p0"]), float(data["p1"])
            except (OSError, TypeError, ValueError, KeyError):
                pass
        points = read_nuslist(work / "nuslist")
        if len(points) < 8:
            return None
        target = max(16, len(points) // 4)
        if target >= len(points):
            return None  # too few sampling points; lightweight is pointless
        step = max(1, len(points) // target)
        sub = points[::step][:target]
        if not sub or 0 not in [int(p[0]) for p in sub]:
            sub[0] = points[0]
        light_dir = work / "light"
        if light_dir.exists():
            shutil.rmtree(light_dir)
        light_dir.mkdir()
        (light_dir / "nuslist").write_text(
            "".join(" ".join(str(v) for v in p) + "\n" for p in sub),
            encoding="utf-8",
        )
        try:
            if "%" in in_file:
                (light_dir / "fid").symlink_to(
                    work / "fid", target_is_directory=True
                )
            else:
                (light_dir / Path(in_file).name).symlink_to(
                    work / Path(in_file).name
                )
        except OSError:
            logs.append(tr("Lightweight SMILE phase search: symbolic link failed, skipped"))
            return None
        nthread = resolve_nthread(params.get("nthread"))
        ext_lo = resolve_ext_lo(params.get("ext_lo"))
        ext_hi = resolve_ext_hi(params.get("ext_hi"))
        extract = _as_bool(params.get("extract", True))
        baseline = expand_baseline(experiment, params.get("baseline"))
        zero_fill = params.get("zero_fill")
        linewidth_hz = params.get("linewidth_hz")
        points_per_line = resolve_points_per_line(
            params.get("points_per_line")
        )
        if experiment.ndim >= 3:
            script_fn = generate_3d_nus_script
            ext = "ft3"
        else:
            script_fn = generate_2d_nus_script
            ext = "ft2"
        from backend.memory_guard import MEM_SAFETY, available_memory_mb

        max_mem_gb = max(available_memory_mb() * MEM_SAFETY / 1024.0, 1.0)
        out_light = f"{experiment.dataset_id}_light.{ext}"
        script = script_fn(
            experiment,
            in_file=in_file,
            nuslist=str(
                params.get("nuslist_file")
                or ("nuslist" if (work / "nuslist").is_file() else "")
            ),
            out_file=out_light,
            nthread=nthread,
            nuslist_count=len(sub),
            ext_lo=ext_lo,
            ext_hi=ext_hi,
            nsigma=5.0,
            thresh=0.95,
            smile_scaling=True,
            smile_report=1,
            max_mem=max_mem_gb,
            direct_phase=(0.0, 0.0),
            extract=extract,
            baseline=baseline,
            zero_fill=zero_fill,
            linewidth_hz=linewidth_hz,
            points_per_line=points_per_line,
            sampling=sampling,
        )
        light_com = light_dir / "light.com"
        light_com.write_text(script, encoding="utf-8", newline="\n")
        logs.append(
            tr(
                "Lightweight SMILE phase search: {p0}/{p1} sampled points,window {p2}-{p3} ppm, "
                "PS(0,0) reconstruction",
                p0=len(sub),
                p1=len(points),
                p2=ext_lo,
                p3=ext_hi,
            )
        )
        result = runtime.run(["csh", "light.com"], cwd=str(light_dir), timeout=600)
        logs.append(f"light.com: rc={result.returncode}")
        light_ft = light_dir / out_light
        if (
            result.returncode != 0
            or not light_ft.is_file()
            or light_ft.stat().st_size == 0
        ):
            logs.append(
                tr(
                "lightweight SMILE phase search failed (falling back to "
                "NU-DFT/default)",
            )
            )
            return None
        try:
            import nmrglue as ng

            _dic, data = ng.pipe.read(str(light_ft))
            est = search_direct_phase_on_spectrum(
                np.asarray(data), cancel=cancel_requested
            )
        except Exception as exc:  # noqa: BLE001
            logs.append(tr("Lightweight spectrum scoring failed (fallback): {p0}", p0=exc))
            return None
        if est is None:
            logs.append(tr("Lightweight spectrum without signal (fallback)"))
            return None
        p0, p1, score = est
        atomic_write_text(
            phase_file,
            json.dumps(
                {
                    "version": 2,
                    "source": "phase_only_recon",
                    "p0": p0,
                    "p1": p1,
                    "score": score,
                    "n": len(sub),
                },
                indent=2,
            ),
        )
        logs.append(
            tr(
                "Lightweight SMILE phase: F2=({p0:g}, {p1:g}) score={p2:.2f}(Written phase.json, "
                "officially refactored and "
                "reused)",
                p0=p0,
                p1=p1,
                p2=score,
            )
        )
        return p0, p1

    def _display_phase_search(
        self,
        experiment: Experiment,
        work: Path,
        logs: list[str],
    ) -> tuple[float, float, float] | None:
        """Display-layer phase search (0.2.96, nmrDraw idea): score the symmetry of
        the complex reconstruction data with no Hilbert transform or extra
        backend. Returns (p0, p1, score); None when there is no clean signal peak.

        0.2.98: a 3D plane file is stored as real data with the first axis
        interleaved real/imaginary (nmrglue reads it as a doubled real array), so
        treating it directly as complex for rotation/scoring was the wrong
        convention -- the complex values are now unpacked with
        read_pipe_complex before the search; the 2D recon.ft1 is complex and
        directly readable by nmrglue (the direct dimension sits on axis 0).

        0.2.199-patch17: a 3D nus3d_rc plane holds "one plane per
        direct-dimension point" (168 of them, each plane being the (F1,F2)
        indirect axes) -- the direct dimension is the **plane index**, not an
        in-plane axis; scoring along axis 0 (F1-related, 434 points) made the
        score surface flat and the phase search meaningless (measured on sampleK:
        it returned (0,25) score=28.3 and was rejected by the threshold). The
        planes are now stacked and scored along axis=-1 (the direct-dimension
        plane axis).
        """
        try:
            import nmrglue as ng

            from core.data.pipe_io import read_pipe_complex
            from core.optimization.phase_search import (
                search_direct_phase_on_spectrum,
            )

            if experiment.ndim >= 3:
                plane_dir = work / "nus3d_rc"
                paths = sorted(plane_dir.glob("test*.ft1"))
                if not paths:
                    return None
                # 0.2.199-patch17: a plane is a direct-dimension point, so
                # subsampling is not allowed (the direct-dimension resolution must be
                # complete; 168 planes x 434x219 x 8B is about 127MB, acceptable)
                # 0.2.199-patch29: truncate by the first plane's FDFILECOUNT so stale
                # test*.ft1 files (left over from earlier runs) take no part and the
                # direct-dimension axis cannot pick up junk planes
                try:
                    first_dic = ng.pipe.read(str(paths[0]))[0]
                    count = int(float(first_dic.get("FDFILECOUNT") or 0))
                except (TypeError, ValueError):
                    count = 0
                if count > 0:
                    paths = paths[:count]
                arrays = [read_pipe_complex(path) for path in paths]
                arr = (
                    np.stack(arrays, axis=-1)
                    if len(arrays) > 1
                    else arrays[0]
                )
                logs.append(
                    tr(
                        "Display-layer phase search: all {p0} 3D replica planes take part (direct "
                        "dimension = plane index, "
                        "axis=-1)",
                        p0=len(arrays),
                    )
                )
                search_axis = -1
            else:
                recon = work / "nus2d" / "recon.ft1"
                if not recon.is_file():
                    return None
                _dic, data = ng.pipe.read(str(recon))
                arr = np.asarray(data)
                search_axis = 0
            est = search_direct_phase_on_spectrum(
                arr,
                axis=search_axis,
                metric="symmetry",
                cancel=cancel_requested,
            )
            if est is None:
                logs.append(tr("Display layer phase search: no clean signal peak"))
                return None
            logs.append(
                tr(
                    "Display layer phase search: F2=({p0:.1f}, {p1:.1f}) "
                    "score={p2:.1f}",
                    p0=est[0],
                    p1=est[1],
                    p2=est[2],
                )
            )
            return est
        except Exception as exc:  # noqa: BLE001
            logs.append(tr("Display layer phase search failed: {p0}", p0=exc))
            return None

    def _apply_direct_phase(
        self,
        experiment: Experiment,
        work: Path,
        p0: float,
        p1: float,
        logs: list[str],
        progress: Callable[[str], None] | None = None,
    ) -> bool:
        """Final phase fill: apply the direct-dimension phase to the complex
        reconstruction data and re-run the stage-2 finalize (cheap, no SMILE), so
        the final spectrum carries the right direct-dimension phase.

        0.2.98: the rotation result is written to a copy (nus3d_rc_ph/ or
        recon_ph.ft1) instead of overwriting the source planes in place, so the
        sources keep their PS(0,0) complex data for later reuse or re-search; a 3D
        plane is stored as real data with the first axis interleaved
        real/imaginary, so read_pipe_complex must unpack it into complex values
        before reading.

        0.2.199-patch17: the 3D direct dimension is the plane index (one plane per
        direct-dimension point), so the phase ramp multiplies element by element
        over the plane index (p0 + p1*k/(n-1)) instead of rotating an in-plane
        axis.
        """
        try:
            if progress is not None:
                progress(tr("Apply direct dimension phase (recon plane rotation)"))
            import nmrglue as ng

            from core.data.pipe_io import read_pipe_complex

            if experiment.ndim >= 3:
                plane_dir = work / "nus3d_rc"
                paths = sorted(plane_dir.glob("test*.ft1"))
                if not paths or not paths[0].is_file():
                    return False
                out_dir = work / "nus3d_rc_ph"
                if out_dir.exists():
                    shutil.rmtree(out_dir)
                out_dir.mkdir()
                # 0.2.199-patch17: the direct dimension is the plane index, so the
                # phase ramp multiplies element by element over the planes
                arrays = [read_pipe_complex(path) for path in paths]
                stack = np.stack(arrays, axis=-1)  # (i0, i1, k=direct dimension)
                n = stack.shape[-1]
                k = np.arange(n, dtype=float)
                ramp = np.exp(
                    1j * np.deg2rad(p0 + p1 * k / max(n - 1, 1))
                ).reshape(*([1] * (stack.ndim - 1)), n)
                rotated = stack * ramp
                for path, plane in zip(paths, np.moveaxis(rotated, -1, 0)):
                    dic, _data = ng.pipe.read(str(path))
                    ng.pipe.write(
                        str(out_dir / path.name),
                        dic,
                        plane.astype(np.complex64),
                        overwrite=True,
                    )
                planes = "nus3d_rc_ph/test%04d.ft1"
            else:
                recon = work / "nus2d" / "recon.ft1"
                if not recon.is_file():
                    return False
                dic, data = ng.pipe.read(str(recon))
                arr = np.asarray(data)
                n = arr.shape[0]
                k = np.arange(n, dtype=float)
                ramp = np.exp(
                    1j * np.deg2rad(p0 + p1 * k / max(n - 1, 1))
                ).reshape(n, *([1] * (arr.ndim - 1)))
                rot = arr * ramp
                out_file = work / "nus2d" / "recon_ph.ft1"
                ng.pipe.write(
                    str(out_file),
                    dic,
                    rot.astype(np.complex64),
                    overwrite=True,
                )
                planes = "nus2d/recon_ph.ft1"
            resp = self.finalize_nus(
                experiment, work_dir=work, planes=planes
            )
            if not resp.get("success"):
                logs.append(tr("finalize rerender failed: {p0}", p0=resp.get('message')))
                return False
            return True
        except Exception as exc:  # noqa: BLE001
            logs.append(tr("Applying direct dimension phase failed: {p0}", p0=exc))
            return False

    def _search_direct_phase(
        self,
        work: Path,
        fid_files: Path | list[Path],
        logs: list[str],
        min_gain: float = 0.02,
        is_nus: bool = False,
        n_f1: int = 0,
        n_f2: int = 1,
        is_1d: bool = False,
    ) -> tuple[float, float]:
        """Direct-dimension phase search: a (p0, p1) frequency-domain search on the
        direct-dimension FT spectrum, with the result cached in phase.json.

        0.2.88: upgraded from "p1 consensus on the raw FID (p0 always 0)" to a
        "frequency-domain search on the direct-dimension FT spectrum": the
        direct-dimension phase of increment i is the common phase plus
        omega1*t1(i) (t1 modulation, t1(0)=0); p1 is the median of a multi-peak
        trace-phase concentration fit (t1 is only a per-peak constant offset and
        does not affect the ramp), while p0 is the negated circular mean of the
        peak phases of the first trace with a peak (increment 0, the PS correction
        convention), with +-180 disambiguated towards the positive-peak solution
        -- so the common direct-dimension p0 is no longer lost. The estimate is
        made at the PS application size (p1 is the total degrees across the axis
        and does not depend on the zero-fill size; 0.2.199-patch29dq zero-fills
        NUS direct dimensions to 2x TD by default, with the memory guard reducing
        it to 1x TD), and the p1 semantics match the script PS, so no scaling is
        needed. fid_files accepts a single file or a list of slices.

        0.2.91 (NUS automatic path): with a nuslist and slices it prefers the
        "non-uniform DFT strongest-peak phase"
        (core.optimization.phase_search.nus_direct_phase) -- a NU-DFT of the
        strongest direct peak's complex value along the increments, where the t1
        modulation cancels exactly at the true F1/F2 frequencies and the peak
        phase is phi(k*), making the direct-dimension (p0, p1) correction
        reliable without manual confirmation; on failure it falls back to
        per-slice anchoring. n_f1/n_f2 are the indirect-dimension grid sizes
        (effective_td).
        """
        phase_file = work / "phase.json"
        if phase_file.is_file():
            try:
                data = json.loads(phase_file.read_text(encoding="utf-8"))
                if data.get("version") != 2 or (
                    is_1d and data.get("source") != "1d_self"
                ):
                    raise ValueError(
                        tr(
                        "Old version cache (algorithm has been updated), need to search "
                        "again",
                    ))
                logs.append(
                    tr(
                        "direct dimension phase (cache): p0={p0:g} "
                        "p1={p1:g}",
                        p0=data['p0'],
                        p1=data['p1'],
                    )
                )
                return float(data["p0"]), float(data["p1"])
            except (OSError, TypeError, ValueError, KeyError):
                pass  # a damaged or outdated cache means a fresh search
        paths = [fid_files] if isinstance(fid_files, Path) else list(fid_files)
        if not paths:
            return 0.0, 0.0
        # 0.2.91: NUS automatic path -- non-uniform DFT strongest-peak phase (uses
        # every slice, no manual confirmation needed)
        if is_nus and n_f1 > 0:
            nuslist_file = work / "nuslist"
            if nuslist_file.is_file():
                try:
                    import nmrglue as ng

                    from core.optimization.phase_search import nus_direct_phase

                    points = read_nuslist(nuslist_file)
                    fids: list[np.ndarray] = []
                    for path in paths:
                        _dic, fid = ng.pipe.read(str(path))
                        arr = np.asarray(fid)
                        if arr.ndim < 1 or arr.shape[-1] < 8:
                            continue
                        fids.append(arr.reshape(-1, arr.shape[-1]))
                    if fids and len(fids) == len(points):
                        est = nus_direct_phase(
                            np.concatenate(fids, axis=0),
                            points,
                            n_f1,
                            n_f2,
                        )
                        if est is not None:
                            p0, p1, score, gain, kstar = est
                            atomic_write_text(
                                phase_file,
                                json.dumps(
                                    {
                                        "version": 2,
                                        "source": "direct_nudft",
                                        "p0": p0,
                                        "p1": p1,
                                        "score": score,
                                        "gain": gain,
                                        "n": int(len(fids)),
                                        "kstar": kstar,
                                    },
                                    indent=2,
                                ),
                            )
                            if score < 2.0:
                                logs.append(
                                    tr(
                                        "direct dimension phase information is weak (coherence "
                                        "SNR={p0:.2f} < 2), keep "
                                        "p0=p1=0",
                                        p0=score,
                                    )
                                )
                                return 0.0, 0.0
                            logs.append(
                                tr(
                                    "direct dimension phase search (NU-DFT): p0={p0:g} p1={p1:g} "
                                    "(correlation SNR={p2:.2f}, peak k*={p3}, {p4} "
                                    "slice)",
                                    p0=p0,
                                    p1=p1,
                                    p2=score,
                                    p3=kstar,
                                    p4=len(fids),
                                )
                            )
                            return p0, p1
                except Exception as exc:  # noqa: BLE001
                    logs.append(
                        tr(
                            "Direct dimension phase search (NU-DFT) fails, fallback slice by "
                            "slice: "
                            "{p0}",
                            p0=exc,
                        )
                    )
        # Per-slice fallback: uniform subsampling, at most 16
        if len(paths) > 16:
            index = np.linspace(0, len(paths) - 1, 16).astype(int)
            paths = [paths[i] for i in index]
        try:
            import nmrglue as ng

            rows: list[np.ndarray] = []
            for path in paths:
                _dic, fid = ng.pipe.read(str(path))
                arr = np.asarray(fid)
                n_points = arr.shape[-1] if arr.ndim >= 1 else 0
                if n_points < 8:
                    continue
                if is_nus:
                    zf_size = None  # the NUS direct-dimension search uses the raw
                    # FID size (no zero fill)
                else:
                    zf_size = 1
                    while zf_size < 2 * n_points:
                        zf_size *= 2
                traces = direct_ft_traces(
                    arr,
                    zf_size=zf_size,
                    sp_off=0.45,
                    sp_end=0.95,
                    sp_pow=1,
                )
                rows.append(traces.reshape(-1, traces.shape[-1]))
            if not rows:
                logs.append(tr("direct dimension phase search: no slices available, keep p0=p1=0"))
                return 0.0, 0.0
            spectra = np.concatenate(rows, axis=0)
            if is_1d:
                # 0.2.199-patch29gk: with the wide 1D window the group delay brings a
                # significant linear phase, so p1 must be fixed before p0 (in 2D/3D
                # the narrow window makes p1 matter little, so those still use
                # search_direct_spectrum_phase; see the user's ruling D-2026-09-05).
                # The joint symmetry (p0,p1) search competes with the old p0-only
                # result: if the dominant peak absorbs less well under the symmetry
                # search than under the old one (e.g. 1H dominated by the water
                # peak), the old result wins, so narrow spectra do not regress.
                est_old = search_direct_spectrum_phase(spectra)
                est_sym = search_direct_phase_on_spectrum(
                    spectra, axis=-1, metric="symmetry",
                    coarse_p0_step=15.0, radius=12, min_windows=3,
                    prefer_p1_zero=False, sign_mode="uniform",
                )
                cands: list[tuple[float, float, float, float, float]] = []
                if est_old is not None:
                    cands.append(
                        (
                            dominant_absorption_ratio(spectra[0], est_old[0], 0.0),
                            est_old[0], 0.0, float(est_old[2]), float(est_old[3]),
                        )
                    )
                if est_sym is not None:
                    cands.append(
                        (
                            dominant_absorption_ratio(
                                spectra[0], float(est_sym[0]), float(est_sym[1])
                            ),
                            float(est_sym[0]), float(est_sym[1]),
                            float(est_sym[2]), 0.0,
                        )
                    )
                if not cands:
                    logs.append(tr("1D phase search: no results available, keep p0=p1=0"))
                    return 0.0, 0.0
                _da, p0, p1, score, gain = max(cands, key=lambda c: c[0])
                # 0.2.199-patch29gk (user: peaks must absorb upwards, not downwards):
                # if the dominant peak is negative, flip p0 by 180 deg so absorption
                # peaks point up (positive).
                p0 = orient_dominant_positive(spectra[0], p0, p1)
                atomic_write_text(
                    phase_file,
                    json.dumps(
                        {
                            "version": 2,
                            "source": "1d_self",
                            "p0": p0,
                            "p1": p1,
                            "score": score,
                            "gain": gain,
                            "n": int(spectra.shape[0]),
                        },
                        indent=2,
                    ),
                )
                logs.append(tr(
                    "1D phase search: p0={p0:g} p1={p1:g} "
                    "(score={p2:.3f})",
                    p0=p0,
                    p1=p1,
                    p2=score,
                ))
                return p0, p1
            est = search_direct_spectrum_phase(spectra)
            if est is None:
                logs.append(
                    tr(
                    "direct dimension phase search: direct dimension spectrum has no signal, keep "
                    "p0=p1=0",
                ))
                return 0.0, 0.0
            p0, p1, score, gain = est
            atomic_write_text(
                phase_file,
                json.dumps(
                    {
                        "version": 2,
                        "p0": p0,
                        "p1": p1,
                        "score": score,
                        "gain": gain,
                        "n": int(spectra.shape[0]),
                    },
                    indent=2,
                ),
            )
            if (gain < min_gain or score < 0.55) and not is_1d:
                logs.append(
                    tr(
                        "direct dimension phase information is weak (gain={p0:.3f}, "
                        "score={p1:.3f}),keeping "
                        "p0=p1=0",
                        p0=gain,
                        p1=score,
                    )
                )
                return 0.0, 0.0
            logs.append(
                tr(
                    "direct dimension phase search: p0={p0:g} p1={p1:g} (score={p2:.3f}, "
                    "gain={p3:.3f}, {p4} "
                    "trace",
                    p0=p0,
                    p1=p1,
                    p2=score,
                    p3=gain,
                    p4=spectra.shape[0],
                )
                + (tr(", 1D weak signal uses estimated value)") if is_1d else ")")
            )
            return p0, p1
        except Exception as exc:  # noqa: BLE001
            logs.append(tr("Direct dimension phase search failed (fallback p0=p1=0): {p0}", p0=exc))
            return 0.0, 0.0

    def _convert(
        self,
        runtime: CshRuntime,
        experiment: Experiment,
        raw: Path,
        work: Path,
        fid_com_overrides: dict[str, str] | None = None,
    ) -> tuple[bool, list[str]]:
        logs: list[str] = []
        is_nus = experiment.sampling.mode is SamplingMode.NUS
        ok = self._convert_dir(
            runtime, experiment, raw, work, is_nus, logs,
            fid_com_overrides=fid_com_overrides,
        )
        if not ok and is_nus:
            logs.append(
                tr(
                "NUS Conversion requires bruker native recognition (without bruk2pipe "
                "fallback)",
            ))
        return ok, logs

    def _split_slices(
        self,
        runtime: CshRuntime,
        work: Path,
        in_file: str,
        out_dir: Path,
        shift_hz: float,
        logs: list[str],
    ) -> bool:
        """Split a single-file fid into 3D plane slices (a leftover tool from the
        slice-merging era).

        Unused since conversion/merging moved to the single file in
        0.2.199-patch16; kept for compatibility with old working directories and
        for debugging. When out_dir already holds slices the split is skipped
        (avoiding duplicates or failure)."""
        out_dir.mkdir(parents=True, exist_ok=True)
        if _slice_candidates(out_dir, Path(in_file).stem):
            existing = _slice_candidates(out_dir, Path(in_file).stem)
            logs.append(
                tr(
                    "slice {p0}: already has slice-form output ({p1}), skip "
                    "splitting",
                    p0=out_dir.name,
                    p1=len(existing),
                )
            )
            return True
        pattern = f"{out_dir.relative_to(work)}/test%03d.fid"
        cmd = ["xyz2pipe", "-in", in_file, "-x"]
        if shift_hz:
            cmd += ["|", "nmrPipe", "-fn", "PS", "-rs", f"{shift_hz}Hz"]
        cmd += ["|", "pipe2xyz", "-out", pattern, "-x"]
        result = runtime.run(cmd, cwd=str(work), timeout=600)
        logs.append(tr("slice {p0}: rc={p1}", p0=out_dir.name, p1=result.returncode))
        if result.returncode != 0 or not list(out_dir.glob("test*.fid")):
            return False
        logs.append(tr("Number of slices: {p0}", p0=len(list(out_dir.glob('test*.fid')))))
        return True

    def _merge_single_fid(
        self,
        runtime: CshRuntime,
        work: Path,
        n_segments: int,
        dataset_id: str,
        logs: list[str],
    ) -> bool:
        """Merge the full-grid single-file fids of every segment pairwise in the time
        domain with addNMR (following the lab's 2ndAdd.com)."""
        merged = work / "merged"
        if merged.exists():
            shutil.rmtree(merged)  # idempotent: clear any earlier merge first
        merged.mkdir(parents=True)
        shutil.copy2(
            work / "seg_001" / f"{dataset_id}.fid", merged / f"{dataset_id}.fid"
        )
        for index in range(2, n_segments + 1):
            tmp = work / "merge_tmp"
            if tmp.exists():
                shutil.rmtree(tmp)
            tmp.mkdir(parents=True)
            result = runtime.run(
                [
                    "addNMR",
                    "-in1",
                    f"seg_{index:03d}/{dataset_id}.fid",
                    "-in2",
                    f"merged/{dataset_id}.fid",
                    "-out",
                    f"merge_tmp/{dataset_id}.fid",
                    "-verb",
                ],
                cwd=str(work),
                timeout=600,
            )
            logs.append(f"addNMR seg_{index:03d}: rc={result.returncode}")
            if result.returncode != 0 or not (
                tmp / f"{dataset_id}.fid"
            ).is_file():
                return False
            shutil.rmtree(merged)
            shutil.move(str(tmp), str(merged))
        logs.append(
            tr(
            "Multi-segment merge completed -> merged/{p0}.fid ({p1} "
            "parts)",
            p0=dataset_id,
            p1=n_segments,
        )
        )
        return True

    def _merge_slices(
        self,
        runtime: CshRuntime,
        work: Path,
        n_segments: int,
        logs: list[str],
    ) -> bool:
        """Merge the slice-style fids of every segment pairwise in the time domain
        with addNMR (merged/fid/test%03d.fid).

        0.2.199-patch28: when any segment is in slice form, all of them are
        normalised to slices first and then merged pairwise by slice index;
        symmetric to _merge_single_fid (single-file merging goes to
        merged/{dataset_id}.fid). 0.2.199-patch29ct: implemented (the call site
        existed but the method was missing, so generating an FID raised
        AttributeError)."""
        merged = work / "merged"
        if merged.exists():
            shutil.rmtree(merged)  # idempotent: clear the earlier merge first
        merged.mkdir(parents=True)
        (merged / "fid").mkdir(parents=True)
        first_slices = sorted((work / "seg_001" / "fid").glob("test*.fid"))
        if not first_slices:
            return False
        for sl in first_slices:
            shutil.copy2(sl, merged / "fid" / sl.name)
        for index in range(2, n_segments + 1):
            seg_slices = sorted(
                (work / f"seg_{index:03d}" / "fid").glob("test*.fid")
            )
            if len(seg_slices) != len(first_slices):
                logs.append(
                    tr(
                        "part {p0} has {p1} slices, inconsistent with first part "
                        "({p2})",
                        p0=index,
                        p1=len(seg_slices),
                        p2=len(first_slices),
                    )
                )
                return False
            tmp = work / "merge_tmp"
            if tmp.exists():
                shutil.rmtree(tmp)
            (tmp / "fid").mkdir(parents=True)
            ok = True
            for sl in seg_slices:
                result = runtime.run(
                    [
                        "addNMR",
                        "-in1",
                        f"seg_{index:03d}/fid/{sl.name}",
                        "-in2",
                        f"merged/fid/{sl.name}",
                        "-out",
                        f"merge_tmp/fid/{sl.name}",
                        "-verb",
                    ],
                    cwd=str(work),
                    timeout=600,
                )
                if result.returncode != 0 or not (
                    tmp / "fid" / sl.name
                ).is_file():
                    ok = False
                    break
            logs.append(
                tr("addNMR seg_{p0:03d} slice merge: rc={p1}", p0=index, p1='ok' if ok else 'fail')
            )
            if not ok:
                return False
            shutil.rmtree(merged)
            shutil.move(str(tmp), str(merged))
        logs.append(tr("Multi-slice merging completed -> merged/fid/ ({p0} parts)", p0=n_segments))
        return True

    def _convert_segments(
        self,
        runtime: CshRuntime,
        experiment: Experiment,
        work: Path,
        shifts: list[float],
        fid_com_overrides: dict[str, str] | None = None,
    ) -> tuple[bool, list[str]]:
        """Multi-segment experiments: convert each segment with bruker, shift it
        (optional), then merge.

        Following the lab's 1stfid.com/2ndAdd.com flow: a segmented experiment is
        one experiment split by acquisition time, and the fid.com parameters bruker
        -AUTO generates are the same for every segment (all use the NusTD grid;
        acqu2s TD reflects only each segment's own point count), so each segment
        is converted independently and addNMR then merges them pairwise in the
        time domain. 0.2.199-patch28: work with bruker's automatic output -- when
        every segment is a single file, merge single files
        (merged/{dataset_id}.fid); when any segment is in slice form, normalise
        all of them to slices and merge those (merged/fid/test%03d.fid). Each
        segment may carry an -rs frequency shift (a single file uses PS -rs
        directly; the slice form streams PS -rs through xyz2pipe).
        """
        logs: list[str] = []
        is_nus = experiment.sampling.mode is SamplingMode.NUS
        if is_nus:
            # 0.2.124: bad points are deleted from the source ser/nuslist with a
            # backup kept (user request, idempotent)
            self._clean_source_nus(
                experiment, [Path(seg) for seg in experiment.segments], logs
            )
        slice_mode = False
        for index, seg_dir in enumerate(experiment.segments, start=1):
            seg_work = work / f"seg_{index:03d}"
            seg_work.mkdir(parents=True, exist_ok=True)
            if not self._convert_dir(
                runtime, experiment, Path(seg_dir), seg_work, is_nus, logs,
                fid_com_overrides=fid_com_overrides,
            ):
                return False, logs + [(
                    tr(
                    "data part {p0} ({p1}) conversion "
                    "failed",
                    p0=index,
                    p1=Path(seg_dir).name,
                )
                )]
            seg_single = (
                seg_work / f"{experiment.dataset_id}.fid"
            ).is_file()
            if not seg_single:
                slice_mode = True
                logs.append(tr(
                    "part {p0}: bruker outputs sliced fid (compatible with "
                    "merging)",
                    p0=index,
                ))
            shift_hz = shifts[index - 1] if index - 1 < len(shifts) else 0.0
            if not shift_hz:
                continue
            if seg_single:
                fid_name = f"{experiment.dataset_id}.fid"
                shifted = seg_work / f"{experiment.dataset_id}_shifted.fid"
                result = runtime.run(
                    [
                        "nmrPipe",
                        "-in",
                        fid_name,
                        "|",
                        "nmrPipe",
                        "-fn",
                        "PS",
                        "-rs",
                        f"{shift_hz}Hz",
                        "-out",
                        shifted.name,
                        "-ov",
                    ],
                    cwd=str(seg_work),
                    timeout=600,
                )
                logs.append(
                    tr(
                        "part {p0} frequency shift -rs {p1}Hz: "
                        "rc={p2}",
                        p0=index,
                        p1=shift_hz,
                        p2=result.returncode,
                    )
                )
                if result.returncode == 0 and shifted.is_file():
                    shifted.replace(seg_work / fid_name)
            else:
                # Slice form: PS -rs on the xyz2pipe stream, rewriting the whole batch
                fid_dir = seg_work / "fid"
                shifted_dir = seg_work / "fid_shifted"
                if shifted_dir.exists():
                    shutil.rmtree(shifted_dir)
                result = runtime.run(
                    [
                        "xyz2pipe",
                        "-in",
                        "fid/test%03d.fid",
                        "-x",
                        "|",
                        "nmrPipe",
                        "-fn",
                        "PS",
                        "-rs",
                        f"{shift_hz}Hz",
                        "|",
                        "pipe2xyz",
                        "-out",
                        "fid_shifted/test%03d.fid",
                        "-x",
                    ],
                    cwd=str(seg_work),
                    timeout=600,
                )
                logs.append(
                    tr(
                        "part {p0} slice-form frequency shift -rs {p1}Hz: "
                        "rc={p2}",
                        p0=index,
                        p1=shift_hz,
                        p2=result.returncode,
                    )
                )
                if result.returncode == 0 and list(
                    shifted_dir.glob("test*.fid")
                ):
                    shutil.rmtree(fid_dir)
                    shifted_dir.replace(fid_dir)
        # 2026-09-23 (user): for multiple parts (segmented acquisition / repeated-experiment
        # averaging) the direct-dimension peaks are aligned before addNMR sums them -
        # unaligned field drift broadens or splits the peaks. With a manual
        # segment_shift_hz the manual values are used (each part already got its PS -rs
        # above) and no automatic check runs.
        if len(experiment.segments) >= 2:
            if any(shifts):
                logs.append(tr(
                    "Manual per-part frequency shift (segment_shift_hz) is set; the automatic "
                    "inter-part field drift check was skipped",
                ))
            else:
                self._correct_group_drift(runtime, experiment, work, logs)
        if not slice_mode:
            if not self._merge_single_fid(
                runtime, work, len(experiment.segments),
                experiment.dataset_id, logs,
            ):
                return False, logs + [tr("Multiple single file merge failed")]
        else:
            # Split the single-file segments into slices and merge slices throughout
            for index in range(1, len(experiment.segments) + 1):
                seg_work = work / f"seg_{index:03d}"
                if (seg_work / f"{experiment.dataset_id}.fid").is_file():
                    if not self._split_slices(
                        runtime,
                        seg_work,
                        f"{experiment.dataset_id}.fid",
                        seg_work / "fid",
                        0.0,
                        logs,
                    ):
                        return False, logs + [tr("data segment {p0} Slicing failed", p0=index)]
            if not self._merge_slices(
                runtime, work, len(experiment.segments), logs
            ):
                return False, logs + [tr("Multiple slice merging failed")]
        return True, logs

    def _segment_fid_inputs(self, work: Path, experiment: Experiment) -> list[list[Path]]:
        """Converted fid files per part: one file per part, or a batch for a slice stream."""
        inputs: list[list[Path]] = []
        for index in range(1, len(experiment.segments) + 1):
            seg_work = work / f"seg_{index:03d}"
            single = seg_work / f"{experiment.dataset_id}.fid"
            if single.is_file():
                inputs.append([single])
                continue
            inputs.append(sorted((seg_work / "fid").glob("test*.fid")))
        return inputs

    def _apply_segment_shift(
        self,
        runtime: CshRuntime,
        experiment: Experiment,
        work: Path,
        index: int,
        shift_hz: float,
        report: Any,
        logs: list[str],
        audit: QcAuditLog,
    ) -> bool:
        """Write one part's drift correction into its fid.com and re-convert it.

        Returns True when the part was patched and re-converted successfully.
        """
        seg_work = work / f"seg_{index + 1:03d}"
        fid_com = seg_work / "fid.com"
        raw_dir = Path(experiment.segments[index])
        if not fid_com.is_file():
            logs.append(tr(
                "part {p0}: fid.com not found, field drift correction skipped",
                p0=index + 1,
            ))
            return False
        patched, applied = insert_ps_shift(
            fid_com.read_text(encoding="utf-8", errors="replace"), shift_hz
        )
        if not applied:
            logs.append(tr(
                "part {p0}: no MULT line in fid.com, field drift correction skipped",
                p0=index + 1,
            ))
            return False
        fid_com.write_text(patched, encoding="utf-8", newline="\n")
        # Same convention as _convert_dir: fid.com lives in the part work directory and its
        # relative paths (./ser) resolve against the original conversion directory
        result = runtime.run(["csh", str(fid_com)], cwd=str(raw_dir), timeout=900)
        logs.append(tr(
            "part {p0} field drift re-conversion -rs {p1}Hz: rc={p2}",
            p0=index + 1,
            p1=f"{shift_hz:.4f}",
            p2=result.returncode,
        ))
        if result.returncode != 0:
            return False
        if not self._finalize_converted_fid(
            raw_dir, seg_work, experiment.dataset_id, logs, ndim=experiment.ndim
        ):
            return False
        audit.record(
            QcAction(
                issue_detected=tr("inter-part direct-dimension field drift"),
                location=f"seg_{index + 1:03d}/fid.com",
                detection_rule=tr(
                    "direct-dimension frequency offset of each part vs part 1 (over {p0} Hz)",
                    p0=DRIFT_HZ_MIN,
                ),
                action_taken="fid_com_ps_rs_shift",
                before_state={
                    "shift_hz": 0.0,
                    "offset_hz": round(float(report.offsets_hz[index] or 0.0), 4),
                    "offset_ppm": round(float(report.offsets_ppm[index] or 0.0), 5),
                },
                after_state={"shift_hz": round(float(shift_hz), 4)},
                extra={"file": "fid.com", "part": index + 1},
            )
        )
        return True

    def _correct_group_drift(
        self,
        runtime: CshRuntime,
        experiment: Experiment,
        work: Path,
        logs: list[str],
    ) -> dict[str, Any] | None:
        """Inter-part field drift: measure each part's direct peak, patch its fid.com, re-convert.

        Reference = part 1; the criterion (Hz: |d| > 1.5, ppm recorded only) and the sign
        convention
        live in ``workflow/field_drift``. The result is written to ``work/field_drift.json``
        (the conversion provenance carries it into ``*.fid.conversion.json``) and every patched
        part is recorded in the QC audit. When a peak cannot be measured, or a residual stays
        over the threshold after re-checking, it is only reported - the conversion is never
        blocked and the shift is never escalated.
        """
        axes = direct_axis_hz(experiment)
        if axes is None:
            logs.append(tr(
                "Inter-part field drift check skipped (direct-dimension SW/OBS unknown)",
            ))
            write_field_drift_record(
                work, {"checked": False, "reason": "direct-axis-unknown"}
            )
            return None
        sw_hz, sf_mhz = axes
        audit = QcAuditLog(work)
        record: dict[str, Any] = {
            "checked": True,
            "reference": 1,
            "sw_hz": round(sw_hz, 3),
            "hz_min": DRIFT_HZ_MIN,
            "ppm_reference": DRIFT_PPM_THRESHOLD,
            "rounds": [],
            "corrected_parts": [],
        }
        last = None
        for _round_index in range(1, MAX_ROUNDS + 1):
            inputs = self._segment_fid_inputs(work, experiment)
            if any(not paths for paths in inputs):
                logs.append(tr(
                    "Inter-part field drift check skipped: a part has no converted fid",
                ))
                record["checked"] = False
                record["reason"] = "missing-part-fid"
                break
            report = detect_group_drift(inputs, sw_hz=sw_hz, sf_mhz=sf_mhz)
            logs += report.reports
            record["rounds"].append(report.as_dict())
            last = report
            if not report.needs_shift:
                break
            for index, shift_hz in sorted(report.needs_shift.items()):
                if self._apply_segment_shift(
                    runtime, experiment, work, index, shift_hz, report, logs, audit
                ):
                    record["corrected_parts"].append(index + 1)
        if last is not None:
            record["max_abs_ppm_after"] = round(last.max_abs_ppm(), 5)
            record["within_threshold_after"] = not last.needs_shift
        write_field_drift_record(work, record)
        return record

    def _clean_source_nus(
        self,
        experiment: Experiment,
        raw_dirs: list[Path],
        logs: list[str],
    ) -> tuple[int, list[tuple[int, ...]], bool]:
        """Source-level cleanup of NUS bad points (user request, 0.2.124): the
        deletion happens on the original ser file rather than by clearing the
        generated fid. ser is deleted in whole nuslist rows (bytes per row =
        ser_size / number of nuslist rows, and this must divide exactly) with the
        nuslist cleaned in step; before deleting, ser/nuslist are backed up as
        .bak (first time only, idempotent), and os.replace breaks hard/symbolic
        links so the external originals stay untouched. Multi-segment raw_dirs
        validation rules (by type since 0.2.199-patch29cx):
        - repeated-experiment co-addition (same NUS points, repeat_nus): the same
          point in different segments is a normal co-addition and is kept; only
          duplicates within a segment or out-of-range points are bad points;
        - segmented/mixed/unknown: a point repeated across segments counts as a
          bad point and is dropped (the original 0.2.124 rule).

        Returns (valid point count, bad-point list, whether source-level deletion
        actually ran); when ser is missing or its size does not divide by row,
        the source-level deletion is skipped and removed=False is returned (the
        caller falls back to clearing the generated FID).
        """
        per_dir: list[list[tuple[int, ...]]] = []
        entries: list[tuple[int, int, tuple[int, ...]]] = []
        for dir_idx, raw_dir in enumerate(raw_dirs):
            nuslist_path = Path(raw_dir) / "nuslist"
            pts = (
                [tuple(p) for p in read_nuslist(nuslist_path)]
                if nuslist_path.is_file()
                else []
            )
            per_dir.append(pts)
            for row_idx, point in enumerate(pts):
                entries.append((dir_idx, row_idx, point))
        if not entries:
            return 0, [], False
        points = [entry[2] for entry in entries]
        repeat = False
        if len(raw_dirs) > 1:
            try:
                from core.data.bruker_reader import classify_segment_kind

                repeat = (
                    classify_segment_kind([Path(p) for p in raw_dirs])
                    == "repeat_nus"
                )
            except Exception:  # noqa: BLE001 - conservative: treat a failure as segmented
                repeat = False
        if repeat:
            # Validate per segment (duplicates within a segment, out-of-range
            # points); identical points across segments are all kept and deduplicated
            valid, bad, reasons = [], [], {}
            for dir_pts in per_dir:
                v, b, r = _validate_nus_points(dir_pts, experiment)
                valid += v
                for point in b:
                    if point not in bad:
                        bad.append(point)
                    reasons.setdefault(point, r.get(point, []))
            seen: set[tuple[int, ...]] = set()
            unique: list[tuple[int, ...]] = []
            for point in valid:
                if point not in seen:
                    seen.add(point)
                    unique.append(point)
            valid = unique
        else:
            valid, bad, reasons = _validate_nus_points(points, experiment)
        if not bad:
            return len(valid), [], False
        kept = set(valid)
        drop_by_dir: dict[int, set[int]] = {}
        for dir_idx, row_idx, point in entries:
            if point not in kept:
                drop_by_dir.setdefault(dir_idx, set()).add(row_idx)
        removed_any = False
        # LOG-011 (2026-09-12): record the real cleanup result per directory; only
        # bad points that really were deleted from the source may be reported as
        # "deleted".
        cleaned_dirs: set[int] = set()
        for dir_idx, drop_rows in drop_by_dir.items():
            if not drop_rows:
                continue
            raw_dir = Path(raw_dirs[dir_idx])
            nuslist_path = raw_dir / "nuslist"
            data_file = raw_dir / "ser"
            if not data_file.is_file():
                logs.append(
                    tr(
                        "⚠ bad point must be deleted from the source ser, but {p0}/ser is missing; "
                        "falling back to cleaning the generated "
                        "FID",
                        p0=raw_dir.name,
                    )
                )
                continue
            n_rows = len(per_dir[dir_idx])
            data_size = data_file.stat().st_size
            if n_rows <= 0 or data_size % n_rows != 0:
                logs.append(
                    tr(
                        "⚠ {p0}/ser size {p1} Cannot press nuslist {p2} rows divide evenly (the "
                        "ser row count may not match the nuslist point count); falling back to "
                        "cleaning the generated FID, not handled "
                        "automatically",
                        p0=raw_dir.name,
                        p1=data_size,
                        p2=n_rows,
                    )
                )
                continue
            # 0.2.195: the ser byte layout varies with the sampling parameters
            # (direct-dimension TD padding + word size + redundancy count), so it is
            # derived and checked from those parameters to avoid deleting by the wrong
            # block size and misaligning the reconstruction
            layout = _ser_point_layout(experiment, data_size, n_rows)
            if layout is None:
                td0 = effective_td(experiment)[0] if effective_td(experiment) else "?"
                logs.append(
                    tr(
                        "⚠ the {p0}/ser layout cannot be determined from the sampling parameters "
                        "(direct-dimension TD={p1}, {p2} bytes per point; redundancy count "
                        "inconsistent or word size unknown); falling back to cleaning the "
                        "generated FID, not handled "
                        "automatically",
                        p0=raw_dir.name,
                        p1=td0,
                        p2=data_size // n_rows,
                    )
                )
                continue
            row_bytes, _vec_bytes, _redundancy = layout
            try:
                backup = raw_dir / "ser.bak"
                if not backup.exists():
                    shutil.copy2(data_file, backup)
                    logs.append(
                        tr(
                            "The source ser has been backed up -> {p0}/ser.bak ({p1} "
                            "bytes)",
                            p0=raw_dir.name,
                            p1=data_size,
                        )
                    )
                raw = data_file.read_bytes()
                kept_bytes = b"".join(
                    raw[i * row_bytes : (i + 1) * row_bytes]
                    for i in range(n_rows)
                    if i not in drop_rows
                )
                tmp = raw_dir / "ser.tmp"
                tmp.write_bytes(kept_bytes)
                os.replace(tmp, data_file)  # breaks hard/symbolic links; the
                # external original is untouched
                nus_backup = raw_dir / "nuslist.bak"
                if not nus_backup.exists():
                    shutil.copy2(nuslist_path, nus_backup)
                text = "".join(
                    " ".join(str(v) for v in p) + "\n"
                    for i, p in enumerate(per_dir[dir_idx])
                    if i not in drop_rows
                )
                nus_tmp = raw_dir / "nuslist.tmp"
                nus_tmp.write_text(text, encoding="utf-8", newline="\n")
                os.replace(nus_tmp, nuslist_path)
                removed_any = True
                cleaned_dirs.add(dir_idx)
                logs.append(
                    tr(
                        "Source cleanup {p0}: nuslist {p1} → {p2} OK, ser {p3} → {p4} bytes "
                        "(backup "
                        ".bak)",
                        p0=raw_dir.name,
                        p1=n_rows,
                        p2=n_rows - len(drop_rows),
                        p3=data_size,
                        p4=len(kept_bytes),
                    )
                )
            except OSError as exc:  # noqa: BLE001 - a failed cleanup must not block
                logs.append(
                    tr(
                    "⚠ {p0} source cleanup failed ({p1}), falling back to cleaning the generated "
                    "FID",
                    p0=raw_dir.name,
                    p1=exc,
                ))
        for point in bad:
            owners = {dir_idx for dir_idx, _row, value in entries if value == point}
            detail = ", ".join(reasons.get(point, []) or [tr("unknown")])
            if owners and owners <= cleaned_dirs:
                logs.append(
                    tr(
                        "⚠ Sampling bad point detected {p0}: {p1},deleted from the source "
                        "ser/nuslist (backup "
                        ".bak)",
                        p0=point,
                        p1=detail,
                    )
                )
            else:
                logs.append(
                    tr(
                        "⚠ Sampling bad point detected {p0}: {p1},not deleted from the source (ser "
                        "missing / layout undecidable / write failed), fell back to zeroing while "
                        "the FID is generated; the original ser/nuslist is "
                        "untouched",
                        p0=point,
                        p1=detail,
                    )
                )
        return len(valid), bad, removed_any

    def _write_merged_nuslist(
        self,
        work: Path,
        segment_dirs: list[Path],
        experiment: Experiment,
        logs: list[str],
    ) -> tuple[int, list[tuple[int, ...]]]:
        """Merge the per-segment nuslists and detect bad sampling points (out of
        range or duplicated); returns (valid point count, bad-point list).

        Segmented sampling can contain the odd "mis-specified and mis-acquired"
        point (e.g. 27 2350 in cc/63, where the F1 index is far beyond the grid
        limit). Bad points are dropped from the merged nuslist and the caller
        cleans the matching FID, while the user is warned with a ⚠ marker.
        0.2.199-patch29cx: for repeated-experiment co-addition (same NUS points) a
        point shared across segments is a normal co-addition and the merged,
        deduplicated list keeps a single copy; segmented/unknown conservatively
        follows the original rule (a point repeated across segments counts as a bad
        point).
        """
        all_points: list[tuple[int, ...]] = []
        for seg_dir in segment_dirs:
            nuslist_path = Path(seg_dir) / "nuslist"
            if nuslist_path.is_file():
                all_points += [tuple(p) for p in read_nuslist(nuslist_path)]
        repeat = False
        if len(segment_dirs) > 1:
            try:
                from core.data.bruker_reader import classify_segment_kind

                repeat = (
                    classify_segment_kind([Path(p) for p in segment_dirs])
                    == "repeat_nus"
                )
            except Exception:  # noqa: BLE001 - conservative: treat a failure as segmented
                repeat = False
        if repeat:
            valid, bad, reasons = [], [], {}
            for seg_dir in segment_dirs:
                nl_path = Path(seg_dir) / "nuslist"
                seg_pts = (
                    [tuple(p) for p in read_nuslist(nl_path)]
                    if nl_path.is_file()
                    else []
                )
                v, b, r = _validate_nus_points(seg_pts, experiment)
                valid += v
                for point in b:
                    if point not in bad:
                        bad.append(point)
                    reasons[point] = r.get(point, [])
            seen: set[tuple[int, ...]] = set()
            unique: list[tuple[int, ...]] = []
            for point in valid:
                if point not in seen:
                    seen.add(point)
                    unique.append(point)
            valid = unique
        else:
            valid, bad, reasons = _validate_nus_points(all_points, experiment)
        for point in bad:
            logs.append(
                tr(
                    "⚠ Sampling bad point detected {p0}:{p1},dropped from the merged "
                    "nuslist",
                    p0=point,
                    p1=','.join(reasons.get(point, []) or ['unknown']),
                )
            )
        text = "".join(" ".join(str(v) for v in point) + "\n" for point in valid)
        (work / "nuslist").write_text(text, encoding="utf-8")
        logs.append(tr(
            "Merge nuslist:{p0} sampling point (bad point "
            "{p1})",
            p0=len(valid),
            p1=len(bad),
        ))
        return len(valid), bad

    def _zero_bad_point_fid(
        self,
        work: Path,
        bad_points: list[tuple[int, ...]],
        logs: list[str],
        in_file: str | None = None,
        dataset_id: str | None = None,
        *,
        audit: QcAuditLog | None = None,
    ) -> None:
        """Clear the FID data matching a bad point (only a fallback since 0.2.124,
        for when source-level deletion is not possible).

        Within the valid grid a bad point (out of range, duplicated, ...) maps to
        the States real pair (2y, 2y+1): in 3D those rows of slice
        test{z:03d}.fid / {dataset_id}{z:03d}.fid and in 2D the same rows of the
        single fid file are zeroed; an out-of-range point has no slot and is only
        recorded.
        """
        if not bad_points:
            return
        import nmrglue as ng

        if in_file and "%" in in_file:
            base = work / in_file.replace("%03d", "{z:03d}").replace("%04d", "{z:03d}")
        else:
            base = work / (in_file or f"{Path(in_file or '').name}") if in_file else None
        slice_dir = work / "merged" / "fid"
        if not slice_dir.is_dir():
            slice_dir = work / "fid"
        for point in bad_points:
            if not point:
                continue
            f2 = int(point[0])
            f1 = int(point[1]) if len(point) > 1 else None
            targets: list[Path] = []
            if f1 is not None and slice_dir.is_dir():
                # States layout: the complex point (f2, f1) lands in rows
                # 2*f2 / 2*f2+1 of slices 2*f1+1 / 2*f1+2 (corrected in 0.2.195:
                # test{f1} used to be used, which zeroed the wrong slice and broke
                # merging/reconstruction)
                for zz in (2 * f1 + 1, 2 * f1 + 2):
                    t = slice_dir / f"test{zz:03d}.fid"
                    if not t.is_file() and dataset_id:
                        t = slice_dir / f"{dataset_id}{zz:03d}.fid"
                    if t.is_file():
                        targets.append(t)
                if not targets:
                    logs.append(
                        tr(
                            "⚠ bad point {p0}: Out of bounds, no corresponding slice, merge FID No "
                            "need to clean "
                            "up",
                            p0=point,
                        )
                    )
                    continue
            elif f1 is None and base is not None and base.is_file():
                targets = [base]
            if not targets:
                logs.append(tr(
                    "⚠ bad point {p0}:No corresponding FID file, no need to "
                    "clean",
                    p0=point,
                ))
                continue
            for target in targets:
                try:
                    dic, data = ng.pipe.read(str(target))
                    arr = np.asarray(data)
                    if arr.ndim < 2:
                        continue
                    rows = [r for r in (2 * f2, 2 * f2 + 1) if r < arr.shape[0]]
                    if not rows:
                        logs.append(
                            tr(
                            "⚠ bad point {p0}:The line is out of bounds, no need to clean "
                            "up",
                            p0=point,
                        ))
                        continue
                    arr[rows, :] = 0
                    ng.pipe.write(str(target), dic, arr, overwrite=True)
                    logs.append(
                        tr(
                            "⚠ bad point {p0}: the corresponding FID increment has been "
                            "zeroed({p1} OK "
                            "{p2})",
                            p0=point,
                            p1=target.name,
                            p2=rows,
                        )
                    )
                except Exception as exc:  # noqa: BLE001 - a failed cleanup must not block
                    logs.append(tr("⚠ bad point {p0}:FID Cleanup failed {p1}", p0=point, p1=exc))

    def _clean_work_nuslist(
        self, work: Path, experiment: Experiment, logs: list[str]
    ) -> tuple[int, list[tuple[int, ...]]]:
        """Validate and clean the working-directory nuslist (single NUS data):
        drop bad points and warn with a ⚠ marker. Returns (valid point count,
        bad-point list)."""
        nuslist_path = work / "nuslist"
        if not nuslist_path.is_file():
            return 0, []
        points = [tuple(p) for p in read_nuslist(nuslist_path)]
        valid, bad, reasons = _validate_nus_points(points, experiment)
        if bad:
            text = "".join(" ".join(str(v) for v in point) + "\n" for point in valid)
            nuslist_path.write_text(text, encoding="utf-8")
            for point in bad:
                logs.append(
                    tr(
                        "⚠ Sampling bad point detected {p0}:{p1},dropped from the nuslist and the "
                        "corresponding FID cleaned "
                        "up",
                        p0=point,
                        p1=','.join(reasons.get(point, []) or ['unknown']),
                    )
                )
            logs.append(
                tr(
                "nuslist cleanup: {p0} → {p1} sampling point (bad point "
                "{p2})",
                p0=len(points),
                p1=len(valid),
                p2=len(bad),
            ))
        return len(valid), bad

    # ------------------------------------------------------------- processing

    def _process(
        self,
        runtime: CshRuntime,
        experiment: Experiment,
        plan: ProcessingPlan,
        work: Path,
        *,
        in_file: str | None = None,
        direct_phase: dict[str, tuple[float, float]] | None = None,
        baseline: dict[str, dict[str, Any]] | None = None,
        window: dict[str, dict[str, Any]] | None = None,
        zero_fill: dict[str, dict[str, Any]] | None = None,
        linewidth_hz: dict[str, float] | None = None,
        points_per_line: float = DEFAULT_POINTS_PER_LINE,
        extract: bool = True,
        ext_lo: str = "10.5",
        ext_hi: str = "6.5",
        sampling: dict[str, Any] | None = None,
        progress: Callable[[str], None] | None = None,
        out_file: str | None = None,
        script_name: str | None = None,
        keep_direct_complex: bool = False,
        keep_complex_all: bool = False,
        preview_axis: str | None = None,
        direct_poly_time: bool = False,
    ) -> tuple[bool, list[str], Path]:
        """Generate and run the NMRPipe processing pipeline (ft2/ft3 output)."""
        logs: list[str] = []
        ext = {1: "ft1", 2: "ft2"}.get(experiment.ndim, "ft3")
        in_file = in_file or f"{experiment.dataset_id}.fid"
        # 0.2.199-patch29ez: an explicit out_file means intermediate rendering
        # (preview/joint/candidate), written uniformly under work/_intermediate
        # (which the ramdisk can take over); the final-run default name stays where
        # it is.
        _render_out = out_file not in (None, "")
        out_file = out_file or f"{experiment.dataset_id}.{ext}"
        if _render_out:
            _render_dir = work / INTERMEDIATE_SUBDIR
            _render_dir.mkdir(parents=True, exist_ok=True)
            out_file = f"{INTERMEDIATE_SUBDIR}/{out_file}"
        if preview_axis:
            script = generate_preview_script(
                experiment,
                plan,
                in_file=in_file,
                out_file=out_file,
                preview_axis=preview_axis,
                fixed_phases=direct_phase,
                baseline=baseline,
                window=window,
                zero_fill=zero_fill,
                ext_lo=ext_lo,
                ext_hi=ext_hi,
                extract=extract,
                sampling=sampling,
            )
        else:
            script = generate_process_script(
                experiment,
                plan,
                in_file=in_file,
                out_file=out_file,
                direct_phase=direct_phase,
                baseline=baseline,
                window=window,
                zero_fill=zero_fill,
                linewidth_hz=linewidth_hz,
                points_per_line=points_per_line,
                extract=extract,
                ext_lo=ext_lo,
                ext_hi=ext_hi,
                sampling=sampling,
                keep_direct_complex=keep_complex_all or keep_direct_complex,
                complex_axes=(frozenset(dim.logical_axis for dim in experiment.dimensions)
                              if keep_complex_all else None),
                direct_poly_time=direct_poly_time,
            )
        process_com = work / (
            script_name or f"{experiment.dataset_id}_process.com"
        )
        process_com.write_text(script, encoding="utf-8", newline="\n")
        run_result = runtime.run(
            ["csh", process_com.name],
            cwd=str(work),
            timeout=7200,
            on_line=(lambda line: progress(line) if progress else None),
        )
        logs.append(f"process.com: rc={run_result.returncode}")
        spectrum = work / out_file
        if (
            run_result.returncode != 0
            or not spectrum.is_file()
            or spectrum.stat().st_size == 0
        ):
            return False, logs + [tr("Not generated {p0}", p0=out_file)], spectrum
        logs.append(tr("spectrum -> {p0}", p0=spectrum))
        return True, logs, spectrum

