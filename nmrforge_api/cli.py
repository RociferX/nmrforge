"""Command-line entry point: ``python -m nmrforge_api <command>`` (no GUI, cluster friendly).

Commands::

    init         create a study and import datasets (use --condition A / B for more)
    reference    run automatic optimisation, freeze the reference spectrum and script
                 (one per condition)
    peaks        reference peak table: pick peaks or register an external table, and write
                 both reference peak tables
    sweep        run workflows from a parameter table (alias: workflows)
    report       recompute the summary from run.json/workflow.json (no reprocessing)
    status       print the current study status (conditions/reference/workflows)
    compat       behaviour compatibility manifest (behaviour fingerprint + change level;
                 --golden runs the golden vector as self-proof)

Parameter combinations (the ``axes`` shortcut in YAML/JSON, or an explicit CSV/YAML
combination table)::

    axes:
      zero_fill: [1, 2, 4]
      "window.F1.off": [0.35, 0.45, 0.55]
    max_runs: 128
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any

from core.user_errors import describe_exception
from nmrforge_api.compat import compat_manifest, write_compat_manifest
from nmrforge_api.direct_range import parse_direct_range
from nmrforge_api.errors import DatasetError, SensitivityError
from nmrforge_api.records import (
    refresh_reference_records,
    write_records,
    write_reference_records,
)
from nmrforge_api.reference import (
    build_reference,
    ensure_reference_peaks,
    load_reference,
    load_references,
    parse_reference_spec,
    set_reference_peaks,
)
from nmrforge_api.session import add_dataset, open_study
from nmrforge_api.study import (
    reference_peaks,
    run_combination_study,
)
from nmrforge_api.sweep import (
    DEFAULT_MAX_RUNS,
    load_combo_table,
    load_plan,
    load_runs,
    load_workflows,
    workflow_summary,
)
from ui_support.i18n import tr

LOCALIZATION_CHOICES: tuple[str, ...] = ("parabolic",)


#: When set to any non-empty value, print the full traceback to help troubleshooting; same
#: as every subcommand's --debug (Phase 21)
DEBUG_ENV = "NMRFORGE_DEBUG"


def _debug_enabled(args: argparse.Namespace | None = None) -> bool:
    """Print the full traceback when --debug or NMRFORGE_DEBUG=1 is set.

    By default the user only sees the actionable hint.
    """
    if getattr(args, "debug", False):
        return True
    return bool(str(os.environ.get(DEBUG_ENV, "")).strip())


def _report_unexpected(exc: BaseException, *, debug: bool) -> int:
    """User-visible exit for an unexpected exception: one actionable line plus the full
    traceback on the debug channel.
    """
    print(tr("Error: {p0}", p0=describe_exception(exc)), file=sys.stderr)
    if debug:
        print(tr("Full traceback (debug):"), file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
    else:
        print(
            tr("Hint: pass --debug or set {p0}=1 to print the full traceback.", p0=DEBUG_ENV),
            file=sys.stderr,
        )
    return 2


def _load_mapping(path: Path | str) -> dict[str, Any]:
    import yaml

    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise SensitivityError(tr("Cannot read file: {p0} ({p1})", p0=source, p1=exc)) from exc
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise SensitivityError(tr("File is not a key/value table (YAML/JSON): {p0}", p0=source))
    return data


def _load_condition_params(path: Path | str) -> dict[str, dict[str, Any]]:
    source = Path(path)
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SensitivityError(tr("Cannot read file: {p0} ({p1})", p0=source, p1=exc)) from exc
    except json.JSONDecodeError as exc:
        raise SensitivityError(
            tr("File is not valid JSON: {p0} ({p1})", p0=source, p1=exc)
        ) from exc
    if not isinstance(data, dict) or any(
        not isinstance(label, str) or not isinstance(params, dict)
        for label, params in data.items()
    ):
        raise SensitivityError(
            tr(
                "Condition parameters must map condition labels to parameter objects: {p0}",
                p0=source,
            )
        )
    return data


def _print(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _selected_datasets(session: Any, condition: str) -> list[Any]:
    """--condition filter (empty = all conditions)."""
    if not condition:
        return list(session.datasets)
    ref = session.dataset_by_condition(condition)
    if ref is None:
        raise SensitivityError(tr("no condition {p0!r} in this study", p0=condition))
    return [ref]


def _parse_carrier_ppm(values: list[str] | None) -> dict[str, float]:
    """Parse repeatable ``AXIS=PPM`` CLI values; reject duplicates before mapping."""
    parsed: dict[str, float] = {}
    for value in values or []:
        if not isinstance(value, str) or value.count("=") != 1:
            raise SensitivityError(
                tr("carrier override must use AXIS=PPM: {p0}", p0=value)
            )
        axis, token = (part.strip() for part in value.split("=", 1))
        if axis in parsed:
            raise SensitivityError(tr("carrier axis was specified more than once: {p0}", p0=axis))
        if axis not in {"F1", "F2", "F3"} or not token:
            raise SensitivityError(tr("invalid carrier override: {p0}", p0=value))
        try:
            parsed[axis] = float(token)
        except ValueError as exc:
            raise SensitivityError(tr("invalid carrier ppm value: {p0}", p0=value)) from exc
    return parsed


def cmd_init(args: argparse.Namespace) -> int:
    datasets = args.dataset or []
    segmented = bool(getattr(args, "segmented", False))
    if segmented and len(datasets) < 2:
        raise DatasetError(tr("segmented import requires at least two dataset directories"))
    if not segmented and len(datasets) > 1:
        raise DatasetError(
            tr("multiple dataset directories require --segmented")
        )

    session = open_study(args.study, name=args.name)
    if datasets:
        dataset = add_dataset(
            session,
            datasets if segmented else datasets[0],
            condition=args.condition,
            title=args.name or args.title,
            **({"segmented": True} if segmented else {}),
        )
        _print(
            {
                "study": str(session.root),
                "condition": dataset.condition,
                "dataset": dataset.to_dict(),
            }
        )
    else:
        _print(
            {
                "study": str(session.root),
                "datasets": [ref.to_dict() for ref in session.datasets],
            }
        )
    return 0


def cmd_reference(args: argparse.Namespace) -> int:
    session = open_study(args.study, name=args.name)
    params = _load_mapping(args.params) if args.params else None
    condition_params = (
        _load_condition_params(args.condition_params)
        if getattr(args, "condition_params", None)
        else {}
    )
    unknown_conditions = set(condition_params) - {ref.condition for ref in session.datasets}
    if unknown_conditions:
        raise SensitivityError(
            tr(
                "condition parameters name unknown condition(s): {p0}",
                p0=", ".join(sorted(unknown_conditions)),
            )
        )
    carrier_values = getattr(args, "carrier_ppm", None)
    if getattr(args, "rebuild_peak_tables", False):
        if carrier_values:
            raise SensitivityError(
                tr("--carrier-ppm cannot be combined with --rebuild-peak-tables")
            )
        if args.force or getattr(args, "direct_range", None):
            raise SensitivityError(
                tr("--rebuild-peak-tables cannot be combined with --force / --direct-range")
            )
        from nmrforge_api.reference import rebuild_reference_peak_tables

        rebuilt: list[dict[str, Any]] = []
        for target in _selected_datasets(session, args.condition):
            reference = rebuild_reference_peak_tables(session, load_reference(session, target))
            rebuilt.append(
                {
                    "condition": reference.condition,
                    "dataset": reference.dataset_key,
                    "spectrum": reference.frozen_spectrum,
                    "spectrum_sha256": reference.spectrum_sha256,
                    "peak_table": reference.peak_table_path,
                    "peak_table_sha256": reference.peak_table_sha256,
                    "peak_count": reference.peak_count,
                    "peak_tables": reference.peak_tables,
                    "exclusive_windows": reference.peak_localization.get("exclusive_windows"),
                }
            )
        _print(rebuilt if len(rebuilt) > 1 else rebuilt[0])
        return 0

    direct = parse_direct_range(getattr(args, "direct_range", None), params=params)
    if direct is not None:
        params = dict(params or {})
        params.update(direct.params())
    carrier_cli = _parse_carrier_ppm(carrier_values)
    out: list[dict[str, Any]] = []
    from core.data.carrier import merge_carrier_params, normalize_carrier_ppm
    from nmrforge_api.processing_audit import merge_condition_params
    from nmrforge_api.reference import _reference_request, validate_reference_input

    targets = _selected_datasets(session, args.condition)
    base_params = merge_condition_params({}, params or {})
    for overrides in condition_params.values():
        try:
            merge_carrier_params(merge_condition_params({}, overrides))
        except ValueError as exc:
            raise SensitivityError(str(exc)) from exc
    if carrier_cli or "carrier_ppm" in base_params:
        try:
            base_params = merge_carrier_params(
                base_params, carrier_cli if carrier_cli else None
            )
        except ValueError as exc:
            raise SensitivityError(str(exc)) from exc
    prepared = {}
    for target in targets:
        target_params = merge_condition_params(
            base_params, condition_params.get(target.condition, {})
        )
        if carrier_cli or "carrier_ppm" in target_params:
            from workflow.stepwise import read_experiment

            try:
                experiment = read_experiment(
                    session.manager, target.exp_id, target.data_id
                )
                available_axes = {dimension.logical_axis for dimension in experiment.dimensions}
                target_params = merge_carrier_params(target_params)
                target_params["carrier_ppm"] = normalize_carrier_ppm(
                    target_params.get("carrier_ppm"), axes=available_axes
                )
            except ValueError as exc:
                raise SensitivityError(str(exc)) from exc
        _reference_request(target_params, direct, args.phase_route)
        existing = None if args.force else load_reference(session, target)
        if existing is not None:
            validate_reference_input(existing, target_params, direct_range=direct,
                                     phase_route=args.phase_route)
        prepared[target.key] = target_params
    for target in targets:
        reference = build_reference(
            session,
            target,
            params=prepared[target.key] or None,
            direct_range=direct,
            phase_route=args.phase_route,
            progress=lambda message: print(message, file=sys.stderr),
            force=bool(args.force),
        )
        out.append(
            {
                "condition": reference.condition,
                "dataset": reference.dataset_key,
                "spectrum": reference.frozen_spectrum,
                "script": reference.script_path,
                "script_sha256": reference.script_sha256,
                "phase_route": reference.phase_route,
                "phase": reference.phase_record(),
                "sampling": reference.sampling,
                # 2026-09-25 (user): FT sign/direction frozen with the reference (manual -neg)
                "sampling_flags": reference.sampling_flags,
                "workflow_supported": reference.sweep_supported,
            }
        )
    _print(out if len(out) > 1 else out[0])
    return 0


def cmd_peaks(args: argparse.Namespace) -> int:
    session = open_study(args.study, name=args.name)
    out: list[dict[str, Any]] = []
    for target in _selected_datasets(session, args.condition):
        reference = load_reference(session, target)
        if reference is None:
            raise SensitivityError(
                tr(
                    "condition {p0!r} has no reference spectrum yet",
                    p0=target.condition,
                )
            )
        if args.peak_table:
            # Optional: an external peak table (public database or already assigned)
            path = session.reference_dir_for(target) / "reference.list"
            path.write_text(
                Path(args.peak_table).read_text(encoding="utf-8-sig"),
                encoding="utf-8",
            )
            reference = set_reference_peaks(session, path, reference, source="external")
            from nmrforge_api.reference import build_reference_peak_tables

            reference = build_reference_peak_tables(session, reference)
        else:
            reference = ensure_reference_peaks(
                session,
                reference,
                sigma_multiplier=args.sigma,
                max_peaks=args.max_peaks,
                force=args.force,
            )
        peaks = reference_peaks(session, reference)
        out.append(
            {
                "condition": reference.condition,
                "dataset": reference.dataset_key,
                "peak_list": reference.peak_table_path,
                "sha256": reference.peak_table_sha256,
                "source": reference.peak_source,
                "count": len(peaks) or reference.peak_count,
                "peak_tables": reference.peak_tables,
                "localization": reference.peak_localization,
                "params": reference.peak_params,
            }
        )
    # Reference-mode artefacts: spectrum/script/both tables/sampling/threshold -> reference.json
    references = load_references(session)
    records = write_reference_records(session, references)
    primary = session.dataset
    if primary is not None and primary.key in references:
        session.save_state(reference=references[primary.key].to_dict())
    session.manager.save()
    _print(
        {
            "mode": "reference",
            "conditions": out,
            "records": records,
        }
    )
    return 0


def _targets_spec_from_args(args: argparse.Namespace) -> Any:
    """CLI ``--localize-peaks[-METHOD]`` -> target spec (per-method keys win)."""
    return getattr(args, "localize_peaks", None)


def cmd_sweep(args: argparse.Namespace) -> int:
    """**Combination mode**: an explicit reference plus a parameter table (builds no reference)."""
    if (args.grid is None) == (args.combos is None):
        raise SensitivityError(
            tr(
                "Give exactly one of: --grid (candidate values per axis, full factorial) or "
                "--combos (an external combination table: orthogonal / fractional factorial / LHS "
                "...)",
            )
        )
    targets_spec = _targets_spec_from_args(args)
    spec = str(args.reference)
    from_root = Path(args.study).expanduser().resolve()
    reference_handle = parse_reference_spec(spec)
    reference_root = Path(reference_handle.root).expanduser().resolve()
    if from_root != reference_root:
        raise SensitivityError(
            tr(
                "--study root must match --reference root; cross-study sweeps are not supported "
                "({p0} != {p1})",
                p0=from_root,
                p1=reference_root,
            )
        )
    if args.condition and "#" not in spec and not spec.endswith("reference.json"):
        spec = f"{spec}#{args.condition}"
    if args.combos:
        combos = load_combo_table(args.combos)
        result = run_combination_study(
            spec,
            combos=combos,
            direct_range=getattr(args, "direct_range", None),
            allow_ext_override=bool(args.allow_ext_override),
            max_runs=int(args.max_runs or DEFAULT_MAX_RUNS),
            localize_peaks=targets_spec,
            edge_margin_ppm=args.edge_margin_ppm,
            resume=not args.no_resume,
            progress=lambda message: print(message, file=sys.stderr),
        )
    else:
        config = _load_mapping(args.grid)
        axes = config.get("axes")
        if not isinstance(axes, dict) or not axes:
            raise SensitivityError(tr("grid file has no axes: {p0}", p0=args.grid))
        max_runs = int(config.get("max_runs", args.max_runs or DEFAULT_MAX_RUNS))
        result = run_combination_study(
            spec,
            axes=axes,
            direct_range=getattr(args, "direct_range", None),
            allow_ext_override=bool(args.allow_ext_override),
            max_runs=max_runs,
            localize_peaks=targets_spec,
            edge_margin_ppm=args.edge_margin_ppm,
            resume=not args.no_resume,
            progress=lambda message: print(message, file=sys.stderr),
        )
    _print(
        {
            "mode": "combination",
            "reference": result.summary.get("reference_spec", spec),
            "workflows": result.plan.n_workflows,
            "conditions": [ref.condition for ref in result.references.values()],
            "status": result.summary.get("status_counts", {}),
            "records": result.records,
        }
    )
    return 0


def cmd_compat(args: argparse.Namespace) -> int:
    """Print the behaviour manifest (optionally running the golden vector)."""
    manifest = compat_manifest()
    if args.golden:
        from nmrforge_api.conformance import check_conformance

        manifest["golden_check"] = check_conformance(workdir=args.workdir or None)
    if args.out:
        manifest["manifest_path"] = str(write_compat_manifest(args.out))
    _print(manifest)
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    session = open_study(args.study, name=args.name)
    plan = load_plan(session)
    if plan is None:
        raise SensitivityError(tr("no workflow plan found (records/sweep_plan.json)"))
    references = load_references(session)
    runs = load_runs(session)
    records = write_records(
        session,
        references=references,
        plan=plan,
        runs=runs,
        peaks=reference_peaks(session, session.dataset),
    )
    # 2026-09-19: rebuilding the records through `report` refreshes the study-level
    # reference aggregate too - it used to be left alone, so an upgraded peak table kept
    # showing the old SHA and version in records/reference.json.
    if references:
        records.update(refresh_reference_records(session, references))
    _print(
        {
            "workflows": len(load_workflows(session)),
            "status": workflow_summary(runs),
            "records": records,
        }
    )
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    session = open_study(args.study, name=args.name)
    references = load_references(session)
    plan = load_plan(session)
    runs = load_runs(session)
    workflows = load_workflows(session)
    _print(
        {
            "study": str(session.root),
            "datasets": [ref.to_dict() for ref in session.datasets],
            "references": {
                key: {
                    "condition": ref.condition,
                    "script_sha256": ref.script_sha256,
                    "spectrum_sha256": ref.spectrum_sha256,
                    "peak_count": ref.peak_count,
                    "peak_source": ref.peak_source,
                    "peak_tables": ref.peak_tables,
                }
                for key, ref in references.items()
            },
            "workflows": {
                "planned": plan.n_workflows if plan else 0,
                "recorded": len(workflows),
                "ids": plan.workflow_ids() if plan else [],
            },
            "runs": workflow_summary(runs),
            "records_dir": str(session.records_dir),
        }
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m nmrforge_api",
        description=tr("NMRForge parameter-combination runner (command line, no GUI)"),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    parser.add_argument(
        "--debug",
        action="store_true",
        help=tr(
            "print the full traceback on error (same as NMRFORGE_DEBUG=1)",
        ),
    )

    def _common(handler: argparse.ArgumentParser) -> None:
        handler.add_argument("--study", required=True, help=tr("study root directory"))
        handler.add_argument(
            "--debug",
            action="store_true",
            help=tr(
                "print the full traceback on error (same as NMRFORGE_DEBUG=1)",
            ),
        )
        handler.add_argument("--name", default="", help=tr("project name when creating a study"))
        handler.add_argument(
            "--condition",
            default="",
            help=tr("condition label (A/B/...); omit for all conditions"),
        )

    init = sub.add_parser("init", help=tr("create a study and import a Bruker dataset"))
    _common(init)
    init.add_argument(
        "--dataset",
        action="append",
        default=[],
        help=tr(
            "Bruker directory downloaded and unpacked from the public "
            "archive (repeatable)",
        ),
    )
    init.add_argument(
        "--segmented",
        action="store_true",
        help=tr("import the ordered dataset directories as one segmented acquisition"),
    )
    init.add_argument("--title", default="", help=tr("experiment title"))
    init.set_defaults(func=cmd_init)

    reference = sub.add_parser(
        "reference",
        help=tr(
            "build and freeze the reference spectrum and script",
        ),
    )
    _common(reference)
    reference.add_argument(
        "--params",
        help=tr(
            "input parameters for automatic processing (YAML/JSON)",
        ),
    )
    reference.add_argument(
        "--condition-params",
        help=tr("JSON mapping condition labels to per-condition processing parameter objects"),
    )
    reference.add_argument(
        "--carrier-ppm",
        action="append",
        default=None,
        metavar="AXIS=PPM",
        help=tr("explicit carrier position in ppm (repeatable for F1/F2/F3)"),
    )
    reference.add_argument("--phase-route", default=None)
    reference.add_argument(
        "--direct-range",
        nargs=2,
        type=float,
        default=None,
        metavar=("HIGH_PPM", "LOW_PPM"),
        help=tr("direct-dimension range (ext_lo high, ext_hi low, ppm); changes require --force"),
    )
    reference.add_argument(
        "--force",
        action="store_true",
        help=tr(
            "rebuild the reference spectrum",
        ),
    )
    reference.add_argument(
        "--rebuild-peak-tables",
        action="store_true",
        help=tr("rebuild only the reference peak table (existing spectrum + reference.list)"),
    )
    reference.set_defaults(func=cmd_reference)

    peaks = sub.add_parser("peaks", help=tr("reference peak table: pick or register"))
    _common(peaks)
    peaks.add_argument(
        "--sigma",
        type=float,
        default=None,
        help=tr(
            "peak-picking threshold (multiples of sigma)",
        ),
    )
    peaks.add_argument(
        "--max-peaks", type=int, default=0, help=tr("keep only the N most intense peaks (0 = all)")
    )
    peaks.add_argument(
        "--force",
        action="store_true",
        help=tr(
            "discard the existing peak table and pick again",
        ),
    )
    peaks.add_argument(
        "--peak-table",
        default="",
        help=tr("optional external peak table (.list or peak_id,H_ppm,N_ppm CSV)"),
    )
    peaks.set_defaults(func=cmd_peaks)

    sweep = sub.add_parser(
        "sweep",
        aliases=["workflows"],
        help=tr("run workflows from a parameter-combination table"),
    )
    _common(sweep)
    sweep.add_argument(
        "--grid",
        default=None,
        help=tr("axis grid YAML/JSON (candidate values per axis, full factorial)"),
    )
    sweep.add_argument(
        "--reference",
        required=True,
        help=tr(
            "combination mode requires an explicit reference: <study root> or "
            "<root>#<condition>,or a reference.json path (produced by reference mode: reference + "
            "peaks)",
        ),
    )
    sweep.add_argument(
        "--combos",
        default=None,
        help=tr("explicit combination table CSV/TSV/YAML/JSON (external design)"),
    )
    sweep.add_argument(
        "--direct-range",
        nargs=2,
        type=float,
        default=None,
        metavar=("HIGH_PPM", "LOW_PPM"),
        help=tr("direct-dimension range (ext_lo high, ext_hi low, ppm): overrides the batch base"),
    )
    sweep.add_argument(
        "--allow-ext-override",
        action="store_true",
        help=tr(
            "allow this batch's --direct-range to disagree with the reference's frozen range "
            "(leaving the run-level warning code direct_range_override); by default a disagreement "
            "is an error rather than a silent window "
            "change",
        ),
    )
    sweep.add_argument("--max-runs", type=int, default=0)
    sweep.add_argument(
        "--localize-peaks",
        default=None,
        metavar="CSV",
        help=tr(
            "targeted localisation: refine only the peaks listed in CSV (it needs a peak_id "
            "column). Detection, row count and peak_id numbering are unchanged and unlisted peaks "
            "are kept (position from the detection-stage parabola); default = the whole spectrum. "
            "The CSV may carry a condition column (multi-condition studies: each condition reads "
            "only its own rows, a missing row is an error), so one file can serve A and B; for one "
            "file per condition use the combination table key "
            "localization.targets",
        ),
    )
    sweep.add_argument(
        "--edge-margin-ppm",
        type=float,
        default=None,
        help=tr(
            "manual edge exclusion width (ppm); default uses acquisition and edge-peak evidence"
        ),
    )
    sweep.add_argument(
        "--no-resume",
        action="store_true",
        help=tr(
            "do not skip completed workflows",
        ),
    )
    sweep.set_defaults(func=cmd_sweep)

    report = sub.add_parser(
        "report",
        help=tr(
            "recompute the summary from existing records (no reprocessing)",
        ),
    )
    _common(report)
    report.set_defaults(func=cmd_report)

    status = sub.add_parser("status", help=tr("print the current study status"))
    _common(status)
    status.set_defaults(func=cmd_status)

    compat = sub.add_parser(
        "compat",
        help=tr(
            "behaviour compatibility manifest: fingerprint + change level (needs no study root)",
        ),
    )
    compat.add_argument("--out", default=None, help=tr("write the manifest to a JSON file"))
    compat.add_argument(
        "--golden",
        action="store_true",
        help=tr(
            "also run the golden vector (a tiny synthetic spectrum) and compare it with the "
            "declared "
            "hashes",
        ),
    )
    compat.add_argument(
        "--workdir",
        default=None,
        help=tr(
            "directory for the golden vector artefacts (default: a temporary directory, discarded "
            "afterwards)",
        ),
    )
    compat.set_defaults(func=cmd_compat)
    return parser


def main(argv: list[str] | None = None) -> int:
    from core.logging_setup import configure_logging

    # Phase 22: logs go to stderr (NMRFORGE_LOG_LEVEL, WARNING); stdout stays pure JSON
    configure_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except SensitivityError as exc:
        # Known API errors: the message is written for the user and carries the fix.
        print(tr("Error: {p0}", p0=exc), file=sys.stderr)
        if _debug_enabled(args):
            print(tr("Full traceback (debug):"), file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print(
            tr("Cancelled (interrupted); unfinished workflows are not recorded as successful."),
            file=sys.stderr,
        )
        return 130
    except (OSError, ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
        # Unexpected exceptions also get one actionable line; the traceback goes to debug.
        return _report_unexpected(exc, debug=_debug_enabled(args))


__all__ = ["build_parser", "main"]
