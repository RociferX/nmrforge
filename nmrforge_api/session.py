"""Study session: one directory is one study (condition datasets, references, workflows).

Directory layout (all paths relative to the study root ``root``)::

    root/
      project.json            the NMRForge project (registers data and runs)
      <exp_id>/<data_id>/     project data (raw/ process/ spectra/ peaks/ ...)
      study/
        study.json            study state (condition datasets, reference summaries)
        work/                 working directory (shared fid + per-run scripts/candidates)
        reference/<key>/      frozen reference spectrum/script, peak tables, reference.json
        workflows/W0001/      one directory per parameter combination
            workflow.json     combination-level record (parameters, status, versions)
            log.txt           full combination-level log
            <condition>/      one subdirectory per condition (A/B)
                process.com   the processing script that actually ran for this condition
                spectrum.ft2  this condition's candidate spectrum (never the active one)
                peak_table_<method>.csv
                log.txt      full run log for this condition
                run.json     full provenance record for this condition
        records/              summary artefacts (manifest/workflows/runs/long-form table)

Design constraints (fully decoupled from the GUI):

- no Qt import; runs headless and on a cluster;
- nothing outside the active spectrum is modified; candidates stay inside study/,
  never replacing the active spectrum under ``spectra/``;
- datasets are read-only: import goes through ``workflow.import_workflow.import_data``
  (including the Kinetics and other policy guards), never around them;
- several conditions (A/B): each gets its own reference (phase and noise optimised on its
  own data), while peak identity and the user parameter set are **shared** - CSP needs both.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.project import ProjectManager
from core.project.manager import atomic_write_text
from core.version import software_version, tool_versions
from nmrforge_api.errors import DatasetError
from ui_support.i18n import tr

STUDY_DIRNAME = "study"
STUDY_STATE_FILENAME = "study.json"
API_VERSION = "0.2"
#: order in which condition labels are assigned (A/B/C...)
CONDITION_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def condition_token(label: str, fallback: str = "condition") -> str:
    """Condition label -> a unique, directory-safe token.

    Already-safe ASCII labels are left alone; a sanitised label gets a short hash of the original,
    so ``A/B`` and ``A_B``, or two non-ASCII labels, cannot land in one directory.
    """
    raw = str(label or "")
    if re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z_.-]*", raw):
        return raw
    if not raw:
        return condition_token(str(fallback or "condition"), "condition")
    stem = re.sub(r"[^0-9A-Za-z_.-]+", "_", raw).strip("_.") or "condition"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]
    return f"{stem[:48]}-{digest}"


@dataclass
class DatasetRef:
    """A reference to one dataset imported into the study (optionally tagged A/B)."""

    exp_id: str
    data_id: str
    title: str = ""
    condition: str = ""
    ndim: int = 2
    nuclei: list[str] = field(default_factory=list)
    sampling: str = "uniform"
    source: str = ""
    raw_dir: str = ""
    file_count: int = 0
    total_bytes: int = 0

    @property
    def key(self) -> str:
        return f"{self.exp_id}/{self.data_id}"

    @property
    def token(self) -> str:
        """Subdirectory name for this condition (falls back to the data key when untagged)."""
        return condition_token(
            self.condition, fallback=f"{self.exp_id}_{self.data_id}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "exp_id": self.exp_id,
            "data_id": self.data_id,
            "title": self.title,
            "condition": self.condition,
            "ndim": int(self.ndim),
            "nuclei": list(self.nuclei),
            "sampling": self.sampling,
            "source": self.source,
            "raw_dir": self.raw_dir,
            "file_count": int(self.file_count),
            "total_bytes": int(self.total_bytes),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DatasetRef:
        return cls(
            exp_id=str(data.get("exp_id", "")),
            data_id=str(data.get("data_id", "")),
            title=str(data.get("title", "")),
            condition=str(data.get("condition", "")),
            ndim=int(data.get("ndim", 2) or 2),
            nuclei=[str(n) for n in (data.get("nuclei") or [])],
            sampling=str(data.get("sampling", "uniform")),
            source=str(data.get("source", "")),
            raw_dir=str(data.get("raw_dir", "")),
            file_count=int(data.get("file_count", 0) or 0),
            total_bytes=int(data.get("total_bytes", 0) or 0),
        )


@dataclass
class StudySession:
    """Study session handle: project, backend and study directory (may hold several conditions)."""

    root: Path
    manager: ProjectManager
    backend: Any
    datasets: list[DatasetRef] = field(default_factory=list)
    created: str = field(default_factory=now_iso)

    # ---- datasets (conditions) ---------------------------------------
    @property
    def dataset(self) -> DatasetRef | None:
        """The primary (first) condition's dataset; keeps single-dataset callers working."""
        return self.datasets[0] if self.datasets else None

    @dataset.setter
    def dataset(self, value: DatasetRef | None) -> None:
        if value is None:
            self.datasets = []
        elif not self.datasets:
            self.datasets = [value]
        else:
            self.datasets[0] = value

    @property
    def conditions(self) -> list[str]:
        return [ref.condition for ref in self.datasets]

    def dataset_by_condition(self, label: str) -> DatasetRef | None:
        wanted = str(label or "")
        for ref in self.datasets:
            if ref.condition == wanted:
                return ref
        return None

    def add_dataset_ref(self, ref: DatasetRef) -> DatasetRef:
        """Register an already-imported dataset reference (condition labels are unique)."""
        if ref.condition and self.dataset_by_condition(ref.condition) is not None:
            raise DatasetError(tr("duplicate condition label: {p0}", p0=ref.condition))
        if any(existing.key == ref.key for existing in self.datasets):
            raise DatasetError(tr("dataset already registered: {p0}", p0=ref.key))
        _validate_dataset_tokens([*self.datasets, ref])
        self.datasets.append(ref)
        self.save_state()
        return ref

    def next_condition_label(self) -> str:
        used = {ref.condition for ref in self.datasets}
        for letter in CONDITION_LETTERS:
            if letter not in used:
                return letter
        return f"C{len(self.datasets) + 1}"

    # ---- directories -------------------------------------------------
    @property
    def study_dir(self) -> Path:
        return self.root / STUDY_DIRNAME

    @property
    def state_path(self) -> Path:
        return self.study_dir / STUDY_STATE_FILENAME

    @property
    def work_dir(self) -> Path:
        return self.study_dir / "work"

    @property
    def reference_dir(self) -> Path:
        return self.study_dir / "reference"

    @property
    def workflows_dir(self) -> Path:
        return self.study_dir / "workflows"

    @property
    def runs_dir(self) -> Path:
        """Legacy alias: where each workflow's directory lives."""
        return self.workflows_dir

    @property
    def records_dir(self) -> Path:
        return self.study_dir / "records"

    def ensure_dirs(self) -> None:
        for path in (
            self.study_dir,
            self.work_dir,
            self.reference_dir,
            self.workflows_dir,
            self.records_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def reference_dir_for(self, dataset: DatasetRef | None = None) -> Path:
        ref = dataset or self.dataset
        if ref is None:
            raise DatasetError(tr("this study has no dataset yet; call add_dataset() first"))
        safe = ref.key.replace("/", "_")
        path = self.reference_dir / safe
        path.mkdir(parents=True, exist_ok=True)
        return path

    # ---- state -------------------------------------------------------
    def save_state(self, **extra: Any) -> Path:
        self.ensure_dirs()
        state = {
            "api_version": API_VERSION,
            "nmrforge_version": software_version(),
            "created": self.created,
            "updated": now_iso(),
            "root": str(self.root),
            "datasets": [ref.to_dict() for ref in self.datasets],
            # legacy read compatibility: the first condition is still "dataset"
            "dataset": self.dataset.to_dict() if self.dataset else None,
        }
        state.update(extra)
        atomic_write_text(
            self.state_path,
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        )
        return self.state_path

    def load_state(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def data_entry(self, dataset: DatasetRef | None = None) -> Any:
        ref = dataset or self.dataset
        if ref is None:
            raise DatasetError(tr("this study has no dataset yet; call add_dataset() first"))
        return self.manager.data(ref.exp_id, ref.data_id)

    def save(self) -> None:
        self.manager.save()
        self.save_state()


def _datasets_from_state(state: dict[str, Any]) -> list[DatasetRef]:
    """Study state -> condition list (tolerates the older state that only had ``dataset``)."""
    raw = state.get("datasets")
    if isinstance(raw, list) and raw:
        refs = [DatasetRef.from_dict(item) for item in raw if isinstance(item, dict)]
        return [ref for ref in refs if ref.data_id]
    legacy = state.get("dataset")
    if isinstance(legacy, dict) and legacy.get("data_id"):
        ref = DatasetRef.from_dict(legacy)
        if not ref.condition:
            ref.condition = "A"
        return [ref]
    return []


def _validate_dataset_tokens(refs: list[DatasetRef]) -> None:
    """Reject labels that would share a directory on a case-insensitive filesystem."""
    seen: dict[str, DatasetRef] = {}
    for ref in refs:
        key = ref.token.casefold()
        previous = seen.get(key)
        if previous is not None and previous.key != ref.key:
            raise DatasetError(
                tr(
                    "condition directory token collision: {p0!r} and {p1!r} both map to "
                    "{p2!r}",
                    p0=previous.condition or previous.key,
                    p1=ref.condition or ref.key,
                    p2=ref.token,
                )
            )
        seen[key] = ref


def open_study(
    root: Path | str,
    *,
    name: str = "",
    backend: Any | None = None,
    config: dict[str, Any] | None = None,
    create: bool = True,
) -> StudySession:
    """Open or create a study (the study root is the NMRForge project root).

    - an existing ``root/project.json`` is opened; otherwise created when ``create=True``;
    - an explicit ``backend`` is used as given (tests and clusters inject one); otherwise an
      NMRPipe backend is built from the configuration (``backend.factory.create_backend``);
    - study state (the condition list) is restored from ``study/study.json``.

    Parameters
    ----------
    root : Path | str
        the study root; created when missing and ``create=True``.
    name : str, optional
        name recorded when creating a study (ignored for an existing one).
    backend : Any, optional
        the processing backend; defaults to NMRPipe and is injectable in tests.
    config : dict[str, Any], optional
        configuration overrides for this session (never written to disk).
    create : bool, default True
        when False, a missing directory is an error rather than being created implicitly.

    Returns
    -------
    StudySession
        a session: ``manager`` (the project), ``datasets`` (conditions), ``root``.

    Raises
    ------
    DatasetError
        the directory is missing with ``create=False``, or is not a valid study root.

    Side effects
    ------------
    Creating a study builds and writes ``study.json``/``records/``/``workflows/``;
    it neither imports data nor runs processing.

    Examples
    --------
    Minimal usage (see ``examples/quickstart.py`` for a full walkthrough)::

        study = open_study("study/", name="demo")
        add_dataset(study, "path/to/bruker/dataset")
    """
    root_path = Path(root).expanduser().resolve()
    project_file = root_path / "project.json"
    if project_file.is_file():
        manager = ProjectManager.open_project(root_path)
    elif create:
        manager = ProjectManager.create_project(root_path, name or root_path.name)
    else:
        raise DatasetError(tr(
            "study root does not exist or is not an NMRForge project: "
            "{p0}",
            p0=root_path,
        ))
    if backend is None:
        from backend.config import load_config
        from backend.factory import create_backend

        backend = create_backend(load_config(config))
    session = StudySession(root=root_path, manager=manager, backend=backend)
    session.datasets = _datasets_from_state(session.load_state())
    _validate_dataset_tokens(session.datasets)
    session.ensure_dirs()
    return session


def add_dataset(
    session: StudySession,
    source: Path | str,
    *,
    condition: str = "",
    exp_id: str = "",
    title: str = "",
    make_default: bool = True,
) -> DatasetRef:
    """Import a raw Bruker dataset and register it with the study (optionally tagged A/B).

    Import only (raw link, metadata, import run record); no conversion or processing.
    Failures are wrapped in :class:`DatasetError` with the original text kept.
    ``condition`` defaults to the next unused letter (A/B/C...); a multi-condition study uses
    different labels for the two datasets of one workflow (A_raw -> W0037 -> A_peak_table).
    ``make_default`` only decides the primary condition for the first dataset in a session.

    Parameters
    ----------
    session : StudySession
        a session returned by :func:`open_study`.
    source : Path | str
        a Bruker data directory (with ``acqus``); imported read-only, originals untouched.
    condition : str, optional
        condition label (``A``/``B``...); conditions share one user parameter set.
    exp_id, title : str, optional
        experiment id and title; derived from the directory name by default.
    make_default : bool, default True
        whether to make this the session default (single-condition studies keep it).

    Returns
    -------
    DatasetRef
        the condition reference (``key``/``exp_id``/``data_id``/``condition``/``ndim``/
        ``nuclei``/``sampling``).

    Raises
    ------
    DatasetError
        missing ``acqus``, an unrecognisable experiment, or data already present for that condition.

    Side effects
    ------------
    Registers the experiment and data entry and writes ``project.json``; raw data stays read-only.

    Examples
    --------
        ref = add_dataset(study, "path/to/bruker", condition="A")
    """
    from workflow.import_workflow import import_data

    label = str(condition or "").strip() or session.next_condition_label()
    existing = session.dataset_by_condition(label)
    if existing is not None:
        raise DatasetError(
            tr(
                "condition label {p0!r} is taken by {p1}; give each condition its own label (A / "
                "B)",
                p0=label,
                p1=existing.key,
            )
        )
    src = Path(source).expanduser()
    if not src.exists():
        raise DatasetError(tr("dataset path does not exist: {p0}", p0=src))
    src = src.resolve()
    try:
        from core.data.bruker_reader import read_dataset

        experiment = read_dataset(src)
    except Exception as exc:  # noqa: BLE001 - public-archive data is often zipped or repacked
        raise DatasetError(
            tr(
                "not recognisable as a raw Bruker dataset: {p0} ({p1}: {p2}). This API needs an "
                "unpacked Bruker directory containing "
                "acqus/ser.",
                p0=src,
                p1=type(exc).__name__,
                p2=exc,
            )
        ) from exc

    manager = session.manager
    target_exp = exp_id
    if target_exp:
        if manager.project is None or manager.project.experiment(target_exp) is None:
            raise DatasetError(tr("experiment does not exist: {p0}", p0=target_exp))
    else:
        entry = manager.create_experiment(title or src.name)
        target_exp = entry.id
    try:
        result = import_data(manager, target_exp, src)
    except Exception as exc:  # noqa: BLE001 - policy guards (e.g. Kinetics) raise too
        raise DatasetError(tr("import failed: {p0}: {p1}", p0=type(exc).__name__, p1=exc)) from exc

    data_id = result.data_id
    if not data_id:
        active = manager.active_data(target_exp)
        data_id = active[-1].id if active else ""
    if not data_id:
        raise DatasetError(tr("import finished but produced no usable data entry"))
    manager.save()

    ref = DatasetRef(
        exp_id=target_exp,
        data_id=data_id,
        title=str(getattr(experiment.experiment_type, "name", "") or ""),
        condition=label,
        ndim=int(experiment.ndim),
        nuclei=[dim.nucleus for dim in experiment.dimensions],
        sampling=str(experiment.sampling.mode),
        source=str(src),
        raw_dir=str(result.raw_dir or ""),
        file_count=int(result.file_count),
        total_bytes=int(result.total_bytes),
    )
    return session.add_dataset_ref(ref)


def dataset_info(
    session: StudySession, dataset: DatasetRef | None = None
) -> dict[str, Any]:
    """Dataset summary (dimensions, nuclei, sampling, origin) for downstream write-ups."""
    ref = dataset or session.dataset
    if ref is None:
        raise DatasetError(tr("this study has no dataset yet; call add_dataset() first"))
    info = ref.to_dict()
    info.update(
        {
            "research_root": str(session.root),
            "conditions": session.conditions,
            "nmrforge_version": software_version(),
            "tool_versions": tool_versions(),
        }
    )
    return info


__all__ = [
    "API_VERSION",
    "CONDITION_LETTERS",
    "DatasetRef",
    "STUDY_DIRNAME",
    "StudySession",
    "add_dataset",
    "condition_token",
    "dataset_info",
    "now_iso",
    "open_study",
]
