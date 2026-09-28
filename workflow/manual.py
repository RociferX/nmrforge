"""Manual processing path backend (user feedback: view / edit / run the script).

Mirrors the automatic path and gathers the manual command-line processing that came before:
- Generate FID: the automatic stage produces fid.com first (backend.convert_to_fid) -> the user
  views it (manual_fid_com) -> edits it -> runs it (run_manual_fid_com, the manual parameters are
  handed to the backend as overrides) -> fid registered;
- Generate spectrum: edit the script (manual_scripts renders it, an existing script in process/
  is shown first -> edit) -> run it (process.com / nus*.com, run_manual_spectrum) -> the final
  spectrum is put back into spectra/ and registered.

Running reuses backend.runtime.CshRuntime; the products are registered through set_data_fid /
set_data_spectrum + WorkflowRun (audit).
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from backend.bruker_workflow import parse_fid_com
from backend.runtime import CshRuntime
from backend.script_generator import render_scripts
from core.data.internal_data_model import Experiment, SamplingMode
from core.project import ProjectManager
from ui_support.i18n import tr
from workflow.stepwise import _read_experiment, _register_spectrum


class ManualRunError(Exception):
    """Manual processing run error (missing script / failed run / missing product)."""


def _resolve_raw_dir(manager: ProjectManager, data_entry: Any) -> Path:
    raw = Path(data_entry.raw_dir) if data_entry.raw_dir else Path(data_entry.source)
    if not raw.is_absolute():
        raw = manager.root / raw
    return raw


def _work_dir(manager: ProjectManager, exp_id: str, data_id: str) -> Path:
    return manager.data_dir(exp_id, data_id, "process")


def _fid_ready(candidate: Path | None) -> bool:
    """A single file or a slice directory (either naming) already counts as converted."""
    if candidate is None:
        return False
    if candidate.is_file():
        return True
    if candidate.is_dir() and list(candidate.glob("*.fid")):
        return True
    return False


def _located_fids(work: Path, experiment: Experiment) -> list[Path]:
    """Where the converted fid actually landed (single file / slice stream) -- the same decision
    the automatic path makes.

    The merged product of multi-part data lands in ``merged/fid/...`` or
    ``merged/{dataset_id}.fid``, unlike the ``{dataset_id}.fid`` / ``fid/...`` of a single
    dataset; the manual path must ask the same implementation
    (``workflow.direct_diagnostics.collect_fid_paths``), otherwise it reports "fid not found"
    even though the conversion succeeded (user report, 2026-09-24).
    """
    from workflow.direct_diagnostics import collect_fid_paths

    return collect_fid_paths(work, experiment)


def _converted_fid_present(
    work: Path, data_entry: Any, experiment: Experiment
) -> bool:
    """True when converted: the registered ``fid_path`` (single file or slice directory), or
    located by where it lands.

    2026-09-24 (user): "the fid of a multi-part dataset sits somewhere else, so the manual run
    failed with fid not found" -- previously only the registered path and
    ``work/{dataset_id}.fid`` were checked.
    """
    registered = str(getattr(data_entry, "fid_path", "") or "")
    if registered and _fid_ready(Path(registered)):
        return True
    return bool(_located_fids(work, experiment))


def _fid_in_argument(
    work: Path, located: list[Path], dataset_id: str
) -> str | None:
    """Located fid -> the relative path the script's ``-in`` has to carry (single file or slice
    wildcard).

    The rendered script writes ``-in {dataset_id}.fid`` by default, but the real landing spot of a
    segmented/sliced product differs (``merged/{dataset_id}.fid``, ``merged/fid/test%03d.fid``,
    ``fid/test%03d.fid``), so the manual run fails unless it is rewritten. Returns None when
    nothing can be located (the rendered value is kept).
    """
    if not located:
        return None
    first = located[0]
    try:
        rel = first.relative_to(work)
    except ValueError:
        return None
    parent = rel.parent.as_posix()
    prefix = "" if parent == "." else parent + "/"
    if len(located) == 1 and first.is_file():
        return prefix + first.name
    for stem in (dataset_id, "test"):
        if first.name.startswith(stem) and first.name.endswith(".fid"):
            return f"{prefix}{stem}%03d.fid"
    return None


def _reference_fid_com_path(
    work: Path, raw_dir: Path, segments: list[Any]
) -> Path | None:
    """The **reference baseline** fid.com for a manual script (``bruker -AUTO`` plus the backend
    patches, untouched).

    During conversion the backend keeps the script without manual overrides as ``fid.com.auto``
    (see ``NMRPipeBackend._convert_dir``); a manual edit has to carry "only the parameters that
    were really changed" to every segment, which requires this baseline for comparison -- without
    it a second run takes the previous manual parameters as the baseline and silently drops them.
    When the baseline is absent (old work directory), fall back to the current ``fid.com``, then
    to raw.
    """
    # 2026-09-24 review B8: the baseline **must** be "the one without manual overrides" --
    # fid.com.auto first, then the original raw/fid.com; never work/seg_001/fid.com (that one is
    # for humans and already carries the previous manual values), otherwise the second manual run
    # diffs to nothing and the previous manual parameters are silently dropped. When neither is
    # available, return None (the caller falls back to "the keys of the whole script", i.e. the
    # old behaviour).
    candidates: list[Path] = []
    if segments:
        candidates.append(work / "seg_001" / "fid.com.auto")
    candidates += [work / "fid.com.auto", raw_dir / "fid.com"]
    for path in candidates:
        if path.is_file():
            return path
    return None


def _changed_fid_com_params(edited: str, reference: str | None) -> dict[str, str]:
    """Manual script vs automatic baseline -> only "the parameters a human really changed" (the
    same set is applied to every segment).

    2026-09-24 (user): editing the script manually means specifying some fid.com parameters by
    hand, while conversion / slicing / merging still go through the full automatic flow. Keys with
    the same value in the baseline are filtered out, so a segment's own acquisition parameters
    (such as its own -yN) are not overwritten by the reference segment's values. When the baseline
    is unavailable it falls back to "the keys of the whole script" (the old behaviour).
    """
    changed = parse_fid_com(edited)
    if reference is None:
        return changed
    base = parse_fid_com(reference)
    return {key: value for key, value in changed.items() if base.get(key) != value}


def _run_quality_check(
    manager: ProjectManager,
    exp_id: str,
    data_entry: Any,
    work: Path,
    data_id: str,
) -> None:
    """Quality diagnosis before a manual spectrum "run" (the optimisation is not re-run).

    The result is written to process/manual_quality.log; the diagnosis also repairs bad points (a
    backup is kept), matching the quality inspection at the start of the automatic
    "Generate spectrum" path (0.2.163-patch13). 0.2.193: running it was moved from opening the
    script editor to clicking "Run" (run_manual_spectrum), so opening the editor no longer stalls.
    """
    try:
        experiment = _read_experiment(manager, exp_id, data_id)
    except Exception:  # noqa: BLE001 - no experiment means no diagnosis; do not block editing
        return
    # 2026-09-24 (user): a multi-part fid lands elsewhere than a single dataset's, so decide by
    # the located paths uniformly
    if not _converted_fid_present(work, data_entry, experiment):
        return  # diagnosis is pointless while the fid is not in place; do not block editing
    try:
        from workflow.direct_diagnostics import load_or_run_direct_diagnostics

        # 2026-09-23: reuse the conclusion persisted by the "Generate FID" step first; run the
        # check for real only for old work directories / manual paths without a record (the same
        # criterion as the automatic path)
        result = load_or_run_direct_diagnostics(work, experiment)
        lines = [tr(
            "== Quality inspection (manual spectrum preparation, multiplexing automatic "
            "optimisation final script) "
            "==",
        )]
        lines += [f"{i + 1}. {report}" for i, report in enumerate(result.reports)]
        lines.append(tr("index: {p0}", p0=result.metrics))
        (work / "manual_quality.log").write_text(
            (chr(10).join(lines) + chr(10)), encoding="utf-8"
        )
    except Exception as exc:  # noqa: BLE001 - a failed quality check must not block editing
        try:
            (work / "manual_quality.log").write_text(
                tr("Quality check failed: {p0}: {p1}", p0=type(exc).__name__, p1=exc) + chr(10),
                encoding="utf-8",
            )
        except OSError:
            pass


def _finish_run(
    manager: ProjectManager,
    exp_id: str,
    data_id: str,
    workflow_ref: str,
    outputs: dict[str, str],
    message: str,
) -> str:
    run = manager.start_run(
        exp_id,
        workflow_ref=workflow_ref,
        inputs={"data_id": data_id},
        params={"mode": "manual"},
    )
    manager.finish_run(run.run_id, "success", outputs=outputs, message=message)
    return run.run_id


def manual_fid_com(
    manager: ProjectManager,
    exp_id: str,
    data_id: str,
    backend: Any | None = None,
) -> str:
    """Get the already generated fid.com content (for manual viewing / editing); does not trigger
    an automatic conversion.

    0.2.199-patch29dm (user): the manual "Generate FID" button only appears after the automatic
    processing succeeded, so this reads the generated fid.com directly -- process/fid.com for a
    single dataset, and the reference segment process/seg_001/fid.com with an explanatory header
    for segmented data (after the parameters are edited, run_manual_fid_com hands them to the
    backend as overrides and it runs them uniformly). A missing fid.com is reported clearly
    instead of converting automatically (so a click does not only open it a while later).
    """
    data_entry = manager.data(exp_id, data_id)
    raw_dir = _resolve_raw_dir(manager, data_entry)
    work = _work_dir(manager, exp_id, data_id)
    segments = list(getattr(data_entry, "segments", None) or [])
    if segments:
        seg_fid = work / "seg_001" / "fid.com"
        if seg_fid.is_file():
            header = (
                tr(
                    "# Segmented acquisition: this is the reference-segment fid.com - edit "
                    "parameters only.\n# Running it applies the parameters you changed to EVERY "
                    "segment's fid.com and then\n# runs the full automatic conversion / slicing / "
                    "merging (the output name and the script\n# structure are still written by the "
                    "backend; changing them has no effect)\n",
                )
            )
            return header + seg_fid.read_text(
                encoding="utf-8", errors="replace"
            )
    fid_com = work / "fid.com"
    if fid_com.is_file():
        return fid_com.read_text(encoding="utf-8", errors="replace")
    legacy = raw_dir / "fid.com"
    if legacy.is_file():
        return legacy.read_text(encoding="utf-8", errors="replace")
    raise ManualRunError(
        tr(
            "fid.com does not exist, please automatically generate FID before opening the manual "
            "editor",
        )
    )


def run_manual_fid_com(
    manager: ProjectManager,
    exp_id: str,
    data_id: str,
    content: str,
    *,
    work_dir: Path | str | None = None,
    timeout: float = 900.0,
    backend: Any | None = None,
    progress: Callable[[str], None] | None = None,
) -> str:
    """Run a manual fid.com and register the fid.

    A single dataset behaves like segmented data (0.2.199-patch2): the manually changed parameters
    are extracted as overrides by parse_fid_com and the backend convert_to_fid runs them uniformly
    (bruker generation / parameter fixes / bad-point cleanup / slicing), so a human only tunes
    parameters while the conversion structure is guaranteed by the backend; a user script is no
    longer run through csh directly (structural changes are not kept, consistent with the
    segmented semantics). A single dataset's product lands in process/ (single file or slices), a
    segmented product in process/merged/fid.
    """
    data_entry = manager.data(exp_id, data_id)
    raw_dir = _resolve_raw_dir(manager, data_entry)
    work = Path(work_dir) if work_dir else _work_dir(manager, exp_id, data_id)
    work.mkdir(parents=True, exist_ok=True)
    experiment = _read_experiment(manager, exp_id, data_id)
    segments = list(getattr(data_entry, "segments", None) or [])
    if backend is None:
        raise ManualRunError(
            tr("a manual fid.com needs the backend to convert/merge, so run it from the interface")
        )
    # 2026-09-24 (user): editing the script manually means specifying some fid.com parameters by
    # hand -- only the parameters **changed** relative to the automatic baseline are handed to the
    # backend; the backend still runs the full automatic flow (per-segment conversion / slicing /
    # merging) and applies the same overrides to every segment's fid.com (see
    # NMRPipeBackend._convert_dir). Structural changes such as the output name have no effect.
    reference = _reference_fid_com_path(work, raw_dir, segments)
    reference_text = (
        reference.read_text(encoding="utf-8", errors="replace") if reference else None
    )
    overrides = _changed_fid_com_params(content, reference_text)
    if hasattr(backend, "work_dir"):
        backend.work_dir = str(work)
    resp = backend.convert_to_fid(
        experiment, raw_dir, fid_com_overrides=overrides, progress=progress
    )
    if not resp.get("success"):
        logs = list(resp.get("logs", []))
        message = (
            tr("fid.com Conversion failed: {p0}", p0=resp.get('message'))
            + (" | " + " | ".join(logs) if logs else "")
        )
        run = manager.start_run(
            exp_id,
            workflow_ref="manual_fid",
            inputs={"data_id": data_id},
            params={"mode": "manual", "segments": len(segments)},
        )
        manager.finish_run(run.run_id, "failed", message=message)
        raise ManualRunError(message)
    if segments:
        fid_path = Path(str(resp.get("fid_path") or (work / "merged" / "fid")))
        run_params = {
            "fid_path": str(fid_path),
            "segments": len(segments),
            "fid_com_overrides": dict(overrides),
        }
        message = tr("Manual FID completed (segmented merge)")
    else:
        fid_path = Path(
            str(resp.get("fid_path") or (work / f"{experiment.dataset_id}.fid"))
        )
        run_params = {"fid_path": str(fid_path), "fid_com_overrides": dict(overrides)}
        message = tr("Manual FID Completed")
    manager.set_data_fid(exp_id, data_id, fid_path)
    _finish_run(manager, exp_id, data_id, "manual_fid", run_params, message)
    return str(fid_path)


def manual_scripts(
    manager: ProjectManager,
    exp_id: str,
    data_id: str,
    params: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Spectrum-step script (process.com / nus*.com, for the script editor to display).

    Generating a spectrum manually is not entirely manual -- data conversion / merging /
    bad-point cleanup stay aligned with the automatic path (0.2.163-patch13): when the fid is
    missing the user is asked to run the "Generate FID" step first (0.2.163-patch14, the spectrum
    entry point does not sneak in a conversion); opening the editor only reads the existing script
    and does not run the quality diagnosis -- that moved to the "Run" click
    (run_manual_spectrum, 0.2.193), so opening the editor of a large dataset no longer stalls. An
    existing script in process/ is preferred; only when there is none is the default script
    rendered again. Only spectrum scripts are returned -- the fid comes from the "Generate FID"
    step.
    """
    data_entry = manager.data(exp_id, data_id)
    raw_dir = _resolve_raw_dir(manager, data_entry)
    work = _work_dir(manager, exp_id, data_id)
    existing: dict[str, str] = {}
    if work.is_dir():
        for path in sorted(work.glob("*.com")):
            if path.name == "fid.com":
                continue
            existing[path.name] = path.read_text(
                encoding="utf-8", errors="replace"
            )
    if existing:
        # Existing scripts are handed over as they are (0.2.193: the quality diagnosis moved to
        # "Run", so opening the editor no longer re-runs it and large datasets do not stall)
        return existing
    # fid missing: ask for the "Generate FID" step first (conversion/merging is done by the
    # automatic path); the manual route only lets a human tune parameters and does not sneak in a
    # conversion at the spectrum entry point (0.2.163-patch14)
    experiment = _read_experiment(manager, exp_id, data_id)
    located = _located_fids(work, experiment)
    if not _converted_fid_present(work, data_entry, experiment):
        raise ManualRunError(tr(
            "The converted fid is missing, please perform the \"Generate FID\" step "
            "first",
        ))
    if params is None:
        params = {}
    nus = dict(params.get("nus") or {})
    if not nus.get("nuslist_count"):
        nuslist_path = raw_dir / "nuslist"
        if nuslist_path.is_file():
            try:
                nus_rows = [
                    row
                    for row in nuslist_path.read_text(
                        encoding="utf-8", errors="replace"
                    ).splitlines()
                    if row.strip() and not row.lstrip().startswith("#")
                ]
                if nus_rows:
                    nus["nuslist_count"] = len(nus_rows)
                    params = {**params, "nus": {**nus}}
            except OSError:
                pass
    try:
        experiment = _read_experiment(manager, exp_id, data_id)
        rendered = render_scripts(experiment, params)
    except NotImplementedError as exc:
        raise ManualRunError(tr(
            "Unable to render and process script (not supported in acquisition mode): "
            "{p0}",
            p0=exc,
        )) from exc
    script_key = (
        "nus.com"
        if experiment.sampling.mode is SamplingMode.NUS
        else "process.com"
    )
    content = rendered[script_key]
    # 0.2.163-patch9 / 2026-09-24: the rendered default in_file is the single file
    # {dataset_id}.fid, while the real landing spot may be a slice directory (fid/test*.fid) or a
    # segmented merge product (merged/fid/test*.fid, merged/{dataset_id}.fid) -- rewrite -in from
    # the located paths so the manual run does not fail (the same -in rule as the automatic path).
    # Only the -in input is changed, never the -out output.
    single = f"{experiment.dataset_id}.fid"
    target = _fid_in_argument(work, located, experiment.dataset_id)
    if target and target != single:
        import re

        content = re.sub(r"(-in )" + re.escape(single), r"\g<1>" + target, content)
    return {script_key: content}


def run_manual_spectrum(
    manager: ProjectManager,
    exp_id: str,
    data_id: str,
    scripts: dict[str, str],
    *,
    work_dir: Path | str | None = None,
    timeout: float = 7200.0,
    progress: Callable[[str], None] | None = None,
) -> str:
    """Run the spectrum script (process.com/nus*.com in process/, consuming the converted fid);
    the final spectrum is put back into spectra/ and registered. fid.com is not executed
    (generating the FID is an independent step).
    """
    data_entry = manager.data(exp_id, data_id)
    raw_dir = _resolve_raw_dir(manager, data_entry)
    work = Path(work_dir) if work_dir else _work_dir(manager, exp_id, data_id)
    work.mkdir(parents=True, exist_ok=True)
    experiment = _read_experiment(manager, exp_id, data_id)
    workflow_ref = (
        "manual_nus"
        if experiment.sampling.mode is SamplingMode.NUS
        else "manual_process"
    )
    if not scripts:
        raise ManualRunError(tr("Missing processing script (editor content is empty)"))
    script_key = next(iter(scripts))
    runtime = CshRuntime()
    try:
        return _run_manual_spectrum_impl(
            manager,
            exp_id,
            data_id,
            data_entry,
            scripts,
            raw_dir,
            work,
            experiment,
            runtime,
            script_key,
            workflow_ref,
            timeout,
            progress,
        )
    except ManualRunError as exc:
        run = manager.start_run(
            exp_id,
            workflow_ref=workflow_ref,
            inputs={"data_id": data_id},
            params={"mode": "manual"},
        )
        manager.finish_run(run.run_id, "failed", message=str(exc))
        raise


def _run_manual_spectrum_impl(
    manager: ProjectManager,
    exp_id: str,
    data_id: str,
    data_entry: Any,
    scripts: dict[str, str],
    raw_dir: Path,
    work: Path,
    experiment: Experiment,
    runtime: CshRuntime,
    script_key: str,
    workflow_ref: str,
    timeout: float,
    progress: Callable[[str], None] | None = None,
) -> str:
    """The actual run of run_manual_spectrum (success path; failures raise ManualRunError).

    The spectrum step only consumes the converted fid (produced by the independent "Generate FID"
    step) and does not execute fid.com.
    """
    if not _converted_fid_present(work, data_entry, experiment):
        raise ManualRunError(
            tr(
                "The converted fid is missing, please generate FID first: "
                "{p0}/{p1}",
                p0=exp_id,
                p1=data_id,
            )
        )

    if experiment.sampling.mode is SamplingMode.NUS:
        nuslist_src = raw_dir / "nuslist"
        nuslist_dst = work / "nuslist"
        if nuslist_src.is_file() and not nuslist_dst.is_file():
            shutil.copy2(nuslist_src, nuslist_dst)

    # 0.2.193: a manual "Run" does the quality diagnosis first (bad-point repair / report), like
    # the start of the automatic spectrum path; opening the script editor no longer runs it (so
    # opening got faster)
    _run_quality_check(manager, exp_id, data_entry, work, data_id)

    script = scripts.get(script_key)
    if script is None:
        raise ManualRunError(tr("Missing script: {p0}", p0=script_key))
    (work / script_key).write_text(script, encoding="utf-8", newline="\n")
    result = runtime.run(
        ["csh", script_key],
        cwd=str(work),
        timeout=timeout,
        on_line=progress,
    )
    out_ext = "ft3" if experiment.ndim >= 3 else "ft2"
    spectrum_src = work / f"{experiment.dataset_id}.{out_ext}"
    if result.returncode != 0 or not spectrum_src.is_file():
        raise ManualRunError(tr("{p0} run failed: {p1}", p0=script_key, p1=result.stderr))

    spectrum_path = _register_spectrum(
        manager, exp_id, data_id, str(spectrum_src)
    )
    # 0.2.199-patch29v: the manual route also produces a spectrum quality report (the final
    # spectrum quality score) and writes {spectrum}.quality.json for the GUI report page --
    # consistent with the automatic route, so a manual spectrum has no "no report record". The
    # data quality diagnosis belongs to the "Generate FID" step since 2026-09-23 and is not part
    # of this report.
    try:
        from workflow.optimization_report import (
            spectrum_quality_report_lines,
            spectrum_report_title,
            write_quality_record,
        )
        from workflow.phase_routes import _sign_mode

        lines = [spectrum_report_title()]
        lines += spectrum_quality_report_lines(
            spectrum_path, sign_mode=_sign_mode(experiment)
        )
        # 2026-09-23 (user request): the diagnosis is not in this report -- the FID-layer
        # conclusions belong to the "Generate FID" step; this report keeps the quality score and
        # the parameter note of this step (the manual run).
        lines.append(tr("◆ Process parameter: manual method (mode=manual)"))
        write_quality_record(
            spectrum_path, {"mode": "manual"}, "\n".join(lines)
        )
        # 0.2.199-patch29ei: the evaluation report is mirrored to progress/logs, visible after a
        # manual run
        if progress is not None:
            for _ln in lines:
                progress(_ln)
    except Exception as exc:  # noqa: BLE001 - a failed report does not affect spectrum generation
        # 0.2.199-patch29eh: a failed evaluation is not silent -- it is written to
        # manual_quality.log and reported
        _msg = (
            tr("Spectrum quality assessment failed: {p0}: {p1}", p0=type(exc).__name__, p1=exc)
        )
        try:
            with (work / "manual_quality.log").open("a", encoding="utf-8") as _fh:
                _fh.write(_msg + "\n")
        except OSError:
            pass
        if progress is not None:
            progress(_msg)
    _finish_run(
        manager,
        exp_id,
        data_id,
        workflow_ref,
        {"spectrum_path": spectrum_path},
        tr("Artificial spectrum completed"),
    )
    return spectrum_path


__all__ = [
    "ManualRunError",
    "manual_fid_com",
    "manual_scripts",
    "run_manual_fid_com",
    "run_manual_spectrum",
]
