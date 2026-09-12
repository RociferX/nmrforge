"""研究会话:一个目录 = 一个研究项目(数据集 + 参考谱 + 扫描记录)。

目录布局(全部相对研究根 ``root``)::

    root/
      project.json            NMRForge 项目(schema 1.4,登记数据集与运行)
      <exp_id>/<data_id>/     项目数据(raw/ process/ spectra/ peaks/ ...)
      study/
        study.json            研究状态(数据集引用、参考谱摘要)
        work/                 处理工作目录:共享 fid + 每次运行的脚本/候选谱
        reference/<key>/      冻结的参考谱与参考脚本 + reference.json
        runs/<run_id>/        每个参数组合的脚本、谱、峰位记录(run.json)
        records/              汇总产物(manifest/runs/peak_positions/...)

设计约束(与 GUI 完全解耦):

- 不 import Qt;可在无显示环境/集群上运行;
- 不修改项目里「活动谱」以外的任何状态;扫描候选谱只写到 study/runs/,
  绝不替换 ``spectra/`` 下的参考谱(与 SMILE Scheme B 同精神);
- 数据集只读:导入走 ``workflow.import_workflow.import_data``(含 Kinetics
  等策略守卫),不绕过策略。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.project import ProjectManager
from core.version import software_version, tool_versions
from nmrforge_api.errors import DatasetError

STUDY_DIRNAME = "study"
STUDY_STATE_FILENAME = "study.json"
API_VERSION = "0.1"


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class DatasetRef:
    """一次导入到研究项目里的数据集引用。"""

    exp_id: str
    data_id: str
    title: str = ""
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "exp_id": self.exp_id,
            "data_id": self.data_id,
            "title": self.title,
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
    """研究会话句柄:项目 + 后端 + 研究目录。"""

    root: Path
    manager: ProjectManager
    backend: Any
    dataset: DatasetRef | None = None
    created: str = field(default_factory=now_iso)

    # ---- 目录 ---------------------------------------------------------
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
    def runs_dir(self) -> Path:
        return self.study_dir / "runs"

    @property
    def records_dir(self) -> Path:
        return self.study_dir / "records"

    def ensure_dirs(self) -> None:
        for path in (
            self.study_dir,
            self.work_dir,
            self.reference_dir,
            self.runs_dir,
            self.records_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def reference_dir_for(self, dataset: DatasetRef | None = None) -> Path:
        ref = dataset or self.dataset
        if ref is None:
            raise DatasetError("研究里还没有数据集,先调用 add_dataset()")
        safe = ref.key.replace("/", "_")
        path = self.reference_dir / safe
        path.mkdir(parents=True, exist_ok=True)
        return path

    # ---- 状态 ---------------------------------------------------------
    def save_state(self, **extra: Any) -> Path:
        self.ensure_dirs()
        state = {
            "api_version": API_VERSION,
            "nmrforge_version": software_version(),
            "created": self.created,
            "updated": now_iso(),
            "root": str(self.root),
            "dataset": self.dataset.to_dict() if self.dataset else None,
        }
        state.update(extra)
        self.state_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return self.state_path

    def load_state(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def data_entry(self) -> Any:
        if self.dataset is None:
            raise DatasetError("研究里还没有数据集,先调用 add_dataset()")
        return self.manager.data(self.dataset.exp_id, self.dataset.data_id)

    def save(self) -> None:
        self.manager.save()
        self.save_state()


def open_study(
    root: Path | str,
    *,
    name: str = "",
    backend: Any | None = None,
    config: dict[str, Any] | None = None,
    create: bool = True,
) -> StudySession:
    """打开/创建研究项目(研究根 = NMRForge 项目根)。

    - ``root/project.json`` 存在则打开,否则在 ``create=True`` 时新建项目;
    - ``backend`` 显式传入时直接使用(测试/集群可注入);否则按默认配置
      构造 NMRPipe 后端(``backend.factory.create_backend``);
    - 研究状态(数据集引用)从 ``study/study.json`` 恢复。
    """
    root_path = Path(root).expanduser().resolve()
    project_file = root_path / "project.json"
    if project_file.is_file():
        manager = ProjectManager.open_project(root_path)
    elif create:
        manager = ProjectManager.create_project(root_path, name or root_path.name)
    else:
        raise DatasetError(f"研究根不存在或不是 NMRForge 项目: {root_path}")
    if backend is None:
        from backend.config import load_config
        from backend.factory import create_backend

        backend = create_backend(load_config(config))
    session = StudySession(root=root_path, manager=manager, backend=backend)
    state = session.load_state()
    dataset = state.get("dataset")
    if isinstance(dataset, dict) and dataset.get("data_id"):
        session.dataset = DatasetRef.from_dict(dataset)
    session.ensure_dirs()
    return session


def add_dataset(
    session: StudySession,
    source: Path | str,
    *,
    exp_id: str = "",
    title: str = "",
    make_default: bool = True,
) -> DatasetRef:
    """导入一个 Bruker 原始数据集(公开库下载目录)并登记为研究数据。

    只做导入(链接 raw + metadata + import 运行记录),不做转换/处理;
    失败信息统一包成 :class:`DatasetError`(含原始异常文本)。
    """
    from workflow.import_workflow import import_data

    src = Path(source).expanduser()
    if not src.exists():
        raise DatasetError(f"数据集路径不存在: {src}")
    src = src.resolve()
    try:
        from core.data.bruker_reader import read_dataset

        experiment = read_dataset(src)
    except Exception as exc:  # noqa: BLE001 - 公开库数据常是压缩包/处理后格式
        raise DatasetError(
            f"无法识别为 Bruker 原始数据集: {src} ({type(exc).__name__}: {exc})。"
            "本接口要求下载后解压出含 acqus/ser 的 Bruker 目录。"
        ) from exc

    manager = session.manager
    target_exp = exp_id
    if target_exp:
        if manager.project is None or manager.project.experiment(target_exp) is None:
            raise DatasetError(f"实验不存在: {target_exp}")
    else:
        entry = manager.create_experiment(title or src.name)
        target_exp = entry.id
    try:
        result = import_data(manager, target_exp, src)
    except Exception as exc:  # noqa: BLE001 - 策略守卫(如 Kinetics 拒绝)也在内
        raise DatasetError(f"导入失败: {type(exc).__name__}: {exc}") from exc

    data_id = result.data_id
    if not data_id:
        active = manager.active_data(target_exp)
        data_id = active[-1].id if active else ""
    if not data_id:
        raise DatasetError("导入完成但没有可用的数据条目")
    manager.save()

    ref = DatasetRef(
        exp_id=target_exp,
        data_id=data_id,
        title=str(getattr(experiment.experiment_type, "name", "") or ""),
        ndim=int(experiment.ndim),
        nuclei=[dim.nucleus for dim in experiment.dimensions],
        sampling=str(experiment.sampling.mode),
        source=str(src),
        raw_dir=str(result.raw_dir or ""),
        file_count=int(result.file_count),
        total_bytes=int(result.total_bytes),
    )
    if make_default:
        session.dataset = ref
        session.save_state()
    return ref


def dataset_info(session: StudySession, dataset: DatasetRef | None = None) -> dict[str, Any]:
    """数据集摘要(维度/核/采样方式/来源),供下游项目写进论文材料。"""
    ref = dataset or session.dataset
    if ref is None:
        raise DatasetError("研究里还没有数据集,先调用 add_dataset()")
    info = ref.to_dict()
    info.update(
        {
            "research_root": str(session.root),
            "nmrforge_version": software_version(),
            "tool_versions": tool_versions(),
        }
    )
    return info


__all__ = [
    "API_VERSION",
    "DatasetRef",
    "STUDY_DIRNAME",
    "StudySession",
    "add_dataset",
    "dataset_info",
    "now_iso",
    "open_study",
]
