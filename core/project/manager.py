"""ProjectManager: project lifecycle and domain operations (the single GUI entry point).

- create/open/save (project.json written atomically, tmp + os.replace)
- directory template (raw/processing/spectra/peaks/analysis/figures/report/metadata)
- experiment CRUD and status inference (registered -> imported -> processed -> picked;
  analyzed went away with the analysis feature and survives only as a status string in old projects)
- sample CRUD (S001 numbering, deletion protected by references)
- audit history (processing_history is append-only)
- WorkflowRun lifecycle (R-YYYYMMDD-NNN append-only, script snapshots)
- extract a processing template (YAML) from a successful run
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from core.logging_setup import append_run_log_line, attach_run_log, detach_run_log
from core.project.artifacts import find_primary_spectrum
from core.project.models import (
    DEFAULT_DIRECTORIES,
    SCHEMA_VERSION,
    DataEntry,
    DataGroupEntry,
    ExperimentEntry,
    ExperimentStatus,
    HistoryEntry,
    ProjectInfo,
    ProteinInfo,
    SampleEntry,
    WorkflowRun,
    now_iso,
)
from core.trash import send_to_trash
from core.version import software_version, tool_versions
from ui_support.i18n import tr


class ProjectError(Exception):
    """Project-management operation error (argument validation / reference protection / IO)."""


def sha256_file(path: Path) -> str:
    """Compute the SHA-256 of a file (the input fingerprint stored in WorkflowRun.inputs)."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_text(path: Path, text: str) -> None:
    """Write text atomically: a temporary file in the same directory plus ``os.replace``.

    A reader only ever sees "the complete old version" or "the complete new version"; an
    interruption mid-write (crash / power loss / a concurrent reader) leaves the old content
    plus a temporary file, never a half-written record. Newlines follow the platform default,
    exactly as ``Path.write_text`` does -- so routing a write through here **does not change
    the file bytes** (2026-09-20: record/state/cache writes all go through here; some of them
    used to overwrite the file in place).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=path.stem + "-", suffix=path.suffix + ".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def atomic_write_json(path: Path, data: Any) -> None:
    """Write JSON atomically (= :func:`atomic_write_text` plus the shared serialisation)."""
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def _next_sequence_id(existing: list[str], prefix: str, width: int = 3) -> str:
    """Generate the next sequential id (exp_001 / S001): the highest existing suffix + 1."""
    max_n = 0
    for value in existing:
        m = re.fullmatch(re.escape(prefix) + r"(\d+)", value)
        if m:
            max_n = max(max_n, int(m.group(1)))
    return f"{prefix}{max_n + 1:0{width}d}"


#: per-run log file name (Phase 22: one per run; see docs/proposals/2026-09-17-run-log.md)
RUN_LOG_NAME = "run.log"


class ProjectManager:
    """Every domain operation on the project root; call save() after any write."""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root).resolve() if root else None
        self.project: ProjectInfo | None = None
        # per-run log FileHandlers (Phase 22: attached by start_run, taken back by finish_run)
        self._run_logs: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    @classmethod
    def create_project(
        cls,
        root: Path | str,
        name: str,
        protein_name: str = "",
        protein_sequence: str = "",
        experiment_type: str = "",
        directories: dict[str, str] | None = None,
    ) -> ProjectManager:
        """Create a project under root: project.json plus the first audit entry
        (schema 1.3 contract §9.2: the file system is the hierarchy, so no flat directory
        template is
        pre-created).
        """
        root_path = Path(root).resolve()
        project_file = root_path / "project.json"
        if project_file.exists():
            raise ProjectError(
                tr("that directory already has a project: {p0}", p0=project_file)
            )
        manager = cls(root_path)
        dir_map = {name_: name_ for name_ in DEFAULT_DIRECTORIES}
        if directories:
            for key, rel in directories.items():
                if key in dir_map:
                    dir_map[key] = rel
        timestamp = now_iso()
        manager.project = ProjectInfo(
            schema_version=SCHEMA_VERSION,
            name=name,
            protein=ProteinInfo(name=protein_name, sequence=protein_sequence),
            experiment_type=experiment_type,
            created=timestamp,
            updated=timestamp,
            directories=dir_map,
        )
        # schema 1.3 (contract §9.2): the file system is the hierarchy, so the flat raw/processing/
        # spectra template directories are not pre-created; data directories appear at import or
        # processing time as <exp>/<data>/{raw,process,spectra,peaks,figures,report}.
        # dir_map only resolves project-level directories (such as the processing run snapshots) and
        # never creates them.
        manager.add_history("project_created", {"name": name, "root": str(root_path)})
        manager.save()
        return manager

    @classmethod
    def open_project(cls, root: Path | str) -> ProjectManager:
        """Open an existing project (reads project.json; schema 1.0/1.1 stay readable)."""
        root_path = Path(root).resolve()
        project_file = root_path / "project.json"
        if not project_file.is_file():
            raise ProjectError(tr("project.json not found: {p0}", p0=project_file))
        try:
            data = json.loads(project_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProjectError(tr("cannot read project.json: {p0}", p0=exc)) from exc
        if (
            not isinstance(data.get("name"), str)
            or not data["name"]
            or not isinstance(data.get("experiments", []), list)
        ):
            raise ProjectError(
                tr("project.json has an invalid structure: {p0}", p0=project_file)
            )
        manager = cls(root_path)
        manager.project = ProjectInfo.from_dict(data)
        # 0.2.199-patch29hf: migrate the old automatic default titles (data group G1 / sample data
        # d_001 -> Group/Data)
        migrated = manager._migrate_legacy_default_titles()
        if manager.project.schema_version != SCHEMA_VERSION:
            # schema 1.0/1.1 -> 1.2: the old source/segments -> data[0]
            # (ExperimentEntry.from_dict already migrated them)
            old = manager.project.schema_version
            manager.project.schema_version = SCHEMA_VERSION
            manager.add_history(
                "project_migrated", {"from": old, "to": SCHEMA_VERSION}
            )
            migrated = True
        if migrated:
            # MIG-010 (2026-09-12): migrating in memory only would lose the migration result and its
  # history when the project is closed without another write -- so it is written
            # atomically
            # right after a successful migration; when atomic_write_json fails the original
            # project.json stays untouched.
            try:
                manager.save()
            except OSError as exc:  # noqa: BLE001 - report explicitly, never drop the migration
                raise ProjectError(
                    tr(
                        "cannot write the migration result (the original file is unchanged): "
                        "{p0}",
                        p0=exc,
                    )
                ) from exc
        # 0.2.199-patch29ex: a soft-deleted entry whose files are back in place is recovered on open
        manager.recover_trashed()
        return manager

    def _migrate_legacy_default_titles(self) -> bool:
        """0.2.199-patch29hf: migrate the old automatic default titles to the Group/Data form (only
        titles matching the generated pattern). Older versions let create_data_group write the
        automatic title "Data Group G1", and sample data was stored as "Sample Data d_001" on
        some paths; display preferred the stored title, so the historical groups still showed
        Chinese even after the fallback had been translated. Only titles that match the
        generated pattern exactly become Group/Data; titles the user renamed are left alone.
        Returns whether anything changed."""
        if self.project is None:
            return False
        migrated = False
        for entry in self.project.experiments or []:
            for group in entry.groups or []:
                # the legacy titles written by older versions are Chinese: keep the
                # literal exactly as stored on disk (never translate it)
                legacy = f"数据组 {group.id}"  # i18n: keep(legacy title, compare as stored)
                if group.title == legacy:
                    group.title = f"Group {group.id}"
                    migrated = True
            for data in entry.data or []:
                legacy = f"样品数据 {data.id}"  # i18n: keep(legacy title, compare as stored)
                if data.title == legacy:
                    data.title = f"Data {data.id}"
                    migrated = True
        return migrated

    def save(self) -> None:
        """Write project.json atomically and refresh the updated timestamp."""
        if self.root is None or self.project is None:
            raise ProjectError(tr("no project is loaded, so it cannot be saved"))
        self.project.updated = now_iso()
        atomic_write_json(self.root / "project.json", self.project.to_dict())

    def close(self) -> None:
        """Close the current project (clears memory only, never saves). Handlers of runs that never
        finished are taken back as well."""
        for handler in list(self._run_logs.values()):
            detach_run_log(handler)
        self._run_logs.clear()
        self.root = None
        self.project = None

    # ------------------------------------------------------------------
    # directory resolution
    # ------------------------------------------------------------------
    def dir_path(self, key: str) -> Path:
        """Resolve a project-level directory (project.directories; the same-named directory
        under the
        project root by default).

        The artifacts of schema 1.4 live under <exp>/<data>/; this mapping only serves project-level
        directories such as the processing/<exp>/runs run snapshots.
        """
        if self.root is None or self.project is None:
            raise ProjectError(tr("project not loaded"))
        rel = self.project.directories.get(key, key)
        return self.root / rel


    def run_dir(self, run_id: str) -> Path:
        """The run directory ``<processing>/<exp_id>/runs/<run_id>/`` (snapshots and run.log
        live here)."""
        run = self._require_run(run_id)
        return self.dir_path("processing") / run.experiment_id / "runs" / run.run_id

    def run_log_path(self, run_id: str) -> Path:
        """The per-run log file ``<run_dir>/run.log`` (Phase 22: every run gets one)."""
        return self.run_dir(run_id) / RUN_LOG_NAME

    def _open_run_log(self, run: WorkflowRun) -> None:
        """Attach the run.log of this run and write the "start" header (independent of the
        global log
        level)."""
        try:
            path = self.run_log_path(run.run_id)
        except ProjectError:  # pragma: no cover - no project loaded
            return
        self._run_logs[run.run_id] = attach_run_log(path.parent)
        append_run_log_line(
            path,
            tr(
                "run {p0} Start: workflow={p1} "
                "inputs={p2}",
                p0=run.run_id,
                p1=run.workflow_ref or '-',
                p2=sorted(run.inputs),
            ),
        )

    def _close_run_log(self, run: WorkflowRun) -> None:
        """Write the "end" line and take the handler back (a run without a matching start is
        skipped)."""
        handler = self._run_logs.pop(run.run_id, None)
        try:
            message = tr("run {p0} end: status={p1}", p0=run.run_id, p1=run.status)
            if run.message:
                message += f" message={run.message}"
            append_run_log_line(self.run_log_path(run.run_id), message)
        except (ProjectError, OSError):
            pass
        detach_run_log(handler)

    def data_base(self, exp_id: str, data_id: str) -> Path:
        """The schema 1.3 data-directory base: <project>/<exp_id>/<data_id>/."""
        if self.root is None:
            raise ProjectError(tr("project not loaded"))
        return self.root / exp_id / data_id

    def data_dir(self, exp_id: str, data_id: str, key: str) -> Path:
        """A subdirectory inside a data entry (contract §9.2):
        raw/process/spectra/peaks/figures/report."""
        if key not in ("raw", "process", "spectra", "peaks", "figures", "report", "smile_optimized"
        ):
            raise ProjectError(tr("Unknown data subdirectory: {p0}", p0=key))
        return self.data_base(exp_id, data_id) / key

    def data_metadata_path(self, exp_id: str, data_id: str) -> Path:
        """Where the data metadata lives: <project>/<exp_id>/<data_id>/metadata.json."""
        return self.data_base(exp_id, data_id) / "metadata.json"

    def _experiment_paths(self, exp_id: str) -> list[Path]:
        """Every file/directory related to an experiment (cleaned up when the experiment is
        deleted)."""
        return [
            self.root / exp_id,  # schema 1.4 data base <exp>/<data>/...
            self.dir_path("processing") / exp_id,  # run script/parameter snapshots
            self.dir_path("analysis") / exp_id,  # historical CSP output (the feature was removed)
        ]


    def _data_paths(self, exp_id: str, data_id: str) -> list[Path]:
        """Every artifact path of a single data entry (cleaned up when the data is deleted)."""
        return [
            self.data_base(exp_id, data_id),
            self.dir_path("analysis") / exp_id / data_id,  # historical CSP output
        ]


    def _ensure_inside_root(self, path: Path) -> Path:
        resolved = path.resolve()
        if self.root is None or not resolved.is_relative_to(self.root):
            raise ProjectError(
                tr(
                "the path leaves the project directory; refusing the operation: "
                "{p0}",
                p0=path,
            )
            )
        return resolved

    # ------------------------------------------------------------------
    # audit history
    # ------------------------------------------------------------------
    def add_history(self, action: str, fields: dict[str, Any] | None = None) -> HistoryEntry:
        if self.project is None:
            raise ProjectError(tr("project not loaded"))
        entry = HistoryEntry(
            id=_next_sequence_id(
                [h.id for h in self.project.processing_history], "H", width=3
            ),
            timestamp=now_iso(),
            action=action,
            fields=dict(fields or {}),
        )
        self.project.processing_history.append(entry)
        return entry

    # ------------------------------------------------------------------
    # experiments
    # ------------------------------------------------------------------
    def create_experiment(
        self,
        title: str = "",
        sample_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> ExperimentEntry:
        """Create an empty experiment (no data, status=registered)."""
        if self.project is None:
            raise ProjectError(tr("project not loaded"))
        if sample_id and self.project.sample(sample_id) is None:
            raise ProjectError(tr("sample does not exist: {p0}", p0=sample_id))
  # 0.2.159: the numbering includes the ids already used in the history (run records,
        # audit), so
        # a deleted number is never reused -- otherwise a new experiment would inherit the notes and
        # run records of the old one
        used_exp = {e.id for e in self.project.experiments}
        used_exp.update(
            str(r.experiment_id)
            for r in (self.project.workflow_runs or [])
        )
        used_exp.update(
            str(h.fields.get("experiment_id", ""))
            for h in (self.project.processing_history or [])
        )
        entry = ExperimentEntry(
            id=_next_sequence_id(sorted(used_exp), "exp_"),
            title=title,
            status=ExperimentStatus.REGISTERED.value,
            metadata=dict(metadata or {}),
            sample_id=sample_id,
            created_at=now_iso(),
        )
        self.project.experiments.append(entry)
        self.add_history(
            "experiment_created",
            {"experiment_id": entry.id, "title": title, "sample_id": sample_id},
        )
        return entry

    def data(self, exp_id: str, data_id: str) -> DataEntry:
        """Fetch a data entry of an experiment (a soft-deleted entry counts as absent)."""
        entry = self._require_experiment(exp_id)
        data_entry = next((d for d in entry.data if d.id == data_id), None)
        if data_entry is None:
            raise ProjectError(tr("data does not exist: {p0}/{p1}", p0=exp_id, p1=data_id))
        if data_entry.trashed:
            raise ProjectError(tr("Data has been moved to trash: {p0}/{p1}", p0=exp_id, p1=data_id))
        return data_entry

    def active_data(self, exp_id: str) -> list[DataEntry]:
        """The data entries of an experiment that are not soft-deleted."""
        entry = self._require_experiment(exp_id)
        return [d for d in entry.data if not d.trashed]

    def _trash_dir(self) -> Path:
        """The fallback in-app trash directory (<project>/.nmrforge_trash)."""
        if self.root is None:
            raise ProjectError(tr("project not loaded"))
        return self.root / ".nmrforge_trash"

    # Only these entries count as "real artifacts": pure UI records (report/log.txt,
    # ui_state.json) do not -- otherwise any UI write-back after deleting data would make
    # recover_trashed believe the user had restored it from the trash (0.2.199-patch29hz).
    _REAL_ARTIFACT_NAMES = (
        "raw",
        "process",
        "spectra",
        "peaks",
        "figures",
        "smile_optimized",
        "metadata.json",
    )

    def _data_base_has_real_content(self, exp_id: str, data_id: str) -> bool:
        """Whether the data directory holds real artifacts (pure UI records such as report/
        ui_state.json do not count)."""
        base = self.data_base(exp_id, data_id)
        if not base.is_dir():
            return False
        return any(
            (base / name).exists() for name in self._REAL_ARTIFACT_NAMES
        )

    def recover_trashed(self) -> int:
        """Recover soft-deleted entries whose files are back at their original path; returns how
        many."""
        if self.project is None or self.root is None:
            return 0
        n = 0
        for exp in self.project.experiments:
            if exp.trashed and (self.root / exp.id).is_dir():
                exp.trashed = False
                exp.trashed_at = ""
                n += 1
            for d in exp.data:
                if (
                    d.trashed
                    and self.data_base(exp.id, d.id).is_dir()
                    and self._data_base_has_real_content(exp.id, d.id)
                ):
                    d.trashed = False
                    d.trashed_at = ""
                    n += 1
        if n:
            self.add_history("trash_restored", {"count": n})
            self.save()
        return n

    def import_data(
        self,
        exp_id: str,
        source: Path | str,
        segments: list[Path | str] | None = None,
        imported_at: str = "",
    ) -> DataEntry:
        """Register a data entry (d_001...); the workflow copies the files and writes the
        metadata."""
        entry = self._require_experiment(exp_id)
        # 0.2.159: the numbering includes the data ids this experiment used in its history (run
  # records, audit), so a deleted number is never reused -- otherwise new data would
        # inherit the
        # notes and run records of the old one
        used_data = {d.id for d in entry.data}
        used_data.update(
            str((r.inputs or {}).get("data_id", ""))
            for r in (self.project.workflow_runs or [])
            if r.experiment_id == exp_id
        )
        used_data.update(
            str(h.fields.get("data_id", ""))
            for h in (self.project.processing_history or [])
            if str(h.fields.get("experiment_id", "")) == exp_id
        )
        data_entry = DataEntry(
            id=_next_sequence_id(sorted(used_data), "d_"),
            source=str(source),
            segments=[str(s) for s in (segments or [])],
            status="imported",
            imported_at=imported_at or now_iso(),
        )
        entry.data.append(data_entry)
        if entry.status == ExperimentStatus.REGISTERED.value:
            entry.status = ExperimentStatus.IMPORTED.value
        self.add_history(
            "data_imported",
            {
                "experiment_id": exp_id,
                "data_id": data_entry.id,
                "source": str(source),
                "segments": list(data_entry.segments),
            },
        )
        return data_entry

    def set_data_fid(
        self, exp_id: str, data_id: str, fid_path: Path | str
    ) -> DataEntry:
        """Record that a FID has been generated for this data entry."""
        data_entry = self.data(exp_id, data_id)
        data_entry.fid_path = str(fid_path)
        data_entry.status = "fid_ready"
        self.add_history(
            "data_fid",
            {"experiment_id": exp_id, "data_id": data_id, "fid_path": str(fid_path)},
        )
        return data_entry

    def set_data_spectrum(
        self, exp_id: str, data_id: str, spectrum_path: Path | str
    ) -> DataEntry:
        """Record that a spectrum has been generated for this data entry."""
        data_entry = self.data(exp_id, data_id)
        data_entry.spectrum_path = str(spectrum_path)
        data_entry.status = "processed"
        self.add_history(
            "data_spectrum",
            {
                "experiment_id": exp_id,
                "data_id": data_id,
                "spectrum_path": str(spectrum_path),
            },
        )
        return data_entry

    def rename_data(self, exp_id: str, data_id: str, title: str) -> DataEntry:
        """Rename a data entry (writes the title and the data_renamed audit entry)."""
        data_entry = self.data(exp_id, data_id)
        old_title = data_entry.title
        data_entry.title = str(title)
        self.add_history(
            "data_renamed",
            {
                "experiment_id": exp_id,
                "data_id": data_id,
                "old_title": old_title,
                "new_title": str(title),
            },
        )
        return data_entry

    def delete_data(self, exp_id: str, data_id: str) -> list[str]:
        """Delete data: the artifacts go to the system trash and the entry is soft-deleted
        (recoverable
        from the trash).

        The files go to the system trash (send2trash, falling back to .nmrforge_trash inside the
        project); the entry keeps its trashed flag, group references and notes -- once the user
        restores the directory from the trash to its original path, opening or refreshing the
        project
        recovers it automatically (recover_trashed). The WorkflowRun audit is preserved.
        """
        entry = self._require_experiment(exp_id)
        data_entry = next((d for d in entry.data if d.id == data_id), None)
        if data_entry is None:
            raise ProjectError(tr("data does not exist: {p0}/{p1}", p0=exp_id, p1=data_id))
        if data_entry.trashed:
            raise ProjectError(tr("Data has been moved to trash: {p0}/{p1}", p0=exp_id, p1=data_id))
        removed: list[str] = []
        for path in self._data_paths(exp_id, data_id):
            target = self._ensure_inside_root(path)
            if not target.exists():
                continue
            dest = send_to_trash(
                target, self._trash_dir(), target.relative_to(self.root)
            )
            removed.append(str(dest))
        data_entry.trashed = True
        data_entry.trashed_at = now_iso()
  # group references and notes are kept, so a restore returns the data to its old group
        # and notes
        if not any(d for d in entry.data if not d.trashed):
            entry.status = ExperimentStatus.REGISTERED.value
        self.add_history(
            "data_deleted",
            {
                "experiment_id": exp_id,
                "data_id": data_id,
                "source": data_entry.source,
                "trashed": True,
                "moved_to": removed,
            },
        )
        return removed

    # ------------------------------------------------------------------
  # data groups (schema 1.4): the batch-processing unit, whose member data_ids are ordered;
    # deleting
    # a group does not disband its data
    # ------------------------------------------------------------------
    def data_groups(self, exp_id: str) -> list[DataGroupEntry]:
        """Every data group of an experiment (an empty experiment yields an empty list)."""
        entry = self._require_experiment(exp_id)
        return list(entry.groups)

    def group(self, exp_id: str, group_id: str) -> DataGroupEntry | None:
        """Fetch a data group by id (None when it does not exist)."""
        entry = self._require_experiment(exp_id)
        return next((g for g in entry.groups if g.id == group_id), None)

    def _next_group_id(self, exp_id: str) -> str:
        """The next data-group id (raised automatically; distinct from the old pipeline_state
        B-prefixed batch groups so the names cannot clash; the numbering includes the history and a
        deleted id is never reused)."""
        entry = self._require_experiment(exp_id)
        used: set[str] = set()
        used.update(g.id for g in entry.groups)
        used.update(
            str(h.fields.get("group_id", ""))
            for h in (self.project.processing_history or [])
            if str(h.fields.get("experiment_id", "")) == exp_id
        )
        max_n = 0
        for group_id in used:
            match = re.fullmatch(r"G(\d+)", group_id)
            if match:
                max_n = max(max_n, int(match.group(1)))
        return f"G{max_n + 1}"

    def create_data_group(
        self,
        exp_id: str,
        title: str = "",
        data_ids: list[str] | None = None,
    ) -> DataGroupEntry:
        """Create a data group (numbered automatically); every entry of data_ids must exist in the
        experiment."""
        entry = self._require_experiment(exp_id)
        group_id = self._next_group_id(exp_id)
        members = [str(x) for x in (data_ids or [])]
        existing = {d.id for d in entry.data}
        unknown = [d for d in members if d not in existing]
        if unknown:
            raise ProjectError(tr("data does not exist: {p0}/{p1}", p0=exp_id, p1=unknown[0]))
        group = DataGroupEntry(
            id=group_id,
            title=title or f"Group {group_id}",
            data_ids=members,
            created_at=now_iso(),
        )
        entry.groups.append(group)
        self.add_history(
            "data_group_created",
            {"experiment_id": exp_id, "group_id": group_id, "data_ids": members},
        )
        return group

    def rename_data_group(self, exp_id: str, group_id: str, title: str) -> DataGroupEntry:
        """Rename a data group (writes the title and the data_group_renamed audit entry)."""
        group = self.group(exp_id, group_id)
        if group is None:
            raise ProjectError(tr("data group does not exist: {p0}/{p1}", p0=exp_id, p1=group_id))
        old_title = group.title
        group.title = str(title)
        self.add_history(
            "data_group_renamed",
            {
                "experiment_id": exp_id,
                "group_id": group_id,
                "old_title": old_title,
                "new_title": str(title),
            },
        )
        return group

    def delete_data_group(self, exp_id: str, group_id: str) -> None:
        """Delete the data-group node (the group goes away; the member data survive as
        individual data)."""
        entry = self._require_experiment(exp_id)
        group = self.group(exp_id, group_id)
        if group is None:
            raise ProjectError(tr("data group does not exist: {p0}/{p1}", p0=exp_id, p1=group_id))
        entry.groups.remove(group)
        self.add_history(
            "data_group_deleted",
            {"experiment_id": exp_id, "group_id": group_id},
        )

    def delete_data_group_with_members(
        self, exp_id: str, group_id: str
    ) -> list[str]:
        """Delete a data group together with all its data (each artifact goes to the system
        trash and is
        soft-deleted).

        Every member is deleted through delete_data (recoverable); the ids of the deleted
        members come
        back as a list; already deleted or missing data are skipped without aborting. After the
        group
        is gone the entries keep their trashed flag and stay recoverable.
        """
        entry = self._require_experiment(exp_id)
        group = self.group(exp_id, group_id)
        if group is None:
            raise ProjectError(tr("data group does not exist: {p0}/{p1}", p0=exp_id, p1=group_id))
        members = list(group.data_ids)
        deleted: list[str] = []
        for data_id in members:
            try:
                self.delete_data(exp_id, data_id)
            except ProjectError:  # noqa: BLE001 - already deleted / missing: skip
                continue
            deleted.append(data_id)
        entry.groups.remove(group)
        self.add_history(
            "data_group_deleted_with_members",
            {
                "experiment_id": exp_id,
                "group_id": group_id,
                "deleted_data_ids": deleted,
            },
        )
        return deleted

    def add_to_group(self, exp_id: str, group_id: str, data_id: str) -> None:
        """Add data to a group (idempotent when already a member; the data must belong to the
        experiment)."""
        group = self.group(exp_id, group_id)
        if group is None:
            raise ProjectError(tr("data group does not exist: {p0}/{p1}", p0=exp_id, p1=group_id))
        self.data(exp_id, data_id)
        if data_id not in group.data_ids:
            group.data_ids.append(data_id)
            self.add_history(
                "data_group_add_data",
                {"experiment_id": exp_id, "group_id": group_id, "data_id": data_id},
            )

    def remove_from_group(self, exp_id: str, group_id: str, data_id: str) -> None:
        """Remove data from a group (idempotent when it is not a member)."""
        group = self.group(exp_id, group_id)
        if group is None:
            raise ProjectError(tr("data group does not exist: {p0}/{p1}", p0=exp_id, p1=group_id))
        if data_id in group.data_ids:
            group.data_ids.remove(data_id)
            self.add_history(
                "data_group_remove_data",
                {"experiment_id": exp_id, "group_id": group_id, "data_id": data_id},
            )

    def group_of_data(self, exp_id: str, data_id: str) -> DataGroupEntry | None:
        """The first data group this data belongs to (None when it belongs to none)."""
        entry = self._require_experiment(exp_id)
        return next((g for g in entry.groups if data_id in g.data_ids), None)

    def group_data_ids(self, exp_id: str, group_id: str) -> list[str]:
        """The data ids inside a group (empty when the group does not exist)."""
        group = self.group(exp_id, group_id)
        return list(group.data_ids) if group is not None else []

    def add_experiment(
        self,
        source: Path | str,
        title: str = "",
        sample_id: str = "",
        segments: list[Path | str] | None = None,
        metadata: dict[str, Any] | None = None,
        imported_at: str = "",
    ) -> ExperimentEntry:
        """Compatibility convenience entry point: create an experiment and import its first data
        entry
        (create_experiment + import_data)."""
        entry = self.create_experiment(
            title=title, sample_id=sample_id, metadata=metadata
        )
        self.import_data(entry.id, source, segments=segments, imported_at=imported_at)
        return entry

    def rename_experiment(self, exp_id: str, new_title: str) -> None:
        entry = self._require_experiment(exp_id)
        old = entry.title
        entry.title = new_title
        self.add_history(
            "experiment_renamed",
            {"experiment_id": exp_id, "old_title": old, "new_title": new_title},
        )

    def delete_experiment(self, exp_id: str) -> list[str]:
        """Delete an experiment: the artifacts go to the system trash and the entry is soft-deleted
        (recoverable from the trash).

        The files go to the system trash (send2trash, falling back to .nmrforge_trash inside the
        project); the entry keeps its trashed flag and is recovered automatically once the
        directory is
        back at its original path. The WorkflowRun audit is preserved.
        """
        entry = self._require_experiment(exp_id)
        if entry.trashed:
            raise ProjectError(tr("experiment has been moved to trash: {p0}", p0=exp_id))
        removed: list[str] = []
        for path in self._experiment_paths(exp_id):
            target = self._ensure_inside_root(path)
            if not target.exists():
                continue
            dest = send_to_trash(
                target, self._trash_dir(), target.relative_to(self.root)
            )
            removed.append(str(dest))
        entry.trashed = True
        entry.trashed_at = now_iso()
        self.add_history(
            "experiment_deleted",
            {
                "experiment_id": exp_id,
                "title": entry.title,
                "trashed": True,
                "moved_to": removed,
                "workflow_runs_kept": [
                    r.run_id for r in self.project.workflow_runs if r.experiment_id == exp_id
                ],
            },
        )
        return removed

    def infer_status(self, exp_id: str) -> ExperimentStatus:
        """Infer the experiment status from its data entries and artifact files (the schema 1.4
        data-level layout).

        registered (no data) -> imported (metadata exists) -> processed (a spectrum exists) ->
        picked
        (a peak table). analyzed went away with the analysis feature (2026-09-12) and is no longer
        inferred from artifacts; when an old project still carries that string it is displayed
        as is and
        never rewritten.
        """
        entry = self._require_experiment(exp_id)
        active = [d for d in entry.data if not d.trashed]
        if not active:
            return ExperimentStatus.REGISTERED
        has_imported = False
        has_spectrum = False
        has_peaks = False
        for d in active:
            if self.data_metadata_path(exp_id, d.id).is_file():
                has_imported = True
            elif d.metadata_path:
                rel = Path(d.metadata_path)
                if not rel.is_absolute() and (self.root / rel).is_file():
                    has_imported = True
            if not has_spectrum:
                has_spectrum = find_primary_spectrum(self, exp_id, d.id) is not None
            peaks = self.data_dir(exp_id, d.id, "peaks")
            if any(peaks.glob("*.list")) or any(peaks.glob("*.csv")):
                has_peaks = True
        checks: list[tuple[ExperimentStatus, bool]] = [
            (ExperimentStatus.IMPORTED, has_imported),
            (ExperimentStatus.PROCESSED, has_spectrum),
            (ExperimentStatus.PICKED, has_peaks),
        ]
        status = ExperimentStatus.REGISTERED
        order = ExperimentStatus.order()
        for candidate, present in checks:
            if present and order.index(candidate) > order.index(status):
                status = candidate
        return status


    # samples
    # ------------------------------------------------------------------
    def add_sample(
        self,
        name: str = "",
        protein_name: str = "",
        sequence: str = "",
        notes: str = "",
        concentration_um: float = 0.0,
        buffer: str = "",
    ) -> SampleEntry:
        if self.project is None:
            raise ProjectError(tr("project not loaded"))
        sample = SampleEntry(
            sample_id=_next_sequence_id(
                [s.sample_id for s in self.project.samples], "S", width=3
            ),
            name=name,
            protein_name=protein_name,
            sequence=sequence,
            notes=notes,
            concentration_um=concentration_um,
            buffer=buffer,
            created=now_iso(),
        )
        self.project.samples.append(sample)
        self.add_history(
            "sample_added", {"sample_id": sample.sample_id, "name": name}
        )
        return sample

    def delete_sample(self, sample_id: str) -> None:
        """Delete a sample; refuse when an experiment references it (reference protection)."""
        sample = self.project.sample(sample_id) if self.project else None
        if sample is None:
            raise ProjectError(tr("sample does not exist: {p0}", p0=sample_id))
        referenced = [e.id for e in self.project.experiments if e.sample_id == sample_id]
        if referenced:
            raise ProjectError(
                tr(
                    "sample {p0} is referenced by an experiment; refusing to delete: "
                    "{p1}",
                    p0=sample_id,
                    p1=', '.join(referenced),
                )
            )
        self.project.samples.remove(sample)
        self.add_history("sample_deleted", {"sample_id": sample_id})

    # ------------------------------------------------------------------
    # WorkflowRun
    # ------------------------------------------------------------------
    def last_run_for_data(
        self, exp_id: str, data_id: str, refs: tuple[str, ...]
    ) -> WorkflowRun | None:
        """The most recent run of this data under the given step refs (strictly owned by data_id).

        0.2.199-patch29hz: only records whose inputs.data_id matches exactly are accepted.
        Peak-picking
        and analysis records written by versions before patch29hi carry no data_id and have no clear
        owner -- in an experiment with several data entries one failure was counted against all of
        them; artifacts of old projects are always regenerated (user confirmed 2026-09-10), so no
        compatibility fallback is kept.
        """
        if self.project is None or not refs:
            return None
        wanted = str(data_id)
        for candidate in reversed(self.project.workflow_runs):
            if candidate.experiment_id != exp_id:
                continue
            if candidate.workflow_ref not in refs:
                continue
            if str((candidate.inputs or {}).get("data_id", "")) != wanted:
                continue
            return candidate
        return None
    def start_run(
        self,
        experiment_id: str,
        workflow_ref: str = "",
        inputs: dict[str, str] | None = None,
        scripts: list[str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> WorkflowRun:
        entry = self._require_experiment(experiment_id)
        if self.project is None:
            raise ProjectError(tr("project not loaded"))
        started = now_iso()
        date_part = started[:10].replace("-", "")
        prefix = f"R-{date_part}-"
        max_n = 0
        for run in self.project.workflow_runs:
            m = re.fullmatch(re.escape(prefix) + r"(\d+)", run.run_id)
            if m:
                max_n = max(max_n, int(m.group(1)))
        run = WorkflowRun(
            run_id=f"{prefix}{max_n + 1:03d}",
            experiment_id=experiment_id,
            workflow_ref=workflow_ref,
            sample_id=entry.sample_id,
            inputs=dict(inputs or {}),
            scripts=list(scripts or []),
            params=dict(params or {}),
            software_version=software_version(),
            tool_versions=tool_versions(),
            started_at=started,
            status="running",
        )
  # PROV-009 (2026-09-12): a run carries its own lifecycle history and parameter
        # provenance. The
  # values themselves live in run.params (with a snapshot when needed); here the source
        # and the
  # key set are recorded, so the same parameters are never stored twice and cannot drift
        # apart.
        run.history = [{"at": started, "event": "started"}]
        run.decisions = [
            {
                "at": started,
                "kind": "params",
                "source": "explicit" if params else "default",
                "keys": sorted(str(key) for key in (params or {})),
            }
        ]
        self.project.workflow_runs.append(run)
        self._open_run_log(run)
        self.add_history(
            "run_started",
            {
                "run_id": run.run_id,
                "experiment_id": experiment_id,
                "workflow_ref": workflow_ref,
            },
        )
        return run

    def finish_run(
        self,
        run_id: str,
        status: str,
        outputs: dict[str, str] | None = None,
        message: str = "",
    ) -> WorkflowRun:
        run = self._require_run(run_id)
        run.status = status
        run.finished_at = now_iso()
        run.outputs = dict(outputs or {})
        run.message = message
        # PROV-009: only the backend can detect the NMRPipe/SMILE versions during processing (see
  # backend/nmrpipe_version.py), so they are merged into this run record at the end; the
        # versions
        # of this software and its dependencies written by start_run stay unchanged.
        merged_versions = dict(run.tool_versions or {})
        merged_versions.update(tool_versions())
        run.tool_versions = merged_versions
        if not run.software_version:
            run.software_version = software_version()
        run.history = list(run.history or []) + [
            {"at": run.finished_at, "event": "finished", "status": status}
        ]
        self.add_history(
            "run_finished",
            {"run_id": run_id, "status": status, "message": message},
        )
        self._close_run_log(run)
        return run

    def snapshot_run(
        self,
        run_id: str,
        scripts: dict[str, str],
        params: dict[str, Any] | None = None,
    ) -> Path:
        """Write the run scripts and the parameter snapshot into
        processing/<exp>/runs/<run_id>/snapshot/."""
        run = self._require_run(run_id)
        self._require_experiment(run.experiment_id)  # keep the check: the experiment must exist
        snapshot = self.run_dir(run.run_id) / "snapshot"
        snapshot.mkdir(parents=True, exist_ok=True)
        for script_name, content in scripts.items():
            (snapshot / script_name).write_text(content, encoding="utf-8")
        params_path = snapshot / "params.json"
        atomic_write_text(
            params_path,
            json.dumps(params if params is not None else run.params, ensure_ascii=False, indent=2)
            + "\n",
        )
        run.snapshot_dir = snapshot.relative_to(self.root).as_posix()
        run.scripts = sorted(set(run.scripts) | set(scripts))
        return snapshot

    # internal helpers
    # ------------------------------------------------------------------
    def _require_experiment(self, exp_id: str) -> ExperimentEntry:
        if self.project is None:
            raise ProjectError(tr("project not loaded"))
        entry = self.project.experiment(exp_id)
        if entry is None:
            raise ProjectError(tr("experiment does not exist: {p0}", p0=exp_id))
        return entry

    def _require_run(self, run_id: str) -> WorkflowRun:
        if self.project is None:
            raise ProjectError(tr("project not loaded"))
        run = self.project.run(run_id)
        if run is None:
            raise ProjectError(tr("run record does not exist: {p0}", p0=run_id))
        return run
