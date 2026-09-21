"""Batch execution of parameter combinations: the reference script is the template.

Semantics (user API spec of 2026-09-13; independent picking from 2026-09-14):

- **workflow_id = W0001...**: one row of the user table is one workflow, numbered uniquely;
- **the reference script is the template**: the sweep base is the resolved parameters of
    that condition's reference run (phase locked); a combination overrides only named keys;
- **three parameter layers**: ``parameters_requested`` (the row as given),
    ``parameters_used`` (what the backend got) and ``parameters_resolved`` (the **actual**
    automatic values: actual_p0/actual_p1, SMILE nSigma/thresh, and the noise sigma);
- **combinations pick independently** (2026-09-14): each picks on **its own candidate** at
    the **reference-locked** threshold, writing its own table with ``reference_peak_id`` and
    ``assignment`` empty (matching back to the reference is **external** work);
- **refinement chosen outside**: ``localization`` = ``parabolic`` (default) / ``gaussian`` /
    ``both``; only the chosen table is written;
- **several conditions, one parameter set**: one workflow uses one
    ``parameters_requested`` for all of them, each with its own reference;
- **three-valued status**: ``success`` / ``success_with_warning`` / ``failed``; a
    failing condition is recorded with its reason and the batch carries on;
- **never replace the active spectrum**: candidates go to ``study/workflows/<id>/<cond>/``.

Software boundary (spec J): this module produces **spectra, peak tables and records** only.
CSP, robustness, statistics and significance are downstream work on the peak tables.
"""

from __future__ import annotations

import copy
import difflib
import hashlib
import inspect
import itertools
import json
import logging
import math
import shutil
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.logging_setup import append_run_log_line, attach_run_log, detach_run_log
from core.peaks import axis_units
from core.planning.method_selector import select_method
from core.project.manager import atomic_write_text, sha256_file
from core.version import software_commit, software_version, tool_versions
from nmrforge_api.compat import record_stamp
from nmrforge_api.errors import SweepError
from nmrforge_api.localization_targets import (
    ALL_METHOD_KEYS,
    METHOD_KEYS,
    ConditionalTargets,
    LocalizationTargets,
    combine_target_specs,
    localization_targets_record,
    resolve_localization_targets_by_method,
)
from nmrforge_api.peak_tables import (
    peak_table_digest,
    write_peak_table,
)
from nmrforge_api.peaks import (
    DEFAULT_DETECTION_SIGMA,
    PeakMeasurement,
)
from nmrforge_api.reference import (
    ReferenceSpectrum,
    load_reference,
    reference_runtime_decisions,
)
from nmrforge_api.session import DatasetRef, StudySession, now_iso
from ui_support.i18n import tr
from workflow.pick_peaks import read_spectrum_axes
from workflow.stepwise import read_experiment

logger = logging.getLogger("nmrforge.api.sweep")

DEFAULT_MAX_RUNS = 256

#: workflow status (spec D9)
STATUS_SUCCESS = "success"
STATUS_WARNING = "success_with_warning"
STATUS_FAILED = "failed"
SUCCESS_STATUSES: frozenset[str] = frozenset({STATUS_SUCCESS, STATUS_WARNING})

#: warning codes (spec D9/G3: anything affecting reading must be recorded, not silent)
# 2026-09-14 (independent picking): the peak-tracking warnings are gone
# (peak_not_detected / peak_window_edge / peak_out_of_range /
# and window_points_fallback); a combination reports its own localisation QC only.
WARN_GAUSSIAN_FALLBACK = "gaussian_fallback"
WARN_GAUSSIAN_BOUNDARY_HIT = "gaussian_boundary_hit"
WARN_GAUSSIAN_UNSUPPORTED = "gaussian_unsupported_ndim"
#: the full processing script is missing (spec D1: every workflow keeps its script)
WARN_SCRIPT_NOT_FOUND = "processing_script_not_found"
#: the combination left the spectrum bit-identical to the reference, so the parameter was
WARN_NO_SPECTRUM_CHANGE = "no_spectrum_change"

#: the combination mode's direct-range override disagrees with the reference's frozen range
WARN_DIRECT_RANGE_OVERRIDE = "direct_range_override"
#: one peak table contains rows sharing a coordinate (duplicate localization)
WARN_DUPLICATE_LOCALIZATION = "duplicate_localization"

# phase axes: phase.<axis>.p0|p1 is absolute, phase_delta.<axis>.p0|p1 is a deviation
# from the reference phase (a manual ±5 degree correction, say).
PHASE_PREFIXES: tuple[str, ...] = ("phase.", "phase_delta.")

# keys the backend actually reads (first path segment decides); others can be gridded
# without error and still do nothing, which plan_sweep notes.
_UNIFORM_KEYS: frozenset[str] = frozenset(
    {
        "extract",
        "ext_lo",
        "ext_hi",
        "window",
        "baseline",
        "zero_fill",
        "linewidth_hz",
        "points_per_line",
        "sampling",
        "direct_poly_time",
        "keep_direct_complex",
        "keep_complex_all",
        "segment_shift_hz",
    }
)
_NUS_KEYS: frozenset[str] = _UNIFORM_KEYS | frozenset(
    {
        "nsigma",
        "nSigma",
        "thresh",
        "nthread",
        "nThread",
        "smile_scaling",
        "smile_report",
        "nuslist_file",
        "nuslist_count",
        "fid_noise",
        "fid_noise_seed",
        "timeout_s",
        "direct_phase_search",
        "display_phase_search",
        "light_phase_search",
    }
)

# deterministic and policy parameters are not free knobs (they move the peak set or
# only the convention); gridding them merely warns.
#: threshold keys belong to reference building, not to workflow processing parameters
_PEAK_PICKING_KEYS: frozenset[str] = frozenset(
    {"sigma_multiplier", "min_snr", "peak_threshold", "threshold_sigma"}
)

_DETERMINISTIC_KEYS: frozenset[str] = frozenset(
    {
        "extract",
        "ext_lo",
        "ext_hi",
        "points_per_line",
        "nuslist_file",
        "nuslist_count",
        "timeout_s",
        "direct_poly_time",
        "keep_direct_complex",
        "keep_complex_all",
        "segment_shift_hz",
        "fid_noise",
        "fid_noise_seed",
    }
)

# writing them breaks phase locking (or is ignored) -> raise and name the alternative.
_LOCKED_AXIS_KEYS: frozenset[str] = frozenset(
    {"phases", "direct_phase", "phase_route", "sampling.auto_phase"}
)


def workflow_id_for(index: int) -> str:
    """Combination index -> workflow_id (spec C4: W0001, W0002...)."""
    return f"W{int(index):04d}"


def is_phase_axis(key: str) -> bool:
    """Whether this key is a phase axis (the phase./phase_delta. prefix)."""
    return str(key).startswith(PHASE_PREFIXES)


def parse_phase_axis(key: str) -> tuple[str, str, str]:
    """``phase.<axis>.p0`` / ``phase_delta.<axis>.p1`` -> (kind, axis, comp).

    kind is ``phase`` (absolute) or ``phase_delta`` (relative); comp is ``p0``/``p1``.
    """
    kind, _, rest = str(key).partition(".")
    if kind not in ("phase", "phase_delta") or not rest:
        raise SweepError(
            tr(
                "malformed phase axis: {p0} (expected phase.<axis>.p0 or "
                "phase_delta.<axis>.p1)",
                p0=key,
            )
        )
    axis, _, comp = rest.partition(".")
    if axis not in ("F1", "F2", "F3") or comp not in ("p0", "p1"):
        raise SweepError(tr("malformed phase axis: {p0} (axis F1/F2/F3, component p0/p1)", p0=key))
    return kind, axis, comp


def apply_phase_axes(
    base_phase: Mapping[str, Sequence[float]],
    phase_part: Mapping[str, float],
) -> dict[str, list[float]]:
    """Apply the phase axes to the reference phases -> per-axis PS (p0, p1).

    ``phase_delta.*`` adds a deviation; ``phase.*`` sets an absolute value.
    """
    effective = {
        str(axis): [float(values[0]), float(values[1])]
        for axis, values in (base_phase or {}).items()
    }
    for key, value in phase_part.items():
        kind, axis, comp = parse_phase_axis(key)
        current = list(effective.get(axis, [0.0, 0.0]))
        index = 0 if comp == "p0" else 1
        if kind == "phase":
            current[index] = float(value)
        else:
            current[index] = float(current[index]) + float(value)
        effective[axis] = current
    return effective


#: the **only** detection key a combination table may carry: per-combination refinement.
#: it never reaches the backend (2026-09-14: combinations pick independently).
DETECTION_KEYS: frozenset[str] = frozenset(
    {
        "localization",
        "localizations",
        "localization_method",
        "targets",
    }
)

#: threshold keys (sigma / min SNR) are chosen when the reference is built and
#: **always raise** in a combination table (user, 2026-09-14: it must stay locked).
_THRESHOLD_KEYS: frozenset[str] = frozenset(
    {"sigma_multiplier", "min_snr", "threshold_sigma", "peak_threshold"}
)


def _detection_subkey(key: Any) -> str | None:
    """A grid/combination key -> its detection sub-key, or None when it is not one."""
    name = str(key)
    if name.startswith("detection."):
        return name.split(".", 1)[1]
    if name.startswith("localization."):
        # localization.targets: the target-peak list for targeted localization
        return name.split(".", 1)[1]
    if name in DETECTION_KEYS or name in _THRESHOLD_KEYS:
        return name
    return None


def _locked_threshold_error(key: Any) -> SweepError:
    """Changing a threshold in a table raises: it is locked to the reference."""
    return SweepError(
        tr(
            "{p0!r} in a combination table is a **picking threshold**: it is chosen when the "
            "reference is built (CLI `peaks --sigma N`, or ensure_reference_peaks(...)). Once "
            "frozen, every workflow must match it, so it cannot be perturbed. To change it, "
            "rebuild the reference (force=True, or delete that condition's study/reference/<key>/ "
            "and run "
            "again).",
            p0=key,
        )
    )


def _detection_note(key: Any) -> str:
    """A note for a detection key (never blocking)."""
    if str(key).split(".", 1)[-1].startswith("targets"):
        return (
            tr(
                "note: {p0} is this row's **target-peak list** (a CSV with a peak_id column): only "
                "the listed peaks take the chosen method's refinement (a per-method key applies to "
                "that method only); detection, row count and peak_id numbering are unchanged and "
                "unlisted peaks are "
                "kept",
                p0=key,
            )
        )
    return (
        tr(
            "note: {p0} overrides the **refinement** per combination (parabolic/gaussian/both); "
            "only the chosen table is written (the threshold stays locked to the "
            "reference)",
            p0=key,
        )
    )


def locked_detection_sigma(
    reference: ReferenceSpectrum,
) -> tuple[float, str]:
    """The **reference-locked** picking threshold (sigma multiples) and where it came from.

    The threshold is settled in reference mode: the recorded
    ``peak_params['detection']['sigma_multiplier']`` first, then a top-level
    ``sigma_multiplier``; failing both, the 35 sigma default, still marked as such.
    """
    detection = dict(reference.peak_params.get("detection") or {})
    value = detection.get("sigma_multiplier")
    if value in (None, ""):
        value = reference.peak_params.get("sigma_multiplier")
    if value in (None, ""):
        return float(DEFAULT_DETECTION_SIGMA), "reference(default:35sigma)"
    return float(value), "reference"


def _localization_methods(value: Any) -> list[str]:
    """A localisation spec -> the method list: parabolic / gaussian / both."""
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple)) else [value]
    out: list[str] = []
    for item in items:
        name = str(item).strip().lower()
        if name in ("", "none"):
            continue
        if name in ("both", "all"):
            for method in ("parabolic", "gaussian"):
                if method not in out:
                    out.append(method)
            continue
        if name not in ("parabolic", "gaussian"):
            raise SweepError(
                tr("unknown localisation: {p0!r} (parabolic / gaussian / both)", p0=value)
            )
        if name not in out:
            out.append(name)
    return out


def split_combo(
    combo: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, float], dict[str, Any]]:
    """A combination -> (processing overrides, phase-axis overrides, detection overrides).

    Of the detection keys only the **refinement** (``localization``) is allowed; it is
    stripped from the processing parameters. Threshold keys always raise.
    """
    params: dict[str, Any] = {}
    phases: dict[str, float] = {}
    detection: dict[str, Any] = {}
    for key, value in combo.items():
        name = str(key)
        if is_phase_axis(name):
            phases[name] = float(value)
            continue
        sub = _detection_subkey(name)
        if sub is not None:
            if sub in _THRESHOLD_KEYS:
                raise _locked_threshold_error(name)
            if sub == "targets":
                # localization.targets = a target-peak CSV (restrict the refined peaks)
                detection["targets"] = value
                continue
            if sub.startswith("targets."):
                # localization.targets.<method>: restrict that method only (per-method)
                method = sub.split(".", 1)[1].strip().lower()
                if method not in METHOD_KEYS + ALL_METHOD_KEYS:
                    raise SweepError(
                        tr(
                            "unknown per-method target key: {p0!r} (methods are limited to "
                            "{p1})",
                            p0=name,
                            p1=METHOD_KEYS + ALL_METHOD_KEYS,
                        )
                    )
                detection.setdefault("targets_by_method", {})[
                    "all" if method == "both" else method
                ] = value
                continue
            if sub in DETECTION_KEYS:
                detection["methods"] = _localization_methods(value)
                continue
            raise SweepError(
                tr(
                    "unknown detection key: {p0!r} (a combination table allows localization = "
                    "parabolic / gaussian / both, plus localization.targets = a target-peak "
                    "CSV)",
                    p0=name,
                )
            )
        params[name] = value
    return params, phases, detection


# NUS (SMILE) key aliases: the backend takes lower case, run records write nSigma
# and friends; both are accepted and normalised before the call.
_NUS_ALIASES: dict[str, str] = {"nSigma": "nsigma", "nThread": "nthread"}


def normalize_nus_params(params: Mapping[str, Any]) -> dict[str, Any]:
    """Normalise NUS parameter keys for the backend (camel-case aliases -> lower case)."""
    out = dict(params)
    for camel, lower in _NUS_ALIASES.items():
        if camel in out and lower not in out:
            out[lower] = out.pop(camel)
    return out


@dataclass
class SweepPlan:
    """A workflow plan: parameter axes -> combination table plus the reference base."""

    axes: dict[str, list[Any]] = field(default_factory=dict)
    combos: list[dict[str, Any]] = field(default_factory=list)
    # every condition starts from its own reference parameters, then this batch override
    base_overrides: dict[str, Any] = field(default_factory=dict)
    grid_sha256: str = ""
    reference_script_sha256: str = ""
    reference_spectrum_sha256: str = ""
    reference_peak_table_sha256: str = ""
    max_runs: int = DEFAULT_MAX_RUNS
    design: str = "full"      # "full" (expanded) | "explicit" (external table)
    n_full: int = 0           # full-factorial size (comparison; explicit == combos)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    phase_locked: bool = True
    notes: list[str] = field(default_factory=list)

    @property
    def n_combos(self) -> int:
        return len(self.combos)

    @property
    def n_workflows(self) -> int:
        return len(self.combos)

    def workflow_ids(self) -> list[str]:
        return [workflow_id_for(index) for index in range(1, self.n_combos + 1)]

    def to_dict(self) -> dict[str, Any]:
        return {
            "axes": self.axes,
            "combos": self.combos,
            "base_overrides": self.base_overrides,
            "grid_sha256": self.grid_sha256,
            "reference_script_sha256": self.reference_script_sha256,
            "reference_spectrum_sha256": self.reference_spectrum_sha256,
            "reference_peak_table_sha256": self.reference_peak_table_sha256,
            "max_runs": int(self.max_runs),
            "design": self.design,
            "n_full": int(self.n_full),
            "diagnostics": self.diagnostics,
            "phase_locked": bool(self.phase_locked),
            "notes": list(self.notes),
            "n_combos": self.n_combos,
            "n_workflows": self.n_workflows,
            "workflow_ids": self.workflow_ids(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SweepPlan:
        if "base_overrides" not in data:
            raise SweepError(
                tr("the legacy SweepPlan(base_params) form is gone; re-plan with this version")
            )
        return cls(
            axes=dict(data.get("axes") or {}),
            combos=[dict(c) for c in (data.get("combos") or [])],
            base_overrides=dict(data.get("base_overrides") or {}),
            grid_sha256=str(data.get("grid_sha256", "")),
            reference_script_sha256=str(data.get("reference_script_sha256", "")),
            reference_spectrum_sha256=str(data.get("reference_spectrum_sha256", "")),
            reference_peak_table_sha256=str(
                data.get("reference_peak_table_sha256", "")
            ),
            max_runs=int(data.get("max_runs", DEFAULT_MAX_RUNS) or DEFAULT_MAX_RUNS),
            design=str(data.get("design", "full")),
            n_full=int(data.get("n_full", 0) or 0),
            diagnostics=dict(data.get("diagnostics") or {}),
            phase_locked=bool(data.get("phase_locked", True)),
            notes=[str(n) for n in (data.get("notes") or [])],
        )


@dataclass
class SweepRun:
    """One (workflow, condition) record: a run, the chosen peak table and provenance.

    ``measurements`` lives in memory; on disk the chosen CSV and run.json are authoritative.
    The field is empty when a record is loaded from disk.
    """

    workflow_id: str
    index: int
    condition: str = ""
    dataset: dict[str, Any] = field(default_factory=dict)
    parameters_requested: dict[str, Any] = field(default_factory=dict)
    parameters_used: dict[str, Any] = field(default_factory=dict)
    parameters_resolved: dict[str, Any] = field(default_factory=dict)
    phase: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"
    warnings: list[dict[str, Any]] = field(default_factory=list)
    message: str = ""
    run_dir: str = ""
    script_path: str = ""
    script_sha256: str = ""
    spectrum_path: str = ""
    spectrum_sha256: str = ""
    log_path: str = ""
    base_script: dict[str, Any] = field(default_factory=dict)
    peak_tables: dict[str, dict[str, Any]] = field(default_factory=dict)
    peak_localization: dict[str, Any] = field(default_factory=dict)
    window: dict[str, Any] = field(default_factory=dict)
    # reference script vs this workflow script (an auditable "only the named lines")
    script_diff: dict[str, Any] = field(default_factory=dict)
    wall_time_s: float = 0.0
    phase_locked: bool = True
    logs_tail: list[str] = field(default_factory=list)
    versions: dict[str, str] = field(default_factory=dict)
    resume_fingerprint: str = ""
    measurements_by_method: dict[str, list[PeakMeasurement]] = field(
        default_factory=dict
    )

    @property
    def run_id(self) -> str:
        """Legacy alias: workflow_id used to be run_id."""
        return self.workflow_id

    @property
    def combo(self) -> dict[str, Any]:
        """Legacy alias: the user's parameter combination row."""
        return self.parameters_requested

    @property
    def params(self) -> dict[str, Any]:
        """Legacy alias: the complete parameters actually used."""
        return self.parameters_used

    @property
    def measurements(self) -> list[PeakMeasurement]:
        """Legacy alias: the parabolic measurements."""
        return list(self.measurements_by_method.get("parabolic") or [])

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "run_id": self.workflow_id,          # legacy field, same value
            "index": int(self.index),
            "condition": self.condition,
            "dataset": self.dataset,
            "parameters_requested": self.parameters_requested,
            "parameters_used": self.parameters_used,
            "parameters_resolved": self.parameters_resolved,
            "phase": self.phase,
            "status": self.status,
            "warnings": self.warnings,
            "message": self.message,
            "run_dir": self.run_dir,
            "script_path": self.script_path,
            "script_sha256": self.script_sha256,
            "spectrum_path": self.spectrum_path,
            "spectrum_sha256": self.spectrum_sha256,
            "log_path": self.log_path,
            "base_script": self.base_script,
            "peak_tables": self.peak_tables,
            "peak_localization": self.peak_localization,
            "window": self.window,
            "script_diff": self.script_diff,
            "wall_time_s": float(self.wall_time_s),
            "phase_locked": bool(self.phase_locked),
            "logs_tail": list(self.logs_tail),
            "versions": self.versions,
            "resume_fingerprint": self.resume_fingerprint,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SweepRun:
        return cls(
            workflow_id=str(data.get("workflow_id") or data.get("run_id") or ""),
            index=int(data.get("index", 0) or 0),
            condition=str(data.get("condition", "")),
            dataset=dict(data.get("dataset") or {}),
            parameters_requested=dict(
                data.get("parameters_requested") or data.get("combo") or {}
            ),
            parameters_used=dict(
                data.get("parameters_used") or data.get("params") or {}
            ),
            parameters_resolved=dict(data.get("parameters_resolved") or {}),
            phase=dict(data.get("phase") or {}),
            status=str(data.get("status", "pending")),
            warnings=[
                dict(item)
                for item in (data.get("warnings") or [])
                if isinstance(item, dict)
            ],
            message=str(data.get("message", "")),
            run_dir=str(data.get("run_dir", "")),
            script_path=str(data.get("script_path", "")),
            script_sha256=str(data.get("script_sha256", "")),
            spectrum_path=str(data.get("spectrum_path", "")),
            spectrum_sha256=str(data.get("spectrum_sha256", "")),
            log_path=str(data.get("log_path", "")),
            base_script=dict(data.get("base_script") or {}),
            peak_tables={
                str(k): dict(v)
                for k, v in (data.get("peak_tables") or {}).items()
                if isinstance(v, dict)
            },
            peak_localization=dict(data.get("peak_localization") or {}),
            window={
                str(k): dict(v)
                for k, v in (data.get("window") or {}).items()
                if isinstance(v, dict)
            },
            script_diff=dict(data.get("script_diff") or {}),
            wall_time_s=float(data.get("wall_time_s", 0.0) or 0.0),
            phase_locked=bool(data.get("phase_locked", True)),
            logs_tail=[str(x) for x in (data.get("logs_tail") or [])],
            versions={
                str(k): str(v) for k, v in (data.get("versions") or {}).items()
            },
            resume_fingerprint=str(data.get("resume_fingerprint", "")),
        )

    def peak_table_path(self, method: str) -> str:
        return str((self.peak_tables.get(str(method)) or {}).get("path", ""))


def expand_grid(axes: Mapping[str, Sequence[Any]]) -> list[dict[str, Any]]:
    """Expand parameter axes into a combination list.

    Keys may be dotted paths (``"window.F1.off"``) with candidate sequences:

    >>> expand_grid({"zero_fill": [1, 2], "window.F1.off": [0.35, 0.45]})
    [{'zero_fill': 1, 'window.F1.off': 0.35}, ...]
    """
    keys = [str(key) for key in axes.keys()]
    if not keys:
        return [{}]
    values: list[list[Any]] = []
    for key in keys:
        options = list(axes[key])
        if not options:
            raise SweepError(tr("parameter axis {p0} has no candidate values", p0=key))
        values.append(options)
    return [
        dict(zip(keys, combination))
        for combination in itertools.product(*values)
    ]


def _axis_root(key: str) -> str:
    """The first segment of a grid key (decides if the backend reads it)."""
    return str(key).split(".", 1)[0]


def validate_axes(
    axes: Mapping[str, Sequence[Any]], *, sampling: str = "uniform"
) -> list[str]:
    """Check grid keys: locked keys raise, deterministic or unknown ones only warn.

    - locked keys (``phases``/``direct_phase``/``phase_route``/``sampling.auto_phase``)
      break phase locking or are ignored, so a `SweepError` points at
      `phase_delta.<axis>.p0|p1` / `phase.<axis>.p0|p1`;
    - deterministic/policy parameters get a "usually not gridded" note;
    - keys the backend does not read and that are not phase axes get "may not take effect".
    """
    live = _NUS_KEYS if str(sampling) == "nus" else _UNIFORM_KEYS
    notes: list[str] = []
    for raw_key in axes:
        key = str(raw_key)
        if key in _LOCKED_AXIS_KEYS:
            if key in ("phases", "direct_phase"):
                raise SweepError(
                    tr(
                        "{p0!r} in the grid would break phase locking: use "
                        "phase_delta.<axis>.p0|p1 (a deviation, e.g. ±5 degrees) or "
                        "phase.<axis>.p0|p1 (an absolute "
                        "value)",
                        p0=key,
                    )
                )
            raise SweepError(
                tr(
                    "{p0!r} is not supported: the reference run or the API decides it, it is not "
                    "free",
                    p0=key,
                )
            )
        if is_phase_axis(key):
            parse_phase_axis(key)
            continue
        root = _axis_root(key)
        sub = _detection_subkey(key)
        if sub is not None:
            if sub in _THRESHOLD_KEYS:
                raise _locked_threshold_error(key)
            if sub in DETECTION_KEYS or sub.startswith("targets."):
                notes.append(_detection_note(key))
                continue
            raise SweepError(
                tr(
                    "unknown detection key: {p0!r} (only localization = parabolic / gaussian / "
                    "both, plus localization.targets = a target-peak "
                    "CSV)",
                    p0=key,
                )
            )
        if key in ("ext_lo", "ext_hi"):
            notes.append(
                tr(
                    "note: {p0} is the **direct range** (ppm): overriding it moves the extraction "
                    "window and with it the peak set; set it in reference mode (`direct_range=` / "
                    "CLI `reference --direct-range`)",
                    p0=key,
                )
            )
            continue
        if key in _PEAK_PICKING_KEYS or root in _PEAK_PICKING_KEYS:
            raise _locked_threshold_error(key)
        if key in _DETERMINISTIC_KEYS or root in _DETERMINISTIC_KEYS:
            notes.append(
                tr(
                    "note: {p0} is a deterministic/policy parameter; it need not be "
                    "gridded(changing it moves the peak set or only the "
                    "convention)",
                    p0=key,
                )
            )
        elif root not in live:
            notes.append(
                tr("warning: {p0} is not in the list the backend reads; check the spelling", p0=key)
            )
    return notes


def merge_overrides(
    base: Mapping[str, Any], overrides: Mapping[str, Any]
) -> dict[str, Any]:
    """Merge dotted-key overrides into the base parameters (the input dict is untouched)."""
    result = copy.deepcopy(dict(base))
    for dotted, value in overrides.items():
        parts = str(dotted).split(".")
        node = result
        for part in parts[:-1]:
            child = node.get(part)
            if not isinstance(child, dict):
                child = {}
                node[part] = child
            node = child
        node[parts[-1]] = value
    return result


def infer_axes(combos: Sequence[Mapping[str, Any]]) -> dict[str, list[Any]]:
    """Infer the values each key took from an explicit table (first-seen order)."""
    axes: dict[str, list[Any]] = {}
    for combo in combos:
        for key, value in combo.items():
            bucket = axes.setdefault(str(key), [])
            if value not in bucket:
                bucket.append(value)
    return axes


def _encode_column(rows: Sequence[Mapping[str, Any]], key: str) -> list[float]:
    """A column's values -> numbers (numeric as-is, categorical by first-seen order)."""
    raw = [row.get(key) for row in rows]
    if all(
        isinstance(v, (int, float)) and not isinstance(v, bool) for v in raw
    ):
        return [float(v) for v in raw]
    order: list[Any] = []
    for value in raw:
        if value not in order:
            order.append(value)
    return [float(order.index(value)) for value in raw]


def _pairwise_correlation(
    rows: Sequence[Mapping[str, Any]], key_a: str, key_b: str
) -> float:
    """Correlation of two coded columns (0 when either side is constant)."""
    values_a = _encode_column(rows, key_a)
    values_b = _encode_column(rows, key_b)
    n = len(values_a)
    if n < 2:
        return 0.0
    mean_a = sum(values_a) / n
    mean_b = sum(values_b) / n
    cov = sum((a - mean_a) * (b - mean_b) for a, b in zip(values_a, values_b))
    var_a = sum((a - mean_a) ** 2 for a in values_a)
    var_b = sum((b - mean_b) ** 2 for b in values_b)
    if var_a <= 0 or var_b <= 0:
        return 0.0
    return float(cov / (var_a**0.5 * var_b**0.5))


def design_diagnostics(
    combos: Sequence[Mapping[str, Any]],
    *,
    axes: Mapping[str, Sequence[Any]] | None = None,
) -> dict[str, Any]:
    """Design diagnostics - informational only, no design decision is taken here.

    - ``level_counts``: how often each value appears per key (level balance);
    - ``duplicated_rows``: how many combinations repeat exactly (wasted runs);
    - ``max_abs_correlation``: the largest |correlation| between coded columns (0 = orthogonal;
        a correlation between levels is a diagnostic, not a statistical test);
    - ``missing_levels``: levels declared in ``axes`` that never appear.

    Orthogonal arrays, fractional factorials, D-optimal and LHS designs come from outside.
    """
    rows = [dict(combo) for combo in combos]
    keys = list(infer_axes(rows).keys())
    level_counts: dict[str, dict[str, int]] = {}
    for key in keys:
        counts: dict[str, int] = {}
        for row in rows:
            token = json.dumps(row.get(key), sort_keys=True, ensure_ascii=False)
            counts[token] = counts.get(token, 0) + 1
        level_counts[key] = counts
    seen: dict[str, int] = {}
    for row in rows:
        token = json.dumps(row, sort_keys=True, ensure_ascii=False)
        seen[token] = seen.get(token, 0) + 1
    duplicated = sum(count - 1 for count in seen.values() if count > 1)
    max_corr = 0.0
    for index, key_a in enumerate(keys):
        for key_b in keys[index + 1:]:
            max_corr = max(
                max_corr, abs(_pairwise_correlation(rows, key_a, key_b))
            )
    diagnostics: dict[str, Any] = {
        "n_runs": len(rows),
        "factors": keys,
        "level_counts": level_counts,
        "duplicated_rows": duplicated,
        "max_abs_correlation": round(max_corr, 6),
    }
    if axes is not None:
        used = infer_axes(rows)
        missing: dict[str, list[Any]] = {}
        for key, values in axes.items():
            absent = [v for v in values if v not in used.get(str(key), [])]
            if absent:
                missing[str(key)] = absent
        diagnostics["missing_levels"] = missing
    return diagnostics


def combos_from_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    axes: Mapping[str, Sequence[Any]] | None = None,
) -> list[dict[str, Any]]:
    """A row table (columns are axis keys) -> combinations; axes validate keys and levels.

    An external design only has to be one combination per row; order is preserved and runs
    execute in table order.
    """
    combos: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, Mapping):
            raise SweepError(tr(
                "row {p0} of the combination table is not a mapping: "
                "{p1!r}",
                p0=index,
                p1=row,
            ))
        # an empty cell or null means **unspecified** (keep the base), not "override to empty":
        # a blank CSV cell leaves that parameter untouched for this row.
        combo = {
            str(key): value
            for key, value in row.items()
            if value is not None and str(value).strip() != ""
        }
        if not combo:
            raise SweepError(tr("row {p0} of the combination table is empty", p0=index))
        if axes is not None:
            for key, value in combo.items():
                if key not in axes:
                    raise SweepError(tr(
                        "{p0!r} in row {p1} was not declared in "
                        "axes",
                        p0=key,
                        p1=index,
                    ))
                levels = list(axes[key])
                if levels and value not in levels:
                    raise SweepError(
                        tr(
                            "{p0}={p1!r} in row {p2} is outside the declared levels "
                            "{p3!r}",
                            p0=key,
                            p1=value,
                            p2=index,
                            p3=levels,
                        )
                    )
        combos.append(combo)
    if not combos:
        raise SweepError(tr("the combination table is empty"))
    return combos


def _parse_cell(text: str) -> Any:
    """A CSV cell -> int/float/bool/str."""
    value = str(text).strip()
    if value.lower() in ("true", "false"):
        return value.lower() == "true"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


#: combination-table keys that name a target-peak CSV (relative paths resolve against
#: the table's own directory)
_TARGET_KEYS: tuple[str, ...] = (
    "localization.targets",
    "detection.localization.targets",
)


def _maybe_target_mapping(token: str) -> Mapping[str, Any] | None:
    """A CSV cell written as ``{A: a.csv, B: b.csv}`` -> a condition mapping.

    A CSV cell can only be a string, so a per-condition mapping is written in the
    YAML/JSON brace form (equivalent to a nested mapping in a YAML table); anything
    that does not parse stays a path.
    """
    text_value = token.strip()
    if not (text_value.startswith("{") and text_value.endswith("}")):
        return None
    try:
        import yaml

        data = yaml.safe_load(text_value)
    except Exception:  # noqa: BLE001 - not a mapping, treat it as a path
        return None
    return data if isinstance(data, Mapping) else None


def _resolve_target_paths(value: Any, base_dir: Path) -> Any:
    """Resolve relative paths inside a target spec against the table directory."""
    if isinstance(value, Mapping):
        return {
            key: _resolve_target_paths(item, base_dir)
            for key, item in value.items()
        }
    if isinstance(value, str):
        token = value.strip()
        if not token:
            return value
        nested = _maybe_target_mapping(token)
        if nested is not None:
            return _resolve_target_paths(nested, base_dir)
        path = Path(token)
        if path.is_absolute():
            return value
        candidate = base_dir / path
        return str(candidate) if candidate.is_file() else value
    return value


def _resolve_combo_targets(
    combos: list[dict[str, Any]], base_dir: Path
) -> list[dict[str, Any]]:
    """Resolve target-peak CSV paths against the **combination table's** directory.

    Absolute paths are kept as written; a relative path that cannot be found under
    ``base_dir`` is left alone too (so read_localization_targets reports "missing"
    with the original spelling). Paths inside a condition mapping
    (``{A: a.csv, B: b.csv}`` / ``default`` plus ``by_condition``) resolve as well.
    """
    def _is_target_key(name: str) -> bool:
        return name in _TARGET_KEYS or any(
            name.startswith(f"{base}.") for base in _TARGET_KEYS
        )

    for combo in combos:
        for key in list(combo):
            if not _is_target_key(str(key)):
                continue
            value = combo.get(key)
            if isinstance(value, str) and not value.strip():
                continue
            resolved = _resolve_target_paths(value, base_dir)
            if resolved != value:
                combo[key] = resolved
    return combos


def load_combo_table(path: Path | str) -> list[dict[str, Any]]:
    """Read a combination table: CSV/TSV (first row is the header) or YAML/JSON.

    An external design tool (pyDOE2, a Taguchi array, LHS, a hand-written table) only has
    to export one of these two shapes.
    """
    target = Path(path)
    if not target.is_file():
        raise SweepError(tr("combination table does not exist: {p0}", p0=target))
    suffix = target.suffix.lower()
    if suffix in (".csv", ".tsv", ".txt"):
        import csv

        delimiter = "\t" if suffix == ".tsv" else ","
        text = target.read_text(encoding="utf-8-sig")
        rows = [
            {str(key): _parse_cell(value) for key, value in row.items() if key}
            for row in csv.DictReader(text.splitlines(), delimiter=delimiter)
        ]
        return _resolve_combo_targets(combos_from_rows(rows), target.parent)
    import yaml

    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("combos")
    if not isinstance(data, list):
        raise SweepError(tr("bad combination table (expected a list or combos:): {p0}", p0=target))
    return _resolve_combo_targets(combos_from_rows(data), target.parent)


def write_combo_table(
    path: Path | str, combos: Sequence[Mapping[str, Any]]
) -> Path:
    """Write a combination table (CSV; columns = every key seen, in first-seen order)."""
    import csv

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    keys = list(infer_axes(combos).keys())
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        for combo in combos:
            writer.writerow({key: combo.get(key, "") for key in keys})
    return target


def _grid_sha256(combos: Sequence[Mapping[str, Any]]) -> str:
    payload = json.dumps(
        [dict(c) for c in combos], sort_keys=True, ensure_ascii=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def plan_sweep(
    reference: ReferenceSpectrum,
    *,
    axes: Mapping[str, Sequence[Any]] | None = None,
    combos: Sequence[Mapping[str, Any]] | None = None,
    max_runs: int = DEFAULT_MAX_RUNS,
    base_overrides: Mapping[str, Any] | None = None,
    notes: Iterable[str] | None = None,
) -> SweepPlan:
    """Build a workflow plan from parameter axes and a reference (validates keys and limits).

    Two entry points, exactly one of which must be given:

    - ``axes``: candidate values per axis, expanded to a full factorial;
    - ``combos``: an **external table** (orthogonal, fractional, D-optimal, LHS, hand-picked),
        run as given in table order, with **no design decision** taken (spec H2).

    Grid what a person would actually change: windows and their parameters, baseline,
    zero-filling factors, phase deviations (`phase_delta.*`) and NUS reconstruction;
    deterministic and policy parameters are reported in `notes`.

    ``base_overrides`` is a batch-level override; at run time each condition starts from its
    own reference parameters, then applies it and the combination. Absolute bases are gone.

    Parameters
    ----------
    reference : ReferenceSpectrum
        the reference: supplies the locked threshold and the per-condition base.
    axes : Mapping[str, Sequence[Any]], optional
        shorthand: candidate values per key, expanded internally; exclusive with ``combos``.
    combos : Sequence[Mapping[str, Any]], optional
        an explicit table run as given (keys are script parameters, e.g. ``"window.F1.off"``).
    max_runs : int, default 256
        maximum number of combinations; exceeding it raises (no runaway jobs).
    base_overrides : Mapping[str, Any], optional
        batch override; precedence is condition base < batch < combination.
    notes : Iterable[str], optional
        notes recorded in the plan (its origin, say).

    Returns
    -------
    SweepPlan
        the plan (no absolute base; the base is resolved per condition at run time).

    Raises
    ------
    SweepError
        neither axes nor combos was given, the limit is exceeded, or a key is illegal.

    Side effects
    ------------
    Pure computation: nothing is written and nothing runs.

    Examples
    --------
        plan = plan_sweep(reference, axes={"zero_fill": [1, 2]})
    """
    if (axes is None) == (combos is None):
        raise SweepError(tr("give exactly one of axes (full factorial) or combos (explicit table)"))
    if combos is not None:
        resolved = combos_from_rows(combos)
        design = "explicit"
    else:
        assert axes is not None
        resolved = expand_grid(axes)
        design = "full"
    n_full = len(resolved)
    inferred = infer_axes(resolved)
    axis_notes = validate_axes(inferred, sampling=reference.sampling)
    gate_notes, gate_errors = _axis_gate_check(reference, resolved)
    if gate_errors:
        raise SweepError("; ".join(gate_errors))
    axis_notes.extend(gate_notes)
    if len(resolved) > int(max_runs):
        raise SweepError(
            tr(
                "{p0} combinations exceed max_runs={p1}; shrink the grid or raise the limit "
                "explicitly (long runs belong in "
                "batches)",
                p0=len(resolved),
                p1=max_runs,
            )
        )
    # every condition starts from its own reference parameters; only the batch override is kept
    condition_overrides = dict(base_overrides or {})
    base = merge_overrides(reference.sweep_params, condition_overrides)
    phase_locked = reference.direct_phase_override() is not None
    if not phase_locked:
        sampling = dict(base.get("sampling") or {})
        if sampling.get("auto_phase") is not False:
            sampling["auto_phase"] = False
            base["sampling"] = sampling
    plan = SweepPlan(
        axes=inferred,
        combos=resolved,
        base_overrides=condition_overrides,
        grid_sha256=_grid_sha256(resolved),
        reference_script_sha256=reference.script_sha256,
        reference_spectrum_sha256=reference.spectrum_sha256,
        reference_peak_table_sha256=reference.peak_table_sha256,
        max_runs=int(max_runs),
        design=design,
        n_full=n_full,
        diagnostics=design_diagnostics(resolved, axes=inferred),
        phase_locked=phase_locked,
        notes=list(notes or []) + axis_notes,
    )
    if not phase_locked:
        plan.notes.append(
            tr(
                "the reference run recorded no direct_phase; workflows disable automatic phase "
                "search (sampling.auto_phase=False) and use the preset "
                "phases",
            )
        )
    return plan


def _supports_nus_candidates(backend: Any) -> tuple[bool, str]:
    """Whether the backend ``reconstruct_nus`` isolates candidates (out_file/script_name)."""
    method = getattr(backend, "reconstruct_nus", None)
    if method is None:
        return False, tr("the backend has no reconstruct_nus(); NUS data cannot be processed")
    try:
        parameters = inspect.signature(method).parameters
    except (TypeError, ValueError):
        return True, ""
    missing = [name for name in ("out_file", "script_name") if name not in parameters]
    if missing:
        return (
            False,
            tr(
                "the backend reconstruct_nus() lacks {p0}, so candidates cannot be isolated "
                "(upgrade or use a backend that has "
                "it)",
                p0=missing,
            ),
        )
    return True, ""


def _phase_entries(
    effective_phase: Mapping[str, Sequence[float]],
    phase_part: Mapping[str, float],
    reference: ReferenceSpectrum,
) -> dict[str, Any]:
    """Phase provenance (spec G1): per-axis phase_mode plus actual_p0/actual_p1."""
    touched_axes = {parse_phase_axis(key)[1] for key in phase_part}
    overridden = {
        axis: sorted(
            {
                parse_phase_axis(key)[1]
                for key in phase_part
                if parse_phase_axis(key)[1] == axis
            }
        )
        for axis in touched_axes
    }
    entries: dict[str, Any] = {}
    for axis, values in effective_phase.items():
        kinds = {
            parse_phase_axis(key)[0]
            for key in phase_part
            if parse_phase_axis(key)[1] == axis
        }
        if kinds == {"phase"}:
            mode = "manual_absolute"
        elif "phase_delta" in kinds:
            mode = "manual_delta_from_reference"
        else:
            mode = "auto_reference_locked"
        entries[str(axis)] = {
            "phase_mode": mode,
            "actual_p0": float(values[0]),
            "actual_p1": float(values[1]),
            "source": (
                f"reference_run:{reference.run_id}"
                if reference.run_id
                else "reference_run"
            ),
            "overridden_components": overridden.get(str(axis), []),
        }
    return entries


def _smile_entries(
    params: Mapping[str, Any],
    requested: Mapping[str, Any],
    effective: Mapping[str, Any],
) -> dict[str, Any]:
    """Actual SMILE auto-selection (spec G2): requested vs actual plus the source."""
    entries: dict[str, Any] = {}
    for key, record_key in (("nsigma", "nSigma"), ("thresh", "thresh")):
        actual = effective.get(record_key, effective.get(key))
        if actual is None:
            actual = params.get(key, params.get(record_key))
        user_key = None
        for candidate in (key, record_key):
            if candidate in requested:
                user_key = candidate
                break
        if actual is None and user_key is None:
            continue
        entries[key] = {
            "requested": (
                requested[user_key] if user_key is not None else "auto(smile_tier)"
            ),
            "actual": actual,
            "source": "user" if user_key is not None else "auto(smile_tier)",
        }
    return entries




def _write_log(
    path: Path,
    *,
    header: Mapping[str, Any],
    logs: Sequence[str],
    warnings: Sequence[Mapping[str, Any]] = (),
) -> Path:
    """Write the full run log, not just the tail (spec D7)."""
    lines = ["# NMRForge workflow run log", ""]
    for key, value in header.items():
        if isinstance(value, (dict, list)):
            rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
        else:
            rendered = str(value)
        lines.append(f"{key}: {rendered}")
    lines.append("")
    lines.append("--- processing log ---")
    lines.extend(str(line) for line in logs)
    if warnings:
        lines.append("")
        lines.append("--- warnings ---")
        for warning in warnings:
            lines.append(
                f"[{warning.get('code')}] {warning.get('message')} "
                f"(count={warning.get('count')})"
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _write_run(run: SweepRun) -> None:
    target = Path(run.run_dir) / "run.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = run.to_dict()
    payload["updated"] = now_iso()
    payload["software_version"] = software_version()
    payload["software_commit"] = software_commit()
    payload["versions"] = dict(run.versions or tool_versions())
    # behaviour stamp (2026-09-19): the artefact says which behaviour produced it,
    # so downstream only has to look at one value
    payload.update(record_stamp())
    atomic_write_text(
        target,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )


def _load_run(run_dir: Path) -> SweepRun | None:
    state_file = run_dir / "run.json"
    if not state_file.is_file():
        return None
    try:
        raw = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    run = SweepRun.from_dict(raw)
    if run.spectrum_path and not Path(run.spectrum_path).is_file():
        return None
    return run


#: window sub-parameters render only when that axis type is not none/off (2026-09-16)
_WINDOW_GATE_SUBKEYS: frozenset[str] = frozenset(
    {"off", "end", "pow", "c", "lb", "g1", "g2", "g3", "size", "start"}
)


def _script_diff(reference_script: str, candidate_script: Path) -> dict[str, Any]:
    """Record the reference vs workflow script difference (the "only named lines" rule)."""
    try:
        ref_lines = (
            Path(str(reference_script)).read_text(encoding="utf-8").splitlines()
            if reference_script
            else []
        )
        cand_lines = candidate_script.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    diff = [
        line
        for line in difflib.unified_diff(ref_lines, cand_lines, lineterm="")
        if line[:1] in "+-" and line[:3] not in ("+++", "---")
    ]
    return {
        "reference_script": str(reference_script),
        "n_changed": len(diff),
        "changed_lines": diff[:20],
    }


def _axis_gate_check(
    reference: ReferenceSpectrum, combos: Sequence[Mapping[str, Any]]
) -> tuple[list[str], list[str]]:
    """Check for sub-parameters whose gating does not match; returns (notes, errors).

    - a ``window.<axis>.<sub>`` (off/end/pow/lb/g1/g2...) without ``type``, on an axis whose
        effective type is ``none``/``off``, can never take effect (a reference with type none
        made ``window.F1.off`` a no-op), so it raises and asks for a paired type;
        a base without ``type`` only warns (sine_bell is assumed).
    - ``baseline.<axis>.order`` on an axis whose ``mode`` is not order does nothing
        (``mode=auto`` renders ``POLY -auto``), so it warns.
    """
    base_window = dict((reference.sweep_params or {}).get("window") or {})
    base_baseline = dict((reference.sweep_params or {}).get("baseline") or {})
    notes: list[str] = []
    errors: list[str] = []
    for index, combo in enumerate(combos, start=1):
        keys = {str(key) for key in combo}
        for key in sorted(keys):
            parts = key.split(".")
            if len(parts) != 3:
                continue
            root, axis, sub = parts
            if root == "window" and sub in _WINDOW_GATE_SUBKEYS:
                if f"window.{axis}.type" in keys:
                    continue
                wtype = str((base_window.get(axis) or {}).get("type", "") or "")
                if wtype in ("none", "off"):
                    errors.append(
                        tr(
                            "combination {p0}, {p1}: this axis has no window "
                            "(window.{p2}.type={p3}), so off/end/pow/lb/g1/g2 cannot take effect; "
                            "write window.{p4}.type=... together with the parameter (keep none for "
                            "no window)",
                            p0=index,
                            p1=key,
                            p2=axis,
                            p3=wtype,
                            p4=axis,
                        )
                    )
                elif not wtype:
                    notes.append(
                        tr(
                            "note: combination {p0} gives {p1} without window.{p2}.type; sine_bell "
                            "is assumed (write type=none explicitly to avoid a "
                            "window)",
                            p0=index,
                            p1=key,
                            p2=axis,
                        )
                    )
            elif root == "baseline" and sub == "order":
                combo_mode = combo.get(f"baseline.{axis}.mode")
                mode = str(
                    combo_mode
                    if combo_mode is not None
                    else (base_baseline.get(axis) or {}).get("mode", "auto")
                ) or "auto"
                if mode != "order":
                    notes.append(
                        tr(
                            "note: combination {p0} gives {p1} but baseline.{p2}.mode={p3} is not "
                            "order, so the order does "
                            "nothing",
                            p0=index,
                            p1=key,
                            p2=axis,
                            p3=mode,
                        )
                    )
    return notes, errors


def _merged_localization_targets(
    detection: Mapping[str, Any],
    methods: Sequence[str],
    fallback: Mapping[str, ConditionalTargets | None] | None,
) -> dict[str, ConditionalTargets | None]:
    """A per-row target list plus the call argument -> the per-condition set per method.

    A per-method key overrides that method only; every other method keeps the call
    argument (neither given = the whole spectrum, in which case the key is absent and
    ``localization_targets_record`` writes ``scope=all``). The list actually in force
    for one condition is ``ConditionalTargets.for_condition(condition)``.
    """
    merged: dict[str, ConditionalTargets | None] = dict(fallback or {})
    spec = combine_target_specs(
        detection.get("targets"), detection.get("targets_by_method")
    )
    if spec is not None:
        merged.update(
            resolve_localization_targets_by_method(
                spec, methods=methods, source="combo"
            )
        )
    return merged


def _resume_fingerprint(
    *,
    combo: Mapping[str, Any],
    target: DatasetRef,
    reference: ReferenceSpectrum,
    roi_f1_ppm: float | None,
    roi_f2_ppm: float | None,
    localization_methods: Sequence[str] | None = None,
    edge_margin_ppm: float | None = None,
    base_params: Mapping[str, Any] | None = None,
    localization_targets: Mapping[str, ConditionalTargets | None] | None = None,
) -> str:
    """Compute the normalised input fingerprint that decides whether a run can be reused.

    Combination mode no longer tracks reference peaks, so the fingerprint carries the locked
    threshold, the refinement and the margin (2026-09-14); a new refinement re-runs.
    """
    param_part, phase_part, detection = split_combo(combo)
    base_params = dict(base_params or {}) or dict(reference.sweep_params)
    params = merge_overrides(base_params, param_part)
    effective_phase = apply_phase_axes(
        {
            axis: list(pair)
            for axis, pair in (reference.direct_phase_override() or {}).items()
        },
        phase_part,
    )
    sigma_locked, sigma_origin = locked_detection_sigma(reference)
    methods = list(
        detection.get("methods") or localization_methods or ["parabolic"]
    )
    # A per-row target list wins over the call argument (a per-method key overrides that
    # method only); the fingerprint must carry the **resolved** list (path + SHA-256 +
    # ids), so the same path with different content re-runs.
    active_by_method = _merged_localization_targets(
        detection, methods, localization_targets
    )
    payload = {
        "schema": "nmrforge_api.resume.v2",
        "dataset": target.to_dict(),
        "combo": dict(combo),
        "parameters_used": params,
        "phase": effective_phase,
        "reference": {
            "dataset_key": reference.dataset_key,
            "script_sha256": reference.script_sha256,
            "spectrum_sha256": reference.spectrum_sha256,
        },
        "detection": {
            "sigma_multiplier": sigma_locked,
            "sigma_origin": sigma_origin,
            "localization": methods,
            "edge_margin_ppm": edge_margin_ppm,
            "roi_f1_ppm": roi_f1_ppm,
            "roi_f2_ppm": roi_f2_ppm,
            # per-condition view: only this condition's own list (a single file written
            # per condition carries no whole-file SHA-256, so editing A's rows does not
            # make B re-run; unchanged numbers never have to re-run)
            "localization_targets": {
                method: (
                    active_by_method[method].fingerprint_view(
                        _condition_name(target)
                    )
                    if active_by_method.get(method) is not None
                    else None
                )
                for method in methods
            },
        },
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _condition_name(target: DatasetRef) -> str:
    """Condition label: its own label when set, otherwise the data key (as recorded)."""
    return str(target.condition or target.key)


def _check_combo_targets(
    combo: Mapping[str, Any],
    *,
    methods: Sequence[str],
    conditions: Sequence[str],
) -> None:
    """Validate a combination row's target list (condition names / missing rows) up front.

    When the target list is written per condition, a condition with no rows makes the
    whole batch fail **before processing** (``on_missing="error"`` by default) instead of
    half way through.
    """
    _params, _phase, detection = split_combo(combo)
    spec = combine_target_specs(
        detection.get("targets"), detection.get("targets_by_method")
    )
    if spec is None:
        return
    resolve_localization_targets_by_method(
        spec, methods=methods, conditions=list(conditions), source="combo"
    )


def _condition_base_params(
    plan: SweepPlan, reference: ReferenceSpectrum
) -> dict[str, Any]:
    """Resolve a condition base: its reference parameters, then the batch override.

    Behaviour the reference run decided through diagnostics or routing (a direct-dimension DC
    correction adding POLY -time, say) must be inherited: it lives only in the reference
    ``params.diagnostics``, and dropping it moves parameters nobody specified.
    """
    base = merge_overrides(reference.sweep_params, plan.base_overrides)
    for key, value in reference_runtime_decisions(reference.params).items():
        base.setdefault(key, value)
    if reference.direct_phase_override() is None:
        sampling = dict(base.get("sampling") or {})
        if sampling.get("auto_phase") is not False:
            sampling["auto_phase"] = False
            base["sampling"] = sampling
    return base


def _candidate_script_paths(
    session: StudySession,
    target: DatasetRef,
    reference: ReferenceSpectrum,
    workflow_id: str,
    response: Mapping[str, Any],
) -> list[Path]:
    """Where a candidate processing script may live, most reliable first.

    1. the ``script_path`` the backend returned (some routes);
    2. the condition working directory from the record (shared, preferred);
    3. ``study/work/`` (older references and backends);
    4. the data-level ``<data>.nmrpipe/`` (the backend default).
    """
    name = f"{workflow_id}_{target.token}.com"
    paths: list[Path] = []
    raw = str((response or {}).get("script_path") or "")
    if raw:
        paths.append(Path(raw))
    if reference.work_dir:
        paths.append(Path(reference.work_dir) / name)
    paths.append(session.work_dir / name)
    paths.append(
        session.root
        / target.exp_id
        / target.data_id
        / f"{target.data_id}.nmrpipe"
        / name
    )
    return paths


def _condition_references(
    session: StudySession,
    datasets: Sequence[DatasetRef],
    reference: ReferenceSpectrum | None,
) -> dict[str, ReferenceSpectrum]:
    """Condition -> reference spectrum (an explicit reference wins for its condition)."""
    refs: dict[str, ReferenceSpectrum] = {}
    for ref in datasets:
        if reference is not None and reference.dataset_key == ref.key:
            refs[ref.key] = reference
            continue
        loaded = load_reference(session, ref)
        if loaded is None:
            raise SweepError(
                tr(
                    "condition {p0} has no reference spectrum yet: call "
                    "build_reference()/ensure_reference_peaks()",
                    p0=ref.condition or ref.key,
                )
            )
        refs[ref.key] = loaded
    return refs


def _workflow_record(runs: Sequence[SweepRun], plan: SweepPlan) -> dict[str, Any]:
    """The summary record of one workflow (workflow.json)."""
    ordered = sorted(runs, key=lambda run: str(run.condition))
    statuses = {run.status for run in ordered}
    if STATUS_FAILED in statuses:
        status = STATUS_FAILED
    elif STATUS_WARNING in statuses:
        status = STATUS_WARNING
    else:
        status = STATUS_SUCCESS
    first = ordered[0] if ordered else None
    warnings = [warning for run in ordered for warning in run.warnings]
    return {
        "workflow_id": first.workflow_id if first else "",
        "index": int(first.index) if first else 0,
        "status": status,
        "message": "; ".join(
            f"{run.condition or run.dataset.get('key', '')}: {run.status}"
            for run in ordered
        ),
        "parameters_requested": dict(first.parameters_requested) if first else {},
        "conditions": [run.condition for run in ordered],
        "condition_records": [
            {
                "condition": run.condition,
                "dataset": run.dataset,
                "status": run.status,
                "message": run.message,
                "parameters_used": run.parameters_used,
                "parameters_resolved": run.parameters_resolved,
                "phase": run.phase,
                "warnings": run.warnings,
                "script_path": run.script_path,
                "script_sha256": run.script_sha256,
                "spectrum_path": run.spectrum_path,
                "spectrum_sha256": run.spectrum_sha256,
                "log_path": run.log_path,
                "peak_tables": run.peak_tables,
                "peak_localization": run.peak_localization,
                "window": run.window,
                "run_json": str(Path(run.run_dir) / "run.json"),
                "versions": run.versions,
                "wall_time_s": run.wall_time_s,
            }
            for run in ordered
        ],
        "warnings": warnings,
        "versions": dict(first.versions) if first else {},
        "base_script": dict(first.base_script) if first else {},
        "grid_sha256": plan.grid_sha256,
        "updated": now_iso(),
    }


def write_workflow_record(
    session: StudySession,
    workflow_id: str,
    plan: SweepPlan,
    runs: Sequence[SweepRun] | None = None,
) -> dict[str, Any]:
    """Write ``study/workflows/<workflow_id>/workflow.json`` plus the combination log."""
    workflow_dir = session.workflows_dir / workflow_id
    current_runs = [
        run
        for run in (runs if runs is not None else load_runs(session))
        if run.workflow_id == workflow_id
    ]
    record = _workflow_record(current_runs, plan)
    workflow_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        (workflow_dir / "workflow.json"),
        json.dumps(record, ensure_ascii=False, indent=2) + "\n",
    )
    logs: list[str] = []
    for run in sorted(current_runs, key=lambda item: str(item.condition)):
        log_path = Path(run.log_path) if run.log_path else None
        if log_path is not None and log_path.is_file():
            logs.append(f"===== {run.condition or run.dataset.get('key', '')} =====")
            logs.append(log_path.read_text(encoding="utf-8").rstrip())
    _write_log(
        workflow_dir / "log.txt",
        header={
            "workflow_id": workflow_id,
            "status": record["status"],
            "parameters_requested": record["parameters_requested"],
            "conditions": record["conditions"],
        },
        logs=logs,
        warnings=record["warnings"],
    )
    return record


def run_sweep(
    session: StudySession,
    plan: SweepPlan,
    *,
    reference: ReferenceSpectrum | None = None,
    datasets: Sequence[DatasetRef] | None = None,
    localization: Any = "parabolic",
    localize_peaks: Any = None,
    edge_margin_ppm: float | None = None,
    sign: str = "abs",
    roi_f1_ppm: float | None = None,
    roi_f2_ppm: float | None = None,
    resume: bool = True,
    stop_on_error: bool = False,
    progress: Callable[[str], None] | None = None,
    on_run: Callable[[SweepRun], None] | None = None,
    extra_warnings: Sequence[Mapping[str, Any]] = (),
) -> list[SweepRun]:
    """Run the plan: every combination processes **all conditions** and picks independently.

    Returns one record per (workflow, condition); successes, warnings and failures included.

    - ``datasets`` defaults to every condition; ``reference`` may supply a condition's
        reference (matched by dataset_key);
    - **combinations pick independently** (2026-09-14): peaks come from the combination's
        **own candidate** at the locked threshold; ``reference_peak_id``/``assignment`` stay
        empty and matching back is **downstream** work;
    - ``localization`` = ``parabolic`` (default) / ``gaussian`` / ``both``; a combination
        may override it with its own ``localization`` key; only that table is written;
    - ``localize_peaks`` (targeted localization, 2026-09-19): a CSV path (with a
        ``peak_id`` column) or a sequence of peak ids; only those peaks take the chosen
        method's refinement, and detection, row count and ``peak_id`` numbering stay
        unchanged (unlisted peaks keep the detection-stage parabola estimate). A
        combination may override this parameter with its ``localization.targets`` key;
        omitted = the whole spectrum;
    - ``edge_margin_ppm``: the **physical width** excluded at the axis edge when picking
        (default 3x the linewidth in ppm), converted per combination into ``run.window``;
    - ``sign`` is legacy: combination mode always uses the dominant sign.
    - each workflow's **full processing script** is copied to ``<run_dir>/process.com``
        (with SHA-256); it shares the condition working directory, so the reference's
        converted fid is reused. A missing script raises the
        ``processing_script_not_found`` warning rather than passing silently.

    Parameters
    ----------
    session : StudySession
        the session (project and conditions).
    plan : SweepPlan
        the plan produced by :func:`plan_sweep`.
    reference : ReferenceSpectrum, optional
        the reference; matched per condition, defaulting to the session references.
    datasets : Sequence[DatasetRef], optional
        run only these conditions (all by default).
    localization : Any, default "parabolic"
        ``parabolic``/``gaussian``/``both``; a combination may override it.
    localize_peaks : Any, optional
        target-peak list: a CSV path (with a ``peak_id`` column, optionally a
        ``reference_peak_id`` column), a :class:`LocalizationTargets` or a sequence of
        peak ids; a combination may override it with its ``localization.targets`` key.
        **Per method**: a mapping ``{"gaussian": ..., "parabolic": ...}`` (``"all"`` /
        ``"*"`` gives a shared default, method keys win); the combination-table form is
        ``localization.targets.<method>``. Default = the whole spectrum.
    edge_margin_ppm : float, optional
        edge exclusion radius (ppm), converted per combination into points.
    sign : str, default "abs"
        legacy parameter; combination mode always uses the dominant sign.
    roi_f1_ppm, roi_f2_ppm : float, optional
        Gaussian ROI radius (ppm).
    resume : bool, default True
        skip successful runs with an identical fingerprint (resume).
    stop_on_error : bool, default False
        stop at the first failure (successful runs stand); otherwise run the batch out.
    progress : Callable[[str], None], optional
        progress callback.
    on_run : Callable[[SweepRun], None], optional
        called after each run, skips included, for incremental summaries.

    Returns
    -------
    list[SweepRun]
        one record per (workflow, condition), failures and warning codes included.

    Raises
    ------
    SweepError
        the plan, reference or conditions disagree, or the backend cannot run the path.

    Side effects
    ------------
    Writes a directory per run (candidates, script, tables, log.txt, run.json, and run.log
    on failure); **the active spectrum is never replaced**.

    Examples
    --------
        runs = run_sweep(session, plan, reference=reference, progress=print)
    """
    methods = _localization_methods(localization) or ["parabolic"]
    targets = list(datasets) if datasets is not None else list(session.datasets)
    if not targets:
        raise SweepError(tr("this study has no dataset yet; call add_dataset() first"))
    conditions = [_condition_name(target) for target in targets]
    # A per-condition target list (a ``condition`` column / a condition mapping) is
    # validated **before processing** (unknown condition names and missing rows raise by
    # default), then resolved into per-condition lists.
    localization_targets = resolve_localization_targets_by_method(
        localize_peaks,
        methods=methods,
        conditions=conditions,
        source="argument",
    )
    for combo in plan.combos:
        _check_combo_targets(combo, methods=methods, conditions=conditions)
    references = _condition_references(session, targets, reference)
    backend = session.backend
    if not hasattr(backend, "process"):
        raise SweepError(tr("the backend has no process(); workflows cannot run"))
    for target in targets:
        ref = references[target.key]
        if not ref.sweep_supported:
            if str(ref.sampling) == "nus":
                raise SweepError(
                    tr(
                        "only 2D NUS combinations are supported; found {p0}D NUS (3D NUS needs "
                        "more work on the slice stream and candidate output; see "
                        "docs/external-api/09-limitations-and-roadmap.md)",
                        p0=ref.ndim,
                    )
                )
            raise SweepError(tr(
                "{p0}D/{p1} combinations are not "
                "supported",
                p0=ref.ndim,
                p1=ref.sampling,
            ))

    experiments = {
        target.key: read_experiment(session.manager, target.exp_id, target.data_id)
        for target in targets
    }
    results: list[SweepRun] = []

    def _emit(message: str) -> None:
        if progress is not None:
            progress(message)

    session.workflows_dir.mkdir(parents=True, exist_ok=True)
    for index, combo in enumerate(plan.combos, start=1):
        workflow_id = workflow_id_for(index)
        workflow_dir = session.workflows_dir / workflow_id
        workflow_dir.mkdir(parents=True, exist_ok=True)
        for target in targets:
            ref = references[target.key]
            experiment = experiments[target.key]
            run_dir = workflow_dir / target.token
            run_dir.mkdir(parents=True, exist_ok=True)
            # share the condition working directory with the reference: its converted fid is reused,
            # candidate scripts sit beside the reference script, candidates go to _intermediate/.
            # an older reference without one falls back to the data directory.
            if ref.work_dir:
                work_dir = Path(ref.work_dir)
            else:
                work_dir = (
                    session.root
                    / target.exp_id
                    / target.data_id
                    / f"{target.data_id}.nmrpipe"
                )
            work_dir.mkdir(parents=True, exist_ok=True)
            if hasattr(session.backend, "work_dir"):
                session.backend.work_dir = str(work_dir)
            condition_base = _condition_base_params(plan, ref)
            fingerprint = _resume_fingerprint(
                base_params=condition_base,
                combo=combo,
                target=target,
                reference=ref,
                localization_methods=methods,
                localization_targets=localization_targets,
                edge_margin_ppm=edge_margin_ppm,
                roi_f1_ppm=roi_f1_ppm,
                roi_f2_ppm=roi_f2_ppm,
            )
            if resume:
                cached = _load_run(run_dir)
                if (
                    cached is not None
                    and cached.status in SUCCESS_STATUSES
                    and cached.resume_fingerprint == fingerprint
                ):
                    results.append(cached)
                    _emit(
                        tr(
                            "[{p0}/{p1}] exists, skipping "
                            "(resume)",
                            p0=workflow_id,
                            p1=target.condition,
                        )
                    )
                    if on_run is not None:
                        on_run(cached)
                    continue
            run = _run_condition_with_log(
                session,
                plan=plan,
                combo=combo,
                workflow_id=workflow_id,
                index=index,
                target=target,
                reference=ref,
                experiment=experiment,
                run_dir=run_dir,
                base_params=condition_base,
                localization_methods=methods,
                localization_targets=localization_targets,
                edge_margin_ppm=edge_margin_ppm,
                roi_f1_ppm=roi_f1_ppm,
                roi_f2_ppm=roi_f2_ppm,
                resume_fingerprint=fingerprint,
                emit=_emit,
                extra_warnings=extra_warnings,
            )
            results.append(run)
            if on_run is not None:
                on_run(run)
            if stop_on_error and run.status == STATUS_FAILED:
                break
        write_workflow_record(session, workflow_id, plan, runs=results)
        if stop_on_error and any(
            run.workflow_id == workflow_id and run.status == STATUS_FAILED
            for run in results
        ):
            break
    return results


def _run_condition_with_log(session: StudySession, **kwargs: Any) -> SweepRun:
    """Phase 22: attach ``<run_dir>/run.log`` to every run (created lazily).

    ``_run_condition`` only calls ``logger.exception`` on failure, so a failed run always
    leaves a run.log with the traceback, while a successful run creates no empty file.
    """
    run_dir = Path(kwargs["run_dir"])
    log_path = run_dir / "run.log"
    handler = attach_run_log(run_dir)
    append_run_log_line(
        log_path,
        tr(
            "run start: workflow={p0} "
            "condition={p1}",
            p0=kwargs.get('workflow_id'),
            p1=getattr(kwargs.get('target'), 'condition', '-'),
        ),
    )
    try:
        run = _run_condition(session, **kwargs)
    finally:
        detach_run_log(handler)
    append_run_log_line(log_path, tr("run end: status={p0}", p0=run.status))
    return run


def _run_condition(
    session: StudySession,
    *,
    plan: SweepPlan,
    combo: Mapping[str, Any],
    workflow_id: str,
    index: int,
    target: DatasetRef,
    reference: ReferenceSpectrum,
    experiment: Any,
    run_dir: Path,
    base_params: Mapping[str, Any],
    roi_f1_ppm: float | None,
    roi_f2_ppm: float | None,
    resume_fingerprint: str,
    localization_methods: Sequence[str] | None = None,
    localization_targets: Mapping[str, ConditionalTargets | None] | None = None,
    edge_margin_ppm: float | None = None,
    emit: Callable[[str], None] | None = None,
    extra_warnings: Sequence[Mapping[str, Any]] = (),
) -> SweepRun:
    """Run one (workflow, condition): process, pick independently, write table and record."""
    logs: list[str] = []

    def _log(message: str) -> None:
        logs.append(str(message))
        emit(f"[{workflow_id}/{target.condition}] {message}")

    param_part, phase_part, detection_part = split_combo(combo)
    methods = list(
        detection_part.get("methods") or localization_methods or ["parabolic"]
    )
    # targeted localization: a per-row target list wins over the call argument (a
    # per-method key overrides that method only); condition granularity resolves this
    # run's own list **before processing**.
    active_by_method = _merged_localization_targets(
        detection_part, methods, localization_targets
    )
    condition_name = _condition_name(target)
    per_run_targets: dict[str, LocalizationTargets | None] = {}
    for method in methods:
        resolved = active_by_method.get(method)
        per_run_targets[method] = (
            resolved.for_condition(condition_name)
            if resolved is not None
            else None
        )
    # a changed fingerprint means a re-run: clear the old table so no stale artefact survives
    for method in ("parabolic", "gaussian"):
        table = run_dir / f"peak_table_{method}.csv"
        table.unlink(missing_ok=True)
        table.with_name(table.name + ".localization.json").unlink(missing_ok=True)
    # the base is resolved per condition: reference, batch override, then combination
    params = merge_overrides(base_params, param_part)
    base_phase = reference.direct_phase_override() or {}
    effective_phase = apply_phase_axes(
        {axis: list(pair) for axis, pair in base_phase.items()}, phase_part
    )
    override = (
        {axis: (values[0], values[1]) for axis, values in effective_phase.items()}
        if effective_phase
        else None
    )
    dataset_label = f"{target.key}"
    run = SweepRun(
        workflow_id=workflow_id,
        index=int(index),
        condition=target.condition,
        dataset={
            "key": target.key,
            "exp_id": target.exp_id,
            "data_id": target.data_id,
            "title": target.title,
            "condition": target.condition,
            "ndim": int(target.ndim),
            "nuclei": list(target.nuclei),
            "sampling": target.sampling,
            "source": target.source,
        },
        parameters_requested={str(k): v for k, v in combo.items()},
        parameters_used=params,
        phase=_phase_entries(effective_phase, phase_part, reference),
        run_dir=str(run_dir),
        phase_locked=override is not None,
        versions=tool_versions(),
        resume_fingerprint=resume_fingerprint,
        base_script={
            "path": reference.script_path,
            "sha256": reference.script_sha256,
            "spectrum_path": reference.frozen_spectrum,
            "spectrum_sha256": reference.spectrum_sha256,
        },
    )
    is_nus = str(getattr(experiment.sampling, "mode", "")) == "nus"
    started = time.perf_counter()
    _log(tr("start {p0}: {p1}", p0=workflow_id, p1=dict(combo)))
    try:
        if is_nus:
            ok, reason = _supports_nus_candidates(session.backend)
            if not ok:
                raise SweepError(reason)
            nus_params = normalize_nus_params(params)
            if override:
                pair = effective_phase.get(f"F{experiment.ndim}")
                if pair is not None:
                    nus_params["direct_phase"] = [float(pair[0]), float(pair[1])]
            if effective_phase:
                nus_params["phases"] = {
                    axis: [values[0], values[1]]
                    for axis, values in effective_phase.items()
                }
            response = session.backend.reconstruct_nus(
                experiment,
                nus_params,
                progress=_log,
                script_name=f"{workflow_id}_{target.token}.com",
                out_file=f"{workflow_id}_{target.token}.ft2",
            )
        else:
            method_plan = select_method(experiment)
            response = session.backend.process(
                experiment,
                method_plan,
                params=params,
                direct_phase_override=override,
                script_name=f"{workflow_id}_{target.token}.com",
                out_file=f"{workflow_id}_{target.token}.ft2",
                progress=_log,
            )
    except Exception as exc:  # noqa: BLE001 - one condition must not stop the batch
        # Phase 21/22: run.message is the actionable line; the traceback goes to run.log
        logger.exception(tr("workflow %s/%s failed"), workflow_id, target.condition)
        response = {
            "success": False,
            "message": f"{type(exc).__name__}: {exc}",
            # snapshot, not alias: if logs.extend got the same list it would append to itself
            # and grow without bound (a MemoryError found by the Phase 12 isolation test).
            "logs": list(logs),
        }
    run.wall_time_s = round(time.perf_counter() - started, 3)
    logs.extend(str(line) for line in (response.get("logs") or []))
    run.logs_tail = logs[-40:]
    if not response.get("success"):
        run.status = STATUS_FAILED
        run.message = str(response.get("message", tr("processing failed")))
        run.log_path = str(
            _write_log(
                run_dir / "log.txt",
                header={
                    "workflow_id": workflow_id,
                    "condition": target.condition,
                    "dataset": dataset_label,
                    "status": run.status,
                    "parameters_requested": run.parameters_requested,
                    "parameters_used": run.parameters_used,
                    "message": run.message,
                },
                logs=logs,
            )
        )
        _write_run(run)
        _log(tr("failed: {p0}", p0=run.message))
        return run

    script_src = next(
        (
            path
            for path in _candidate_script_paths(
                session, target, reference, workflow_id, response
            )
            if path.is_file() and path.stat().st_size > 0
        ),
        None,
    )
    script_warning: dict[str, Any] | None = None
    if script_src is not None:
        target_script = run_dir / "process.com"
        shutil.copy2(script_src, target_script)
        run.script_path = str(target_script)
        run.script_sha256 = sha256_file(target_script)
        logs.append(tr("processing script -> {p0} (from {p1})", p0=target_script, p1=script_src))
        run.script_diff = _script_diff(reference.script_path, target_script)
        if run.script_diff.get("n_changed"):
            logs.append(
                tr(
                    "script diff (reference -> this workflow): {p0} lines; ",
                    p0=run.script_diff['n_changed'],
                )
                + " | ".join(
                    str(line)
                    for line in (run.script_diff.get("changed_lines") or [])[:6]
                )
            )
    else:
        script_warning = {
            "code": WARN_SCRIPT_NOT_FOUND,
            "message": (
                tr(
                    "the full processing script for this workflow was not found ({p0}_{p1}.com): "
                    "tried the backend return value, the reference work dir, study/work and "
                    "{p2}.nmrpipe; the script is not on record (the results and the peak table are "
                    "still valid)",
                    p0=workflow_id,
                    p1=target.token,
                    p2=target.data_id,
                )
            ),
            "count": 1,
            "peaks": [],
            "localization_method": ",".join(methods),
        }
        logs.append(script_warning["message"])
    spectrum_src = Path(str(response.get("spectrum_path", "")))
    if not spectrum_src.is_file():
        run.status = STATUS_FAILED
        run.message = tr("the spectrum the backend returned does not exist: {p0}", p0=spectrum_src)
        run.log_path = str(
            _write_log(
                run_dir / "log.txt",
                header={
                    "workflow_id": workflow_id,
                    "condition": target.condition,
                    "dataset": dataset_label,
                    "status": run.status,
                    "message": run.message,
                },
                logs=logs,
            )
        )
        _write_run(run)
        return run
    target_spectrum = run_dir / f"spectrum{spectrum_src.suffix}"
    shutil.copy2(spectrum_src, target_spectrum)
    run.spectrum_path = str(target_spectrum)
    run.spectrum_sha256 = sha256_file(target_spectrum)
    # axis effects are per condition (2026-09-16): compare bit-for-bit with its reference
    reference_sha = str(reference.spectrum_sha256 or "")
    no_spectrum_change = bool(reference_sha) and run.spectrum_sha256 == reference_sha

    # combination mode (2026-09-14): each picks on **its own spectrum** at the
    # **reference-locked** threshold, writing its own table; matching back is
    # **external** work (reference_peak_id and assignment stay empty).
    from nmrforge_api.peaks import detect_and_localize

    sigma_locked, sigma_origin = locked_detection_sigma(reference)
    try:
        spectrum_axes = read_spectrum_axes(target_spectrum)
        tables: dict[str, list[dict[str, Any]]] = {}
        metas: dict[str, dict[str, Any]] = {}
        measurements: dict[str, list[PeakMeasurement]] = {}
        for method in methods:
            rows, meta = detect_and_localize(
                target_spectrum,
                axes=spectrum_axes,
                sigma_multiplier=sigma_locked,
                edge_margin_ppm=edge_margin_ppm,
                method=method,
                roi_f1_ppm=roi_f1_ppm,
                roi_f2_ppm=roi_f2_ppm,
                targets=(
                    per_run_targets[method].peak_ids
                    if per_run_targets.get(method) is not None
                    else None
                ),
                # condition granularity: a condition that explicitly declares
                # on_missing="none" has an empty target list, and "refine nothing" is
                # then declared behaviour rather than an empty-list error
                allow_empty_targets=bool(
                    per_run_targets.get(method) is not None
                    and not per_run_targets[method].peak_ids
                ),
            )
            for row in rows:
                row["workflow_id"] = workflow_id
                row["condition"] = target.condition
                row["dataset"] = dataset_label
            tables[method] = rows
            metas[method] = meta
            measurements[method] = [
                PeakMeasurement(
                    peak_id=int(row["peak_id"]),
                    assignment="",
                    reference_peak_id="",
                    positions={
                        nucleus: float(row[key])
                        for nucleus, key in (("1H", "H_ppm"), ("15N", "N_ppm"))
                        if isinstance(row.get(key), float)
                        and not math.isnan(row[key])
                    },
                    intensity=float(row["intensity"]),
                    noise_sigma=float(meta["noise_sigma"]),
                    snr=float(row["SNR"]),
                    found=True,
                )
                for row in rows
            ]
    except Exception as exc:  # noqa: BLE001 - a picking failure fails this condition
        run.status = STATUS_FAILED
        run.message = tr("independent picking failed: {p0}: {p1}", p0=type(exc).__name__, p1=exc)
        logs.append(run.message)
        run.logs_tail = logs[-40:]
        run.log_path = str(
            _write_log(
                run_dir / "log.txt",
                header={
                    "workflow_id": workflow_id,
                    "condition": target.condition,
                    "dataset": dataset_label,
                    "status": run.status,
                    "message": run.message,
                },
                logs=logs,
            )
        )
        _write_run(run)
        return run

    run.measurements_by_method = measurements
    for method in methods:
        conditional = active_by_method.get(method)
        if conditional is None:
            continue
        if conditional.is_conditional:
            logs.append(tr("localization scope [{p0}]: {p1}", p0=method, p1=conditional.describe()))
        resolved = per_run_targets.get(method)
        if resolved is None:
            logs.append(
                tr(
                    "localization scope [{p0}]: this condition is unlimited (whole-spectrum "
                    "refinement)",
                    p0=method,
                )
            )
            continue
        logs.append(
            tr(
                "localization scope [{p0}]: {p1} target peaks / {p2} detected "
                "({p3})",
                p0=method,
                p1=resolved.n_targets,
                p2=len(tables[method]),
                p3=resolved.describe(),
            )
        )
    warnings: list[dict[str, Any]] = []
    warnings.extend(dict(item) for item in extra_warnings)
    if script_warning is not None:
        warnings.append(script_warning)
    if no_spectrum_change:
        warnings.append(
            {
                "code": WARN_NO_SPECTRUM_CHANGE,
                "message": (
                    tr(
                        "this left condition {p0} unchanged (bit-identical to the reference, "
                        "sha256={p1}...): these parameters may have been ignored (a window or "
                        "gating mismatch) or have no "
                        "effect",
                        p0=target.condition,
                        p1=reference_sha[:12],
                    )
                ),
                "count": 1,
                "peaks": [],
                "localization_method": ",".join(methods),
                "reference_spectrum_sha256": reference_sha,
                "spectrum_sha256": run.spectrum_sha256,
            }
        )
    for method in methods:
        meta = metas[method]
        if int(meta.get("n_peaks", 0)) == 0:
            warnings.append(
                {
                    "code": "peak_count_zero",
                    "message": (
                        tr(
                            "{p0}: this combination found no peak at sigma={p1:g} (check the "
                            "threshold and the "
                            "data)",
                            p0=method,
                            p1=meta.get('sigma_multiplier'),
                        )
                    ),
                    "count": 0,
                    "peaks": [],
                    "localization_method": method,
                }
            )
        if int(meta.get("n_fallback", 0)):
            warnings.append(
                {
                    "code": WARN_GAUSSIAN_FALLBACK,
                    "message": (
                        tr(
                            "{p0}: {p1} peaks fell back from the Gaussian fit(reasons "
                            "{p2})",
                            p0=method,
                            p1=meta['n_fallback'],
                            p2=meta.get('fallback_reasons'),
                        )
                    ),
                    "count": int(meta["n_fallback"]),
                    "peaks": [],
                    "localization_method": method,
                }
            )
        if int(meta.get("n_boundary_hit", 0)):
            warnings.append(
                {
                    "code": WARN_GAUSSIAN_BOUNDARY_HIT,
                    "message": tr(
                        "{p0}: {p1} peaks hit a fit "
                        "boundary",
                        p0=method,
                        p1=meta['n_boundary_hit'],
                    ),
                    "count": int(meta["n_boundary_hit"]),
                    "peaks": [],
                    "localization_method": method,
                }
            )
        n_duplicate = int((meta.get("duplicate_localization") or {}).get("n_extra", 0))
        if n_duplicate:
            warnings.append(
                {
                    "code": WARN_DUPLICATE_LOCALIZATION,
                    "message": (
                        tr(
                            "{p0}: {p1} rows share a coordinate with another row (duplicate "
                            "localization); the table flags them with "
                            "duplicate_localization=true",
                            p0=method,
                            p1=n_duplicate,
                        )
                    ),
                    "count": n_duplicate,
                    "peaks": [],
                    "localization_method": method,
                }
            )
    for method in methods:
        path = write_peak_table(
            run_dir / f"peak_table_{method}.csv", tables[method]
        )
        run.peak_tables[method] = peak_table_digest(path)
    run.peak_localization = {
        method: {
            "n_peaks": int(metas[method]["n_peaks"]),
            "n_detected": int(metas[method]["n_peaks"]),
            "n_missing": 0,
            "n_fallback": int(metas[method]["n_fallback"]),
            "fallback_reasons": dict(metas[method]["fallback_reasons"]),
            "n_boundary_hit": int(metas[method]["n_boundary_hit"]),
            "localization_scope": str(
                metas[method].get("localization_scope", "all")
            ),
            "n_targeted": int(metas[method].get("n_targeted", 0)),
            "n_skipped": int(metas[method].get("n_skipped", 0)),
            "n_duplicate": int(
                (metas[method].get("duplicate_localization") or {}).get("n_extra", 0)
            ),
        }
        for method in methods
    }
    first_meta = metas[methods[0]]
    axis0_ppm = spectrum_axes.ppm[0] if spectrum_axes.ppm else None
    run.window = (
        {
            "0": {
                "points": int(first_meta["edge_margin_points"]),
                "ppm": float(first_meta["edge_margin_ppm"]),
                "effective_ppm": float(first_meta["edge_margin_ppm"]),
                "source": str(first_meta["edge_margin_source"]),
                "nucleus": (
                    spectrum_axes.nuclei[0] if spectrum_axes.nuclei else ""
                ),
                "obs_mhz": (
                    float(spectrum_axes.obs[0]) if spectrum_axes.obs else 0.0
                ),
                "ppm_per_point": (
                    round(axis_units.ppm_per_point(axis0_ppm), 6)
                    if axis0_ppm is not None
                    else 0.0
                ),
            }
        }
        if axis0_ppm is not None
        else {}
    )
    run.parameters_resolved = {
        "phase": run.phase,
        # the threshold is settled with the reference, so each workflow records it as locked
        "detection": {
            "sigma_multiplier": float(first_meta["sigma_multiplier"]),
            "sigma_multiplier_origin": sigma_origin,
            "source": "reference(locked)",
            "min_snr": float(first_meta["min_snr"]),
            "sign_mode": str(first_meta["sign_mode"]),
            "edge_margin_points": int(first_meta["edge_margin_points"]),
            "edge_margin_ppm": float(first_meta["edge_margin_ppm"]),
            "edge_margin_source": str(first_meta["edge_margin_source"]),
            "noise_sigma": float(first_meta["noise_sigma"]),
            "methods": list(methods),
            "independent": True,
            "reference_matching": "external",
            # targeted localization (2026-09-19): the target list is recorded
            # (path + SHA-256 + peak count, same style as direct_range.source)
            "localization_targets": localization_targets_record(
                {method: active_by_method.get(method) for method in methods},
                {method: metas[method] for method in methods},
                condition=condition_name,
            ),
        },
        "peak_counts": {m: int(metas[m]["n_peaks"]) for m in methods},
        "direct_range": {
            "ext_lo": params.get("ext_lo"),
            "ext_hi": params.get("ext_hi"),
            "source": (
                "combo"
                if ("ext_lo" in param_part or "ext_hi" in param_part)
                else "reference_or_base"
            ),
        },
        "sampling": {
            "effective": str(reference.sampling),
            "schedule": str(reference.sampling_schedule or ""),
            "route": (
                "reconstruct_nus"
                if str(reference.sampling) == "nus"
                else "process"
            ),
            "evidence": list(reference.sampling_evidence or [])[:5],
        },
        # the **actual** automatic values (spec G1/G2): SMILE nSigma/thresh
        "smile": (
            _smile_entries(params, combo, response.get("effective_params") or {})
            if is_nus
            else {}
        ),
        # this combination's own noise sigma (robust MAD), the SNR denominator
        "spectrum_noise_sigma": {
            "value": float(first_meta["noise_sigma"]),
            "source": "core.qc.noise(robust MAD)",
            "used_for": "SNR",
        },
        "effective_params_backend": response.get("effective_params") or {},
    }
    run.warnings = warnings
    if warnings:
        run.status = STATUS_WARNING
        run.message = (
            tr("done with warnings: {p0}", p0=', '.join(str(w.get('code')) for w in warnings))
        )
    else:
        run.status = STATUS_SUCCESS
        run.message = (
            tr(
                "done: independent picking at sigma={p0:g}({p1}, locked), peaks ",
                p0=first_meta['sigma_multiplier'],
                p1=sigma_origin,
            )
            + ", ".join(f"{m}={metas[m]['n_peaks']}" for m in methods)
        )
    run.log_path = str(
        _write_log(
            run_dir / "log.txt",
            header={
                "workflow_id": workflow_id,
                "condition": target.condition,
                "dataset": dataset_label,
                "status": run.status,
                "parameters_requested": run.parameters_requested,
                "parameters_used": run.parameters_used,
                "parameters_resolved": run.parameters_resolved,
                "phase": run.phase,
                "base_script": run.base_script,
                "script_sha256": run.script_sha256,
                "spectrum_sha256": run.spectrum_sha256,
                "peak_tables": run.peak_tables,
                "versions": run.versions,
                "wall_time_s": run.wall_time_s,
            },
            logs=logs,
            warnings=warnings,
        )
    )
    try:
        spectrum_src.unlink()
    except OSError:
        pass
    _write_run(run)
    _log(f"{run.status}: {run.message}")
    return run


def load_plan(session: StudySession) -> SweepPlan | None:
    """Read the workflow plan from a study (``records/sweep_plan.json``)."""
    path = session.records_dir / "sweep_plan.json"
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return SweepPlan.from_dict(raw) if isinstance(raw, dict) else None


def load_runs(session: StudySession) -> list[SweepRun]:
    """Read every (workflow, condition) record, ordered by workflow_id then condition."""
    runs: list[SweepRun] = []
    root = session.workflows_dir
    if not root.is_dir():
        return runs
    plan = load_plan(session)
    active = set(plan.workflow_ids()) if plan is not None else None
    for workflow_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        if active is not None and workflow_dir.name not in active:
            continue
        for condition_dir in sorted(
            path for path in workflow_dir.iterdir() if path.is_dir()
        ):
            run = _load_run(condition_dir)
            if run is not None:
                runs.append(run)
    return runs


def load_workflows(session: StudySession) -> list[dict[str, Any]]:
    """Read combination records (``workflows/<id>/workflow.json``; corrupt ones skipped)."""
    records: list[dict[str, Any]] = []
    root = session.workflows_dir
    if not root.is_dir():
        return records
    plan = load_plan(session)
    active = set(plan.workflow_ids()) if plan is not None else None
    for workflow_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        if active is not None and workflow_dir.name not in active:
            continue
        path = workflow_dir / "workflow.json"
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


def workflow_summary(runs: Sequence[SweepRun]) -> dict[str, Any]:
    """Count workflow statuses (success / success_with_warning / failed)."""
    return {
        "n_runs": len(runs),
        "success": sum(1 for run in runs if run.status == STATUS_SUCCESS),
        "success_with_warning": sum(
            1 for run in runs if run.status == STATUS_WARNING
        ),
        "failed": sum(1 for run in runs if run.status == STATUS_FAILED),
    }


__all__ = [
    "DEFAULT_MAX_RUNS",
    "STATUS_FAILED",
    "STATUS_SUCCESS",
    "STATUS_WARNING",
    "SUCCESS_STATUSES",
    "SweepPlan",
    "SweepRun",
    "WARN_GAUSSIAN_BOUNDARY_HIT",
    "WARN_GAUSSIAN_FALLBACK",
    "WARN_GAUSSIAN_UNSUPPORTED",
    "WARN_SCRIPT_NOT_FOUND",
    "WARN_NO_SPECTRUM_CHANGE",
    "locked_detection_sigma",
    "apply_phase_axes",
    "combos_from_rows",
    "design_diagnostics",
    "expand_grid",
    "infer_axes",
    "is_phase_axis",
    "load_combo_table",
    "load_plan",
    "load_runs",
    "load_workflows",
    "merge_overrides",
    "normalize_nus_params",
    "parse_phase_axis",
    "plan_sweep",
    "run_sweep",
    "split_combo",
    "validate_axes",
    "workflow_id_for",
    "workflow_summary",
    "write_combo_table",
    "write_workflow_record",
]
