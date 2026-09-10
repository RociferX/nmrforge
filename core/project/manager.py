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
import tempfile
from pathlib import Path
from typing import Any

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
        """在 root 下创建项目:project.json + 首条审计历史
        (schema 1.3 契约 §9.2:文件系统即层级,不预建扁平目录模板)。
        """
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
        # schema 1.3(契约 §9.2):文件系统即层级,不预建扁平 raw/processing/
        # spectra 等模板目录;数据目录在导入/处理时按
        # <exp>/<data>/{raw,process,spectra,peaks,figures,report} 创建。
        # dir_map 只作项目级目录解析(如 processing 运行快照),不 mkdir。
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
        # 0.2.199-补29hf:迁移旧自动默认标题(数据组 G1/样品数据 d_001 → Group/Data)
        manager._migrate_legacy_default_titles()
        if manager.project.schema_version != SCHEMA_VERSION:
            # schema 1.0/1.1 → 1.2:旧 source/segments → data[0]
            # (ExperimentEntry.from_dict 已完成迁移)
            old = manager.project.schema_version
            manager.project.schema_version = SCHEMA_VERSION
            manager.add_history(
                "project_migrated", {"from": old, "to": SCHEMA_VERSION}
            )
        # 0.2.199-补29ex:软删除条目若已从回收站恢复到原位,打开即自动还原
        manager.recover_trashed()
        return manager

    def _migrate_legacy_default_titles(self) -> bool:
        """0.2.199-补29hf:把旧自动默认标题迁移为 Group/Data 形式(仅匹配自动生成模式)。

        旧版本 create_data_group 会把自动默认标题写成"数据组 G1",样品数据在
        (个别)路径下存过"样品数据 d_001";display 优先用存储 title,导致即使
        fallback 已改英文,历史组仍显示中文。此处仅把与自动生成模式完全一致的
        标题改为 Group/Data,用户手动命名的标题不动。返回是否有改动。
        """
        if self.project is None:
            return False
        migrated = False
        for entry in self.project.experiments or []:
            for group in entry.groups or []:
                legacy = f"数据组 {group.id}"
                if group.title == legacy:
                    group.title = f"Group {group.id}"
                    migrated = True
            for data in entry.data or []:
                legacy = f"样品数据 {data.id}"
                if data.title == legacy:
                    data.title = f"Data {data.id}"
                    migrated = True
        return migrated

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
        """项目级目录解析(project.directories,默认项目根下同名目录)。

        schema 1.4 的产物在 <exp>/<data>/ 下;该映射只服务项目级目录,
        如 processing/<exp>/runs 运行快照与 analysis 分析输出。
        """
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
        if key not in ("raw", "process", "spectra", "peaks", "figures", "report", "smile_optimized"
        ):
            raise ProjectError(f"未知数据子目录: {key}")
        return self.data_base(exp_id, data_id) / key

    def data_metadata_path(self, exp_id: str, data_id: str) -> Path:
        """数据 metadata 落盘:<project>/<exp_id>/<data_id>/metadata.json。"""
        return self.data_base(exp_id, data_id) / "metadata.json"

    def _experiment_paths(self, exp_id: str) -> list[Path]:
        """实验全部相关文件/目录(删除实验时按此清理)。"""
        return [
            self.root / exp_id,  # schema 1.4 数据基座 <exp>/<data>/...
            self.dir_path("processing") / exp_id,  # 运行脚本/参数快照
            self.dir_path("analysis") / exp_id,  # CSP 分析输出
        ]


    def _data_paths(self, exp_id: str, data_id: str) -> list[Path]:
        """单个数据的全部产物路径(删除数据时清理)。"""
        return [
            self.data_base(exp_id, data_id),
            self.dir_path("analysis") / exp_id / data_id,  # CSP 分析输出
        ]


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
        # 0.2.159:编号含历史(运行记录/审计)中已用过的实验号,删除不复用,
        # 避免新实验沿用旧实验的注释/运行记录
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
        """取实验下的数据条目(软删除条目视为不存在)。"""
        entry = self._require_experiment(exp_id)
        data_entry = next((d for d in entry.data if d.id == data_id), None)
        if data_entry is None:
            raise ProjectError(f"数据不存在: {exp_id}/{data_id}")
        if data_entry.trashed:
            raise ProjectError(f"数据已移入回收站: {exp_id}/{data_id}")
        return data_entry

    def active_data(self, exp_id: str) -> list[DataEntry]:
        """实验下未软删除的数据条目。"""
        entry = self._require_experiment(exp_id)
        return [d for d in entry.data if not d.trashed]

    def _trash_dir(self) -> Path:
        """应用内回收站回退目录(<项目>/.nmrforge_trash)。"""
        if self.root is None:
            raise ProjectError("未加载项目")
        return self.root / ".nmrforge_trash"

    # 只有这些条目算「真实产物」:纯界面记录(report/log.txt、
    # ui_state.json)不算——否则删除数据后任何一次界面写回都会让
    # recover_trashed 误判为「用户从回收站恢复了数据」(0.2.199-补29hz)。
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
        """数据目录是否含真实产物(纯界面记录 report/ui_state.json 不算)。"""
        base = self.data_base(exp_id, data_id)
        if not base.is_dir():
            return False
        return any(
            (base / name).exists() for name in self._REAL_ARTIFACT_NAMES
        )

    def recover_trashed(self) -> int:
        """把已恢复到原路径的软删除条目自动还原;返回还原数量。"""
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
        """登记数据条目(d_001...);文件复制/metadata 落盘由 workflow 完成。"""
        entry = self._require_experiment(exp_id)
        # 0.2.159:编号含历史(运行记录/审计)中该实验用过的数据号,删除不复用,
        # 避免新数据沿用旧数据的注释/运行记录
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

    def delete_data(self, exp_id: str, data_id: str) -> list[str]:
        """删除数据:产物移入系统回收站,条目软删除(可从回收站恢复)。

        文件进系统回收站(send2trash;失败回退项目内 .nmrforge_trash);
        条目保留 trashed 标记与组引用/注释——用户从回收站恢复目录到原路径后,
        项目打开/刷新自动还原(recover_trashed)。WorkflowRun 审计保留。
        """
        entry = self._require_experiment(exp_id)
        data_entry = next((d for d in entry.data if d.id == data_id), None)
        if data_entry is None:
            raise ProjectError(f"数据不存在: {exp_id}/{data_id}")
        if data_entry.trashed:
            raise ProjectError(f"数据已移入回收站: {exp_id}/{data_id}")
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
        # 组引用与注释保留:恢复后无损回到原组/原注释
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
    # 数据组(schema 1.4):批量处理单元,成员 data_ids 有序;删除组不解散数据
    # ------------------------------------------------------------------
    def data_groups(self, exp_id: str) -> list[DataGroupEntry]:
        """实验下全部数据组(空实验返回空列表)。"""
        entry = self._require_experiment(exp_id)
        return list(entry.groups)

    def group(self, exp_id: str, group_id: str) -> DataGroupEntry | None:
        """按 id 取数据组(不存在返回 None)。"""
        entry = self._require_experiment(exp_id)
        return next((g for g in entry.groups if g.id == group_id), None)

    def _next_group_id(self, exp_id: str) -> str:
        """下一个数据组编号(G1, G2, ...;与旧 pipeline_state B 前缀批量组
        区分,避免同名冲突;编号含历史不复用,删除组后不回收)。"""
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
        """新建数据组(自动编号 B1...);data_ids 必须是该实验已有数据。"""
        entry = self._require_experiment(exp_id)
        group_id = self._next_group_id(exp_id)
        members = [str(x) for x in (data_ids or [])]
        existing = {d.id for d in entry.data}
        unknown = [d for d in members if d not in existing]
        if unknown:
            raise ProjectError(f"数据不存在: {exp_id}/{unknown[0]}")
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
        """重命名数据组(落盘 title,写审计 data_group_renamed)。"""
        group = self.group(exp_id, group_id)
        if group is None:
            raise ProjectError(f"数据组不存在: {exp_id}/{group_id}")
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
        """删除数据组节点(仅移除组,成员数据保留为单个数据)。"""
        entry = self._require_experiment(exp_id)
        group = self.group(exp_id, group_id)
        if group is None:
            raise ProjectError(f"数据组不存在: {exp_id}/{group_id}")
        entry.groups.remove(group)
        self.add_history(
            "data_group_deleted",
            {"experiment_id": exp_id, "group_id": group_id},
        )

    def delete_data_group_with_members(
        self, exp_id: str, group_id: str
    ) -> list[str]:
        """删除数据组并连同组内全部数据(各产物移入系统回收站+软删除)。

        组内每个成员调用 delete_data(可回收恢复);被删成员返回其 id 列表;
        已删/不存在的数据跳过不阻断。删除组后条目保留 trashed 标记可恢复。
        """
        entry = self._require_experiment(exp_id)
        group = self.group(exp_id, group_id)
        if group is None:
            raise ProjectError(f"数据组不存在: {exp_id}/{group_id}")
        members = list(group.data_ids)
        deleted: list[str] = []
        for data_id in members:
            try:
                self.delete_data(exp_id, data_id)
            except ProjectError:  # noqa: BLE001 - 已删/不存在跳过
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
        """把数据加入组(已在组内幂等;数据必须属于该实验)。"""
        group = self.group(exp_id, group_id)
        if group is None:
            raise ProjectError(f"数据组不存在: {exp_id}/{group_id}")
        self.data(exp_id, data_id)
        if data_id not in group.data_ids:
            group.data_ids.append(data_id)
            self.add_history(
                "data_group_add_data",
                {"experiment_id": exp_id, "group_id": group_id, "data_id": data_id},
            )

    def remove_from_group(self, exp_id: str, group_id: str, data_id: str) -> None:
        """把数据移出组(不在组内幂等)。"""
        group = self.group(exp_id, group_id)
        if group is None:
            raise ProjectError(f"数据组不存在: {exp_id}/{group_id}")
        if data_id in group.data_ids:
            group.data_ids.remove(data_id)
            self.add_history(
                "data_group_remove_data",
                {"experiment_id": exp_id, "group_id": group_id, "data_id": data_id},
            )

    def group_of_data(self, exp_id: str, data_id: str) -> DataGroupEntry | None:
        """数据所属的第一个数据组(未入组返回 None)。"""
        entry = self._require_experiment(exp_id)
        return next((g for g in entry.groups if data_id in g.data_ids), None)

    def group_data_ids(self, exp_id: str, group_id: str) -> list[str]:
        """组内数据 id 列表(组不存在返回空)。"""
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

    def delete_experiment(self, exp_id: str) -> list[str]:
        """删除实验:产物移入系统回收站,实验条目软删除(可从回收站恢复)。

        文件进系统回收站(send2trash;失败回退项目内 .nmrforge_trash);
        条目保留 trashed 标记,恢复目录到原路径后自动还原。WorkflowRun 审计保留。
        """
        entry = self._require_experiment(exp_id)
        if entry.trashed:
            raise ProjectError(f"实验已移入回收站: {exp_id}")
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
        """按数据条目与产物文件推断实验状态(schema 1.4 数据级布局)。

        registered(无数据)→ imported(metadata 存在)→ processed(谱存在)→
        picked(峰表)→ analyzed(报告产物)。
        """
        entry = self._require_experiment(exp_id)
        active = [d for d in entry.data if not d.trashed]
        if not active:
            return ExperimentStatus.REGISTERED
        has_imported = False
        has_spectrum = False
        has_peaks = False
        has_report = False
        for d in active:
            if self.data_metadata_path(exp_id, d.id).is_file():
                has_imported = True
            elif d.metadata_path:
                rel = Path(d.metadata_path)
                if not rel.is_absolute() and (self.root / rel).is_file():
                    has_imported = True
            if not has_spectrum and d.spectrum_path:
                candidate = Path(d.spectrum_path)
                if not candidate.is_absolute():
                    candidate = self.root / candidate
                has_spectrum = candidate.is_file()
            spectra = self.data_dir(exp_id, d.id, "spectra")
            if not has_spectrum and (
                any(spectra.glob("*.ft2")) or any(spectra.glob("*.ft3"))
            ):
                has_spectrum = True
            peaks = self.data_dir(exp_id, d.id, "peaks")
            if any(peaks.glob("*.list")) or any(peaks.glob("*.csv")):
                has_peaks = True
            report = self.data_dir(exp_id, d.id, "report")
            for ext in ("pdf", "html", "json"):
                if any(report.glob(f"*.{ext}")):
                    has_report = True
                    break
        checks: list[tuple[ExperimentStatus, bool]] = [
            (ExperimentStatus.IMPORTED, has_imported),
            (ExperimentStatus.PROCESSED, has_spectrum),
            (ExperimentStatus.PICKED, has_peaks),
            (ExperimentStatus.ANALYZED, has_report),
        ]
        status = ExperimentStatus.REGISTERED
        order = ExperimentStatus.order()
        for candidate, present in checks:
            if present and order.index(candidate) > order.index(status):
                status = candidate
        return status


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
    def last_run_for_data(
        self, exp_id: str, data_id: str, refs: tuple[str, ...]
    ) -> WorkflowRun | None:
        """该数据在指定步骤 ref 下的最近一次运行(严格按 data_id 归属)。

        0.2.199-补29hz:只接受 inputs.data_id 精确匹配的记录。补29hi 之前的
        版本写下的峰挑选/分析记录没有 data_id,归属不明——多数据实验里会把
        一条失败算到所有数据头上;老项目产物一律重新生成(用户 2026-09-10
        确认),因此不再保留兼容回退。
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
