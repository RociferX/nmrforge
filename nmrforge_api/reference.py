"""Reference spectrum, script and both peak tables: run the automatic chain once, freeze it.

The reference is the zero point of the whole study (2026-09-13 specification):

1. run the automatic chain once (``generate_fid`` -> ``generate_spectrum``, with unified
   phase optimisation) to get the best spectrum and **the script that actually ran**
   (``process.com`` + SHA-256);
2. pick peaks automatically (or take an external table) to get stable
   ``reference_peak_id``s (R0001...), then localise parabolically and with a 2D Gaussian
   on that same spectrum and write **two structurally identical tables**;
3. extract the sweep base: drop run-derived keys and keep only what can be fed back to
   ``process()``, so a workflow starts from the reference and changes only swept axes.
Multi-condition (A/B): each condition has its own reference (phase and noise from its own
data), but **peak identity is shared**: one ``reference_peak_id`` means one peak everywhere.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.project.manager import atomic_write_text, sha256_file
from core.project.run_refs import STEP_RUN_REFS
from core.version import software_commit, software_version, tool_versions
from nmrforge_api.direct_range import (
    DirectRange,
    direct_range_record,
    parse_direct_range,
    resolved_ext_range,
)
from nmrforge_api.errors import ReferenceError, SweepError
from nmrforge_api.peak_tables import (
    REFERENCE_WORKFLOW_ID,
    gaussian_fallback_rows,
    mark_duplicate_localization,
    peak_table_digest,
    peak_table_rows,
    write_peak_table,
)
from nmrforge_api.peaks import (
    measure_peak_positions,
    pick_reference_peaks,
    read_reference_peaks,
    window_points_by_axis,
)
from nmrforge_api.session import (
    DatasetRef,
    StudySession,
    now_iso,
    open_study,
)
from ui_support.i18n import tr
from workflow.pick_peaks import read_spectrum_axes

# run-derived or GUI-only keys never take part in a sweep; leaving them in the base would
# change backend branching (preview_axis, for instance, switches process() to preview)
_NON_SWEEP_KEYS: tuple[str, ...] = (
    "phase_route",
    "preview_axis",
    "projections",
    "backend_runs",
    "diagnostics",
    "fill",
    "nus",
    "final_ext_lo",
    "final_ext_hi",
    "segment_shift_hz",
    # reference-stage optimisation switches (tests/reproduction only; not processing params)
    "reference_optimize",
)

#: behaviour decided by **automatic diagnostics/routing** that later runs must inherit:
#: they live only in ``params['diagnostics']``; without promotion a combination falls back
#: to backend defaults, so scripts differ on parameters nobody set. Real case: a direct-
#: dimension DC offset put ``nmrPipe -fn POLY -time`` in the reference script only.
_RUNTIME_DECISION_KEYS: tuple[tuple[str, str], ...] = (
    ("direct_poly_time", "apply_poly_time"),
)

REFERENCE_FILENAME = "reference.json"
REFERENCE_PEAK_LIST_FILENAME = "reference.list"
REFERENCE_TABLE_FILENAMES = {
    "parabolic": "reference_peak_table_parabolic.csv",
    "gaussian": "reference_peak_table_gaussian.csv",
}
GAUSSIAN_UNSUPPORTED_NDIM_REASON = "gaussian_unsupported_ndim"


@dataclass
class ReferenceSpectrum:
    """The frozen reference: spectrum, script, resolved parameters and both peak tables."""

    dataset_key: str
    exp_id: str
    data_id: str
    condition: str = ""
    run_id: str = ""
    phase_route: str = ""
    ndim: int = 2
    # effective sampling: full sampling (even when labelled NUS) degrades to uniform
    sampling: str = "uniform"
    sampling_schedule: str = ""      # origin: nuslist/params/full_sampling/...
    sampling_evidence: list[str] = field(default_factory=list)
    spectrum_path: str = ""            # the active spectrum under the project spectra/
    frozen_spectrum: str = ""          # the copy kept inside the study
    script_path: str = ""              # the reference script copy inside the study
    script_sha256: str = ""
    spectrum_sha256: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    sweep_params: dict[str, Any] = field(default_factory=dict)
    direct_range: dict[str, Any] = field(default_factory=dict)
    # this condition's working directory, shared by the reference and every workflow:
    # one converted fid, scripts side by side; empty for older references, then derived.
    work_dir: str = ""
    # per-axis PS (p0, p1) from the reference run, passed as direct_phase_override so that
    # candidates carry the **actual** phase the reference produced.
    direct_phase: dict[str, list[float]] = field(default_factory=dict)
    # reference tables: reference.list holds the identity, the two CSVs the two methods
    peak_table_path: str = ""
    peak_table_sha256: str = ""
    peak_count: int = 0
    peak_source: str = ""          # auto (NMRForge picking) | external | shared:<condition>
    peak_params: dict[str, Any] = field(default_factory=dict)
    peak_created_at: str = ""
    peak_tables: dict[str, dict[str, Any]] = field(default_factory=dict)
    peak_localization: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=now_iso)
    software_version: str = ""
    software_commit: str = ""
    tool_versions: dict[str, str] = field(default_factory=dict)
    logs_tail: list[str] = field(default_factory=list)

    @property
    def sweep_supported(self) -> bool:
        """Sweep capability: uniform (any dimensionality) and **2D NUS**; 3D NUS is not open."""
        if str(self.sampling) == "nus" and int(self.ndim) != 2:
            return False
        return True

    @property
    def peak_table_parabolic_path(self) -> str:
        return str((self.peak_tables.get("parabolic") or {}).get("path", ""))

    @property
    def peak_table_gaussian_path(self) -> str:
        return str((self.peak_tables.get("gaussian") or {}).get("path", ""))

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_key": self.dataset_key,
            "exp_id": self.exp_id,
            "data_id": self.data_id,
            "condition": self.condition,
            "run_id": self.run_id,
            "phase_route": self.phase_route,
            "ndim": int(self.ndim),
            "sampling": self.sampling,
            "sampling_schedule": self.sampling_schedule,
            "sampling_evidence": self.sampling_evidence,
            "spectrum_path": self.spectrum_path,
            "frozen_spectrum": self.frozen_spectrum,
            "script_path": self.script_path,
            "script_sha256": self.script_sha256,
            "spectrum_sha256": self.spectrum_sha256,
            "params": self.params,
            "sweep_params": self.sweep_params,
            "direct_range": self.direct_range,
            "work_dir": self.work_dir,
            "direct_phase": self.direct_phase,
            "peak_table_path": self.peak_table_path,
            "peak_table_sha256": self.peak_table_sha256,
            "peak_count": int(self.peak_count),
            "peak_source": self.peak_source,
            "peak_params": self.peak_params,
            "peak_created_at": self.peak_created_at,
            "peak_tables": self.peak_tables,
            "peak_localization": self.peak_localization,
            "created_at": self.created_at,
            "software_version": self.software_version,
            "software_commit": self.software_commit,
            "tool_versions": self.tool_versions,
            "logs_tail": self.logs_tail,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReferenceSpectrum:
        return cls(
            dataset_key=str(data.get("dataset_key", "")),
            exp_id=str(data.get("exp_id", "")),
            data_id=str(data.get("data_id", "")),
            condition=str(data.get("condition", "")),
            run_id=str(data.get("run_id", "")),
            phase_route=str(data.get("phase_route", "")),
            ndim=int(data.get("ndim", 2) or 2),
            sampling=str(data.get("sampling", "uniform")),
            sampling_schedule=str(data.get("sampling_schedule", "")),
            sampling_evidence=[
                str(x) for x in (data.get("sampling_evidence") or [])
            ],
            spectrum_path=str(data.get("spectrum_path", "")),
            frozen_spectrum=str(data.get("frozen_spectrum", "")),
            script_path=str(data.get("script_path", "")),
            script_sha256=str(data.get("script_sha256", "")),
            spectrum_sha256=str(data.get("spectrum_sha256", "")),
            params=dict(data.get("params") or {}),
            sweep_params=dict(data.get("sweep_params") or {}),
            direct_range=dict(data.get("direct_range") or {}),
            work_dir=str(data.get("work_dir", "")),
            direct_phase={
                str(k): [float(v[0]), float(v[1])]
                for k, v in (data.get("direct_phase") or {}).items()
                if isinstance(v, (list, tuple)) and len(v) >= 2
            },
            peak_table_path=str(data.get("peak_table_path", "")),
            peak_table_sha256=str(data.get("peak_table_sha256", "")),
            peak_count=int(data.get("peak_count", 0) or 0),
            peak_source=str(data.get("peak_source", "")),
            peak_params=dict(data.get("peak_params") or {}),
            peak_created_at=str(data.get("peak_created_at", "")),
            peak_tables={
                str(k): dict(v)
                for k, v in (data.get("peak_tables") or {}).items()
                if isinstance(v, dict)
            },
            peak_localization=dict(data.get("peak_localization") or {}),
            created_at=str(data.get("created_at", "")),
            software_version=str(data.get("software_version", "")),
            software_commit=str(data.get("software_commit", "")),
            tool_versions={
                str(k): str(v) for k, v in (data.get("tool_versions") or {}).items()
            },
            logs_tail=[str(x) for x in (data.get("logs_tail") or [])],
        )

    def direct_phase_override(self) -> dict[str, tuple[float, float]] | None:
        """Reference phases -> the backend ``direct_phase_override`` used for phase locking.

        The backend applies it per axis (see the phase branch of ``script_generator._stage_lines``),
        so every axis PS from the reference run is returned: the direct-dimension search is skipped
        and the indirect ones keep the reference result; otherwise a phase difference would leak
        into the measured peak positions.
        """
        if not self.direct_phase:
            return None
        return {
            axis: (float(values[0]), float(values[1]))
            for axis, values in self.direct_phase.items()
        }

    def normalized_direct_phase(self) -> dict[str, list[float]]:
        return {
            axis: [float(values[0]), float(values[1])]
            for axis, values in self.direct_phase.items()
        }

    def phase_record(self) -> dict[str, Any]:
        """Phase provenance (spec G1): ``phase_mode`` plus the ``actual_p0/p1`` really used.

        For automatic recognition (``phase_mode="auto"``) the actual values are the PS of the
        reference run; workflows lock to it and apply offsets through ``phase_delta.*``.
        """
        record: dict[str, Any] = {}
        for axis, values in self.direct_phase.items():
            record[str(axis)] = {
                "phase_mode": "auto",
                "actual_p0": float(values[0]),
                "actual_p1": float(values[1]),
                "source": (
                    f"reference_run:{self.run_id}" if self.run_id else "reference_run"
                ),
            }
        return record


@dataclass(frozen=True)
class ReferenceHandle:
    """An **explicitly** named reference: a study root plus condition, or a reference.json.

    Accepted forms:

    - ``"~/studies/s1"``                -> the primary condition of that study;
    - ``"~/studies/s1#B"``              -> condition B of that study;
    - ``".../study/reference/<key>/reference.json"`` -> the file itself,
        with the study root inferred from the path.
    """

    root: str
    condition: str = ""
    reference_json: str = ""

    def describe(self) -> str:
        if self.reference_json:
            return self.reference_json
        if self.condition:
            return f"{self.root}#{self.condition}"
        return self.root


def parse_reference_spec(spec: Any) -> ReferenceHandle:
    """External reference spec -> :class:`ReferenceHandle` (empty or malformed raises)."""
    if isinstance(spec, ReferenceHandle):
        return spec
    text = str(spec or "").strip()
    if not text:
        raise ReferenceError(
            tr(
                "combination mode needs an explicit reference: a study root or <root>#<condition>, "
                "or a reference.json path; reference mode produces it(run_reference_study / CLI "
                "reference + "
                "peaks)",
            )
        )
    path = Path(text).expanduser()
    if path.is_file() and path.name == REFERENCE_FILENAME:
        # <root>/study/reference/<key>/reference.json -> infer the study root
        root = path.parent.parent.parent.parent
        return ReferenceHandle(
            root=str(root), condition="", reference_json=str(path)
        )
    condition = ""
    if "#" in text:
        text, _, condition = text.partition("#")
        path = Path(text).expanduser()
    if not str(path).strip():
        raise ReferenceError(tr("malformed reference spec in combination mode: {p0!r}", p0=spec))
    return ReferenceHandle(root=str(path), condition=condition.strip())


def resolve_reference(
    spec: Any, *, backend: Any | None = None
) -> tuple[StudySession, DatasetRef, ReferenceSpectrum]:
    """External spec -> (session, condition dataset, reference); raises when not built."""
    handle = parse_reference_spec(spec)
    root = Path(handle.root).expanduser().resolve()
    if not (root / "project.json").is_file():
        raise ReferenceError(
            tr(
                "the reference given in combination mode is unusable: {p0} is not an NMRForge "
                "study root (no "
                "project.json)",
                p0=handle.describe(),
            )
        )
    session = open_study(root, backend=backend)
    if session.dataset is None:
        raise ReferenceError(tr("study {p0} has no data yet: run reference mode first", p0=root))
    target: DatasetRef | None = None
    if handle.reference_json:
        try:
            payload = json.loads(
                Path(handle.reference_json).read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as exc:
            raise ReferenceError(tr(
                "cannot read the reference file: {p0} "
                "({p1})",
                p0=handle.reference_json,
                p1=exc,
            ))
        key = str(payload.get("dataset_key", ""))
        target = next((ref for ref in session.datasets if ref.key == key), None)
        if target is None:
            raise ReferenceError(
                tr(
                    "the reference file {p0} points at dataset {p1!r}, which is not part of study "
                    "{p2}",
                    p0=handle.reference_json,
                    p1=key,
                    p2=root,
                )
            )
    elif handle.condition:
        target = session.dataset_by_condition(handle.condition)
        if target is None:
            raise ReferenceError(
                tr(
                    "study {p0} has no condition {p1!r}: available conditions are "
                    "{p2}",
                    p0=root,
                    p1=handle.condition,
                    p2=session.conditions,
                )
            )
    else:
        target = session.dataset
    reference = load_reference(session, target)
    if reference is None:
        raise ReferenceError(
            tr(
                "combination mode needs a built reference: {p0} (condition {p1}) has no reference "
                "spectrum yet; run reference mode "
                "first",
                p0=root,
                p1=target.condition,
            )
        )
    if not (reference.peak_table_path and Path(reference.peak_table_path).is_file()):
        raise ReferenceError(
            tr(
                "reference peak table missing: {p0} (condition {p1}); pick peaks in reference mode "
                "first (ensure_reference_peaks / CLI "
                "peaks)",
                p0=root,
                p1=target.condition,
            )
        )
    return session, target, reference


def reference_runtime_decisions(params: Mapping[str, Any]) -> dict[str, Any]:
    """Reference parameters -> the runtime decisions a combination must inherit.

    These come from the reference run's automatic diagnostics and routing; they live only under
    ``diagnostics``, and without promotion a combination silently uses backend defaults.
    """
    diagnostics = dict((params or {}).get("diagnostics") or {})
    return {
        key: bool(diagnostics[source])
        for key, source in _RUNTIME_DECISION_KEYS
        if source in diagnostics
    }


def sanitize_sweep_params(params: dict[str, Any]) -> dict[str, Any]:
    """Resolved parameters -> the base that can be fed back to the backend process().

    Run-derived and GUI-only keys are dropped, while ``reference_runtime_decisions()`` are
    promoted to top-level keys (an explicit top-level value is not overwritten).
    """
    cleaned = {
        str(key): value
        for key, value in dict(params or {}).items()
        if key not in _NON_SWEEP_KEYS
    }
    for key, value in reference_runtime_decisions(params).items():
        cleaned.setdefault(key, value)
    return cleaned


def normalize_direct_phase(raw: object) -> dict[str, list[float]]:
    """The reference run's direct_phase in the canonical {"F2": [p0, p1]} form."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[float]] = {}
    for axis, values in raw.items():
        if not isinstance(values, (list, tuple)) or len(values) < 2:
            continue
        try:
            out[str(axis)] = [float(values[0]), float(values[1])]
        except (TypeError, ValueError):
            continue
    return out


def _as_phase_pair(raw: object) -> list[float] | None:
    """``[p0, p1]`` / ``(p0, p1)`` -> a float pair; None when the shape is wrong."""
    if not isinstance(raw, (list, tuple)) or len(raw) < 2:
        return None
    try:
        return [float(raw[0]), float(raw[1])]
    except (TypeError, ValueError):
        return None


def reference_phase(
    effective: dict[str, Any], *, ndim: int = 2
) -> dict[str, list[float]]:
    """The reference parameters -> per-axis PS (p0, p1) used for locking.

    - ``phases``: the per-axis PS dict (unified route; indirect phases live here);
    - ``direct_phase``: a dict (unified route) or a **flat** ``[p0, p1]`` (the NUS route,
        where the direct dimension is ``F{ndim}``).

    The two are merged, with ``direct_phase`` winning for the direct dimension.
    """
    locked: dict[str, list[float]] = {}
    phases = normalize_direct_phase(effective.get("phases"))
    if phases:
        locked.update(phases)
    direct = normalize_direct_phase(effective.get("direct_phase"))
    if not direct:
        pair = _as_phase_pair(effective.get("direct_phase"))
        if pair is not None:
            direct = {f"F{int(ndim)}": pair}
    locked.update(direct)
    return locked


def dataset_for_reference(
    session: StudySession, reference: ReferenceSpectrum
) -> DatasetRef | None:
    """Reference -> its condition dataset in the session (None when not found)."""
    for ref in session.datasets:
        if ref.key == reference.dataset_key:
            return ref
    return session.dataset


def reference_work_dir(session: Any, dataset: Any) -> Path:
    """The **condition-level** working directory shared by reference and combinations.

    The fid conversion, the optimised script and every later candidate script land here:
    combinations reuse the converted fid, candidate scripts sit next to the reference script,
    and conditions cannot overwrite each other through a shared ``<data_id>.fid``.
    """
    key = f"{getattr(dataset, 'exp_id', '')}_{getattr(dataset, 'data_id', '')}"
    work = session.work_dir / str(key).strip("_")
    work.mkdir(parents=True, exist_ok=True)
    return work


def _find_reference_script(work: Path, data_id: str) -> Path:
    """Locate the script this reference run actually executed.

    The backend writes it under ``script_name`` or ``<dataset_id>_process.com`` (see
    ``backend/nmrpipe_backend._process``); look in that order, then fall back to the newest
    non-fid script, and raise rather than guess.
    """
    preferred = [
        work / f"{data_id}_process.com",
        work / f"{data_id}_nus.com",
        work / "process.com",
        work / "nus.com",
    ]
    for candidate in preferred:
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    candidates = [
        path
        for path in work.glob("*.com")
        if path.is_file()
        and path.name != "fid.com"
        and "_nus_rank" not in path.name
        and not path.name.startswith("preview")
    ]
    if candidates:
        return max(candidates, key=lambda p: p.stat().st_mtime_ns)
    raise ReferenceError(tr("the reference run left no usable processing script: {p0}", p0=work))


def save_reference(session: StudySession, reference: ReferenceSpectrum) -> Path:
    """Write the reference state to ``study/reference/<key>/reference.json``."""
    target = session.reference_dir_for(dataset_for_reference(session, reference))
    state_file = target / REFERENCE_FILENAME
    atomic_write_text(
        state_file,
        json.dumps(reference.to_dict(), ensure_ascii=False, indent=2) + "\n",
    )
    return state_file


def build_reference(
    session: StudySession,
    dataset: DatasetRef | None = None,
    *,
    params: dict[str, Any] | None = None,
    direct_range: DirectRange | None = None,
    phase_route: str | None = None,
    progress: Callable[[str], None] | None = None,
    force: bool = False,
) -> ReferenceSpectrum:
    """Run the automatic optimisation once and freeze the reference (idempotent).

    ``params`` only overrides the automatic flow's input (``ext_lo``, say); ``phase_route``
    defaults to the data type's route (uniform is usually ``unified``, automatic phase).
    The **actual** result of automatic recognition (the per-axis PS) goes into ``direct_phase``,
    so workflows lock phase and record ``actual_p0/actual_p1``.

    Parameters
    ----------
    session : StudySession
        the session (call :func:`add_dataset` first).
    dataset : DatasetRef, optional
        the condition to build; the session default by default.
    params : dict[str, Any], optional
        processing overrides (same key convention as the combination table).
    phase_route : str, optional
        the phase route (``"auto"``/``"none"``...); use ``"none"`` for tests and reproduction.
    progress : Callable[[str], None], optional
        progress callback, called with each log line.
    force : bool, default False
        rebuild an existing reference (which re-runs the optimisation).

    Returns
    -------
    ReferenceSpectrum
        the frozen reference: paths and hashes, full script, resolved parameters, phase, work dir.

    Raises
    ------
    ReferenceError
        the backend failed, data is missing or the reference is unusable; nothing partial is kept.

    Side effects
    ------------
    Writes ``study/reference/<condition>/`` (script, spectrum, reference.json) and a run record;
    **the active spectrum is never replaced**.

    Examples
    --------
        reference = build_reference(study, params={"phase_route": "none"})
    """
    from workflow.stepwise import generate_fid, generate_spectrum, read_experiment

    target_ref = dataset or session.dataset
    if target_ref is None:
        raise ReferenceError(tr("this study has no dataset yet; call add_dataset() first"))
    target_dir = session.reference_dir_for(target_ref)
    state_file = target_dir / REFERENCE_FILENAME
    if state_file.is_file() and not force:
        existing = load_reference(session, target_ref)
        if existing is not None:
            return existing

    logs: list[str] = []

    def _log(message: str) -> None:
        logs.append(str(message))
        if progress is not None:
            progress(str(message))

    manager = session.manager
    exp_id, data_id = target_ref.exp_id, target_ref.data_id
    run_params = dict(params or {})
    if phase_route:
        run_params["phase_route"] = phase_route

    # one working directory per condition, shared by the reference and all its workflows
    work = reference_work_dir(session, target_ref)
    _log(tr("reference [{p0}]: generating the FID", p0=target_ref.condition))
    generate_fid(
        manager, exp_id, data_id, session.backend,
        work_dir=work, progress=_log,
    )
    _log(tr("reference [{p0}]: generating the spectrum (optimisation)", p0=target_ref.condition))
    spectrum_path = generate_spectrum(
        manager, exp_id, data_id, session.backend,
        params=run_params, work_dir=work, progress=_log,
    )
    run = manager.last_run_for_data(exp_id, data_id, STEP_RUN_REFS["spectrum"])
    if run is None or run.status != "success":
        raise ReferenceError(tr("the reference run produced no successful WorkflowRun"))
    effective = dict(run.params or {})
    # P1-4: freeze where the direct-dimension range came from (explicit argument / the
    # legacy params spelling / a default), so a third party need not guess.
    direct = direct_range if direct_range is not None else _direct_from_params(run_params)

    script = _find_reference_script(work, data_id)

    frozen_spectrum = target_dir / f"reference{Path(spectrum_path).suffix}"
    frozen_script = target_dir / "process.com"
    shutil.copy2(spectrum_path, frozen_spectrum)
    frozen_script.write_text(
        script.read_text(encoding="utf-8"), encoding="utf-8", newline="\n"
    )

    experiment = read_experiment(manager, exp_id, data_id)
    # effective sampling: detection already degraded "labelled NUS but full" to uniform
    # (full sampling takes the ordinary FT, no SMILE; the evidence is recorded too).
    sampling = experiment.sampling
    reference = ReferenceSpectrum(
        dataset_key=target_ref.key,
        exp_id=exp_id,
        data_id=data_id,
        condition=target_ref.condition,
        run_id=run.run_id,
        phase_route=str(effective.get("phase_route", "") or ""),
        ndim=int(experiment.ndim),
        sampling=str(sampling.mode),
        sampling_schedule=str(sampling.schedule_type or ""),
        sampling_evidence=[str(item) for item in (sampling.evidence or [])],
        spectrum_path=str(spectrum_path),
        frozen_spectrum=str(frozen_spectrum),
        script_path=str(frozen_script),
        script_sha256=sha256_file(frozen_script),
        spectrum_sha256=sha256_file(frozen_spectrum),
        params=effective,
        direct_range=direct_range_record(direct, effective),
        sweep_params=sanitize_sweep_params(effective),
        work_dir=str(work),
        direct_phase=reference_phase(effective, ndim=int(experiment.ndim)),
        peak_table_path="",
        software_version=software_version(),
        software_commit=software_commit(),
        tool_versions=tool_versions(),
        logs_tail=logs[-40:],
    )
    save_reference(session, reference)
    session.save_state(reference=reference.to_dict())
    # project state must reach disk: the reference registers fid/spectrum (and a WorkflowRun),
    # and the CLI reference/workflows steps are separate processes that would not see it.
    session.manager.save()
    return reference


def _direct_from_params(params: Mapping[str, Any] | None) -> DirectRange | None:
    """Parse a complete legacy ``ext_lo``/``ext_hi`` pair from params; else None."""
    if not params:
        return None
    if params.get("ext_lo") in (None, "") or params.get("ext_hi") in (None, ""):
        return None
    try:
        return parse_direct_range(params=params)
    except SweepError:
        return None


def reference_ext_range(
    reference: ReferenceSpectrum | None,
) -> tuple[float, float] | None:
    """The direct-dimension range frozen in the reference (ppm), or None when unknown.

    Prefers the P1-4 ``direct_range`` record and falls back to ``ext_lo``/``ext_hi`` (or
    ``final_ext_lo``/``final_ext_hi``) in the effective params. The combination mode uses
    it to decide whether this run's override disagrees with the reference: a disagreement
    either needs an explicit ``allow_ext_override`` or a rebuilt reference, never a silent
    window change.
    """
    if reference is None:
        return None
    record = dict(getattr(reference, "direct_range", None) or {})
    lo, hi = record.get("ext_lo"), record.get("ext_hi")
    if lo is not None and hi is not None:
        try:
            return (float(lo), float(hi))
        except (TypeError, ValueError):
            pass
    return resolved_ext_range(reference.params)


def load_reference(
    session: StudySession, dataset: DatasetRef | None = None
) -> ReferenceSpectrum | None:
    """Read a condition's reference spectrum (None when absent; missing artefacts raise)."""
    target_ref = dataset or session.dataset
    if target_ref is None:
        return None
    state_file = session.reference_dir_for(target_ref) / REFERENCE_FILENAME
    if not state_file.is_file():
        return None
    try:
        raw = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReferenceError(tr(
            "reference state file is corrupt: {p0} "
            "({p1})",
            p0=state_file,
            p1=exc,
        )) from exc
    reference = ReferenceSpectrum.from_dict(raw)
    if not reference.condition:
        reference.condition = target_ref.condition
    for path in (reference.frozen_spectrum, reference.script_path):
        if not path or not Path(path).is_file():
            raise ReferenceError(
                tr(
                "reference artefacts are missing; rebuild with force=True: "
                "{p0}",
                p0=path,
            )
            )
    return reference


def load_references(session: StudySession) -> dict[str, ReferenceSpectrum]:
    """Read every condition's reference; conditions without one are simply absent."""
    out: dict[str, ReferenceSpectrum] = {}
    for ref in session.datasets:
        reference = load_reference(session, ref)
        if reference is not None:
            out[ref.key] = reference
    return out


def set_reference_peaks(
    session: StudySession,
    peak_table: Path | str,
    reference: ReferenceSpectrum | None = None,
    *,
    source: str = "external",
    params: dict[str, Any] | None = None,
) -> ReferenceSpectrum:
    """Record the reference identity table: path, SHA-256, peak count, source, picking params.

    ``source`` is ``auto`` (peaks picked on the reference spectrum), ``external``, or
    ``shared:<condition>`` when the primary identity is reused. All three freeze into
    ``reference.json``, and records only trust that snapshot.
    """
    ref = reference or load_reference(session)
    if ref is None:
        raise ReferenceError(tr("there is no reference spectrum yet; call build_reference() first"))
    path = Path(peak_table)
    if not path.is_file():
        raise ReferenceError(tr("peak table does not exist: {p0}", p0=path))
    ref.peak_table_path = str(path)
    ref.peak_table_sha256 = sha256_file(path)
    ref.peak_count = _count_peaks(path)
    ref.peak_source = str(source or "")
    ref.peak_params = dict(params or {})
    ref.peak_created_at = now_iso()
    save_reference(session, ref)
    return ref


def _count_peaks(path: Path) -> int:
    """Row count of a peak table (0 when unreadable; never blocks the flow)."""
    try:
        return len(read_reference_peaks(path))
    except Exception:  # noqa: BLE001 - recording only
        return 0


def build_reference_peak_tables(
    session: StudySession,
    reference: ReferenceSpectrum,
    *,
    window_pts: int | None = None,
    window_ppm: float | None = None,
    roi_f1_ppm: float | None = None,
    roi_f2_ppm: float | None = None,
) -> ReferenceSpectrum:
    """Write both reference peak tables (parabolic + gaussian) from one reference spectrum.

    Both share the same row set and the same ``reference_peak_id``; their column structure is
    fixed by ``peak_tables.PEAK_TABLE_COLUMNS``. For non-2D data the Gaussian table records
    ``fallback=true`` / ``fallback_reason=gaussian_unsupported_ndim`` (positions come from the
    parabola) instead of being skipped silently.
    """
    dataset = dataset_for_reference(session, reference)
    condition = dataset.condition if dataset is not None else reference.condition
    dataset_label = dataset.key if dataset is not None else reference.dataset_key
    spectrum_path = Path(reference.frozen_spectrum)
    rows = read_reference_peaks(reference.peak_table_path)
    axes = read_spectrum_axes(spectrum_path)
    parabolic = measure_peak_positions(
        spectrum_path,
        rows,
        axes=axes,
        window_pts=window_pts,
        window_ppm=window_ppm,
        refine="parabolic",
    )
    parabolic_rows = peak_table_rows(
        parabolic,
        workflow_id=REFERENCE_WORKFLOW_ID,
        condition=condition,
        dataset=dataset_label,
        method="parabolic",
    )
    if int(reference.ndim) == 2 and int(axes.ndim) == 2:
        gaussian = measure_peak_positions(
            spectrum_path,
            rows,
            axes=axes,
            window_pts=window_pts,
            window_ppm=window_ppm,
            refine="gaussian",
            roi_f1_ppm=roi_f1_ppm,
            roi_f2_ppm=roi_f2_ppm,
        )
        gaussian_rows = peak_table_rows(
            gaussian,
            workflow_id=REFERENCE_WORKFLOW_ID,
            condition=condition,
            dataset=dataset_label,
            method="gaussian",
        )
    else:
        gaussian_rows = gaussian_fallback_rows(
            parabolic,
            workflow_id=REFERENCE_WORKFLOW_ID,
            condition=condition,
            dataset=dataset_label,
            reason=GAUSSIAN_UNSUPPORTED_NDIM_REASON,
        )
    target_dir = session.reference_dir_for(dataset)
    written: dict[str, Path] = {}
    for method, table_rows in (
        ("parabolic", parabolic_rows),
        ("gaussian", gaussian_rows),
    ):
        written[method] = write_peak_table(
            target_dir / REFERENCE_TABLE_FILENAMES[method], table_rows
        )
    reference.peak_tables = {
        method: peak_table_digest(path) for method, path in written.items()
    }
    reference.peak_localization = {
        "parabolic": _localization_summary(parabolic_rows),
        "gaussian": _localization_summary(gaussian_rows),
        # wording on record: one cell per peak since 2026-09-19, not one shared window
        "exclusive_windows": True,
        "window_by_axis": {
            str(axis): dict(spec)
            for axis, spec in window_points_by_axis(
                axes, window_pts=window_pts, window_ppm=window_ppm
            ).items()
        },
        "window_ppm": window_ppm,
        "window_pts": window_pts,
    }
    # Fixed 2026-09-19: the tables were produced by the code that is running now, so the
    # version and commit in the record have to follow. Otherwise a study root upgraded
    # with `reference --rebuild-peak-tables` still claims the old version and an empty
    # commit, and "before / after the fix" is indistinguishable in the record again -
    # which is exactly what P0-1 set out to prevent.
    reference.software_version = software_version()
    reference.software_commit = software_commit()
    reference.tool_versions = tool_versions()
    save_reference(session, reference)
    session.save_state(reference=reference.to_dict())
    return reference


def rebuild_reference_peak_tables(
    session: StudySession,
    reference: ReferenceSpectrum | None = None,
    *,
    window_pts: int | None = None,
    window_ppm: float | None = None,
    roi_f1_ppm: float | None = None,
    roi_f2_ppm: float | None = None,
) -> ReferenceSpectrum:
    """Rebuild the two reference peak tables from the **existing** frozen spectrum.

    The frozen reference spectrum and the peak identity table (``reference.list``) are
    never touched: only ``reference_peak_table_{parabolic,gaussian}.csv`` and
    ``reference.json`` are rewritten. This is the upgrade path for study roots whose
    reference tables were produced with an older wording/column set, without re-running
    the whole reference chain (fid -> processing -> phase/baseline -> peak picking).

    Parameters
    ----------
    session : StudySession
        the study session.
    reference : ReferenceSpectrum, optional
        the reference to rebuild; the study's existing reference by default.
    window_pts, window_ppm, roi_f1_ppm, roi_f2_ppm : optional
        the same knobs as ``build_reference_peak_tables``.

    Returns
    -------
    ReferenceSpectrum
        the refreshed reference (``peak_tables`` / ``peak_localization`` updated,
        ``spectrum_sha256`` unchanged).

    Raises
    ------
    ReferenceError
        no reference exists, the frozen spectrum or ``reference.list`` is missing, or the
        SHA-256 of the spectrum / peak identity table changed while rebuilding (the data
        was touched: refuse instead of continuing quietly).

    Side effects
    ------------
    Rewrites ``reference_peak_table_{parabolic,gaussian}.csv`` and ``reference.json``;
    never re-processes data and never changes ``reference.list``.

    Examples
    --------
        rebuild_reference_peak_tables(session)   # upgrade an existing study root
    """
    ref = reference if reference is not None else load_reference(session)
    if ref is None:
        raise ReferenceError(tr("this study has no reference yet; build one first"))
    spectrum = Path(ref.frozen_spectrum)
    peaks = Path(ref.peak_table_path)
    if not spectrum.is_file():
        raise ReferenceError(tr("the frozen reference spectrum is missing: {p0}", p0=spectrum))
    if not peaks.is_file():
        raise ReferenceError(tr("the reference peak identity table is missing: {p0}", p0=peaks))
    spectrum_before = sha256_file(spectrum)
    peaks_before = sha256_file(peaks)
    updated = build_reference_peak_tables(
        session,
        ref,
        window_pts=window_pts,
        window_ppm=window_ppm,
        roi_f1_ppm=roi_f1_ppm,
        roi_f2_ppm=roi_f2_ppm,
    )
    if sha256_file(spectrum) != spectrum_before:
        raise ReferenceError(tr("the reference spectrum file changed while rebuilding"))
    if updated.spectrum_sha256 and updated.spectrum_sha256 != spectrum_before:
        raise ReferenceError(tr("the recorded reference spectrum sha256 no longer matches"))
    if sha256_file(peaks) != peaks_before:
        raise ReferenceError(
            tr(
            "recomputing the tables changed the reference peak identity table ({p0}), "
            "aborted",
            p0=peaks.name,
        )
        )
    # Fixed 2026-09-19: refresh the study-level aggregate records/reference.json. The CLI
    # `--rebuild-peak-tables` branch returns right here, so without this the downstream
    # reader keeps seeing the old SHA, the old version and no exclusive_windows.
    from nmrforge_api.records import refresh_reference_records

    refs = load_references(session)
    refs[str(updated.dataset_key)] = updated
    refresh_reference_records(session, refs)
    return updated


def _localization_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Peak-table rows -> localisation QC counts (detected / fallback / boundary hit)."""
    total = len(rows)
    detected = sum(1 for row in rows if row.get("detected"))
    fallback = sum(1 for row in rows if row.get("fallback"))
    reasons: dict[str, int] = {}
    for row in rows:
        if not row.get("fallback"):
            continue
        reason = str(row.get("fallback_reason", "") or "")
        reasons[reason] = reasons.get(reason, 0) + 1
    ratios = [
        float(row["intensity_ratio_vs_picked"])
        for row in rows
        if isinstance(row.get("intensity_ratio_vs_picked"), (int, float))
        and row["intensity_ratio_vs_picked"] == row["intensity_ratio_vs_picked"]
    ]
    ratios.sort()
    ratio_summary = {
        "n": len(ratios),
        "median": ratios[len(ratios) // 2] if ratios else float("nan"),
        "max": ratios[-1] if ratios else float("nan"),
    }
    # P2-5: rows that share a coordinate (after the exclusive-cell fix the reference
    # table can only collide when two records round to the same grid point). Same
    # wording as the sweep: n_duplicate = rows minus unique coordinates.
    n_duplicate = mark_duplicate_localization(rows)
    return {
        "n_peaks": int(total),
        "n_duplicate": int(n_duplicate),
        "n_cell_edge": sum(1 for row in rows if row.get("cell_edge") is True),
        "intensity_ratio_vs_picked": ratio_summary,
        "n_detected": int(detected),
        "n_missing": int(total - detected),
        "n_fallback": int(fallback),
        "fallback_reasons": reasons,
        "n_boundary_hit": sum(1 for row in rows if row.get("boundary_hit")),
    }


def ensure_reference_peaks(
    session: StudySession,
    reference: ReferenceSpectrum | None = None,
    *,
    sigma_multiplier: float | None = None,
    max_peaks: int = 0,
    force: bool = False,
    localization_method: str = "parabolic",
    gaussian_roi_f1_ppm: float | None = None,
    gaussian_roi_f2_ppm: float | None = None,
) -> ReferenceSpectrum:
    """Ensure the identity table and both reference tables exist (peaks are picked by default).

    - the primary condition calls ``pick_reference_peaks()`` and freezes ``reference.list``;
    - other conditions copy that identity table, then measure both tables on their own spectrum;
    - an existing identity table with both files is reused unless ``force`` is given;
    - **the picking threshold is chosen when the reference is built and then locked**:
        ``sigma_multiplier`` (35 sigma by default) may be supplied while no reference table exists;
        once the reference is fixed a **different** value raises ``ReferenceError``, because every
        later workflow must use the reference threshold. Changing it means rebuilding:
        ``force=True``, or delete ``study/reference/<key>/`` and run again;
    - the actual usage lands in ``peak_params``: ``sigma_multiplier`` (the reference value),
        ``previous_sigma_multiplier`` (the previous one on a forced rebuild)
      ``detection.sigma_multiplier`` / ``detection.threshold_source``;
    - ``localization_method`` only decides how **reference positions** are read (parabolic by
        default); both tables are always produced together (2026-09-13 specification B2).

    Parameters
    ----------
    session : StudySession
        the session.
    reference : ReferenceSpectrum, optional
        an existing reference; ``None`` uses the frozen one in the session.
    sigma_multiplier : float, optional
        picking threshold (sigma). **Only settable while building**; a different later value raises.
    max_peaks : int, default 0
        maximum number of reference peaks (0 = keep all).
    force : bool, default False
        rebuild an existing table (only a rebuild may change the threshold, and it is recorded).
    localization_method : str, default "parabolic"
        ``parabolic`` or ``gaussian`` (Gaussian is 2D only).
    gaussian_roi_f1_ppm, gaussian_roi_f2_ppm : float, optional
        Gaussian ROI radius (ppm); config default.

    Returns
    -------
    ReferenceSpectrum
        the reference with table paths and counts; the frozen threshold is recorded too.

    Raises
    ------
    ReferenceError
        the reference is not frozen, the threshold conflicts with it, or picking failed.

    Side effects
    ------------
    Writes both tables and ``reference.json``; no processing is re-run.

    Examples
    --------
        reference = ensure_reference_peaks(reference, sigma_multiplier=20)
    """
    ref = reference or load_reference(session)
    if ref is None:
        raise ReferenceError(tr("there is no reference spectrum yet; call build_reference() first"))
    requested_sigma = (
        float(sigma_multiplier)
        if sigma_multiplier and float(sigma_multiplier) > 0
        else None
    )
    stored_sigma = ref.peak_params.get("sigma_multiplier")
    stored_sigma_value = (
        float(stored_sigma) if stored_sigma not in (None, "") else None
    )
    stored_detection = dict(ref.peak_params.get("detection") or {})
    # the effective threshold: the picking default (35 sigma) when unrecorded, used to decide
    # whether an external value agrees with the frozen reference.
    effective_stored_sigma = stored_detection.get("sigma_multiplier")
    if effective_stored_sigma is None:
        effective_stored_sigma = stored_sigma_value
    else:
        effective_stored_sigma = float(effective_stored_sigma)
    reference_peaks_frozen = bool(
        ref.peak_table_path
        and Path(ref.peak_table_path).is_file()
        and ref.peak_tables
    )
    # 2026-09-14 (user): the threshold is **settled when the reference is built**; afterwards
    # every perturbation reuses it, and a different value raises instead of re-picking.
    # changing it means rebuilding: force=True, or delete that condition's reference/.
    if (
        requested_sigma is not None
        and effective_stored_sigma is not None
        and requested_sigma != effective_stored_sigma
        and reference_peaks_frozen
        and not force
    ):
        raise ReferenceError(
            tr(
                "the picking threshold is locked to the reference ({p0:g} sigma; every later "
                "perturbation must use the same threshold as the reference): got {p1:g} sigma. "
                "Changing it means rebuilding: pass force=True, or delete that condition's "
                "study/reference/<key>/ and "
                "rebuild.",
                p0=effective_stored_sigma,
                p1=requested_sigma,
            )
        )
    if (
        not force
        and ref.peak_table_path
        and Path(ref.peak_table_path).is_file()
        and ref.peak_tables
        and all(
            Path(str((ref.peak_tables.get(method) or {}).get("path", ""))).is_file()
            for method in ("parabolic", "gaussian")
        )
    ):
        return ref
    dataset = dataset_for_reference(session, ref)
    primary = load_reference(session, session.dataset) if session.dataset else None
    is_primary = (
        primary is None
        or dataset is None
        or session.dataset is None
        or dataset.key == session.dataset.key
        or primary.dataset_key == ref.dataset_key
    )
    target = session.reference_dir_for(dataset) / REFERENCE_PEAK_LIST_FILENAME
    if is_primary:
        details: dict[str, Any] = {}
        pick_reference_peaks(
            session,
            sigma_multiplier=sigma_multiplier,
            out_path=target,
            details=details,
            localization_method=localization_method,
            gaussian_roi_f1_ppm=gaussian_roi_f1_ppm,
            gaussian_roi_f2_ppm=gaussian_roi_f2_ppm,
            dataset=dataset,
        )
        if max_peaks and max_peaks > 0:
            _keep_top_peaks(target, int(max_peaks))
        ref = set_reference_peaks(
            session,
            target,
            ref,
            source="auto",
            params={
                "sigma_multiplier": sigma_multiplier,
                "previous_sigma_multiplier": stored_sigma_value,
                "max_peaks": int(max_peaks),
                # the margin width/point conversion (user option A), for cross-resolution rechecking
                "detection": details.get("detection") or {},
                "localization_method": str(localization_method),
                "localization": details.get("localization") or {},
            },
        )
    else:
        assert primary is not None  # with is_primary=False the primary reference must exist
        source_list = Path(primary.peak_table_path)
        if not source_list.is_file():
            raise ReferenceError(
                tr(
                    "a non-primary condition needs the primary identity first: call "
                    "ensure_reference_peaks()",
                )
            )
        target.write_text(
            source_list.read_text(encoding="utf-8"), encoding="utf-8"
        )
        ref = set_reference_peaks(
            session,
            target,
            ref,
            source=f"shared:{primary.condition}",
            params={
                "sigma_multiplier": sigma_multiplier,
                "max_peaks": int(max_peaks),
                "localization_method": str(localization_method),
                "shared_from": primary.dataset_key,
                "shared_peak_count": int(primary.peak_count),
                "shared_peak_table_sha256": primary.peak_table_sha256,
            },
        )
    # picking and copying both record runs; writing to disk keeps them visible across processes
    session.manager.save()
    return build_reference_peak_tables(
        session,
        ref,
        roi_f1_ppm=gaussian_roi_f1_ppm,
        roi_f2_ppm=gaussian_roi_f2_ppm,
    )


def _keep_top_peaks(path: Path, keep: int) -> None:
    """Keep the first keep peaks by Intensity (same path, POKY .list layout preserved)."""
    from core.peaks.peak_table import export_peaks_poky, load_peaks

    rows = load_peaks(path)
    if len(rows) <= keep:
        return
    rows.sort(
        key=lambda row: abs(float(row.get("Intensity") or 0.0)), reverse=True
    )
    export_peaks_poky(path, rows[:keep])
    # the frozen identity table is usually free of attachments; if this path happens to have
    # localisation attachments, truncate them by row too; normally a no-op.
    from core.peaks.localize import trim_localization_records

    trim_localization_records(path, keep)


__all__ = [
    "GAUSSIAN_UNSUPPORTED_NDIM_REASON",
    "ReferenceHandle",
    "REFERENCE_FILENAME",
    "REFERENCE_PEAK_LIST_FILENAME",
    "REFERENCE_TABLE_FILENAMES",
    "ReferenceSpectrum",
    "build_reference",
    "build_reference_peak_tables",
    "rebuild_reference_peak_tables",
    "dataset_for_reference",
    "ensure_reference_peaks",
    "load_reference",
    "load_references",
    "normalize_direct_phase",
    "parse_reference_spec",
    "reference_phase",
    "reference_runtime_decisions",
    "reference_work_dir",
    "resolve_reference",
    "sanitize_sweep_params",
    "save_reference",
    "set_reference_peaks",
]
