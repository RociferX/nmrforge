"""ProjectManager:项目生命周期与领域操作(GUI 唯一入口)。

- create/open/save(project.json 原子写,tmp + os.replace)
- 目录模板(raw/processing/spectra/peaks/analysis/figures/report/metadata)
- 实验 CRUD、状态推断(registered → imported → processed → picked → analyzed)
- 样本 CRUD(S001 自动编号、删除引用保护)
- 审计历史(processing_history 只追加)
- WorkflowRun 生命周期(R-YYYYMMDD-NNN 只追加,脚本快照)
- 从成功运行提取处理模板(YAML)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from core.project.models import (
    DEFAULT_DIRECTORIES,
    SCHEMA_VERSION,
    DataEntry,
    ExperimentEntry,
    ExperimentStatus,
    HistoryEntry,
    ProjectInfo,
    ProteinInfo,
    SampleEntry,
    WorkflowRun,
    now_iso,
)


class ProjectError(Exception):
    """项目管理操作错误(参数校验/引用保护/IO)。"""


def sha256_file(path: Path) -> str:
    """计算文件 SHA-256(输入指纹,供 WorkflowRun.inputs)。"""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """原子写 JSON:同目录临时文件 + os.replace。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.stem + "-", suffix=".json.tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _next_sequence_id(existing: list[str], prefix: str, width: int = 3) -> str:
    """生成连续编号:exp_001 / S001,取现有最大后缀 +1。"""
    max_n = 0
    for value in existing:
        m = re.fullmatch(re.escape(prefix) + r"(\d+)", value)
        if m:
            max_n = max(max_n, int(m.group(1)))
    return f"{prefix}{max_n + 1:0{width}d}"


class ProjectManager:
    """项目根目录上的全部领域操作;所有写操作后需 save() 落盘。"""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root).resolve() if root else None
        self.project: ProjectInfo | None = None

    # ------------------------------------------------------------------
    # 生命周期
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
        """在 root 下创建项目:目录模板 + project.json + 首条审计历史。"""
        root_path = Path(root).resolve()
        project_file = root_path / "project.json"
        if project_file.exists():
            raise ProjectError(f"目录已存在项目: {project_file}")
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
        for rel in dir_map.values():
            (root_path / rel).mkdir(parents=True, exist_ok=True)
        manager.add_history("project_created", {"name": name, "root": str(root_path)})
        manager.save()
        return manager

    @classmethod
    def open_project(cls, root: Path | str) -> ProjectManager:
        """打开已有项目(读 project.json,schema 1.0/1.1 均兼容)。"""
        root_path = Path(root).resolve()
        project_file = root_path / "project.json"
        if not project_file.is_file():
            raise ProjectError(f"未找到 project.json: {project_file}")
        try:
            data = json.loads(project_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProjectError(f"project.json 读取失败: {exc}") from exc
        if (
            not isinstance(data.get("name"), str)
            or not data["name"]
            or not isinstance(data.get("experiments", []), list)
        ):
            raise ProjectError(f"project.json 结构无效: {project_file}")
        manager = cls(root_path)
        manager.project = ProjectInfo.from_dict(data)
        if manager.project.schema_version != SCHEMA_VERSION:
            # schema 1.0/1.1 → 1.2:旧 source/segments → data[0]
            # (ExperimentEntry.from_dict 已完成迁移)
            old = manager.project.schema_version
            manager.project.schema_version = SCHEMA_VERSION
            manager.add_history(
                "project_migrated", {"from": old, "to": SCHEMA_VERSION}
            )
        return manager

    def save(self) -> None:
        """原子写 project.json 并刷新 updated 时间戳。"""
        if self.root is None or self.project is None:
            raise ProjectError("未加载项目,无法保存")
        self.project.updated = now_iso()
        atomic_write_json(self.root / "project.json", self.project.to_dict())

    def close(self) -> None:
        """关闭当前项目(仅清内存,不自动保存)。"""
        self.root = None
        self.project = None

    # ------------------------------------------------------------------
    # 目录解析
    # ------------------------------------------------------------------
    def dir_path(self, key: str) -> Path:
        """兼容层:旧扁平目录(项目根下 raw/processing/spectra/...)。"""
        if self.root is None or self.project is None:
            raise ProjectError("未加载项目")
        rel = self.project.directories.get(key, key)
        return self.root / rel

    def data_base(self, exp_id: str, data_id: str) -> Path:
        """schema 1.3 数据目录基座:<project>/<exp_id>/<data_id>/。"""
        if self.root is None:
            raise ProjectError("未加载项目")
        return self.root / exp_id / data_id

    def data_dir(self, exp_id: str, data_id: str, key: str) -> Path:
        """数据内子目录(契约 §9.2):raw/process/spectra/peaks/figures/report。"""
        if key not in ("raw", "process", "spectra", "peaks", "figures", "report"):
            raise ProjectError(f"未知数据子目录: {key}")
        return self.data_base(exp_id, data_id) / key

    def data_metadata_path(self, exp_id: str, data_id: str) -> Path:
        """数据 metadata 落盘:<project>/<exp_id>/<data_id>/metadata.json。"""
        return self.data_base(exp_id, data_id) / "metadata.json"

    def _experiment_paths(self, exp_id: str) -> list[Path]:
        """实验全部相关文件/目录(删除实验时按此清理;兼容新旧布局)。"""
        candidates: list[Path] = [self.root / exp_id]  # schema 1.3 数据基座
        for key in ("raw", "processing", "spectra", "peaks", "analysis", "figures", "report"):
            base = self.dir_path(key)
            if key in ("raw", "processing", "analysis", "figures"):
                candidates.append(base / exp_id)
            else:
                candidates.extend(base.glob(f"{exp_id}.*"))
                candidates.extend(base.glob(f"{exp_id}-*.*"))
        candidates.append(self.dir_path("metadata") / f"{exp_id}.json")
        candidates.extend(self.dir_path("metadata").glob(f"{exp_id}-*.json"))
        return candidates

    def _data_paths(self, exp_id: str, data_id: str) -> list[Path]:
        """单个数据的全部产物路径(删除数据时清理;兼容新旧布局)。"""
        candidates = [self.data_base(exp_id, data_id)]
        candidates.append(self.dir_path("raw") / exp_id / data_id)
        candidates.append(self.dir_path("processing") / exp_id / data_id)
        for key in ("spectra", "peaks", "report"):
            candidates.extend(self.dir_path(key).glob(f"{exp_id}-{data_id}.*"))
        candidates.append(self.dir_path("metadata") / f"{exp_id}-{data_id}.json")
        return candidates

    def _ensure_inside_root(self, path: Path) -> Path:
        resolved = path.resolve()
        if self.root is None or not resolved.is_relative_to(self.root):
            raise ProjectError(f"路径超出项目目录,拒绝操作: {path}")
        return resolved

    # ------------------------------------------------------------------
    # 审计历史
    # ------------------------------------------------------------------
    def add_history(self, action: str, fields: dict[str, Any] | None = None) -> HistoryEntry:
        if self.project is None:
            raise ProjectError("未加载项目")
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
    # 实验
    # ------------------------------------------------------------------
    def create_experiment(
        self,
        title: str = "",
        sample_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> ExperimentEntry:
        """新建空白实验(无数据,status=registered)。"""
        if self.project is None:
            raise ProjectError("未加载项目")
        if sample_id and self.project.sample(sample_id) is None:
            raise ProjectError(f"样本不存在: {sample_id}")
        entry = ExperimentEntry(
            id=_next_sequence_id([e.id for e in self.project.experiments], "exp_"),
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
        """取实验下的数据条目。"""
        entry = self._require_experiment(exp_id)
        data_entry = next((d for d in entry.data if d.id == data_id), None)
        if data_entry is None:
            raise ProjectError(f"数据不存在: {exp_id}/{data_id}")
        return data_entry

    def import_data(
        self,
        exp_id: str,
        source: Path | str,
        segments: list[Path | str] | None = None,
        imported_at: str = "",
    ) -> DataEntry:
        """登记数据条目(d_001...);文件复制/metadata 落盘由 workflow 完成。"""
        entry = self._require_experiment(exp_id)
        data_entry = DataEntry(
            id=_next_sequence_id([d.id for d in entry.data], "d_"),
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
        """登记数据已生成 FID。"""
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
        """登记数据已生成谱图。"""
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
        """重命名数据条目(落盘 title,写审计历史 data_renamed)。"""
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

    def delete_data(self, exp_id: str, data_id: str) -> None:
        """删除数据条目与产物文件;WorkflowRun 审计保留。"""
        entry = self._require_experiment(exp_id)
        data_entry = self.data(exp_id, data_id)
        removed: list[str] = []
        for path in self._data_paths(exp_id, data_id):
            target = self._ensure_inside_root(path)
            if target.is_dir():
                shutil.rmtree(target)
                removed.append(str(target))
            elif target.is_file():
                target.unlink()
                removed.append(str(target))
        entry.data.remove(data_entry)
        if not entry.data:
            entry.status = ExperimentStatus.REGISTERED.value
        self.add_history(
            "data_deleted",
            {
                "experiment_id": exp_id,
                "data_id": data_id,
                "source": data_entry.source,
                "removed_files": removed,
            },
        )

    def add_experiment(
        self,
        source: Path | str,
        title: str = "",
        sample_id: str = "",
        segments: list[Path | str] | None = None,
        metadata: dict[str, Any] | None = None,
        imported_at: str = "",
    ) -> ExperimentEntry:
        """兼容便捷入口:创建实验并导入第一个数据(create_experiment + import_data)。"""
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

    def set_experiment_notes(self, exp_id: str, notes: str) -> None:
        entry = self._require_experiment(exp_id)
        entry.notes = notes
        self.add_history(
            "experiment_notes", {"experiment_id": exp_id, "notes": notes}
        )

    def delete_experiment(self, exp_id: str) -> None:
        """删除实验条目并清理产物文件;WorkflowRun 审计记录保留。"""
        entry = self._require_experiment(exp_id)
        removed: list[str] = []
        for path in self._experiment_paths(exp_id):
            target = self._ensure_inside_root(path)
            if target.is_dir():
                shutil.rmtree(target)
                removed.append(str(target))
            elif target.is_file():
                target.unlink()
                removed.append(str(target))
        self.project.experiments.remove(entry)
        self.add_history(
            "experiment_deleted",
            {
                "experiment_id": exp_id,
                "title": entry.title,
                "removed_files": removed,
                "workflow_runs_kept": [
                    r.run_id for r in self.project.workflow_runs if r.experiment_id == exp_id
                ],
            },
        )

    def infer_status(self, exp_id: str) -> ExperimentStatus:
        """按数据条目与产物文件推断实验状态(兼容 schema 1.1 旧命名)。

        registered(无数据)→ imported(metadata 存在)→ processed(谱存在)→
        picked(峰表)→ analyzed(报告/分析产物)。
        """
        entry = self._require_experiment(exp_id)
        if not entry.data:
            return ExperimentStatus.REGISTERED
        metadata_dir = self.dir_path("metadata")
        spectra_dir = self.dir_path("spectra")
        # 兼容:旧命名 metadata/<exp_id>.json 与 spectra/<exp_id>.ft2
        has_imported = (metadata_dir / f"{exp_id}.json").is_file()
        for d in entry.data:
            # schema 1.3 规范布局:<exp>/<data>/metadata.json
            if self.data_metadata_path(exp_id, d.id).is_file():
                has_imported = True
            if d.metadata_path:
                rel = Path(d.metadata_path)
                # schema 1.3:<exp>/<data>/metadata.json(项目内相对路径)
                if not rel.is_absolute() and (self.root / rel).is_file():
                    has_imported = True
                elif (metadata_dir / rel.name).is_file():  # 旧扁平命名
                    has_imported = True
            if (metadata_dir / f"{exp_id}-{d.id}.json").is_file():
                has_imported = True
            # 优先按 DataEntry 记录的产物路径(可能在工作目录),再回退旧命名 glob
            has_spectrum = False
            for d in entry.data:
                if d.spectrum_path:
                    candidate = Path(d.spectrum_path)
                    if not candidate.is_absolute():
                        candidate = self.root / candidate
                    if candidate.is_file():
                        has_spectrum = True
                        break
            if not has_spectrum:
                has_spectrum = any(spectra_dir.glob(f"{exp_id}.*")) or any(
                    spectra_dir.glob(f"{exp_id}-*.*")
                )
            if not has_spectrum:
                for d in entry.data:
                    spectra = self.data_dir(exp_id, d.id, "spectra")
                    if any(spectra.glob("*.ft2")) or any(spectra.glob("*.ft3")):
                        has_spectrum = True
                        break
        checks: list[tuple[ExperimentStatus, bool]] = [
            (ExperimentStatus.IMPORTED, has_imported),
            (ExperimentStatus.PROCESSED, has_spectrum),
            (
                ExperimentStatus.PICKED,
                self.dir_path("peaks").joinpath(f"{exp_id}.csv").is_file()
                or bool(list(self.dir_path("peaks").glob(f"{exp_id}-*.csv")))
                or any(
                    list(self.data_dir(exp_id, d.id, "peaks").glob("*.csv"))
                    for d in entry.data
                ),
            ),
            (
                ExperimentStatus.ANALYZED,
                any(
                    self.dir_path("report").joinpath(f"{exp_id}.{ext}").is_file()
                    for ext in ("pdf", "html", "json")
                )
                or bool(list(self.dir_path("report").glob(f"{exp_id}-*.*")))
                or self.dir_path("analysis").joinpath(exp_id).exists()
                or any(
                    list(self.data_dir(exp_id, d.id, "report").glob("*.pdf"))
                    or list(self.data_dir(exp_id, d.id, "report").glob("*.html"))
                    or list(self.data_dir(exp_id, d.id, "report").glob("*.json"))
                    for d in entry.data
                ),
            ),
        ]
        status = ExperimentStatus.REGISTERED
        order = ExperimentStatus.order()
        for candidate, present in checks:
            if present and order.index(candidate) > order.index(status):
                status = candidate
        return status

    def update_experiment_status(self, exp_id: str, status: ExperimentStatus) -> None:
        entry = self._require_experiment(exp_id)
        old = entry.status
        entry.status = status.value
        self.add_history(
            "experiment_status",
            {"experiment_id": exp_id, "old_status": old, "new_status": status.value},
        )

    # ------------------------------------------------------------------
    # 样本
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
            raise ProjectError("未加载项目")
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
        """删除样本;若有实验引用则拒绝(引用保护)。"""
        sample = self.project.sample(sample_id) if self.project else None
        if sample is None:
            raise ProjectError(f"样本不存在: {sample_id}")
        referenced = [e.id for e in self.project.experiments if e.sample_id == sample_id]
        if referenced:
            raise ProjectError(
                f"样本 {sample_id} 被实验引用,拒绝删除: {', '.join(referenced)}"
            )
        self.project.samples.remove(sample)
        self.add_history("sample_deleted", {"sample_id": sample_id})

    # ------------------------------------------------------------------
    # WorkflowRun
    # ------------------------------------------------------------------
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
            raise ProjectError("未加载项目")
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
            started_at=started,
            status="running",
        )
        self.project.workflow_runs.append(run)
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
        self.add_history(
            "run_finished",
            {"run_id": run_id, "status": status, "message": message},
        )
        return run

    def snapshot_run(
        self,
        run_id: str,
        scripts: dict[str, str],
        params: dict[str, Any] | None = None,
    ) -> Path:
        """把运行脚本与参数快照写入 processing/<exp>/runs/<run_id>/snapshot/。"""
        run = self._require_run(run_id)
        exp = self._require_experiment(run.experiment_id)
        snapshot = self.dir_path("processing") / exp.id / "runs" / run.run_id / "snapshot"
        snapshot.mkdir(parents=True, exist_ok=True)
        for script_name, content in scripts.items():
            (snapshot / script_name).write_text(content, encoding="utf-8")
        params_path = snapshot / "params.json"
        params_path.write_text(
            json.dumps(params if params is not None else run.params, ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
        run.snapshot_dir = snapshot.relative_to(self.root).as_posix()
        run.scripts = sorted(set(run.scripts) | set(scripts))
        return snapshot

    def build_template_from_run(
        self, run_id: str, template_dir: Path | str | None = None
    ) -> Path:
        """从成功运行提取 preset/overrides/nus 生成模板 YAML(默认 presets/templates/)。"""
        run = self._require_run(run_id)
        if run.status != "success":
            raise ProjectError(f"仅成功运行可提取模板: {run_id} status={run.status}")
        params = run.params or {}
        template = {
            "name": f"{run.workflow_ref or run.experiment_id}_{run.run_id}",
            "source_run": run.run_id,
            "preset": run.workflow_ref,
            "overrides": params.get("overrides", {}),
            "nus": params.get("nus", {}),
            "created": now_iso(),
        }
        target_dir = Path(template_dir) if template_dir else self.root / "presets" / "templates"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{template['name']}.yaml"
        try:
            import yaml
        except ImportError as exc:  # pragma: no cover - PyYAML 是必需依赖
            raise ProjectError("PyYAML 不可用,无法导出模板") from exc
        target.write_text(
            yaml.safe_dump(template, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        self.add_history(
            "template_created",
            {"run_id": run_id, "template": str(target)},
        )
        return target

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------
    def _require_experiment(self, exp_id: str) -> ExperimentEntry:
        if self.project is None:
            raise ProjectError("未加载项目")
        entry = self.project.experiment(exp_id)
        if entry is None:
            raise ProjectError(f"实验不存在: {exp_id}")
        return entry

    def _require_run(self, run_id: str) -> WorkflowRun:
        if self.project is None:
            raise ProjectError("未加载项目")
        run = self.project.run(run_id)
        if run is None:
            raise ProjectError(f"运行记录不存在: {run_id}")
        return run
