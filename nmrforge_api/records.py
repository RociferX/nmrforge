"""Results on disk: workflow records, the long-form peak table and the manifest.

Artefacts (under the study root, ``study/records/``):

- ``manifest.json``: the condition datasets, each condition's reference (script/spectrum/hashes),
  the plan and grid hashes, peak identity, tool versions, status counts, boundary statement;
- ``workflows.json``: the full record of each workflow (``parameters_requested`` /
  ``parameters_used`` / ``parameters_resolved``, status, warnings, chosen peak table,
  run logs, versions);
- ``runs.json``: one flat record per (workflow, condition);
- ``peak_table_<method>.csv``: the **long-form** workflow x condition table of the chosen method
  (unified fields); summaries of the unchosen method are removed;
- ``measurement.json``: the measurement convention (width-to-points, method, QC counts).

Boundary (spec J): this module **only** aggregates processing artefacts and provenance.
Sigma, delta-delta bounds, robustness and significance testing are not computed here.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from core.project.manager import atomic_write_text
from core.version import software_commit, software_version, tool_versions
from nmrforge_api.compat import record_stamp
from nmrforge_api.peak_tables import read_peak_table, write_peak_table
from nmrforge_api.reference import ReferenceSpectrum
from nmrforge_api.session import StudySession, now_iso
from nmrforge_api.sweep import (
    SweepPlan,
    SweepRun,
    load_workflows,
    workflow_summary,
)
from ui_support.i18n import tr

WINDOW_POLICY = (
    tr(
        "Combination mode does not track reference peaks: each combination picks peaks on its own "
        "candidate spectrum using the reference-locked detection threshold; edge peaks are "
        "excluded by physical width (3x the linewidth in ppm by default, core.peaks.axis_units), "
        "converted to points at run time: zero filling by k changes the point spacing, not the ppm "
        "width covered",
    )
)

BOUNDARY_STATEMENT = (
    tr(
        "This software only executes processing and writes spectra, peak tables and processing "
        "records (provenance + QC). CSP, robustness, statistical analysis, significance testing "
        "and scientific conclusions are out of scope; downstream analysis does that from the "
        "unified peak "
        "table.",
    )
)


def _write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )
    return path


def _reference_record(
    reference: ReferenceSpectrum, include_peak_tables: bool = True
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "dataset_key": reference.dataset_key,
        "condition": reference.condition,
        "ndim": reference.ndim,
        "sampling": reference.sampling,
        "sampling_schedule": reference.sampling_schedule,
        "sampling_evidence": reference.sampling_evidence,
        "run_id": reference.run_id,
        "phase_route": reference.phase_route,
        "script_path": reference.script_path,
        "script_sha256": reference.script_sha256,
        "spectrum_path": reference.frozen_spectrum,
        "spectrum_sha256": reference.spectrum_sha256,
        "params": reference.params,
        "direct_phase": reference.direct_phase,
        "phase": reference.phase_record(),
        "created_at": reference.created_at,
        "peak_list_path": reference.peak_table_path,
        "peak_list_sha256": reference.peak_table_sha256,
        "peak_count": reference.peak_count,
        "peak_source": reference.peak_source,
        "peak_params": reference.peak_params,
    }
    if include_peak_tables:
        record["peak_tables"] = reference.peak_tables
        record["peak_localization"] = reference.peak_localization
    return record


def measurement_record(
    references: Mapping[str, ReferenceSpectrum] | Sequence[ReferenceSpectrum],
    runs: Sequence[SweepRun],
) -> dict[str, Any]:
    """Record the picking/localisation convention: threshold source, per-axis margins, per-peak QC.

    Combination mode picks independently since 2026-09-14: ``window_by_axis`` is the last
    **edge-exclusion margin actually used**, expressed as a physical width;
    ``window_points_seen`` lists every point count each axis saw, so the same physical width under
    1x/2x/4x zero filling is visibly the same ppm width at different point counts.
    """
    refs = list(references.values()) if isinstance(references, Mapping) else list(
        references
    )
    by_axis: dict[str, dict[str, Any]] = {}
    seen: dict[str, dict[str, Any]] = {}
    for run in runs:
        for axis, spec in (run.window or {}).items():
            if not isinstance(spec, Mapping):
                continue
            key = str(axis)
            by_axis[key] = dict(spec)
            bucket = seen.setdefault(
                key,
                {
                    "nucleus": str(spec.get("nucleus", "")),
                    "points": [],
                    "ppm": [],
                    "effective_ppm": [],
                    "ppm_per_point": [],
                },
            )
            for field in ("points", "ppm", "effective_ppm", "ppm_per_point"):
                value = spec.get(field)
                if value is not None and value not in bucket[field]:
                    bucket[field].append(value)
    localization: dict[str, Any] = {}
    for method in ("parabolic", "gaussian"):
        totals = {"n_peaks": 0, "n_detected": 0, "n_fallback": 0, "n_boundary_hit": 0}
        reasons: dict[str, int] = {}
        for run in runs:
            summary = (run.peak_localization or {}).get(method) or {}
            for key in totals:
                totals[key] += int(summary.get(key, 0) or 0)
            for reason, count in (summary.get("fallback_reasons") or {}).items():
                reasons[str(reason)] = reasons.get(str(reason), 0) + int(count)
        localization[method] = {**totals, "fallback_reasons": reasons}
    return {
        "peak_position_method": {
            "parabolic": (
                tr(
                "|intensity| extremum in the window plus a three-point parabolic sub-pixel "
                "refine",
            )
            ),
            "gaussian": (
                tr(
                "2D Gaussian least squares on the same candidate (2D only; falls back and records "
                "why)",
            )
            ),
        },
        "window_policy": WINDOW_POLICY,
        "window_by_axis": by_axis,
        "window_points_seen": seen,
        "reference": [
            {
                "condition": ref.condition,
                "peak_localization": ref.peak_localization,
                "edge_margin": (ref.peak_params or {}).get("detection"),
            }
            for ref in refs
        ],
        "workflow_localization": localization,
    }


def combined_peak_table(
    runs: Sequence[SweepRun], method: str
) -> list[dict[str, Any]]:
    """Concatenate the per-workflow/condition peak tables into one long table."""
    rows: list[dict[str, Any]] = []
    for run in runs:
        path = run.peak_table_path(method)
        if not path:
            continue
        rows.extend(read_peak_table(path))
    return rows


def write_reference_records(
    session: StudySession,
    references: Mapping[str, ReferenceSpectrum] | Sequence[ReferenceSpectrum],
    *,
    reference_spec: str = "",
) -> dict[str, str]:
    """Reference-mode artefact: ``records/reference.json``.

    Records, per condition, the reference spectrum/script/two peak tables (path + SHA-256), the
    resolved parameters, the sampling convention (with full-sampling evidence), the picking
    threshold
    and the versions; combination mode only references these hashes.
    """
    out_dir = session.records_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    refs = (
        list(references.values())
        if isinstance(references, Mapping)
        else list(references)
    )
    payload = {
        "api_version": "0.2",
        "created": now_iso(),
        "nmrforge_version": software_version(),
        "software_commit": software_commit(),
        "tool_versions": tool_versions(),
        "research_root": str(session.root),
        "mode": "reference",
        **record_stamp(),
        "reference_spec": reference_spec,
        "boundary": BOUNDARY_STATEMENT,
        "datasets": [ref.to_dict() for ref in session.datasets],
        "references": [_reference_record(ref) for ref in refs],
    }
    return {"reference": str(_write_json(out_dir / "reference.json", payload))}


def _reference_spec_on_disk(session: StudySession) -> str:
    """The ``reference_spec`` of the existing ``records/reference.json``, if any."""
    path = session.records_dir / "reference.json"
    if not path.is_file():
        return ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(payload.get("reference_spec", "") or "")


def refresh_reference_records(
    session: StudySession,
    references: (
        Mapping[str, ReferenceSpectrum] | Sequence[ReferenceSpectrum] | None
    ) = None,
) -> dict[str, str]:
    """Refresh the study-level ``records/reference.json`` from what is on disk (2026-09-19).

    Whenever the reference peak tables are upgraded in place
    (:func:`nmrforge_api.rebuild_reference_peak_tables`) or a run is rebuilt with
    ``nmrforge_api report``, the aggregate record has to follow; otherwise a downstream
    reader keeps seeing the old SHA, the old version and no ``exclusive_windows``. The
    existing ``reference_spec`` is preserved.

    Parameters
    ----------
    session : StudySession
        The study session.
    references : Mapping | Sequence | None, optional
        References to write; by default every condition is read back from disk.

    Returns
    -------
    dict[str, str]
        ``{"reference": <path of records/reference.json>}``.

    Side effects
    ------------
    Rewrites ``records/reference.json`` only; the reference spectrum, the peak tables and
    the workflow records are untouched.
    """
    if references is None:
        from nmrforge_api.reference import load_references

        references = load_references(session)
    return write_reference_records(
        session, references, reference_spec=_reference_spec_on_disk(session)
    )


def write_records(
    session: StudySession,
    *,
    reference: ReferenceSpectrum | None = None,
    references: Mapping[str, ReferenceSpectrum] | Sequence[ReferenceSpectrum] | None = None,
    plan: SweepPlan,
    runs: Sequence[SweepRun],
    peaks: Sequence[dict[str, Any]] | None = None,
    reference_spec: str = "",
) -> dict[str, str]:
    """Write every summary artefact; returns {name: path}."""
    out_dir = session.records_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}
    if references is None:
        ref_list: list[ReferenceSpectrum] = [reference] if reference else []
    elif isinstance(references, Mapping):
        ref_list = list(references.values())
    else:
        ref_list = list(references)
    workloads = workflow_summary(runs)
    manifest = {
        "api_version": "0.2",
        "created": now_iso(),
        "nmrforge_version": software_version(),
        "software_commit": software_commit(),
        "tool_versions": tool_versions(),
        "research_root": str(session.root),
        "mode": "combination",
        **record_stamp(),
        "reference_spec": str(reference_spec),
        "boundary": BOUNDARY_STATEMENT,
        "datasets": [ref.to_dict() for ref in session.datasets],
        "references": [_reference_record(ref) for ref in ref_list],
        "plan": {
            "axes": plan.axes,
            "base_overrides": plan.base_overrides,
            "n_workflows": plan.n_workflows,
            "n_combos": plan.n_combos,
            "workflow_ids": plan.workflow_ids(),
            "grid_sha256": plan.grid_sha256,
            "max_runs": plan.max_runs,
            "design": plan.design,
            "phase_locked": plan.phase_locked,
            "notes": plan.notes,
        },
        "sweep": {
            "axes": plan.axes,
            "base_overrides": plan.base_overrides,
            "n_combos": plan.n_combos,
            "grid_sha256": plan.grid_sha256,
            "max_runs": plan.max_runs,
            "phase_locked": plan.phase_locked,
            "notes": plan.notes,
        },
        "peak_identity": {
            "reference_peak_id_scheme": (
                tr(
                "R0001... (reference-table row order; only in the reference "
                "table)",
            )
            ),
            "matching": tr(
                "external: combination tables leave reference_peak_id/assignment empty and "
                "downstream analysis matches them back (this software neither matches nor computes "
                "CSP)",
            ),
            "reference": [
                {
                    "condition": ref.condition,
                    "peak_list_path": ref.peak_table_path,
                    "peak_list_sha256": ref.peak_table_sha256,
                    "peak_count": ref.peak_count,
                    "source": ref.peak_source,
                }
                for ref in ref_list
            ],
        },
        "peaks": (
            {
                "path": ref_list[0].peak_table_path if ref_list else "",
                "sha256": ref_list[0].peak_table_sha256 if ref_list else "",
                "count": ref_list[0].peak_count if ref_list else 0,
                "source": ref_list[0].peak_source if ref_list else "",
                "params": ref_list[0].peak_params if ref_list else {},
                "created_at": ref_list[0].peak_created_at if ref_list else "",
            }
        ),
        "workflows": workloads,
        "runs": workloads,
        "measurement": measurement_record(ref_list, runs),
    }
    written["manifest"] = str(_write_json(out_dir / "manifest.json", manifest))
    written["sweep_plan"] = str(
        _write_json(out_dir / "sweep_plan.json", plan.to_dict())
    )
    written["runs"] = str(
        _write_json(out_dir / "runs.json", [run.to_dict() for run in runs])
    )
    written["measurement"] = str(
        _write_json(out_dir / "measurement.json", manifest["measurement"])
    )
    stored = load_workflows(session)
    written["workflows"] = str(
        _write_json(
            out_dir / "workflows.json",
            stored
            or [
                _workflow_record_from_runs(runs, workflow_id)
                for workflow_id in _workflow_ids(runs)
            ],
        )
    )
    known_methods = ("parabolic", "gaussian")
    methods = [
        method
        for method in known_methods
        if any((run.peak_tables or {}).get(method) for run in runs)
    ]
    for method in known_methods:
        path = out_dir / f"peak_table_{method}.csv"
        if method in methods:
            write_peak_table(path, combined_peak_table(runs, method))
            written[f"peak_table_{method}"] = str(path)
        else:
            path.unlink(missing_ok=True)
    # Legacy summary aliases are out of contract; remove leftovers when upgrading.
    (out_dir / "peak_positions.csv").unlink(missing_ok=True)
    return written


def _workflow_ids(runs: Sequence[SweepRun]) -> list[str]:
    seen: list[str] = []
    for run in runs:
        if run.workflow_id not in seen:
            seen.append(run.workflow_id)
    return seen


def _workflow_record_from_runs(
    runs: Sequence[SweepRun], workflow_id: str
) -> dict[str, Any]:
    """Group by workflow (workflow.json on disk is authoritative; this is a summary copy)."""
    from nmrforge_api.sweep import _workflow_record

    selected = [run for run in runs if run.workflow_id == workflow_id]
    plan = SweepPlan(grid_sha256="")
    return _workflow_record(selected, plan)


__all__ = [
    "BOUNDARY_STATEMENT",
    "WINDOW_POLICY",
    "write_reference_records",
    "combined_peak_table",
    "measurement_record",
    "write_records",
]
