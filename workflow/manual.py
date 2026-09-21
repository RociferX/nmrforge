"""Manual processing path backend (user feedback: Check/Revise/run script). Corresponding to
automatic processing, it integrates the previous manual processing processes on the command
line: - Generate FID: the automatic phase produces fid.com(backend.convert_to_fid) first ->
Manually view the content (manual_fid_com) -> Modify -> Run (run_manual_fid_com, manual
parameter is handed over to the backend as an overlay) -> Register fid; - Generate spectrum:
script Edit (manual_scripts render, process/ existing scripts are displayed first -> modify) ->
run (process.com / nus*.com, run_manual_spectrum) -> final spectrum returns to spectra/ and
registers. Run reuse backend.runtime.CshRuntime; product registration set_data_fid /
set_data_spectrum + WorkflowRun (audit)."""

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
    """Manually handle runtime errors (script Missing/Execution failed/Product missing)."""


def _resolve_raw_dir(manager: ProjectManager, data_entry: Any) -> Path:
    raw = Path(data_entry.raw_dir) if data_entry.raw_dir else Path(data_entry.source)
    if not raw.is_absolute():
        raw = manager.root / raw
    return raw


def _work_dir(manager: ProjectManager, exp_id: str, data_id: str) -> Path:
    return manager.data_dir(exp_id, data_id, "process")


def _slice_files(directory: Path, dataset_id: str) -> list[Path]:
    """Slice fid candidate: The new name {dataset_id}*.fid takes precedence and is compatible with
    the old test*.fid."""
    if not directory.is_dir():
        return []
    new_style = sorted(directory.glob(f"{dataset_id}*.fid"))
    legacy = sorted(directory.glob("test*.fid"))
    seen = {p.name for p in new_style}
    return new_style + [p for p in legacy if p.name not in seen]


def _fid_ready(candidate: Path | None) -> bool:
    """The presence of a single file or slice directory (either named) is considered converted."""
    if candidate is None:
        return False
    if candidate.is_file():
        return True
    if candidate.is_dir() and list(candidate.glob("*.fid")):
        return True
    return False


def _run_quality_check(
    manager: ProjectManager,
    exp_id: str,
    data_entry: Any,
    work: Path,
    data_id: str,
) -> None:
    """Quality diagnosis before manually "running" the spectrum (without re-running optimisation).
    The results are written to process/manual_quality.log; the diagnosis will repair the bad
    point (backup) incidentally, which is consistent with the quality inspection at the
    beginning of the automatic path "Generate spectrum" (0.2.163-patch13). 0.2.193: When opening
    from the script editor, click "Run" and execute (run_manual_spectrum), and the editor will
    no longer freeze."""
    fid_candidate = (
        Path(data_entry.fid_path)
        if data_entry.fid_path
        else work / f"{data_id}.fid"
    )
    if not _fid_ready(fid_candidate):
        return  # fid When not in place, diagnosis is meaningless and editing is not blocked.
    try:
        from workflow.direct_diagnostics import run_direct_diagnostics

        experiment = _read_experiment(manager, exp_id, data_id)
        result = run_direct_diagnostics(work, experiment)
        lines = [(
            tr(
            "== Quality inspection (manual spectrum preparation, multiplexing automatic "
            "optimisation final script) "
            "==",
        )
        )]
        lines += [f"{i + 1}. {report}" for i, report in enumerate(result.reports)]
        lines.append(tr("index: {p0}", p0=result.metrics))
        (work / "manual_quality.log").write_text(
            (chr(10).join(lines) + chr(10)), encoding="utf-8"
        )
    except Exception as exc:  # noqa: BLE001 - Quality check failure does not block editing.
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
    """Get the generated fid.com content (For human viewing/Revise); do not trigger automatic
    conversion. 0.2.199-patch29dm(user): The manual button to generate FID will only appear
    after the automatic processing is successful. Here, directly read the generated fid.com --
    single dataset process/fid.com; segment the reference segment process/seg_001/fid.com and
    add a prompt header (after manually changing the parameter, run_manual_fid_com As an
    overlay, it is handed over to the backend for unified execution). fid.com Explicitly report
    an error when it does not exist, and no longer automatically convert (avoid opening after a
    while after clicking)."""
    data_entry = manager.data(exp_id, data_id)
    raw_dir = _resolve_raw_dir(manager, data_entry)
    work = _work_dir(manager, exp_id, data_id)
    segments = list(getattr(data_entry, "segments", None) or [])
    if segments:
        seg_fid = work / "seg_001" / "fid.com"
        if seg_fid.is_file():
            header = (
                tr(
                    "# Segmented acquisition: this is the reference-segment fid.com; parameter "
                    "edits apply to every segment\n# (conversion / slicing / merging is done by "
                    "the backend; do not change the output "
                    "name)\n",
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
    """Run manual fid.com and register fid. The single dataset is consistent with the segmentation
    (0.2.199-patch2): the manually modified parameters are extracted as overrides by
    parse_fid_com, and are uniformly executed by the backend convert_to_fid to generate bruker /
    parameter correction / bad point clean up/Slice return -- Manually adjust parameters only,
    and the conversion structure is guaranteed by the backend, and no longer directly csh to run
    user scripts (structural changes are not retained, consistent with segmentation semantics).
    Single dataset product placement process/(single file or slice), segmented product placement
    process/merged/fid."""
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
    overrides = parse_fid_com(content)
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
        run_params = {"fid_path": str(fid_path), "segments": len(segments)}
        message = tr("Manual FID completed (segmented merge)")
    else:
        fid_path = Path(
            str(resp.get("fid_path") or (work / f"{experiment.dataset_id}.fid"))
        )
        run_params = {"fid_path": str(fid_path)}
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
    """Spectrum step script (process.com / nus*.com, for script editor display). Manual generation
    of spectrum is not completely manual, data conversion/merge/ bad point cleaning and
    automatic path alignment (0.2.163-patch13): when fid is missing, the user is prompted to
    perform the "Generate FID" step first (0.2.163-patch14, no sneak conversion at the spectrum
    entrance); open the editor to only read the existing script, and do not run quality
    diagnosis -- Diagnosis is changed to "Run" to execute (run_manual_spectrum, 0.2.193), and
    the big data script editor is no longer stuck. Priority is given to returning the existing
    script under the process/ directory; only the default script is re-rendered if there is no
    such script. Only spectrum scripts are returned -- fid is produced by the "Generate FID"
    step."""
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
        # There is an existing script that can be directly given to people (0.2.193: Quality
        # diagnosis is executed when the quality diagnosis is changed to "Run", and the editor is no
        # longer re-run to avoid lags every time the big data is opened).
        return existing
    # Fid is missing: Prompt to perform the "Generate [2]]" step first (Convert/Merging is done by
    # automatic paths). The manual method is only for adjusting parameters for people, not for sneak
    # conversion at the spectrum entrance (0.2.163-patch14).
    experiment = _read_experiment(manager, exp_id, data_id)
    fid_candidate = (
        Path(data_entry.fid_path)
        if data_entry.fid_path
        else work / f"{experiment.dataset_id}.fid"
    )
    if not _fid_ready(fid_candidate) and not _slice_files(
        work / "fid", experiment.dataset_id
    ):
        raise ManualRunError(
            tr(
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
        raise ManualRunError(
            tr(
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
    # 0.2.163-patch9: The fid of 3D uniform/NUS is the slice directory (fid/test*.fid). The default
    # rendering in_file is a single file {dataset_id}.fid -- When a slice is detected, it is
    # rewritten as a slice stream so that manual operation does not fail (consistent with the
    # automatic path backend slice switching).
    slice_dir = work / "fid"
    slices = _slice_files(slice_dir, experiment.dataset_id)
    if slices:
        single = f"{experiment.dataset_id}.fid"
        new_style = any(
            p.name.startswith(experiment.dataset_id) for p in slices
        )
        sliced = (
            f"fid/{experiment.dataset_id}%03d.fid"
            if new_style
            else "fid/test%03d.fid"
        )
        # Only change the -in input of xyz2pipe/nmrPipe and leave the -out output unchanged.
        import re

        content = re.sub(
            r"(-in )" + re.escape(single),
            r"\g<1>" + sliced,
            content,
        )
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
    """Run spectrum script (process.com/nus*.com in process/, consumption has been converted to
    fid), final spectrum returns to spectra/ and registers; does not execute fid.com (generating
    FID is an independent step)."""
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
    """Actual execution of run_manual_spectrum (success path; ManualRunError thrown on failure).
    The spectrum step only consumes the converted fid (the output of the independent step
    "Generate FID") and does not execute fid.com."""
    fid_candidate = (
        Path(data_entry.fid_path)
        if data_entry.fid_path
        else work / f"{experiment.dataset_id}.fid"
    )
    if not _fid_ready(fid_candidate):
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

    # 0.2.193: When manually "running", first run a quality diagnosis (bad point repair/Report),
    # which is consistent with the beginning of the automatic path generation spectrum; open the
    # script editor and no longer execute it (opening becomes faster).
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
    # 0.2.199-patch29v: The manual approach also generates a spectrum quality report (data quality
    # diagnosis + final spectrum quality score), writing {spectrum}.quality.json for display on the
    # GUI report page -- consistent with the automatic approach to avoid "no report record" for
    # manual spectrum.
    try:
        from workflow.optimization_report import (
            spectrum_quality_report_lines,
            write_quality_record,
        )
        from workflow.phase_routes import _sign_mode

        lines = [tr("== spectrum quality and data quality report ==")]
        lines += spectrum_quality_report_lines(
            spectrum_path, sign_mode=_sign_mode(experiment)
        )
        qlog = work / "manual_quality.log"
        if qlog.is_file():
            diag = [
                ln
                for ln in qlog.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines()
                if ln.strip()
            ]
            if diag:
                lines += [tr("◆ Data quality diagnosis (manual approach)")] + diag
        lines.append(tr("◆ Process parameter: manual method (mode=manual)"))
        write_quality_record(
            spectrum_path, {"mode": "manual"}, "\n".join(lines)
        )
        # 0.2.199-patch29ei: The evaluation report is synchronously output to the progress/log and
        # is visible after manual operation.
        if progress is not None:
            for _ln in lines:
                progress(_ln)
    except Exception as exc:  # noqa: BLE001 - Report failure does not affect spectrum generation.
        # 0.2.199-patch29eh: Evaluation failure is not silent -- Write manual_quality.log and
        # prompt.
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
