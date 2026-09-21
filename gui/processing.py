"""Processing flow control: two paths: automation and manual. - Step-by-step (contract v1.2 /
G2B-002): ``import_data`` -> ``generate_fid`` -> ``generate_spectrum``, each step has
independent buttons and states; - Manual: ``manual_fid_com`` / ``run_manual_fid_com`` /
``manual_scripts`` / ``run_manual_spectrum`` docking workflow/manual (fid.com and spectrum
script)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from core.project import ExperimentEntry, ProjectManager
from core.project.run_refs import MANUAL_SPECTRUM_RUN_REFS, STEP_RUN_REFS
from core.user_errors import describe_exception
from gui.pipeline_state import record_step_success
from ui_support.i18n import tr

# The key file to determine "whether the subdirectory contains data file" (same origin as
# pipeline_state input fingerprint).
_DATA_KEY_FILES = ('acqus', 'acqu2s', 'acqu3s', 'ser', 'fid', 'nuslist')


def _segment_dirs(root: Path) -> list[Path]:
    """Directly contain the data segment subdirectory of acqus (for segmented import). Only the
    subdirectory containing acqus is recognized as the data segment; subdirectories that only
    have data files such as ser/fid but lack acqus, or have no files at all are ignored (0.2.198
    user rule) -- The selected total file folder does not have acqus at the top level, which is
    a normal form of the container and should not be reported as missing."""
    return sorted(
        p for p in root.iterdir() if p.is_dir() and (p / "acqus").is_file()
    )


def is_segmented_container(path) -> bool:
    """Container directory determination: It is not a Bruker dataset (no acqus at the top level),
    but contains >= 2 segmented subdirectories with acqus directly (used for segmented
    collection and import)."""
    try:
        root = Path(path)
        if not root.is_dir() or (root / "acqus").is_file():
            return False
        return len(_segment_dirs(root)) >= 2
    except OSError:
        return False


def resolve_import_source(path) -> tuple[str, bool]:
    """Parse the import source (2026-08-19 Task F: Ignore non-data sub-file folders). Return
    (data_source, is_segmented): - It is a Bruker dataset (including acqus) -> (path, False); -
    The container contains >= 2 data segments containing acqus directory -> (path, True), adopt
    segmented collection and import; - Exactly 1 data segment containing acqus The data segment
    (The rest of the non-data/lack acqus, ignored) -> (the subdirectory, False); - 0 -> throw
    ImportWorkflowError."""
    from workflow.import_workflow import ImportWorkflowError

    root = Path(path)
    if (root / 'acqus').is_file():
        return str(root), False
    if not root.is_dir():
        raise ImportWorkflowError(tr("directory does not exist: {p0}", p0=root))
    segments = _segment_dirs(root)
    if len(segments) >= 2:
        return str(root), True
    if len(segments) == 1:
        return str(segments[0]), False
    data_subdirs = sorted(
        p
        for p in root.iterdir()
        if p.is_dir() and any((p / name).is_file() for name in _DATA_KEY_FILES)
    )
    if data_subdirs:
        # The subdirectory only has data files but lacks acqus: Ignore non-data file folders, but if
        # they are all like this, they cannot be imported, and a clear prompt is given (do not
        # regard the lack of acqus at the top level as a problem, 0.2.198).
        raise ImportWorkflowError(
            tr(
                "The subdirectories of the selected directory contain data files but none of them "
                "has an acqus, so it cannot be imported as a data set (only a subdirectory "
                "containing acqus counts as a data segment); non-data subdirectories were "
                "ignored",
            )
        )
    raise ImportWorkflowError(
        tr(
            "The selected directory is neither a Bruker data set nor a directory whose "
            "subdirectories contain data files (acqus/acqu2s/acqu3s/ser/fid/nuslist); non-data "
            "subdirectories were "
            "ignored",
        )
    )


class ProcessingController:
    """GUI Layer processing control: three-step process (import_data -> generate_fid ->
    generate_spectrum) docking workflow/stepwise(contract v1.2 §8.3); artificial path docking
    workflow/manual."""

    def __init__(self, manager: ProjectManager | None = None) -> None:
        self._backend = None
        self._manager = manager

    def set_manager(self, manager) -> None:
        """After the project object is replaced, the current ProjectManager is bound (for step-by-
        step calling)."""
        self._manager = manager

    def _backend_instance(self):
        """Lazy creation of ProcessingBackend (configuration reading goes through
        backend.config,0.2.164)."""
        if self._backend is None:
            from backend.config import load_config
            from backend.factory import create_backend

            self._backend = create_backend(load_config())
        return self._backend


    # ------------------------------------------------------------------
    # Step-by-step processing (G2B-002 / Contract v1.2): Import sample data -> Generate FID ->
    # Generate spectrum. The implementation is located in workflow/(import_workflow / stepwise +
    # backend). This controller is responsible for wiring, status registration and parameter
    # assembly.
    # ------------------------------------------------------------------
    def import_data(self, entry: ExperimentEntry, source: str, copy: bool = True) -> dict:
        """Step 1: Import sample data (read-only parameter + copy raw) and return ImportResult
        dict."""
        from workflow.import_workflow import import_data

        if self._manager is None:
            raise RuntimeError(tr("The processing controller is not bound to a project yet"))
        result = import_data(self._manager, entry.id, source, copy=copy)
        data_id = getattr(result, "data_id", "") or ""
        if data_id:
            record_step_success(
                self._manager, entry.id, data_id, "import", params={"copy": copy}
            )
        self._manager.save()
        return {
            "experiment_id": entry.id,
            "data_id": data_id,
            "run_id": getattr(result, "run_id", ""),
            "warnings": list(getattr(result, "warnings", []) or []),
        }

    def import_segmented_dataset(
        self,
        source: str,
        *,
        exp_id: str = "",
        title: str = "",
        sample_id: str = "",
        copy: bool = True,
    ):
        """Segmented collection import transparent transmission (0.2.108/G2B-011): Multiple
        segmented subdirectories containing acqus under the container directory are merged into
        one sample data (backend segment by segment conversion + addNMR merge). exp_id is
        imported to the experiment type when it is not empty (consistent with ordinary single
        import), and when it is empty, the backend creates a new experiment (old behaviour);
        clearly distinguished from batch import (multiple entries). Return ImportResult."""
        from workflow.import_workflow import import_segmented_dataset

        self._require_manager()
        return import_segmented_dataset(
            self._manager,
            source,
            exp_id=exp_id,
            title=title,
            sample_id=sample_id,
            copy=copy,
        )

    def batch_import(
        self, exp_id: str, folders: list, group: bool = True,
        on_progress: Callable[[str, str, bool, str], None] | None = None,
    ) -> dict:
        """Batch import multiple data directories to experiment type; group=True is classified into
        the same data group (group id is batch). group=False is not grouped (equivalent to
        multiple single imports, batch_id is empty); returns {"batch_id", "results": [{folder,
        data_id, ok, error}]}; failure of a single directory does not block the entire batch
        (error information is included in the result, 0.2.162-patch12). 0.2.164-patch1: The data
        group is the only source, no more double-writing pipeline_state marks."""
        self._require_manager()
        if self._manager.project is None:
            raise RuntimeError(tr("The processing controller is not bound to a project yet"))
        entry = self._manager.project.experiment(exp_id)
        if entry is None:
            raise RuntimeError(tr("experiment type does not exist: {p0}", p0=exp_id))
        # 0.2.163: Import the created data group in batches (schema 1.4, the group id is batch);
        # 0.2.164-patch1: The data group is the only source, no more double-writing pipeline_state
        # batch tag.
        batch = ""
        if group:
            batch = self._manager.create_data_group(exp_id).id
        results: list[dict] = []
        for folder in folders:
            item: dict = {
                "folder": str(folder),
                "data_id": "",
                "ok": False,
                "error": "",
            }
            _f = Path(str(folder))
            # 0.2.199-patch29hd: Only 2D spectra are supported in batches -- Before importing 3D
            # data, press raw directory to determine whether it contains acqu3s/acqu3 and skip it
            # directly (not import) to avoid triggering SMILE (unstable host power outage) during
            # batch processing; 2D data is imported according to the original process.
            if group and (
                not (_f / "acqu2s").is_file()
                or (_f / "acqu3s").is_file()
                or (_f / "acqu3").is_file()
            ):
                item["error"] = tr(
                    "Non-2D spectrum, batch only supports 2D for now, has been "
                    "skipped",
                )
                results.append(item)
                if on_progress is not None:
                    on_progress(str(folder), "", False, item["error"])
                continue
            try:
                # 0.2.199-patch29gl: Batch import first verifies the existence of the original data
                # file. If the file is missing, it will be directly marked as failure to avoid
                # "failure only after watching the import"; Incomplete collection/More will not be
                # intercepted here, and the generation steps of FID will be reversed based on the
                # actual data.
                _is_nd = (_f / "acqu2s").is_file() or (_f / "acqu3s").is_file()
                _data_file = "ser" if _is_nd else "fid"
                if not (_f / _data_file).is_file():
                    raise RuntimeError(tr("Missing data file {p0}", p0=_data_file))
                result = self.import_data(entry, str(folder))
                data_id = str(result.get("data_id", "") or "")
                item["data_id"] = data_id
                if data_id and group:
                    self._manager.add_to_group(exp_id, batch, data_id)
                item["ok"] = True
            # A single failure does not block the entire batch.
            except Exception as exc:  # noqa: BLE001 -
                item["error"] = describe_exception(exc)
            results.append(item)
            if on_progress is not None:
                on_progress(
                    str(folder),
                    item.get("data_id", ""),
                    bool(item.get("ok")),
                    item.get("error", ""),
                )
        # Remove empty groups when all failures occur to avoid remaining empty data group nodes.
        if group and not any(item.get("ok") for item in results):
            try:
                self._manager.delete_data_group(exp_id, batch)
            except Exception:  # noqa: BLE001 - Group deletion fails and is not blocked.
                pass
        self._manager.save()
        return {"batch_id": batch if group else "", "results": results}

    def run_group_batch(
        self,
        exp_id: str,
        group_id: str,
        steps: list[str],
        reference_data_id: str = "",
        progress: Callable[[str], None] | None = None,
        params: dict | None = None,
        on_data_done: Callable[[dict], None] | None = None,
    ) -> dict:
        """Perform batch processing (workflow.batch.run_batch) on the data group. steps:
        BATCH_STEPS subset (such as ["fid"] is only processed until FID is generated); when
        reference_data_id is not empty, take the valid parameters of the most recent successful
        spectrum run as the spectrum step parameter base; explicit params coverage refers to
        parameter."""
        from workflow.batch import run_batch

        self._require_manager()
        return run_batch(
            self._manager,
            exp_id,
            group_id,
            steps,
            self._backend_instance(),
            reference_data_id=reference_data_id or None,
            progress=progress,
            params=params,
            on_data_done=on_data_done,
        )

    def generate_fid(
        self,
        data,
        exp_id: str | None = None,
        data_id: str | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> str:
        """Step 2: Generate FID(backend.convert_to_fid) and return the fid path. progress optional
        callback: stage progress (G2B-006; forward the real stage log after the backend is
        implemented)."""
        import inspect

        from workflow.stepwise import generate_fid as stepwise_fid

        if self._manager is None:
            raise RuntimeError(tr("The processing controller is not bound to a project yet"))
        exp_id = exp_id or getattr(data, "exp_id", "")
        data_id = data_id or getattr(data, "id", "")

        def emit(message: str) -> None:
            if progress is not None:
                progress(message)

        emit(tr("bruker Converting(fid.com)"))
        kwargs: dict = {}
        if "progress" in inspect.signature(stepwise_fid).parameters:
            kwargs["progress"] = emit
        fid_path = stepwise_fid(
            self._manager,
            exp_id,
            data_id,
            self._backend_instance(),
            **kwargs,
        )
        emit(tr("Complete FID conversion"))
        if data_id:
            record_step_success(self._manager, exp_id, data_id, "fid")
            self._snapshot_step(
                exp_id,
                data_id,
                ("convert_to_fid",),
                self._fid_com_script(exp_id, data_id),
            )
        self._manager.save()
        return fid_path

    def generate_spectrum(
        self,
        data,
        exp_id: str | None = None,
        data_id: str | None = None,
        progress: Callable[[str], None] | None = None,
        phase_optimize: bool = True,
        params: dict | None = None,
    ) -> str:
        """Step 3: Generate spectrum (process/reconstruct_nus, including NUS SMILE reconstruction).
        From 0.2.146, spectrum is generated, that is, unified automatic processing (dimensional
        complex preview + memory phase modulation + parameter optimisation + complete final run,
        see workflow/phase_routes.unified_route); params can optionally be passed
        phase_route="unified"(default)/"none"(escape hatch). phase_optimize parameter Retained
        for compatibility callers only, no longer overlaying the old per-dimensional brute force
        optimisation (0.2.154 removed -- this branch used to make phase optimisation repeat
        after the final SMILE). progress Optional callback: phase progress."""
        import inspect

        from workflow.stepwise import generate_spectrum as stepwise_spectrum

        if self._manager is None:
            raise RuntimeError(tr("The processing controller is not bound to a project yet"))
        exp_id = exp_id or getattr(data, "exp_id", "")
        data_id = data_id or getattr(data, "id", "")

        def emit(message: str) -> None:
            if progress is not None:
                progress(message)

        emit(tr("Read data and prepare for processing"))
        linewidth_by_axis: dict[str, float] | None = None
        try:
            experiment = self._read_experiment(exp_id, data_id)
            # 0.2.112: Software setting "line width" access (nuclide -> axis mapping, explicit
            # params takes precedence).
            linewidth_by_axis = self._linewidth_by_axis(experiment)
            from core.data.internal_data_model import SamplingMode

            if experiment.sampling.mode is SamplingMode.NUS:
                emit(tr("NUS Data: Start SMILE reconstruction (including direct dimension phase)"))
            else:
                emit(
                    tr(
                    "Uniform sampling: Start NMRPipe processing (including direct dimension "
                    "phase)",
                )
                )
        # Sampling information is not available for general prompts.
        except Exception:  # noqa: BLE001 -
            emit(tr("backend running (conversion / reconstruction / phase optimisation)"))
        params = dict(params or {})
        if linewidth_by_axis is not None and "linewidth_hz" not in params:
            params["linewidth_hz"] = linewidth_by_axis
        kwargs: dict = {}
        if params:
            kwargs["params"] = dict(params)
        if "progress" in inspect.signature(stepwise_spectrum).parameters:
            kwargs["progress"] = emit
        spectrum_path = stepwise_spectrum(
            self._manager,
            exp_id,
            data_id,
            self._backend_instance(),
            **kwargs,
        )
        # 0.2.154: Generate spectrum, that is, unified automatic processing (0.2.146 removes the
        # path drop-down, params has no phase_route, and the old branch once triggered the
        # dimension-by-dimensional violent phase optimisation again, causing phase optimisation to
        # run to the final SMILE and then be executed repeatedly) -- no post-phase optimisation will
        # be superimposed.
        route = (params or {}).get("phase_route")
        label = {"unified": tr(
            "Unified automatic "
            "processing",
        ), "none": tr(
            "None(escape "
            "hatch)",
        )}.get(
            str(route), route or tr("Unified automatic processing")
        )
        emit(tr("Spectrum generation is completed, phase route: {p0}", p0=label))
        if data_id:
            record_step_success(self._manager, exp_id, data_id, "spectrum")
            # Repair 24: Unify the use of STEP_RUN_REFS["spectrum"] (including
            # phase_optimize_unified, originally only process/reconstruct_nus was recognized ->
            # snapshots can never be matched under the unified route).
            self._snapshot_step(
                exp_id,
                data_id,
                STEP_RUN_REFS["spectrum"],
                self._spectrum_scripts(exp_id, data_id),
            )
        self._manager.save()
        return spectrum_path

    # ------------------------------------------------------------------
    # Peak Picking/Analysis (G2B-004).
    # ------------------------------------------------------------------
    def pick_peaks(
        self,
        data,
        exp_id: str | None = None,
        data_id: str | None = None,
        sigma_multiplier: float | None = None,
        ref_peaks: list[dict] | None = None,
        ref_nuclei: list[str] | None = None,
        tolerance_ppm: dict[str, float] | None = None,
        ref_name: str = "",
        localization_method: str = "parabolic",
        gaussian_roi_f1_ppm: float | None = None,
        gaussian_roi_f2_ppm: float | None = None,
    ) -> dict:
        """Peak picking: adjust workflow.pick_peaks, return {status, peak_path, peak_count, logs}.
        ``localization_method``(2026-09-13): ``parabolic`` (default, existing behaviour) or
        ``gaussian`` (2D Gaussian fit, 2D only); ROI is ppm physical width, read config
        ``peaks.localization`` when ``None``."""
        try:
            from workflow.pick_peaks import pick_peaks as backend_pick_peaks
        except ImportError as exc:  # pragma: no cover - Backend Not yet landed.
            raise NotImplementedError(
                tr(
                "Peak picking (workflow.pick_peaks) is to be implemented by "
                "Backend",
            )
            ) from exc
        if self._manager is None:
            raise RuntimeError(tr("The processing controller is not bound to a project yet"))
        exp_id = exp_id or getattr(data, "exp_id", "")
        data_id = data_id or getattr(data, "id", "")
        result = backend_pick_peaks(
            self._manager, exp_id, data_id, sigma_multiplier=sigma_multiplier,
            ref_peaks=ref_peaks, ref_nuclei=ref_nuclei,
            tolerance_ppm=tolerance_ppm,
            ref_name=ref_name,
            localization_method=localization_method,
            gaussian_roi_f1_ppm=gaussian_roi_f1_ppm,
            gaussian_roi_f2_ppm=gaussian_roi_f2_ppm,
        )
        if data_id and result.get("status") == "success":
            record_step_success(self._manager, exp_id, data_id, "peaks")
        self._manager.save()
        return result

    # Artificial path (workflow/manual wiring).
    # ------------------------------------------------------------------
    def manual_fid_com(self, data, exp_id: str | None = None, data_id: str | None = None) -> str:
        """Get/generate fid.com Content (for viewing and modification)."""
        from workflow.manual import manual_fid_com as backend_manual_fid_com

        self._require_manager()
        exp_id = exp_id or getattr(data, "exp_id", "")
        data_id = data_id or getattr(data, "id", "")
        return backend_manual_fid_com(
            self._manager, exp_id, data_id, self._backend_instance()
        )

    def run_manual_fid_com(
        self,
        data,
        content: str,
        exp_id: str | None = None,
        data_id: str | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> str:
        """Write and run the modified fid.com."""
        from workflow.manual import run_manual_fid_com as backend_run_fid

        self._require_manager()
        exp_id = exp_id or getattr(data, "exp_id", "")
        data_id = data_id or getattr(data, "id", "")
        result = backend_run_fid(
            self._manager,
            exp_id,
            data_id,
            content,
            backend=self._backend_instance(),
            progress=progress,
        )
        if data_id:
            record_step_success(self._manager, exp_id, data_id, "fid")
            self._snapshot_step(
                exp_id, data_id, ("manual_fid",), {"fid.com": content}
            )
        self._manager.save()
        return result

    def manual_scripts(
        self,
        data,
        params: dict | None = None,
        exp_id: str | None = None,
        data_id: str | None = None,
    ) -> dict:
        """Render spectrum step script (process.com/nus*.com)."""
        from workflow.manual import manual_scripts as backend_manual_scripts

        self._require_manager()
        exp_id = exp_id or getattr(data, "exp_id", "")
        data_id = data_id or getattr(data, "id", "")
        return backend_manual_scripts(
            self._manager, exp_id, data_id, params=params
        )

    def run_manual_spectrum(
        self,
        data,
        scripts: dict,
        exp_id: str | None = None,
        data_id: str | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> str:
        """Run spectrum script (consume converted fid)."""
        from workflow.manual import run_manual_spectrum as backend_run_spectrum

        self._require_manager()
        exp_id = exp_id or getattr(data, "exp_id", "")
        data_id = data_id or getattr(data, "id", "")
        result = backend_run_spectrum(
            self._manager, exp_id, data_id, scripts, progress=progress
        )
        if data_id:
            record_step_success(self._manager, exp_id, data_id, "spectrum")
            self._snapshot_step(
                exp_id, data_id, MANUAL_SPECTRUM_RUN_REFS, scripts
            )
        self._manager.save()
        return result

    def save_peaks_manual(
        self,
        data,
        peaks: list[dict],
        exp_id: str | None = None,
        data_id: str | None = None,
        *,
        nuclei: list[str] | None = None,
    ) -> str:
        """Save artificial peak table: write Poky.list (peak table file, i.e. list) and register to
        run."""
        from gui.peaks_io import export_peaks_poky

        self._require_manager()
        exp_id = exp_id or getattr(data, "exp_id", "")
        data_id = data_id or getattr(data, "id", "")
        ndim = 3 if peaks and "F1_shift" in peaks[0] else 2
        peaks_dir = self._manager.data_dir(exp_id, data_id, "peaks")
        peaks_dir.mkdir(parents=True, exist_ok=True)
        target_path = peaks_dir / f"{exp_id}-{data_id}.list"
        # Manual addition and deletion/move/Importing Summit makes the row order and coordinates of
        # automatic positioning attachments invalid.. Delete the derived diagnosis first, and then
        # overwrite the main peak table; if the attachment is occupied and cannot be deleted, it
        # will be aborted to avoid leaving false matches.
        from core.peaks.localize import localization_records_path

        localization_records_path(target_path).unlink(missing_ok=True)
        list_path = export_peaks_poky(
            target_path, peaks,
            ndim=ndim, nuclei=nuclei,
        )
        if data_id:
            record_step_success(self._manager, exp_id, data_id, "peaks")
        run = self._manager.start_run(
            exp_id,
            workflow_ref="manual_peaks",
            inputs={"data_id": data_id},
            params={"mode": "manual", "peaks": len(peaks), "format": "list"},
        )
        try:
            self._manager.finish_run(
                run.run_id, "success", outputs={"peaks": str(list_path)},
                message=tr("Manual peak table editing ({p0} peak)", p0=len(peaks)),
            )
        except Exception:  # noqa: BLE001
            self._manager.finish_run(run.run_id, "failed", message=tr("peak table save failed"))
        self._manager.save()
        return str(list_path)

    def data_facts(self, exp_id: str, data_id: str) -> dict[str, object]:
        """The reading fact of this data: ndim / direct_nucleus / is_nus. 0.2.199-patch29hz:GUI
        gate (such as Pipeline's SMILE/Peak steps visible and hidden, direct dimension range
        gear) originally adjusted private _read_experiment and cached separately, and the
        caliber is easy to fork; it is taken from here. If the reading fails, an empty dict is
        returned, and the caller downgrades by default."""
        try:
            experiment = self._read_experiment(exp_id, data_id)
        except Exception:  # noqa: BLE001 - Read failure is downgraded by the caller.
            return {}
        from core.data.internal_data_model import SamplingMode

        dim = getattr(experiment, "direct_dimension", None)
        sampling = getattr(experiment, "sampling", None)
        mode = getattr(sampling, "mode", None)
        return {
            "ndim": int(getattr(experiment, "ndim", 2) or 2),
            "direct_nucleus": str(getattr(dim, "nucleus", "") or ""),
            "is_nus": mode is SamplingMode.NUS,
            "sampling_mode": str(getattr(mode, "value", "") or ""),
        }

    def _read_experiment(self, exp_id: str, data_id: str):
        """Read data corresponding to Experiment (reuse workflow.stepwise unified implementation,
        0.2.164)."""
        from workflow.stepwise import _read_experiment as _read

        return _read(self._manager, exp_id, data_id)

    def _linewidth_by_axis(self, experiment) -> dict[str, float]:
        """The software sets "linewidth" (nuclide -> Hz) -> axis mapping (generates spectrum
        params, 0.2.112). The backend params["linewidth_hz"] takes the value according to the
        axis (logical_axis); nuclides not configured in the settings are set to 0, and the
        backend falls back to the nuclide default table."""
        from gui.settings import load_settings

        settings = load_settings()
        lw = settings.get("linewidth_hz") or {}
        mapping: dict[str, float] = {}
        for dim in getattr(experiment, "dimensions", None) or []:
            axis = str(getattr(dim, "logical_axis", "") or "").strip()
            nucleus = str(getattr(dim, "nucleus", "") or "").strip()
            try:
                value = float(lw.get(nucleus) or 0.0)
            except (TypeError, ValueError):
                value = 0.0
            if axis:
                mapping[axis] = value
        return mapping

    def _last_spectrum_params(self, exp_id: str, data_id: str) -> dict:
        """The latest successfully generated spectrum running parameter (as SMILE optimisation base
        parameter). 0.2.199-patch29hz-Xiu18(user): Originally only `process`/`reconstruct_nus`
        was recognized, and the normal unified route was registered as `phase_optimize_unified`
        (the artificial path is manual_process/manual_nus) -- So the base parameter of SMILE
        optimisation is always empty**, and the template returns to the default window / phase /
        baseline: neither equals The final script (violating "use the final script as the
        template and only change the SMILE parameter") will estimate the memory according to the
        default wide window and falsely report "insufficient memory". Change to reuse
        `gui.pipeline_state.STEP_RUN_REFS["spectrum"]` (single source), data_id strict
        attribution (same caliber as repair 1)."""
        from core.project.run_refs import STEP_RUN_REFS

        refs = STEP_RUN_REFS.get("spectrum", ())
        for run in reversed(self._manager.project.workflow_runs):
            if (
                run.experiment_id == exp_id
                and run.workflow_ref in refs
                and str((run.inputs or {}).get("data_id", "")) == data_id
                and run.status == "success"
            ):
                return dict(run.params or {})
        return {}

    def optimize_smile(
        self,
        data,
        exp_id=None,
        data_id=None,
        progress: Callable[[str], None] | None = None,
        grid_size: int | None = None,
        rank_mode: str | None = None,
    ) -> str:
        """SMILE optimisation (optional): Use the final script as the template and only replace
        SMILE parameters for scanning. grid_size: optimisation degree 2..5 (2x2..5x5); Read the
        data when None ui_state, and then use 4x4 (16 groups) by default. 0.2.199-patch29hz -
        Modify 3 (user plan): run direct dimension once to get slices -> run once for each group
        of parameters "SMILE + indirect dimension" gets the final spectrum -> evaluate the
        indicator immediately -> delete the spectrum (the candidate spectrum only exists in the
        memory disk temporarily). Finally, write the parameter combination sorting table + the
        top three scripts, **do not replace the active spectrum** (Option B; to use the optimal
        parameter to generate the spectrum, please click "Rerun according to Rank1")."""
        from backend import memory_disk
        from core.data.internal_data_model import SamplingMode
        from workflow.smile_optimize import (
            scan_smile_parameters,
            write_smile_scan_output,
        )

        self._require_manager()
        exp_id = exp_id or getattr(data, "exp_id", "")
        data_id = data_id or getattr(data, "id", "")
        experiment = self._read_experiment(exp_id, data_id)
        if experiment.sampling.mode is not SamplingMode.NUS:
            raise RuntimeError(
                tr(
                "SMILE optimisation only works on NUS data (currently uniformly "
                "sampled)",
            )
            )
        if int(getattr(experiment, "ndim", 2) or 2) != 2:
            # 0.2.199-patch29hz-Xiu21(user): The SMILE optimisation of 3D NUS is not ideal for the
            # time being, and the entrance is hidden.
            raise RuntimeError(
                tr(
                "SMILE optimisation currently only supports 2D NUS (3D NUS is not available "
                "yet)",
            )
            )
        base_params = self._last_spectrum_params(exp_id, data_id)
        def _smile_progress(index: int, total: int, msg: str) -> None:
            if progress is not None:
                progress(msg)

        if grid_size is None:
            try:
                from gui.per_data_records import load_ui_state

                grid_size = int(
                    (load_ui_state(self._manager, exp_id, data_id).get("smile") or {})
                    .get("grid_size", 4)
                )
            except Exception:  # noqa: BLE001 - Can't read, use default.
                grid_size = 4
        if rank_mode is None:
            try:
                from gui.per_data_records import load_ui_state

                rank_mode = str(
                    (load_ui_state(self._manager, exp_id, data_id).get("smile") or {})
                    .get("rank_mode", "true_peaks")
                )
            except Exception:  # noqa: BLE001 - Can't read, use default.
                rank_mode = "true_peaks"
        work = self._manager.data_dir(exp_id, data_id, "process")
        work.mkdir(parents=True, exist_ok=True)
        # Candidate spectrum will be deleted immediately after evaluation: the intermediate
        # directory will be placed on the memory disk first.
        intermediate_root, memory_dir = memory_disk.prepare_intermediate(
            work, experiment, params=base_params
        )
        scan_dir = Path(intermediate_root) / "smile_scan"
        try:
            result = scan_smile_parameters(
                experiment,
                self._backend_instance(),
                base_params,
                scan_dir=scan_dir,
                grid_size=int(grid_size or 4),
                rank_mode=str(rank_mode or "true_peaks"),
                progress=_smile_progress,
            )
        finally:
            memory_disk.teardown_intermediate(work, memory_dir)
        rows = list(result["rows"])
        top = rows[:3]
        paths = write_smile_scan_output(
            self._manager, exp_id, data_id, rows, result["scripts"]
        )
        # Plan B (user 2026-09-10): Do not replace the activity spectrum, only produce the sorting
        # table + the top three scripts.
        run = self._manager.start_run(
            exp_id,
            workflow_ref="smile_optimize",
            inputs={"data_id": data_id},
            params={"ranking": top, "n_combos": int(result["n_combos"])},
        )
        self._manager.finish_run(
            run.run_id,
            "success",
            outputs=dict(paths),
            message=tr("SMILE parameter scan: ")
            + ", ".join(
                tr(
                    "Rank{p0} nSigma={p1:g}/thresh={p2:g}(stable peaks "
                    "{p3})",
                    p0=r["rank"],
                    p1=r["nsigma"],
                    p2=r["thresh"],
                    p3=r["stable_count"],
                )
                for r in top
            ),
        )
        if data_id:
            record_step_success(self._manager, exp_id, data_id, "smile")
        self._manager.save()
        summary = "; ".join(
            tr(
                "Rank{p0}: nSigma={p1:g} thresh={p2:g} stable peaks {p3} mean S/N {p4:.1f} quality "
                "{p5:.1f}",
                p0=r["rank"],
                p1=r["nsigma"],
                p2=r["thresh"],
                p3=r["stable_count"],
                p4=r["mean_snr"],
                p5=r["quality"],
            )
            for r in top
        )
        return (
            tr(
                "{p0} Group scan completed (delete after candidate spectrum has been "
                "evaluated);{p1}; ranking table "
                "{p2}",
                p0=result['n_combos'],
                p1=summary,
                p2=paths['csv'],
            )
        )

    def rerun_smile_rank1(
        self,
        exp_id: str,
        data_id: str,
        progress: Callable[[str], None] | None = None,
    ) -> str:
        """Run and use SMILE Rank1, and refresh all companion products and traceability."""
        import json

        from backend.runtime import CshRuntime
        from workflow.optimization_report import (
            spectrum_quality_report_lines,
            write_quality_record,
        )
        from workflow.ucsf_export import export_ucsf

        self._require_manager()
        proc = self._manager.data_dir(exp_id, data_id, "process")
        script = proc / f"{data_id}_nus_rank1.com"
        if not script.is_file():
            raise RuntimeError(tr("Rank1 script not found, please run SMILE optimisation first"))
        experiment = self._read_experiment(exp_id, data_id)
        ext = {1: "ft1", 2: "ft2"}.get(experiment.ndim, "ft3")

        ranking_path = (
            self._manager.data_dir(exp_id, data_id, "smile_optimized")
            / f"{exp_id}-{data_id}_smile_ranking.json"
        )
        rank_params: dict = {}
        try:
            payload = json.loads(ranking_path.read_text(encoding="utf-8"))
            rank_params = next(
                (
                    dict(row)
                    for row in payload.get("rows", [])
                    if int(row.get("rank", 0) or 0) == 1
                ),
                {},
            )
        except (OSError, ValueError, TypeError):
            rank_params = {}

        run_params = self._last_spectrum_params(exp_id, data_id)
        run_params["smile_rank"] = 1
        run_params["smile_candidate"] = rank_params
        run = self._manager.start_run(
            exp_id,
            workflow_ref="smile_optimize_rank1",
            inputs={
                "data_id": data_id,
                "script": str(script),
                "ranking_json": str(ranking_path) if ranking_path.is_file() else "",
            },
            params=run_params,
        )
        try:
            self._manager.snapshot_run(
                run.run_id,
                {script.name: script.read_text(encoding="utf-8", errors="replace")},
                params=run_params,
            )
            if progress is not None:
                progress(tr("Press Rank1 to rerun the script: {p0}", p0=script.name))
            run_result = CshRuntime().run(
                ["csh", script.name], cwd=str(proc), timeout=7200.0
            )
            produced = proc / f"{data_id}.{ext}"
            if (
                run_result.returncode != 0
                or not produced.is_file()
                or produced.stat().st_size == 0
            ):
                raise RuntimeError(
                    tr(
                        "Rank1 rerun failed (rc={p0}), not generated "
                        "{p1}",
                        p0=run_result.returncode,
                        p1=produced.name,
                    )
                )

            spectra = self._manager.data_dir(exp_id, data_id, "spectra")
            spectra.mkdir(parents=True, exist_ok=True)
            target = spectra / produced.name
            if produced.resolve() != target.resolve():
                produced.replace(target)
            self._manager.set_data_spectrum(exp_id, data_id, target)

            ucsf_target = spectra / f"{target.stem}.ucsf"
            ucsf_path, ucsf_message = export_ucsf(target, ucsf_target)
            if progress is not None:
                progress(ucsf_message)

            quality_path = Path(f"{target}.quality.json")
            quality_path.unlink(missing_ok=True)
            try:
                logs = str(run_result.stdout or "").splitlines()
                quality_lines = spectrum_quality_report_lines(
                    str(target), optimization_logs=logs, progress=progress
                )
                if quality_lines:
                    write_quality_record(
                        str(target), run_params, "\n".join(quality_lines)
                    )
            # Cache failure is not revoked and is valid final spectrum.
            except Exception as exc:  # noqa: BLE001 - QC
                if progress is not None:
                    progress(tr(
                        "Rank1 quality record generation failed and has been skipped: "
                        "{p0}",
                        p0=exc,
                    ))

            outputs = {"spectrum_path": str(target)}
            if ucsf_path:
                outputs["ucsf_path"] = str(ucsf_path)
            if quality_path.is_file():
                outputs["quality_record"] = str(quality_path)
            self._manager.finish_run(
                run.run_id,
                "success",
                outputs=outputs,
                message=(
                    tr(
                    "Press Rank1 (SMILE to scan the optimal parameters) and rerun the final "
                    "spectrum and refresh the companion "
                    "products",
                )
                ),
            )
            record_step_success(self._manager, exp_id, data_id, "spectrum")
            self._manager.save()
            return str(target)
        except Exception as exc:
            if run.status == "running":
                self._manager.finish_run(
                    run.run_id, "failed", message=tr("Rank1 rerun failed: {p0}", p0=exc)
                )
                self._manager.save()
            raise


    # ------------------------------------------------------------------
    # Script snapshot (GUI wiring): after the step is successful, write the executed script /
    # parameter into WorkflowRun.
    # ------------------------------------------------------------------
    def _snapshot_step(
        self,
        exp_id: str,
        data_id: str,
        workflow_refs: tuple[str, ...],
        scripts: dict[str, str],
    ) -> str:
        """Add the WorkflowRun of the latest matching step to the script snapshot (snapshot_run).
        Return to the snapshot directory (an empty string indicates no matching run or has been
        snapshotted). The back-end step only registers the run and does not drop the script; GUI
        Here, write the actual executed fid.com/process.com/nus*.com and parameter into
        run.snapshot_dir to ensure reproducibility (Contract §2)."""
        if self._manager is None or self._manager.project is None:
            return ""
        run = None
        for candidate in reversed(self._manager.project.workflow_runs):
            if candidate.experiment_id != exp_id:
                continue
            if candidate.workflow_ref not in workflow_refs:
                continue
            recorded_data = (candidate.inputs or {}).get("data_id", "")
            if recorded_data and recorded_data != data_id:
                continue
            run = candidate
            break
        if run is None or run.snapshot_dir:
            return ""
        try:
            snapshot = self._manager.snapshot_run(
                run.run_id, dict(scripts or {}), params=dict(run.params or {})
            )
            return str(snapshot)
        except Exception:  # noqa: BLE001 - Snapshot failure does not block processing.
            return ""

    def _fid_com_script(self, exp_id: str, data_id: str) -> dict[str, str]:
        """Read fid.com(0.2.91 under process/; old data falls back to raw/)."""
        if self._manager is None:
            return {}
        try:
            entry = self._manager.data(exp_id, data_id)
        except Exception:  # noqa: BLE001
            return {}
        raw = (
            Path(entry.raw_dir)
            if getattr(entry, "raw_dir", "")
            else Path(entry.source)
        )
        if not raw.is_absolute():
            raw = self._manager.root / raw
        if not raw.is_dir():
            # Schema 1.3 data level raw directory fallback (when registration is missing).
            raw = self._manager.data_dir(exp_id, data_id, "raw")
        fid_com = self._manager.data_dir(exp_id, data_id, "process") / "fid.com"
        if not fid_com.is_file():
            fid_com = raw / "fid.com"
        if fid_com.is_file():
            return {
                "fid.com": fid_com.read_text(
                    encoding="utf-8", errors="replace"
                )
            }
        return {}

    def _spectrum_scripts(self, exp_id: str, data_id: str) -> dict[str, str]:
        """Read the spectrum script (process.com/nus*.com, excluding fid.com) under the process
        directory."""
        scripts: dict[str, str] = {}
        if self._manager is None:
            return scripts
        proc = self._manager.data_dir(exp_id, data_id, "process")
        try:
            paths = sorted(proc.glob("*.com"))
        except OSError:
            return scripts
        for path in paths:
            if path.name != "fid.com":
                scripts[path.name] = path.read_text(
                    encoding="utf-8", errors="replace"
                )
        return scripts


    def _require_manager(self) -> None:
        if self._manager is None:
            raise RuntimeError(tr("The processing controller is not bound to a project yet"))
