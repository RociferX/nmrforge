"""导入工作流:把 Bruker 数据集导入项目(步骤化流程第 1 步)。

流程(API_CONTRACT §8 / G2B-002):只读实验参数 + 复制必要文件到
raw/<exp_id>/<data_id>/ → SHA-256 指纹 → metadata/<exp_id>-<data_id>.json
→ WorkflowRun(import) 登记。不生成 FID、不生成谱(第 2/3 步分别由
convert_to_fid 与 process/reconstruct_nus 完成)。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.data.bruker_reader import read_dataset
from core.data.internal_data_model import Experiment
from core.project import ProjectManager, WorkflowRun
from core.project.manager import atomic_write_json, sha256_file

METADATA_SCHEMA_VERSION = "1.0"
IMPORT_WORKFLOW_REF = "import"

# 指纹计算的权威 Bruker 参数/数据文件(存在即计入 WorkflowRun.inputs)
KEY_FILES = ("acqus", "acqu2s", "acqu3s", "ser", "fid", "nuslist")


class ImportWorkflowError(Exception):
    """导入工作流错误(参数校验/IO/项目状态)。"""


@dataclass
class ImportResult:
    """一次导入的结果摘要(GUI 展示用)。"""

    experiment_id: str
    run_id: str
    source: Path
    raw_dir: Path | None
    metadata_path: Path
    checksums: dict[str, str]
    file_count: int
    total_bytes: int
    warnings: list[str] = field(default_factory=list)
    data_id: str = ""  # schema 1.2:数据条目 d_001


def _dataset_summary(experiment: Experiment) -> dict[str, Any]:
    """把 Experiment 压缩为 metadata JSON 的可序列化摘要。"""
    return {
        "dataset_id": experiment.dataset_id,
        "ndim": experiment.ndim,
        "dimensions": [
            {
                "logical_axis": dim.logical_axis,
                "nucleus": dim.nucleus,
                "sf": dim.sf,
                "sw": dim.sw,
                "o1": dim.o1,
                "o1p": dim.o1p,
                "td": dim.td,
                "ft_size": dim.ft_size,
                "acquisition_mode": dim.acquisition_mode,
                "role": dim.role.value if dim.role else "",
            }
            for dim in experiment.dimensions
        ],
        "sampling": {
            "mode": experiment.sampling.mode.value,
            "sampling_fraction": experiment.sampling.sampling_fraction,
            "schedule_type": experiment.sampling.schedule_type,
        },
        "experiment_type": {
            "name": experiment.experiment_type.name,
            "confidence": experiment.experiment_type.confidence,
            "evidence": list(experiment.experiment_type.evidence),
        },
    }


def _iter_files(root: Path) -> list[Path]:
    """目录下全部文件(按相对路径排序,保证清单稳定)。"""
    return sorted(p for p in root.rglob("*") if p.is_file())


def _manifest(root: Path) -> tuple[dict[str, str], int, int]:
    """计算目录内每个文件的 SHA-256,返回 (相对路径→摘要, 文件数, 字节数)。"""
    checksums: dict[str, str] = {}
    total_bytes = 0
    for path in _iter_files(root):
        rel = path.relative_to(root).as_posix()
        checksums[rel] = sha256_file(path)
        total_bytes += path.stat().st_size
    return checksums, len(checksums), total_bytes


def _key_checksums(root: Path) -> dict[str, str]:
    """权威 Bruker 文件指纹(ser/fid 大文件只算存在者)。"""
    return {
        name: sha256_file(root / name)
        for name in KEY_FILES
        if (root / name).is_file()
    }


def _validate_dataset_dir(path: Path) -> None:
    if not (path / "acqus").is_file():
        raise ImportWorkflowError(f"不是 Bruker 数据集目录(缺少 acqus): {path}")


def import_data(
    manager: ProjectManager,
    exp_id: str,
    source: Path | str,
    *,
    segments: list[Path | str] | None = None,
    copy: bool = True,
) -> ImportResult:
    """导入数据到实验:只读参数 + 复制 raw/<exp_id>/<data_id>/ + metadata + import run。

    不生成 FID、不生成谱;调用方需自行 manager.save()。
    """
    if manager.project is None or manager.root is None:
        raise ImportWorkflowError("未加载项目,无法导入数据")
    src = Path(source).resolve()
    _validate_dataset_dir(src)
    segment_paths = [Path(seg).resolve() for seg in (segments or [])]
    for seg in segment_paths:
        _validate_dataset_dir(seg)

    # 登记前先解析元数据,避免留下半成品条目
    experiment = read_dataset(src)

    warnings: list[str] = []
    should_copy = copy
    if should_copy and src.is_relative_to(manager.root):
        warnings.append(f"源目录已在项目内,跳过复制(引用原路径): {src}")
        should_copy = False

    data_entry = manager.import_data(exp_id, str(src), segments=segment_paths)
    data_id = data_entry.id

    run: WorkflowRun | None = None
    copied_dir: Path | None = None
    metadata_path = manager.dir_path("metadata") / f"{exp_id}-{data_id}.json"
    try:
        effective_root = src
        if should_copy:
            copied_dir = manager.dir_path("raw") / exp_id / data_id
            shutil.copytree(src, copied_dir)
            data_entry.raw_dir = str(copied_dir)
            if segment_paths:
                seg_base = copied_dir / "segments"
                seg_base.mkdir()
                copied_segments: list[str] = []
                for index, seg in enumerate(segment_paths, start=1):
                    dest = seg_base / f"{index:02d}"
                    shutil.copytree(seg, dest)
                    copied_segments.append(str(dest))
                data_entry.segments = copied_segments
            effective_root = copied_dir

        checksums = _key_checksums(effective_root)
        manifest_checksums, file_count, total_bytes = _manifest(effective_root)
        data_entry.checksums = checksums

        inputs = {"source_path": str(src)}
        inputs.update(
            {f"sha256:{name}": digest for name, digest in sorted(checksums.items())}
        )
        run = manager.start_run(
            exp_id,
            workflow_ref=IMPORT_WORKFLOW_REF,
            inputs=inputs,
            params={
                "copy": should_copy,
                "data_id": data_id,
                "segments": [str(seg) for seg in data_entry.segments],
                "file_count": file_count,
                "total_bytes": total_bytes,
            },
        )

        metadata = {
            "schema_version": METADATA_SCHEMA_VERSION,
            "experiment_id": exp_id,
            "data_id": data_id,
            "source_path": str(src),
            "copied_to": (
                copied_dir.relative_to(manager.root).as_posix()
                if copied_dir is not None
                else None
            ),
            "imported_at": data_entry.imported_at,
            "dataset": _dataset_summary(experiment),
            "segments": [str(seg) for seg in data_entry.segments],
            "manifest": {
                "file_count": file_count,
                "total_bytes": total_bytes,
                "checksums": manifest_checksums,
            },
            "workflow_run_id": run.run_id,
        }
        atomic_write_json(metadata_path, metadata)
        data_entry.metadata_path = metadata_path.relative_to(manager.root).as_posix()

        outputs = {"metadata": metadata_path.relative_to(manager.root).as_posix()}
        if copied_dir is not None:
            outputs["raw_dir"] = copied_dir.relative_to(manager.root).as_posix()
        manager.finish_run(run.run_id, "success", outputs=outputs, message="导入完成")
    except Exception:
        if run is not None:
            try:
                manager.finish_run(
                    run.run_id, "failed", message="导入失败,已回滚登记"
                )
            except Exception:  # noqa: BLE001 - 回滚失败不掩盖原始错误
                pass
        if copied_dir is not None and copied_dir.is_dir():
            shutil.rmtree(copied_dir)
        if metadata_path.is_file():
            metadata_path.unlink()
        if manager.project is not None:
            entry = manager.project.experiment(exp_id)
            if entry is not None and data_entry in entry.data:
                entry.data.remove(data_entry)
        raise

    return ImportResult(
        experiment_id=exp_id,
        run_id=run.run_id,
        source=src,
        raw_dir=copied_dir,
        metadata_path=metadata_path,
        checksums=checksums,
        file_count=file_count,
        total_bytes=total_bytes,
        warnings=warnings,
        data_id=data_id,
    )


def import_bruker_dataset(
    manager: ProjectManager,
    source: Path | str,
    *,
    title: str = "",
    sample_id: str = "",
    segments: list[Path | str] | None = None,
    copy: bool = True,
) -> ImportResult:
    """便捷入口(向后兼容):创建实验并导入第一个数据。

    等价 create_experiment + import_data;失败时移除空白实验。
    """
    if manager.project is None:
        raise ImportWorkflowError("未加载项目,无法导入实验")
    entry = manager.create_experiment(title=title, sample_id=sample_id)
    try:
        return import_data(manager, entry.id, source, segments=segments, copy=copy)
    except Exception:
        if (
            manager.project is not None
            and entry in manager.project.experiments
            and not entry.data
        ):
            manager.project.experiments.remove(entry)
        raise


__all__ = [
    "IMPORT_WORKFLOW_REF",
    "ImportResult",
    "ImportWorkflowError",
    "import_bruker_dataset",
    "import_data",
]
