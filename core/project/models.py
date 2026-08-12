"""项目管理领域模型(project.json schema 1.2,兼容读 1.0/1.1)。

层级:Project → Experiment(可空白)→ Data(可多组)。
对应 API_CONTRACT §8.1(G2B-002 已批准):
- ExperimentEntry 持有 data: list[DataEntry],移除顶层 source/segments/imported_at;
- schema 1.1 项目打开时自动迁移:旧 source/segments/imported_at → data[0]
  (migrated_from_1_1: true);
- 兼容只读属性 source/segments/imported_at(指向 data[0]),便于过渡期旧代码读取。
所有模型提供 to_dict/from_dict,JSON 落盘统一走 ProjectManager 原子写。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

SCHEMA_VERSION = "1.3"

# 项目目录模板(相对路径,创建项目时逐项建目录)
DEFAULT_DIRECTORIES = [
    "raw",
    "processing",
    "spectra",
    "peaks",
    "analysis",
    "figures",
    "report",
    "metadata",
]


def now_iso() -> str:
    """当前 UTC 时间的 ISO-8601 字符串(秒级)。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ExperimentStatus(str, Enum):
    """实验状态机:registered(仅登记) → imported → processed → picked → analyzed。"""

    REGISTERED = "registered"
    IMPORTED = "imported"
    PROCESSED = "processed"
    PICKED = "picked"
    ANALYZED = "analyzed"

    @classmethod
    def order(cls) -> tuple[ExperimentStatus, ...]:
        return (
            cls.REGISTERED,
            cls.IMPORTED,
            cls.PROCESSED,
            cls.PICKED,
            cls.ANALYZED,
        )


@dataclass
class ProteinInfo:
    """蛋白元数据(名称/序列/备注)。"""

    name: str = ""
    sequence: str = ""
    notes: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> ProteinInfo:
        data = data or {}
        return cls(
            name=str(data.get("name", "")),
            sequence=str(data.get("sequence", "")),
            notes=str(data.get("notes", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "sequence": self.sequence, "notes": self.notes}


@dataclass
class DataEntry:
    """一次导入的数据(实验下的独立条目,d_001...,schema 1.2)。"""

    id: str
    title: str = ""                   # 数据标题(缺省空,GUI 回退 id)
    source: str = ""                 # 外部 Bruker 数据集目录(导入时)
    raw_dir: str = ""                # 项目内 raw/<exp_id>/<data_id>/ 副本
    segments: list[str] = field(default_factory=list)
    status: str = "imported"         # imported / fid_ready / processed
    imported_at: str = ""
    metadata_path: str = ""          # metadata/<exp_id>-<data_id>.json(相对路径)
    fid_path: str = ""               # 生成 FID 后(空串表示未生成)
    spectrum_path: str = ""          # 生成谱后(空串表示未生成)
    checksums: dict[str, str] = field(default_factory=dict)
    migrated_from_1_1: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DataEntry:
        return cls(
            id=str(data.get("id", "")),
            title=str(data.get("title", "")),
            source=str(data.get("source", "")),
            raw_dir=str(data.get("raw_dir", "")),
            segments=[str(s) for s in (data.get("segments") or [])],
            status=str(data.get("status", "imported")),
            imported_at=str(data.get("imported_at", "")),
            metadata_path=str(data.get("metadata_path", "")),
            fid_path=str(data.get("fid_path", "")),
            spectrum_path=str(data.get("spectrum_path", "")),
            checksums={str(k): str(v) for k, v in (data.get("checksums") or {}).items()},
            migrated_from_1_1=bool(data.get("migrated_from_1_1", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "source": self.source,
            "raw_dir": self.raw_dir,
            "segments": list(self.segments),
            "status": self.status,
            "imported_at": self.imported_at,
            "metadata_path": self.metadata_path,
            "fid_path": self.fid_path,
            "spectrum_path": self.spectrum_path,
            "checksums": dict(self.checksums),
            "migrated_from_1_1": self.migrated_from_1_1,
        }


@dataclass
class ExperimentEntry:
    """实验(可空白创建);数据经 data 列表挂载(schema 1.2)。"""

    id: str
    title: str = ""
    status: str = ExperimentStatus.REGISTERED.value
    sample_id: str = ""
    notes: str = ""
    data: list[DataEntry] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""

    # ---- schema 1.1 兼容只读属性(指向 data[0]) -------------------------
    @property
    def source(self) -> str:
        """旧字段兼容:首个数据目录(项目内副本优先)。"""
        if not self.data:
            return ""
        first = self.data[0]
        return first.raw_dir or first.source

    @property
    def segments(self) -> list[str]:
        """旧字段兼容:首个数据的段列表。"""
        return list(self.data[0].segments) if self.data else []

    @property
    def imported_at(self) -> str:
        """旧字段兼容:首个数据的导入时间。"""
        return self.data[0].imported_at if self.data else ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExperimentEntry:
        exp_id = str(data.get("id", ""))
        entries = [DataEntry.from_dict(d) for d in (data.get("data") or [])]
        # schema 1.0/1.1 迁移:旧顶层 source/segments/imported_at → data[0]
        if not entries and (
            data.get("source") or data.get("segments") or data.get("imported_at")
        ):
            entries = [
                DataEntry(
                    id="d_001",
                    source=str(data.get("source", "")),
                    segments=[str(s) for s in (data.get("segments") or [])],
                    status="imported",
                    imported_at=str(data.get("imported_at", "")),
                    metadata_path=f"{exp_id}.json",  # 旧命名 metadata/<exp_id>.json
                    migrated_from_1_1=True,
                )
            ]
        return cls(
            id=exp_id,
            title=str(data.get("title", "")),
            status=str(data.get("status", ExperimentStatus.REGISTERED.value)),
            sample_id=str(data.get("sample_id", "")),
            notes=str(data.get("notes", "")),
            data=entries,
            metadata=dict(data.get("metadata") or {}),
            created_at=str(data.get("created_at", "") or data.get("imported_at", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "sample_id": self.sample_id,
            "notes": self.notes,
            "data": [d.to_dict() for d in self.data],
            "metadata": self.metadata,
            "created_at": self.created_at,
        }


@dataclass
class SampleEntry:
    """样本(S001 自动编号,可被实验引用)。"""

    sample_id: str
    name: str = ""
    protein_name: str = ""
    sequence: str = ""
    notes: str = ""
    concentration_um: float = 0.0
    buffer: str = ""
    created: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SampleEntry:
        return cls(
            sample_id=str(data.get("sample_id", "")),
            name=str(data.get("name", "")),
            protein_name=str(data.get("protein_name", "")),
            sequence=str(data.get("sequence", "")),
            notes=str(data.get("notes", "")),
            concentration_um=float(data.get("concentration_um", 0.0) or 0.0),
            buffer=str(data.get("buffer", "")),
            created=str(data.get("created", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "name": self.name,
            "protein_name": self.protein_name,
            "sequence": self.sequence,
            "notes": self.notes,
            "concentration_um": self.concentration_um,
            "buffer": self.buffer,
            "created": self.created,
        }


@dataclass
class HistoryEntry:
    """审计历史条目(只追加):id/时间戳/动作/字段。"""

    id: str
    timestamp: str
    action: str
    fields: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HistoryEntry:
        return cls(
            id=str(data.get("id", "")),
            timestamp=str(data.get("timestamp", "")),
            action=str(data.get("action", "")),
            fields=dict(data.get("fields") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "action": self.action,
            "fields": self.fields,
        }


@dataclass
class WorkflowRun:
    """一次处理运行(只追加,R-YYYYMMDD-NNN):输入 SHA-256、参数、产物、快照引用。"""

    run_id: str
    experiment_id: str = ""
    workflow_ref: str = ""
    sample_id: str = ""
    inputs: dict[str, str] = field(default_factory=dict)
    scripts: list[str] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, str] = field(default_factory=dict)
    software_version: str = ""
    tool_versions: dict[str, str] = field(default_factory=dict)
    started_at: str = ""
    finished_at: str = ""
    status: str = "pending"
    message: str = ""
    snapshot_dir: str = ""
    history: list[dict[str, Any]] = field(default_factory=list)
    decisions: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkflowRun:
        return cls(
            run_id=str(data.get("run_id", "")),
            experiment_id=str(data.get("experiment_id", "")),
            workflow_ref=str(data.get("workflow_ref", "")),
            sample_id=str(data.get("sample_id", "")),
            inputs={str(k): str(v) for k, v in (data.get("inputs") or {}).items()},
            scripts=[str(s) for s in (data.get("scripts") or [])],
            params=dict(data.get("params") or {}),
            outputs={str(k): str(v) for k, v in (data.get("outputs") or {}).items()},
            software_version=str(data.get("software_version", "")),
            tool_versions={str(k): str(v) for k, v in (data.get("tool_versions") or {}).items()},
            started_at=str(data.get("started_at", "")),
            finished_at=str(data.get("finished_at", "")),
            status=str(data.get("status", "pending")),
            message=str(data.get("message", "")),
            snapshot_dir=str(data.get("snapshot_dir", "")),
            history=[dict(h) for h in (data.get("history") or [])],
            decisions=[dict(d) for d in (data.get("decisions") or [])],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "experiment_id": self.experiment_id,
            "workflow_ref": self.workflow_ref,
            "sample_id": self.sample_id,
            "inputs": dict(self.inputs),
            "scripts": list(self.scripts),
            "params": self.params,
            "outputs": dict(self.outputs),
            "software_version": self.software_version,
            "tool_versions": dict(self.tool_versions),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "status": self.status,
            "message": self.message,
            "snapshot_dir": self.snapshot_dir,
            "history": list(self.history),
            "decisions": list(self.decisions),
        }


@dataclass
class ProjectInfo:
    """项目根对象,对应 project.json 顶层结构。"""

    schema_version: str = SCHEMA_VERSION
    name: str = ""
    protein: ProteinInfo = field(default_factory=ProteinInfo)
    experiment_type: str = ""
    created: str = ""
    updated: str = ""
    directories: dict[str, str] = field(default_factory=dict)
    experiments: list[ExperimentEntry] = field(default_factory=list)
    processing_history: list[HistoryEntry] = field(default_factory=list)
    samples: list[SampleEntry] = field(default_factory=list)
    workflow_runs: list[WorkflowRun] = field(default_factory=list)
    decisions: list[dict[str, Any]] = field(default_factory=list)

    def experiment(self, exp_id: str) -> ExperimentEntry | None:
        return next((e for e in self.experiments if e.id == exp_id), None)

    def sample(self, sample_id: str) -> SampleEntry | None:
        return next((s for s in self.samples if s.sample_id == sample_id), None)

    def run(self, run_id: str) -> WorkflowRun | None:
        return next((r for r in self.workflow_runs if r.run_id == run_id), None)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectInfo:
        return cls(
            schema_version=str(data.get("schema_version", "1.0")),
            name=str(data.get("name", "")),
            protein=ProteinInfo.from_dict(data.get("protein")),
            experiment_type=str(data.get("experiment_type", "")),
            created=str(data.get("created", "")),
            updated=str(data.get("updated", "")),
            directories={str(k): str(v) for k, v in (data.get("directories") or {}).items()},
            experiments=[ExperimentEntry.from_dict(e) for e in (data.get("experiments") or [])],
            processing_history=[
                HistoryEntry.from_dict(h) for h in (data.get("processing_history") or [])
            ],
            samples=[SampleEntry.from_dict(s) for s in (data.get("samples") or [])],
            workflow_runs=[WorkflowRun.from_dict(r) for r in (data.get("workflow_runs") or [])],
            decisions=[dict(d) for d in (data.get("decisions") or [])],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "name": self.name,
            "protein": self.protein.to_dict(),
            "experiment_type": self.experiment_type,
            "created": self.created,
            "updated": self.updated,
            "directories": dict(self.directories),
            "experiments": [e.to_dict() for e in self.experiments],
            "processing_history": [h.to_dict() for h in self.processing_history],
            "samples": [s.to_dict() for s in self.samples],
            "workflow_runs": [r.to_dict() for r in self.workflow_runs],
            "decisions": list(self.decisions),
        }
