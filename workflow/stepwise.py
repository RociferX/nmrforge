"""Step-by-step processing and arrangement (API_CONTRACT §8.3 / G2B-002). Three steps: 1.
import_data -- workflow.import_workflow.import_data (read-only parameter + link (G2B-009)); 2.
generate_fid -- backend.convert_to_fid (generate NMRPipe fid); 3. generate_spectrum --
backend.process / reconstruct_nus (inclusive NUS SMILE reconstruction). phase optimisation:
first use SMILE reconstruction to generate the spectrum, and then repeatedly run the backend
(violent) optimisation candidate by candidate, and the final spectrum is output by the real
pipeline (memory phase search + final script write back, 0.2.164 unify together)."""

from __future__ import annotations

import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from backend import memory_disk
from core.data.bruker_reader import read_dataset, read_dataset_container
from core.data.internal_data_model import Experiment, SamplingMode
from core.planning.method_selector import select_method
from core.project import ProjectManager
from ui_support.i18n import tr
from workflow.import_workflow import ImportResult, import_data
from workflow.ucsf_export import export_ucsf


class StepwiseError(Exception):
    """Handle errors in steps."""


def _require_data(manager: ProjectManager, exp_id: str, data_id: str) -> Any:
    return manager.data(exp_id, data_id)


def _read_experiment(manager: ProjectManager, exp_id: str, data_id: str) -> Experiment:
    """Read the Experiment from the data entry (prioritize the raw copy in the project; use each
    segment directory for single data segment collection)."""
    data_entry = _require_data(manager, exp_id, data_id)
    if data_entry.segments:
        # Segmented collection: source is the container directory (without acqus), and each segment
        # is in data_entry.segments.
        from core.data.bruker_reader import read_segments

        seg_paths: list[Path] = []
        for seg in data_entry.segments:
            seg_path = Path(seg)
            if not seg_path.is_absolute():
                seg_path = manager.root / seg_path
            seg_paths.append(seg_path)
        experiment = read_segments(seg_paths)
    else:
        source = Path(data_entry.raw_dir) if data_entry.raw_dir else Path(data_entry.source)
        if not source.is_absolute():
            source = manager.root / source
        try:
            experiment = read_dataset(source)
        except ValueError:
            # Container directory (old data/Segmentation residual) falls back to container reading
            # (0.2.164 is unified with manual).
            experiment = read_dataset_container(source)[0]
    # 2026-08-19: The prefix of the intermediate product/final spectrum is unified with the data id
    # (d_001), which does not change with renaming; read_dataset and dataset_id are taken from the
    # raw directory name (usually raw) and must be overwritten.
    experiment.dataset_id = data_id
    # 0.2.199-patch29fd: The experiment type selected by user in GUI (metadata
    # evidence=gui_user_selected) authoritatively covers the live classification
    # (pdata/title/PULPROG),deal with/The peak selection shall be based on the.
    _apply_gui_type_override(manager, exp_id, data_id, experiment)
    return experiment


def _apply_gui_type_override(
    manager: ProjectManager, exp_id: str, data_id: str, experiment: Experiment
) -> None:
    """If the experiment_type mark of the data metadata is gui_user_selected, it shall prevail."""
    try:
        from core.data.internal_data_model import ExperimentType

        data_entry = _require_data(manager, exp_id, data_id)
        path = manager.data_metadata_path(exp_id, data_id)
        if not path.is_file() and data_entry.metadata_path:
            mp = Path(data_entry.metadata_path)
            path = mp if mp.is_absolute() else manager.root / mp
        if not path.is_file():
            return
        import json

        payload = json.loads(path.read_text(encoding="utf-8"))
        et = ((payload.get("dataset") or {}).get("experiment_type") or {})
        if not et:
            return
        evidence = [str(e) for e in (et.get("evidence") or [])]
        if not any("gui_user_selected" in e for e in evidence):
            return
        name = str(et.get("name", "") or "")
        if not name:
            return
        experiment.experiment_type = ExperimentType(
            name=name, confidence=1.0, evidence=evidence
        )
    except Exception:
        return


def _work_dir(manager: ProjectManager, exp_id: str, data_id: str) -> Path:
    """Independent working directory for each data:<exp_id>/<data_id>/process/(Contract §9.2)."""
    return manager.data_dir(exp_id, data_id, "process")


def _rewrite_duplicate_nucleus_labels(
    spectrum_path: str,
    experiment: Any,
) -> bool:
    """Homonuclear spectrum duplicate labels are unique (0.2.199-patch29af/patch29ag/patch29ah).
    proj3D Press FDF label to select axis, re-review label is ambiguity; when processing,
    change: re-review, press index priority "direct dimension > acqu2 > acqu3" and add x/y/z (2D
    double 1H: F2 (direct) -> 1Hx, F1 -> 1Hy; 3D Three identical cores: F3 (direct) -> 1Hx, F2
    -> 1Hy, F1 -> 1Hz;HNN Double 15N:F2(acqu2,HSQC's N) -> 15Nx, F1 -> 15Ny). GUI's
    nucleus_symbol will display 15Nx as Nx. Return whether to overwrite."""
    import nmrglue as ng
    import numpy as np

    try:
        dic, data = ng.pipe.read(str(spectrum_path))
        data = np.asarray(data)
        ndim = data.ndim
        if ndim not in (2, 3):
            return False
        order = [int(v) for v in dic.get("FDDIMORDER") or []]

        def _fdf(axis_idx: int) -> str:
            if len(order) >= ndim:
                dim = order[ndim - 1 - axis_idx]
                if 1 <= dim <= 4:
                    return f"FDF{dim}"
            return f"FDF{axis_idx + 1}"

        # Numpy stores the logical name of the axis: 2D (F1, F2); 3D (F2, F1, F3).
        logical = ["F1", "F2"] if ndim == 2 else ["F2", "F1", "F3"]
        # Re-review index priority: direct dimension > acqu2 > acqu3 (2D:F2 direct dimension,
        # F1=acqu2;3D:F3 direct dimension, F2=acqu2, F1=acqu3).
        priority = ["F2", "F1"] if ndim == 2 else ["F3", "F2", "F1"]
        labels = [
            str(dic.get(f"{_fdf(i)}LABEL", "") or "") for i in range(ndim)
        ]
        counts: dict[str, int] = {}
        for lbl in labels:
            counts[lbl] = counts.get(lbl, 0) + 1
        dups = {lbl for lbl, c in counts.items() if c > 1 and lbl}
        if not dups:
            return False
        changed = False
        for dup in sorted(dups):
            dup_axes = sorted(
                (i for i, lbl in enumerate(labels) if lbl == dup),
                key=lambda i: priority.index(logical[i]),
            )
            for rank, axis_idx in enumerate(dup_axes):
                suf = "xyz"[rank] if rank < 3 else str(rank + 1)
                dic[f"{_fdf(axis_idx)}LABEL"] = dup + suf
                changed = True
        if changed:
            ng.pipe.write(str(spectrum_path), dic, data, overwrite=True)
        return changed
    except Exception:  # noqa: BLE001 - Failure to rewrite labels does not affect spectrum.
        return False


def _register_spectrum(
    manager: ProjectManager,
    exp_id: str,
    data_id: str,
    spectrum_path: str,
) -> str:
    """Return the final spectrum produced by the backend to <exp_id>/<data_id>/spectra/ (move,
    process does not leave a copy) and register it."""
    source = Path(spectrum_path)
    spectra_dir = manager.data_dir(exp_id, data_id, "spectra")
    spectra_dir.mkdir(parents=True, exist_ok=True)
    # 2026-08-19: The final spectrum naming prefix uses data id (d_001), renaming will not affect.
    target = spectra_dir / f"{data_id}{source.suffix}"
    if source.is_file() and source.resolve() != target.resolve():
        shutil.move(str(source), str(target))
    # 0.2.199-patch29af: Homonuclear spectrum (HNN/NNH double 15N) unique label (15Nx/15Ny), making
    # proj3D axis selection by label available; after rewriting, both projection and GUI display
    # Nx/Ny.
    try:
        experiment = _read_experiment(manager, exp_id, data_id)
        _rewrite_duplicate_nucleus_labels(str(target), experiment)
    except Exception:  # noqa: BLE001 - Failure to rewrite does not affect spectrum registration.
        pass
    manager.set_data_spectrum(exp_id, data_id, target)
    return str(target)


def _export_ucsf(
    manager: ProjectManager,
    exp_id: str,
    data_id: str,
    spectrum_path: str,
) -> tuple[str | None, str]:
    """After final spectrum is returned, Sparky UCSF file (spectra/<data_id>.ucsf) is generated."""
    # 0.2.199-patch29gj-Fix (user): 1D spectrum (.ft1) does not generate UCSF file -- UCSF For
    # 2D/3D, pipe2ucsf is meaningless for 1D; skip it directly to avoid redundant products and
    # failure log.
    if Path(spectrum_path).suffix.lower() == ".ft1":
        return None, tr("1D spectrum does not generate UCSF file (skip)")
    spectra_dir = manager.data_dir(exp_id, data_id, "spectra")
    spectra_dir.mkdir(parents=True, exist_ok=True)
    target = spectra_dir / f"{Path(spectrum_path).stem}.ucsf"
    return export_ucsf(spectrum_path, target)


def _ensure_work_dir(backend: Any, work: Path) -> None:
    """Pin the backend working directory to the data-level directory (when backend.work_dir is
    writable)."""
    if hasattr(backend, "work_dir"):
        backend.work_dir = str(work)


def _finish_step(
    manager: ProjectManager,
    exp_id: str,
    data_id: str,
    workflow_ref: str,
    outputs: dict[str, str],
    message: str,
    params: dict[str, Any] | None = None,
) -> str:
    """Register the step WorkflowRun once (append only, audit)."""
    run = manager.start_run(
        exp_id,
        workflow_ref=workflow_ref,
        inputs={"data_id": data_id},
        params=dict(params or {}),
    )
    manager.finish_run(run.run_id, "success", outputs=outputs, message=message)
    return run.run_id


def generate_fid(
    manager: ProjectManager,
    exp_id: str,
    data_id: str,
    backend: Any,
    *,
    work_dir: Path | str | None = None,
    progress: Callable[[str], None] | None = None,
) -> str:
    """Step 2: Convert Bruker raw data to NMRPipe fid (independent phase)."""
    experiment = _read_experiment(manager, exp_id, data_id)
    data_entry = _require_data(manager, exp_id, data_id)
    data_dir = Path(data_entry.raw_dir) if data_entry.raw_dir else Path(data_entry.source)
    if not data_dir.is_absolute():
        data_dir = manager.root / data_dir
    work = Path(work_dir) if work_dir else _work_dir(manager, exp_id, data_id)
    _ensure_work_dir(backend, work)
    resp = backend.convert_to_fid(experiment, data_dir, progress=progress)
    logs = list(resp.get("logs", []))
    if not resp.get("success"):
        raise StepwiseError(
            str(resp.get("message", tr("Conversion failed"))) + " | " + " | ".join(logs)
        )
    # 0.2.199-patch29gu: Successfully also forwards the backend detailed log progress(Group
    # batch/Can be seen individually).
    if progress is not None:
        for _lg in logs:
            progress(_lg)
    fid_path = str(resp.get("fid_path", ""))
    manager.set_data_fid(exp_id, data_id, fid_path)
    merged_params = dict(resp.get("effective_params") or {})
    _finish_step(
        manager,
        exp_id,
        data_id,
        "convert_to_fid",
        outputs={"fid_path": fid_path},
        message=tr("Generate FID"),
        params=merged_params,
    )
    return fid_path



def _apply_note_overrides(manager, exp_id: str, data_id: str, experiment) -> None:
    """Override automatic classification and presets (0.2.199-patch29hc) with data annotations
    (experiment type of data type /peak symbol)."""
    try:
        project = getattr(manager, "project", None)
        if project is None:
            return
        exp = project.experiment(exp_id)
        if exp is None:
            return
        note = ((exp.metadata or {}).get("data_notes") or {}).get(data_id)
        if not isinstance(note, dict):
            return
        tname = str(note.get("experiment_type", "") or "").strip()
        if tname:
            from gui.notes import experiment_type_options
            if tname in experiment_type_options(experiment.ndim):
                # 0.2.199-patch29hc: Fill in the type name string, and an ExperimentType object
                # (.name/.confidence/.evidence) must be constructed, otherwise the downstream
                # select_method/ import_workflow will report an AttributeError when
                # accessing.confidence.
                from core.data.internal_data_model import ExperimentType
                experiment.experiment_type = ExperimentType(
                    name=tname, confidence=1.0, evidence=["data_note"]
                )
        psign = str(note.get("peak_sign", "") or "").strip()
        if psign in ("uniform", "mixed"):
            experiment.note_peak_sign = psign
    except Exception:  # noqa: BLE001 - Comment coverage failure does not block.
        pass


def _default_phase_route(experiment) -> str:
    """Press dimension to select the default phase_route: 1D without indirect dimension, directly
    connected to process(patch29gj)."""
    return "none" if int(getattr(experiment, "ndim", 2) or 2) == 1 else "unified"


def generate_spectrum(
    manager: ProjectManager,
    exp_id: str,
    data_id: str,
    backend: Any,
    *,
    params: dict[str, Any] | None = None,
    work_dir: Path | str | None = None,
    progress: Callable[[str], None] | None = None,
) -> str:
    """Step 3: Generate spectrum (NUS automatically go through SMILE reconstruction; reuse
    converted fid). params["phase_route"] Select processing path: - "unified" (default): unified
    scheme -- first pass of dimensionally complex preview (only search axis without -di), memory
    phase modulation (old algorithm judgment standard, zero extra backend), complete final run;
    - "none": keep the old path, directly process/reconstruct_nus, no additional optimisation
    (escape hatch). 0.2.199-patch29ey: When work_dir is not explicitly specified, press
    processing.intermediate_memory to adaptively put the working directory of the intermediate
    spectrum into the memory disk (when the memory margin is sufficient), and delete it entirely
    when it is used up."""
    experiment = _read_experiment(manager, exp_id, data_id)
    work = Path(work_dir) if work_dir else _work_dir(manager, exp_id, data_id)
    _ensure_work_dir(backend, work)

    def _sweep_intermediates() -> None:
        """Clean up this data unified intermediate product residue (0.2.199-patch29gi). Execute
        once before running and once at the end: clear the legacy of the last hard interrupt
        (SIGKILL/power outage) before running, clear this residue after ending (including
        exception); only delete the intermediate product, final spectrum / final script /fid
        retain."""
        from workflow.phase_routes import _cleanup_unified_intermediates

        _cleanup_unified_intermediates(
            work,
            experiment.dataset_id,
            experiment=experiment,
            backend=backend,
        )

    _sweep_intermediates()
    memory_dir: Path | None = None
    if work_dir is None:
        # 0.2.199-patch29ez (user plan): Intermediate products are unified into the
        # work/_intermediate subdirectory. When the memory margin is sufficient, the subdirectory is
        # symbolically linked to the memory disk; the remaining contents of the working directory
        # (fid/script/phase.json/final run) will keep the original logic on the disk.
        _intermediate_root, memory_dir = memory_disk.prepare_intermediate(
            work, experiment, params=params
        )
        if memory_dir is not None and progress is not None:
            progress(
                tr(
                "Intermediate spectrum working directory using ramdisk (adaptive): "
                "{p0}",
                p0=_intermediate_root,
            ))
        elif progress is not None:
            _reason = memory_disk.selection_reason(experiment, params=params)
            progress(
                tr("Intermediate spectrum work directory using disk")
                + (f"({_reason})" if _reason else tr("(Memory directory creation failed)"))
            )
    try:
        return _generate_spectrum_impl(
            manager,
            exp_id,
            data_id,
            backend,
            work=work,
            params=params,
            progress=progress,
        )
    finally:
        # 0.2.199-patch29gi:abnormal/Interruption leaves no intermediate products (Clean the hard
        # interrupt before running).
        _sweep_intermediates()
        if work_dir is None:
            memory_disk.teardown_intermediate(work, memory_dir)


def _generate_spectrum_impl(
    manager: ProjectManager,
    exp_id: str,
    data_id: str,
    backend: Any,
    *,
    work: Path,
    params: dict[str, Any] | None = None,
    progress: Callable[[str], None] | None = None,
) -> str:
    """Original generate_spectrum principal (working directory has been determined by the outer
    layer)."""
    experiment = _read_experiment(manager, exp_id, data_id)
    params = dict(params or {})
    route = str(params.pop("phase_route", _default_phase_route(experiment)))
    # 0.2.199-patch29hc:"data type/Peak symbol"Prioritize automatic classification/Default. in data
    # annotation.
    _apply_note_overrides(manager, exp_id, data_id, experiment)
    plan = select_method(experiment)
    if route == "none":
        # 0.2.162-patch15: The escape hatch only runs once, and directly maps the final run direct
        # dimension range to ext.
        for key, target_key in (("final_ext_lo", "ext_lo"), ("final_ext_hi", "ext_hi")):
            if key in params and str(params[key]).strip():
                params[target_key] = str(params[key])
            params.pop(key, None)
        if experiment.sampling.mode is SamplingMode.NUS:
            workflow_ref = "reconstruct_nus"
            resp = backend.reconstruct_nus(experiment, params, progress=progress)
        else:
            workflow_ref = "process"
            resp = backend.process(
                experiment, plan, params=params, progress=progress
            )
        logs = list(resp.get("logs", []))
        if not resp.get("success"):
            raise StepwiseError(
                str(resp.get("message", tr(
                    "Spectrum generation "
                    "failed",
                ))) + " | " + " | ".join(logs)
            )
        # 0.2.199-patch29gu: Successfully also forwards the backend detailed log progress.
        if progress is not None:
            for _lg in logs:
                progress(_lg)
        spectrum_path = _register_spectrum(
            manager, exp_id, data_id, str(resp.get("spectrum_path", ""))
        )
        ucsf_path, ucsf_msg = _export_ucsf(
            manager, exp_id, data_id, spectrum_path
        )
        if progress is not None:
            progress(ucsf_msg)
        merged_params = dict(resp.get("effective_params") or {})
        merged_params.update(params)
        outputs: dict[str, str] = {"spectrum_path": spectrum_path}
        if ucsf_path:
            outputs["ucsf_path"] = ucsf_path
        _finish_step(
            manager,
            exp_id,
            data_id,
            workflow_ref,
            outputs=outputs,
            message=tr("generate spectrum"),
            params=merged_params,
        )
        return spectrum_path

    if route != "unified":
        raise StepwiseError(tr("Unknown phase_route: {p0}", p0=route))

    from workflow.phase_routes import unified_route

    try:
        result = unified_route(
            experiment,
            backend,
            plan=plan,
            work_dir=work,
            base_params=params,
            progress=progress,
        )
    except (RuntimeError, StepwiseError) as exc:  # noqa: BLE001
        # 0.2.199-patch29gy: Unified automatic phase replication preview will fail for some data
        # (such as solid CANH) (NMRPipe reports data in Frequency Domain / Broken pipe); fall back
        # to phase_route=none escape hatch, reuse the converted fid, and ensure normal spectrum
        # production (phase p0=p1=0).
        msg = str(exc)
        # The phase routes raise "Replica preview (...)" failures and "NMRPipe
        # processing failed"; both needles go through tr() so the escape hatch keeps
        # covering every wording in either language.
        if tr("Replica preview") not in msg and tr("NMRPipe processing failed") not in msg:
            raise
        if progress is not None:
            progress(tr(
                "Unified replication preview failed ({p0}); fallback "
                "phase_route=none",
                p0=msg,
            ))
        fb_params = dict(params)
        fb_params["phase_route"] = "none"
        return _generate_spectrum_impl(
            manager,
            exp_id,
            data_id,
            backend,
            work=work,
            params=fb_params,
            progress=progress,
        )
    workflow_ref = "phase_optimize_unified"

    if not result.get("spectrum_path"):
        raise StepwiseError(tr("phase optimisation does not produce spectrum"))
    spectrum_path = _register_spectrum(
        manager, exp_id, data_id, str(result.get("spectrum_path"))
    )
    ucsf_path, ucsf_msg = _export_ucsf(manager, exp_id, data_id, spectrum_path)
    if progress is not None:
        progress(ucsf_msg)
    merged_params = dict(params)
    merged_params["phase_route"] = route
    for key in (
        "phases",
        "baseline",
        "zero_fill",
        "window",
        "fill",
        "backend_runs",
        "direct_phase",
        "diagnostics",
    ):
        if key in result:
            merged_params[key] = result[key]
    run_id = _finish_step(
        manager,
        exp_id,
        data_id,
        workflow_ref,
        outputs=(
            {"spectrum_path": spectrum_path, "ucsf_path": ucsf_path}
            if ucsf_path
            else {"spectrum_path": spectrum_path}
        ),
        message=tr("generate spectrum"),
        params=merged_params,
    )
    # Task E(0.2.133): 3D final spectrum uses NMRPipe proj3D.tcl to generate three projections,
    # falling into spectra/<id>_<core A>-<core B>.ft2 (the file name contains the actual two plane
    # cores, GUI uses <data_id>_*.ft2 wildcard scanning, the old *_proj_*.ft2 is also compatible).
    if experiment.ndim >= 3 and getattr(backend, "project_3d", None):
        try:
            spectra_dir = manager.data_dir(exp_id, data_id, "spectra")
            proj = backend.project_3d(
                spectrum_path,
                spectra_dir,
                prefix=f"{data_id}_proj",
            )
            labels = proj.get("labels", {})
            nuclei = proj.get("nuclei", {})
            for tag, path in proj.get("paths", {}).items():
                if proj.get("numpy_fallback"):
                    # 0.2.199-patch29w:HNN Equal weight review tag -- The core name projection will
                    # conflict (two 15N-1H planes), use fixed logical axis naming
                    # {data_id}_proj_F{n}, GUI _proj_F{n} for compatible parsing; the backend has
                    # been written out with the same name, no need to rename.
                    logical = str(tag)
                    target = spectra_dir / f"{data_id}_proj_{logical}.ft2"
                else:
                    fixed_nucleus = str(labels.get(tag, "") or "")
                    logical = next(
                        (
                            dim.logical_axis
                            for dim in experiment.dimensions
                            if dim.nucleus == fixed_nucleus
                        ),
                        "",
                    )
                    target = spectra_dir / projection_filename(
                        data_id, nuclei.get(tag), logical, tag
                    )
                if Path(path) != target:
                    if target.exists():
                        target.unlink()
                    Path(path).replace(str(target))
                merged_params.setdefault("projections", {})[logical or tag] = str(
                    target
                )
        except Exception as exc:  # noqa: BLE001 - Spectrum is not blocked if projection fails.
            merged_params.setdefault("projections", {})["error"] = str(exc)
        _run = manager.project.run(run_id)
        if _run is not None and "projections" in merged_params:
            # 0.2.133: Projection registration writeback run parameter (for GUI/Report reading).
            _run.params["projections"] = merged_params["projections"]
    # 0.2.199-patch29t: The report cache disk must be placed after the projection registration --
    # run.params will append projections. If the record is written in advance, the fingerprint will
    # not match the run.params read by GUI, and the report will always display "No report record"
    # (all previous 3D spectra were hit).
    try:
        from workflow.optimization_report import (
            report_text_from_logs,
            write_quality_record,
        )

        report_text = report_text_from_logs(list(result.get("logs", [])))
        if report_text:
            write_quality_record(spectrum_path, merged_params, report_text)
    # Failure of cache recording does not affect spectrum generation.
    except Exception:  # noqa: BLE001 -
        pass
    return spectrum_path


def projection_filename(
    data_id: str,
    nuclei: list[str] | None,
    logical: str,
    tag: str,
) -> str:
    """Projection file name (0.2.133):d_001_15N-1H.ft2 -- Contains the actual two cores of the
    plane. Nuclear deletion/Fallback to old name when unavailable d_001_proj_<logical|tag>.ft2
    (still hit by GUI <data_id>_*.ft2 and *_proj_*.ft2 wildcard scan, compatible with historical
    files)."""
    if nuclei and len(nuclei) >= 2:
        safe = [
            re.sub(r"[^A-Za-z0-9]", "", str(nuc or ""))
            for nuc in nuclei[:2]
        ]
        if all(safe):
            return f"{data_id}_{safe[0]}-{safe[1]}.ft2"
    return f"{data_id}_proj_{logical or tag}.ft2"






def read_experiment(manager: ProjectManager, exp_id: str, data_id: str) -> Any:
    """Public reading experiment entrance: Read Experiment by data entry (same caliber as the
    processing chain). 2026-09-12 The parameter sensitivity interface (nmrforge_api) requires
    the same experimental object as the processing (including experiment type/peak symbol
    coverage in the data annotation), so the internal implementation is explicitly exposed to
    avoid a second set of reading logic in the external interface."""
    return _read_experiment(manager, exp_id, data_id)


__all__ = [
    "ImportResult",
    "StepwiseError",
    "generate_fid",
    "generate_spectrum",
    "import_data",
    "read_experiment",
]
