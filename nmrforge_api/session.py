"""研究会话:一个目录 = 一个研究项目(一个或多个条件数据集 + 参考 + workflow)。

目录布局(全部相对研究根 ``root``)::

    root/
      project.json            NMRForge 项目(登记数据与运行)
      <exp_id>/<data_id>/     项目数据(raw/ process/ spectra/ peaks/ ...)
      study/
        study.json            研究状态(条件数据集列表、参考谱摘要)
        work/                 处理工作目录(共享 fid + 每次运行的脚本/候选谱)
        reference/<key>/      冻结的参考谱/参考脚本 + 参考峰表 + reference.json
        workflows/W0001/      每个参数组合一个目录
            workflow.json     组合级记录(parameters_requested/used、状态、版本)
            log.txt           组合级完整日志
            <condition>/      每个条件一个子目录(A/B)
                process.com   该条件实际执行的完整处理脚本
                spectrum.ft2  该条件候选谱(不替换活动谱)
                peak_table_<所选方法>.csv
                log.txt      该条件的完整运行日志
                run.json     该条件的完整溯源记录
        records/              汇总产物(manifest/workflows/runs/峰表长表)

设计约束(与 GUI 完全解耦):

- 不 import Qt;可在无显示环境/集群上运行;
- 不修改项目里「活动谱」以外的任何状态;workflow 候选谱只写到 study/ 内,
  绝不替换 ``spectra/`` 下的活动谱;
- 数据集只读:导入走 ``workflow.import_workflow.import_data``(含 Kinetics
  等策略守卫),不绕过策略;
- 多条件(A/B):每个条件一份参考(相位/噪声按该条件自身数据自动优化),
  **参考峰身份与用户参数组合全条件共享**——CSP 需要同一批峰、同一组参数。
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
from core.version import software_version, tool_versions
from nmrforge_api.errors import DatasetError

STUDY_DIRNAME = "study"
STUDY_STATE_FILENAME = "study.json"
API_VERSION = "0.2"
#: 条件标签自动分配顺序(A/B/C…)
CONDITION_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def condition_token(label: str, fallback: str = "condition") -> str:
    """条件标签 → 唯一且目录安全的 token。

    已经安全的 ASCII 标签保持不变；清洗过的标签追加原文短哈希，避免
    ``A/B`` 与 ``A_B``、不同非 ASCII 标签映射到同一目录。
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
    """一次导入到研究项目里的数据集引用(可带条件标签 A/B)。"""

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
        """workflow 目录里的条件子目录名(缺条件标签时用数据 key)。"""
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
    """研究会话句柄:项目 + 后端 + 研究目录(可含多个条件数据集)。"""

    root: Path
    manager: ProjectManager
    backend: Any
    datasets: list[DatasetRef] = field(default_factory=list)
    created: str = field(default_factory=now_iso)

    # ---- 数据集(条件) -------------------------------------------------
    @property
    def dataset(self) -> DatasetRef | None:
        """主(第一个)条件的数据集;兼容单数据集调用方。"""
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
        """登记一个已导入的数据集引用(条件标签唯一)。"""
        if ref.condition and self.dataset_by_condition(ref.condition) is not None:
            raise DatasetError(f"条件标签重复: {ref.condition}")
        if any(existing.key == ref.key for existing in self.datasets):
            raise DatasetError(f"数据集已登记: {ref.key}")
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
    def workflows_dir(self) -> Path:
        return self.study_dir / "workflows"

    @property
    def runs_dir(self) -> Path:
        """旧名(兼容):每个 workflow 的目录所在位置。"""
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
            "datasets": [ref.to_dict() for ref in self.datasets],
            # 旧读兼容:第一条件仍是 dataset
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

    def data_entry(self, dataset: DatasetRef | None = None) -> Any:
        ref = dataset or self.dataset
        if ref is None:
            raise DatasetError("研究里还没有数据集,先调用 add_dataset()")
        return self.manager.data(ref.exp_id, ref.data_id)

    def save(self) -> None:
        self.manager.save()
        self.save_state()


def _datasets_from_state(state: dict[str, Any]) -> list[DatasetRef]:
    """研究状态 → 条件数据集列表(兼容只有 ``dataset`` 的旧状态)。"""
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
    """拒绝在大小写不敏感文件系统上会共用目录的数据集标签。"""
    seen: dict[str, DatasetRef] = {}
    for ref in refs:
        key = ref.token.casefold()
        previous = seen.get(key)
        if previous is not None and previous.key != ref.key:
            raise DatasetError(
                "条件目录 token 冲突: "
                f"{previous.condition or previous.key!r} 与 "
                f"{ref.condition or ref.key!r} 都映射为 {ref.token!r}"
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
    """打开/创建研究项目(研究根 = NMRForge 项目根)。

    - ``root/project.json`` 存在则打开,否则在 ``create=True`` 时新建项目;
    - ``backend`` 显式传入时直接使用(测试/集群可注入);否则按默认配置
      构造 NMRPipe 后端(``backend.factory.create_backend``);
    - 研究状态(条件数据集列表)从 ``study/study.json`` 恢复。
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
    """导入一个 Bruker 原始数据集并登记为研究数据(可带条件标签 A/B)。

    只做导入(链接 raw + metadata + import 运行记录),不做转换/处理;
    失败信息统一包成 :class:`DatasetError`(含原始异常文本)。
    ``condition`` 缺省时自动分配下一个未用字母(A/B/C…);多条件研究用不同
    标签区分同一 workflow 的两组数据(A_raw → W0037 → A_peak_table)。
    ``make_default`` 只在会话里第一个数据集时决定「主条件」。
    """
    from workflow.import_workflow import import_data

    label = str(condition or "").strip() or session.next_condition_label()
    existing = session.dataset_by_condition(label)
    if existing is not None:
        raise DatasetError(
            f"条件标签 {label!r} 已被 {existing.key} 占用;"
            "多条件请给不同标签(如 A / B)"
        )
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
    """数据集摘要(维度/核/采样方式/来源),供下游项目写进论文材料。"""
    ref = dataset or session.dataset
    if ref is None:
        raise DatasetError("研究里还没有数据集,先调用 add_dataset()")
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
