"""Run a whole parameter study in one call (the main downstream entry point).

Flow (2026-09-13 specification)::

    Raw data (A/B...) -> reference workflow (1 script + 2 peak tables)
        -> user parameter table -> W0001... each workflow runs every condition
        -> parabolic / gaussian peak tables + full provenance + QC

Boundary: this API **only** processes and records. CSP, robustness, statistics and
conclusions belong to downstream analysis on the unified peak table.

Typical usage::

    from nmrforge_api import run_parameter_study

    result = run_parameter_study(
        "~/studies/hsqc_params",                     # study root (reusable, resumable)
        datasets={"A": "~/data/bmrxxxx/1",           # condition A
                            "B": "~/data/bmrxxxx/2"},          # condition B (same parameters)
        combos=[{"zero_fill": 1}, {"zero_fill": 2}], # the user parameter table
    )
    print(result.workflows_by_status())
"""

from __future__ import annotations

import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nmrforge_api.direct_range import (
    direct_matches_ext,
    parse_direct_range,
)
from nmrforge_api.errors import DatasetError, SweepError
from nmrforge_api.records import write_records, write_reference_records
from nmrforge_api.reference import (
    ReferenceSpectrum,
    build_reference,
    build_reference_peak_tables,
    ensure_reference_peaks,
    load_reference,
    load_references,
    parse_reference_spec,
    reference_ext_range,
    resolve_reference,
    set_reference_peaks,
)
from nmrforge_api.session import (
    DatasetRef,
    StudySession,
    add_dataset,
    open_study,
)
from nmrforge_api.sweep import (
    DEFAULT_MAX_RUNS,
    STATUS_FAILED,
    STATUS_SUCCESS,
    STATUS_WARNING,
    WARN_DIRECT_RANGE_OVERRIDE,
    SweepPlan,
    SweepRun,
    load_workflows,
    plan_sweep,
    run_sweep,
    workflow_summary,
)
from ui_support.i18n import tr


@dataclass
class StudyResult:
    """The complete handle for one study (no statistical inference)."""

    session: StudySession
    plan: SweepPlan
    references: dict[str, ReferenceSpectrum] = field(default_factory=dict)
    runs: list[SweepRun] = field(default_factory=list)
    workflows: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    records: dict[str, str] = field(default_factory=dict)

    @property
    def root(self) -> Path:
        return self.session.root

    @property
    def reference(self) -> ReferenceSpectrum | None:
        """The primary (first) condition's reference spectrum."""
        if self.session.dataset is None:
            return None
        return self.references.get(self.session.dataset.key)

    @property
    def peak_table_path(self) -> str:
        ref = self.reference
        return ref.peak_table_path if ref is not None else ""

    @property
    def peak_tables(self) -> dict[str, str]:
        """Paths to both reference peak tables (parabolic / gaussian)."""
        ref = self.reference
        if ref is None:
            return {}
        return {
            "parabolic": ref.peak_table_parabolic_path,
            "gaussian": ref.peak_table_gaussian_path,
        }

    @property
    def conditions(self) -> list[str]:
        return self.session.conditions

    @property
    def failed_runs(self) -> list[SweepRun]:
        return [run for run in self.runs if run.status == STATUS_FAILED]

    def workflows_by_status(self) -> dict[str, int]:
        return workflow_summary(self.runs)


def _resolve_conditions(
    datasets: Mapping[str, str] | Sequence[Any] | None,
    dataset: str | Path | None,
) -> list[tuple[str, str]]:
    """``datasets``/``dataset`` -> [(condition label, dataset path)].

    - ``datasets={"A": path, "B": path}`` (recommended for several conditions);
    - ``datasets=[("A", path), ("B", path)]`` or ``[pathA, pathB]`` (labelled A/B);
    - ``dataset=path``: a single condition (legacy callers).
    """
    out: list[tuple[str, str]] = []
    if datasets is not None:
        if isinstance(datasets, Mapping):
            for label, path in datasets.items():
                out.append((str(label), str(path)))
        else:
            for item in datasets:
                if isinstance(item, (tuple, list)) and len(item) == 2:
                    out.append((str(item[0]), str(item[1])))
                else:
                    out.append(("", str(item)))
    if dataset is not None:
        out.append(("", str(dataset)))
    return out


def _register_conditions(
    session: StudySession, conditions: Sequence[tuple[str, str]]
) -> None:
    """Register the user condition datasets; an identical existing one is reused."""
    for label, path in conditions:
        source = str(Path(path).expanduser().resolve())
        existing = (
            session.dataset_by_condition(label)
            if label
            else (session.dataset if len(session.datasets) == 0 else None)
        )
        if existing is not None:
            if Path(existing.source).resolve() != Path(source):
                raise DatasetError(
                    tr(
                        "condition {p0!r} is bound to dataset {p1}; use another condition label "
                        "for {p2}, or a separate study "
                        "root",
                        p0=existing.condition,
                        p1=existing.source,
                        p2=source,
                    )
                )
            continue
        add_dataset(
            session,
            source,
            condition=label,
            title="",
        )


@dataclass
class ReferenceResult:
    """Reference-mode result: one reference per condition (spectrum, script, two tables)."""

    session: StudySession
    references: dict[str, ReferenceSpectrum] = field(default_factory=dict)
    records: dict[str, str] = field(default_factory=dict)

    @property
    def root(self) -> Path:
        return self.session.root

    @property
    def conditions(self) -> list[str]:
        return self.session.conditions

    def reference(self, condition: str = "") -> ReferenceSpectrum | None:
        """Reference for a condition (the primary one by default)."""
        if condition:
            dataset = self.session.dataset_by_condition(condition)
            return self.references.get(dataset.key) if dataset else None
        primary = self.session.dataset
        return self.references.get(primary.key) if primary else None

    @property
    def peak_tables(self) -> dict[str, str]:
        """Paths to the primary condition's two reference peak tables."""
        reference = self.reference()
        if reference is None:
            return {}
        return {
            "parabolic": reference.peak_table_parabolic_path,
            "gaussian": reference.peak_table_gaussian_path,
        }


def run_reference_study(
    root: Path | str,
    dataset: Path | str | None = None,
    *,
    datasets: Mapping[str, str] | Sequence[Any] | None = None,
    name: str = "",
    params: dict[str, Any] | None = None,
    phase_route: str | None = None,
    peaks: Path | str | None = None,
    direct_range: Any = None,
    ext_lo: Any = None,
    ext_hi: Any = None,
    sigma_multiplier: float | None = None,
    max_peaks: int = 0,
    localization_method: str = "parabolic",
    gaussian_roi_f1_ppm: float | None = None,
    gaussian_roi_f2_ppm: float | None = None,
    force: bool = False,
    backend: Any | None = None,
    write: bool = True,
    progress: Callable[[str], None] | None = None,
) -> ReferenceResult:
    """**Reference mode**: import the condition data and freeze a reference plus both tables.

    This builds references only and runs no combination. Combination mode
    (:func:`run_combination_study`) must **reference** what this produces. The picking
    threshold ``sigma_multiplier`` is set here and locked: later workflows reuse it.

    The direct-dimension range (**ppm**, ``ext_lo`` = high end / ``ext_hi`` = low end) can be
    given as ``direct_range=(high, low)`` (reversed is accepted and swapped back),
    ``direct_range={"lo":..., "hi":...}`` or explicit ``ext_lo=/ext_hi=``.
    A range that disagrees with the frozen reference **rebuilds it and re-measures both tables**;
    ``force=True`` rebuilds unconditionally.

    Parameters
    ----------
    root : Path | str
        the study root (created or reused).
    dataset : Path | str, optional
        a single dataset directory; use ``datasets={condition: directory}`` for more.
    datasets : Mapping[str, str] | Sequence[Any], optional
        condition -> directory (A/B...); exclusive with ``dataset``.
    name : str, optional
        name recorded when creating a study.
    params : dict[str, Any], optional
        processing overrides (same key convention as the combination table).
    phase_route : str, optional
        phase route; ``"none"`` for tests and reproduction.
    peaks : Path | str, optional
        an external reference table (``.list``): registered as the peak identity, no auto-picking.
    direct_range, ext_lo, ext_hi : Any, optional
        the direct range (``(high, low)`` or a dict); precedence is documented
        :func:`nmrforge_api.direct_range.parse_direct_range`.
    sigma_multiplier : float, optional
        reference picking threshold (sigma) - **fixed and locked here**; cannot change later.
    max_peaks : int, default 0
        maximum number of reference peaks (0 = keep all).
    localization_method : str, default "parabolic"
        reference-table refinement (``gaussian`` is 2D only).
    gaussian_roi_f1_ppm, gaussian_roi_f2_ppm : float, optional
        Gaussian ROI radius (ppm); config default.
    force : bool, default False
        rebuild an existing reference (only a rebuild may change the threshold).
    backend : Any, optional
        the processing backend; built from config, injectable in tests.
    write : bool, default True
        when False, compute without writing anything (self-checks).
    progress : Callable[[str], None], optional
        progress callback, called with each log line.

    Returns
    -------
    ReferenceResult
        the result: ``session``, ``references`` (condition -> reference) and ``records``.

    Raises
    ------
    DatasetError
        an invalid directory, an unrecognisable experiment, or a duplicate condition.

    Side effects
    ------------
    Imports and registers data, runs the reference and freezes it (``study/reference/``);
    no parameter combination is generated.

    Examples
    --------
        result = run_reference_study("study/", "path/to/bruker", sigma_multiplier=35)
        reference = result.reference()
    """
    session = open_study(root, name=name, backend=backend)
    conditions = _resolve_conditions(datasets, dataset)
    _register_conditions(session, conditions)
    if not session.datasets:
        raise DatasetError(
            tr(
                "this study has no dataset yet: pass datasets=<{condition: directory}> or "
                "dataset=<Bruker directory> on the first "
                "run",
            )
        )
    direct = parse_direct_range(
        direct_range, ext_lo=ext_lo, ext_hi=ext_hi, params=params
    )
    run_params = dict(params or {})
    if direct is not None:
        run_params.update(direct.params())
    references: dict[str, ReferenceSpectrum] = {}
    rebuilt: list[str] = []
    for ref in session.datasets:
        reference = None if force else load_reference(session, ref)
        if reference is not None and direct is not None:
            # the direct range defines the reference: a mismatch with the frozen one rebuilds it
            if not direct.matches_params(reference.params):
                reference = None
                rebuilt.append(ref.condition or ref.key)
        if reference is None:
            if progress is not None and rebuilt:
                progress(
                    tr(
                        "direct range differs from the frozen reference; rebuilding: ext_lo={p0:g} "
                        "ext_hi={p1:g} "
                        "ppm",
                        p0=direct.lo,
                        p1=direct.hi,
                    )
                    if direct is not None
                    else tr("rebuilding the reference")
                )
            reference = build_reference(
                session,
                ref,
                params=run_params or None,
                direct_range=direct,
                phase_route=phase_route,
                progress=progress,
                force=True,
            )
        references[ref.key] = reference
    # peak identity and both tables: the primary condition picks, others share the identity
    for ref in session.datasets:
        references[ref.key] = ensure_reference_peaks(
            session,
            references[ref.key],
            sigma_multiplier=sigma_multiplier,
            max_peaks=max_peaks,
            localization_method=localization_method,
            gaussian_roi_f1_ppm=gaussian_roi_f1_ppm,
            gaussian_roi_f2_ppm=gaussian_roi_f2_ppm,
            force=bool(rebuilt),
        )
    if peaks is not None:
        # optional: the study's own table (public archive or assigned) becomes the primary identity
        primary = session.datasets[0]
        target = session.reference_dir_for(primary) / "reference.list"
        target.write_text(
            Path(peaks).read_text(encoding="utf-8-sig"), encoding="utf-8"
        )
        primary_reference = set_reference_peaks(
            session, target, references[primary.key], source="external"
        )
        references[primary.key] = build_reference_peak_tables(
            session, primary_reference
        )
        # non-primary conditions re-copy the primary identity table, keeping identity consistent
        for ref in session.datasets[1:]:
            references[ref.key] = ensure_reference_peaks(
                session,
                references[ref.key],
                force=True,
                sigma_multiplier=sigma_multiplier,
                max_peaks=max_peaks,
                localization_method=localization_method,
                gaussian_roi_f1_ppm=gaussian_roi_f1_ppm,
                gaussian_roi_f2_ppm=gaussian_roi_f2_ppm,
            )
    records: dict[str, str] = {}
    if write:
        records = write_reference_records(session, references)
    primary_reference = references[session.datasets[0].key]
    session.save_state(reference=primary_reference.to_dict(), records=records)
    session.manager.save()
    return ReferenceResult(session=session, references=references, records=records)


def run_combination_study(
    reference: Any,
    *,
    combos: Sequence[Mapping[str, Any]] | None = None,
    axes: Mapping[str, Sequence[Any]] | None = None,
    max_runs: int = DEFAULT_MAX_RUNS,
    localization: Any = "parabolic",
    localize_peaks: Any = None,
    edge_margin_ppm: float | None = None,
    # window_pts/window_ppm/sign: legacy, unused since independent picking
    window_pts: int | None = None,
    window_ppm: float | None = None,
    sign: str = "abs",
    direct_range: Any = None,
    ext_lo: Any = None,
    ext_hi: Any = None,
    allow_ext_override: bool = False,
    roi_f1_ppm: float | None = None,
    roi_f2_ppm: float | None = None,
    resume: bool = True,
    backend: Any | None = None,
    write: bool = True,
    progress: Callable[[str], None] | None = None,
) -> StudyResult:
    """**Combination mode**: run workflows from a parameter table on an explicit reference.

    ``reference`` is **required**: the study root (``<root>`` or ``<root>#<condition>``)
    or a ``reference.json`` path. Combinations run against it:

    - the base is that reference's resolved parameters (phase locked); a combination overrides
      only the keys it names;
      - **combinations pick independently**: the threshold is locked to the reference and cannot
        be changed here (threshold keys in a table raise); each picks on **its own candidate**
        spectrum with that locked threshold, writing its own table with reference_peak_id and
        assignment empty - matching back is **downstream work**;
    - localization = parabolic (default) / gaussian / both: only the chosen table is written
        (a combination may override it with its own localization key);
    - localize_peaks = a target-peak CSV / a sequence of peak ids: only those peaks take
        part in the chosen method's refinement (a combination may override it with the
        localization.targets key); default = the whole spectrum. **Condition granularity**:
        the CSV may carry a condition column (each condition reads only its own rows), or
        give a mapping such as {"A": "a.csv", "B": "b.csv"} / {"default": "x.csv",
        "by_condition": {"A": "a.csv"}}; a condition with no rows raises by default
        (on_missing="all"/"none" relaxes that, and the choice is recorded);
    - the direct range (``ext_lo``/``ext_hi``, ppm) can be overridden for **this batch** with
        ``direct_range=`` (the reference is not rebuilt), and again per combination;
        actual values land in ``parameters_resolved.direct_range`` per workflow;
    - a named condition runs only that one; the bare root runs every condition;
    - candidates and tables go to ``study/workflows/<workflow_id>/<condition>/``;
    - window_pts / window_ppm / sign are legacy: there is no search window any more,
        only the physical edge margin (edge_margin_ppm).

    Parameters
    ----------
    reference : Any
        how to find the reference: ``<root>``, ``<root>#<condition>``, a ``reference.json`` path
        or :class:`ReferenceHandle`; combination mode **requires it**.
    combos : Sequence[Mapping[str, Any]], optional
        an explicit combination table (keys are script parameters); exclusive with ``axes``.
    axes : Mapping[str, Sequence[Any]], optional
        shorthand: candidate values per axis, expanded into combinations.
    max_runs : int, default 256
        maximum number of combinations; exceeding it raises.
    localization : Any, default "parabolic"
        ``parabolic``/``gaussian``/``both``; a combination may override it.
    localize_peaks : Any, optional
        target-peak list (a CSV path / ``LocalizationTargets`` / a sequence of peak ids);
        a combination may override it with the ``localization.targets`` key. Default = the
        whole spectrum. **Per condition**: the CSV may carry a ``condition`` column, or
        give a condition mapping ``{"A": "a.csv", "B": "b.csv"}`` (``default`` plus
        ``by_condition`` is also accepted); ``peak_id`` is validated against each
        condition's own spectrum, and a condition with no rows raises by default
        (``on_missing="all"`` leaves that condition unlimited, ``"none"`` refines nothing
        there, and the strategy is recorded in ``run.json``).
    edge_margin_ppm : float, optional
        edge exclusion radius when picking (ppm).
    window_pts, window_ppm : int | float, optional
        peak-position search window (points/ppm); prefer the physical one.
    sign : str, default "abs"
        legacy parameter; combination mode uses dominant-sign detection.
    direct_range, ext_lo, ext_hi : Any, optional
        direct-range override (the reference is not rebuilt).
    allow_ext_override : bool, default False
        Only with True may this batch's override disagree with the reference's frozen
        direct-range range (leaving the run-level warning code
        ``direct_range_override``); a disagreement without it is an error rather than
        a silent window change.
    roi_f1_ppm, roi_f2_ppm : float, optional
        Gaussian ROI radius (ppm).
    resume : bool, default True
        successful runs with an identical fingerprint are skipped (resume).
    backend : Any, optional
        the processing backend (injected by tests).
    write : bool, default True
        when False, compute without writing.
    progress : Callable[[str], None], optional
        progress callback.

    Returns
    -------
    StudyResult
        ``session``, ``plan``, ``references``, ``runs`` (per workflow x condition)
        ``workflows``, ``summary``, ``records``.

    Raises
    ------
    ReferenceError
        no reference was given, it is unusable, or it does not match the combinations.

    Side effects
    ------------
    Writes ``study/workflows/<W0001...>/<condition>/`` (candidates, script, tables, logs,
    run.json) and the summary records; it neither builds a reference nor replaces the spectrum.

    Examples
    --------
        result = run_combination_study("study/", axes={"zero_fill": [1, 2]})
    """
    handle = parse_reference_spec(reference)
    session, target, ref = resolve_reference(reference, backend=backend)
    targets = (
        [target]
        if (handle.condition or handle.reference_json)
        else list(session.datasets)
    )
    references: dict[str, ReferenceSpectrum] = {}
    for item in targets:
        if item.key == target.key:
            references[item.key] = ref
            continue
        other = load_reference(session, item)
        if other is None or not other.peak_table_path:
            raise ReferenceError(
                tr(
                    "condition {p0} has no usable reference: run reference mode "
                    "(run_reference_study) for every condition "
                    "first",
                    p0=item.condition or item.key,
                )
            )
        references[item.key] = other
    direct = parse_direct_range(direct_range, ext_lo=ext_lo, ext_hi=ext_hi)
    base_overrides: dict[str, Any] = {}
    notes: list[str] = []
    override_warnings: list[dict[str, Any]] = []
    if direct is not None:
        base_overrides.update(direct.params())
        frozen_ext = reference_ext_range(ref)
        if frozen_ext is None:
            notes.append(
                tr(
                    "the direct-dimension range is given by the combination mode: ext_lo={p0:g} "
                    "ext_hi={p1:g} ppm(the reference is not rebuilt; the reference recorded no "
                    "range, so consistency cannot be "
                    "checked)",
                    p0=direct.lo,
                    p1=direct.hi,
                )
            )
        elif not direct_matches_ext(direct, frozen_ext):
            if not allow_ext_override:
                raise SweepError(
                    tr(
                        "this direct-dimension range ext_lo={p0:g} ext_hi={p1:g} ppm disagrees "
                        "with the reference's frozen range ext_lo={p2:g} ext_hi={p3:g} ppm: the "
                        "reference spectrum is not rebuilt, but peak positions and the peak set "
                        "move with the window. Either rebuild the reference with the reference "
                        "mode (`reference --direct-range`) or pass `allow_ext_override=True` (CLI "
                        "`--allow-ext-override`), which leaves the run-level warning code "
                        "`direct_range_override`",
                        p0=direct.lo,
                        p1=direct.hi,
                        p2=frozen_ext[0],
                        p3=frozen_ext[1],
                    )
                )
            notes.append(
                tr(
                    "the direct-dimension range is **explicitly overridden** by the combination "
                    "mode and disagrees with the reference: ext_lo={p0:g} ext_hi={p1:g} ppm(the "
                    "reference is not rebuilt; run-level warning "
                    "direct_range_override)",
                    p0=direct.lo,
                    p1=direct.hi,
                )
            )
            override_warnings.append(
                {
                    "code": WARN_DIRECT_RANGE_OVERRIDE,
                    "message": (
                        tr(
                            "the direct-dimension range was overridden by the combination mode to "
                            "ext_lo={p0:g} ext_hi={p1:g} ppm, which disagrees with the reference's "
                            "frozen range ext_lo={p2:g} ext_hi={p3:g} ppm (the reference spectrum "
                            "was not rebuilt; peak positions and the peak set move with the "
                            "window)",
                            p0=direct.lo,
                            p1=direct.hi,
                            p2=frozen_ext[0],
                            p3=frozen_ext[1],
                        )
                    ),
                    "count": 1,
                    "peaks": [],
                    "localization_method": "",
                }
            )
        else:
            notes.append(
                tr(
                    "the direct-dimension range is given by the combination mode and agrees with "
                    "the reference: ext_lo={p0:g} ext_hi={p1:g} ppm(the reference is not "
                    "rebuilt)",
                    p0=direct.lo,
                    p1=direct.hi,
                )
            )
    plan = plan_sweep(
        ref,
        axes=axes,
        combos=combos,
        max_runs=max_runs,
        base_overrides=base_overrides,
        notes=notes,
    )
    runs = run_sweep(
        session,
        plan,
        reference=ref,
        datasets=targets,
        localization=localization,
        localize_peaks=localize_peaks,
        edge_margin_ppm=edge_margin_ppm,
        roi_f1_ppm=roi_f1_ppm,
        roi_f2_ppm=roi_f2_ppm,
        resume=resume,
        extra_warnings=override_warnings,
        progress=progress,
    )
    summary = _summary(plan, runs, references)
    summary["reference_spec"] = handle.describe()
    if direct is not None:
        summary["direct_range"] = direct.to_dict()
    records: dict[str, str] = {}
    if write:
        records = write_records(
            session,
            references=references,
            plan=plan,
            runs=runs,
            peaks=reference_peaks(session, ref),
            reference_spec=handle.describe(),
        )
    workflows = load_workflows(session)
    session.save_state(reference=ref.to_dict(), records=records)
    session.manager.save()
    return StudyResult(
        session=session,
        plan=plan,
        references=references,
        runs=runs,
        workflows=workflows,
        summary=summary,
        records=records,
    )


def run_parameter_study(
    root: Path | str,
    dataset: Path | str | None = None,
    *,
    datasets: Mapping[str, str] | Sequence[Any] | None = None,
    axes: Mapping[str, Sequence[Any]] | None = None,
    combos: Sequence[Mapping[str, Any]] | None = None,
    name: str = "",
    params: dict[str, Any] | None = None,
    phase_route: str | None = None,
    peaks: Path | str | None = None,
    direct_range: Any = None,
    ext_lo: Any = None,
    ext_hi: Any = None,
    sigma_multiplier: float | None = None,
    max_peaks: int = 0,
    max_runs: int = DEFAULT_MAX_RUNS,
    window_pts: int | None = None,          # legacy (unused in combination mode)
    window_ppm: float | None = None,        # legacy (unused in combination mode)
    sign: str = "abs",
    roi_f1_ppm: float | None = None,
    roi_f2_ppm: float | None = None,
    localization: Any = "parabolic",        # combination-mode refinement (incl. both)
    localize_peaks: Any = None,              # combination-mode target peaks (CSV/ids)
    edge_margin_ppm: float | None = None,   # combination picking margin (physical)
    localization_method: str = "parabolic",  # reference peak-position method
    gaussian_roi_f1_ppm: float | None = None,
    gaussian_roi_f2_ppm: float | None = None,
    force: bool = False,
    resume: bool = True,
    backend: Any | None = None,
    write: bool = True,
    progress: Callable[[str], None] | None = None,
) -> StudyResult:
    """Convenience one-call entry: **reference mode + combination mode**.

    The two modes split on 2026-09-14: :func:`run_reference_study` builds the reference and
    :func:`run_combination_study` runs it with an **explicit reference**. This stays as the
    one-call entry and for compatibility: it runs reference mode, then combination mode.
    The direct range (``direct_range=`` / ``ext_lo`` / ``ext_hi``) acts at the reference level;
    localization / localize_peaks / edge_margin_ppm go to combination mode (parabolic
    default / gaussian / both; the margin defaults to 3x the linewidth; localize_peaks
    refines only the named peaks).

    Parameters
    ----------
    root, dataset, datasets, name, params, phase_route, peaks, sigma_multiplier,
    max_peaks, localization_method, gaussian_roi_f1_ppm, gaussian_roi_f2_ppm, force
        the same as :func:`run_reference_study` (reference-mode part).
    axes, combos, max_runs
        the same as :func:`run_combination_study` (combination-mode part).
    direct_range, ext_lo, ext_hi, window_pts, window_ppm, sign, roi_f1_ppm,
    roi_f2_ppm, localization, localize_peaks, edge_margin_ppm, resume, backend, write,
    progress
        shared; ``direct_range`` defines the reference then overrides the base.

    Returns
    -------
    StudyResult
        the same handle as :func:`run_combination_study` (reference plus every run).

    Raises
    ------
    DatasetError, ReferenceError, SweepError
        re-raised from the inner modes (bad data, unusable reference, invalid combinations).

    Side effects
    ------------
    Equivalent to running reference mode then combination mode: writes ``study/reference/``,
    ``study/workflows/`` and the summary records; never replaces the active spectrum.

    Examples
    --------
        Run reference and combinations in one call (convenience entry)::

            result = run_parameter_study(
                "study/", "path/to/bruker", axes={"zero_fill": [1, 2]}
            )
    """
    reference_result = run_reference_study(
        root,
        dataset,
        datasets=datasets,
        name=name,
        params=params,
        phase_route=phase_route,
        peaks=peaks,
        direct_range=direct_range,
        ext_lo=ext_lo,
        ext_hi=ext_hi,
        sigma_multiplier=sigma_multiplier,
        max_peaks=max_peaks,
        localization_method=localization_method,
        gaussian_roi_f1_ppm=gaussian_roi_f1_ppm,
        gaussian_roi_f2_ppm=gaussian_roi_f2_ppm,
        force=force,
        backend=backend,
        write=write,
        progress=progress,
    )
    # combination mode gets an explicit reference (the root) and runs every condition
    spec = str(reference_result.session.root)
    result = run_combination_study(
        spec,
        combos=combos,
        axes=axes,
        max_runs=max_runs,
        localization=localization,
        localize_peaks=localize_peaks,
        edge_margin_ppm=edge_margin_ppm,
        roi_f1_ppm=roi_f1_ppm,
        roi_f2_ppm=roi_f2_ppm,
        resume=resume,
        backend=backend,
        write=write,
        progress=progress,
    )
    result.references = reference_result.references
    merged = dict(reference_result.records)
    merged.update(result.records)
    result.records = merged
    return result

def _summary(
    plan: SweepPlan,
    runs: Sequence[SweepRun],
    references: Mapping[str, ReferenceSpectrum],
) -> dict[str, Any]:
    """Study summary: conditions, workflow count, per-workflow status, reference tables.

    It counts **execution results** only (status, peak counts), never a statistical quantity.
    """
    counts = workflow_summary(runs)
    per_workflow: dict[str, dict[str, Any]] = {}
    for run in runs:
        entry = per_workflow.setdefault(
            run.workflow_id,
            {
                "workflow_id": run.workflow_id,
                "parameters_requested": run.parameters_requested,
                "conditions": {},
            },
        )
        entry["conditions"][run.condition or run.dataset.get("key", "")] = {
            "status": run.status,
            "warnings": [w.get("code") for w in run.warnings],
            "peak_table_parabolic": run.peak_table_path("parabolic"),
            "peak_table_gaussian": run.peak_table_path("gaussian"),
            "log_path": run.log_path,
        }
    return {
        "conditions": [ref.condition for ref in references.values()],
        "n_conditions": len(references),
        "n_workflows": plan.n_workflows,
        "workflow_ids": plan.workflow_ids(),
        "design": plan.design,
        "grid_sha256": plan.grid_sha256,
        "status_counts": counts,
        "per_workflow": per_workflow,
        "references": {
            key: {
                "condition": ref.condition,
                "peak_count": ref.peak_count,
                "peak_source": ref.peak_source,
                "sigma_multiplier": (ref.peak_params or {}).get(
                    "sigma_multiplier"
                ),
                "peak_tables": ref.peak_tables,
                "script_sha256": ref.script_sha256,
                "spectrum_sha256": ref.spectrum_sha256,
            }
            for key, ref in references.items()
        },
        "boundary": tr("processing and records only; CSP/robustness/statistics are downstream"),
    }


def reference_peaks(
    session: StudySession,
    reference: ReferenceSpectrum | DatasetRef | None = None,
) -> list[dict[str, Any]]:
    """Read a reference peak table for records and review; an empty table on failure.

    ``reference`` may be a spectrum object, a condition dataset (DatasetRef)
    or empty (the session's primary condition).
    """
    ref = (
        reference
        if isinstance(reference, ReferenceSpectrum)
        else load_reference(session, reference)
    )
    if ref is None or not ref.peak_table_path:
        return []
    try:
        from core.peaks.peak_table import load_peaks

        return load_peaks(Path(ref.peak_table_path))
    except Exception:  # noqa: BLE001 - recording only; a failure must not block
        return []


def copy_peak_table(source: Path | str, target: Path | str) -> Path:
    """Copy an external peak table into the study (a stable path for the record)."""
    src = Path(source)
    dst = Path(target)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return dst


__all__ = [
    "STATUS_FAILED",
    "STATUS_SUCCESS",
    "STATUS_WARNING",
    "DatasetRef",
    "ReferenceResult",
    "StudyResult",
    "copy_peak_table",
    "load_references",
    "reference_peaks",
    "run_combination_study",
    "run_parameter_study",
    "run_reference_study",
]
