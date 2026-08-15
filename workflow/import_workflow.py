"""导入工作流:把 Bruker 数据集导入项目(步骤化流程第 1 步)。

流程(API_CONTRACT §8 / G2B-002/G2B-009):只读实验参数 + 链接必要文件到
raw/<exp_id>/<data_id>/(硬链接→符号链接→复制回退;fid.com 等后端可写
文件实体复制)→ SHA-256 指纹 → metadata/<exp_id>-<data_id>.json
→ WorkflowRun(import) 登记。不生成 FID、不生成谱(第 2/3 步分别由
convert_to_fid 与 process/reconstruct_nus 完成)。
"""

from __future__ import annotations

import os
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

# 后端会在 raw 目录改写的文件(如 bruker -AUTO / patch_fid_com 生成的
# fid.com):必须实体复制,不能链接——硬链接/符号链接会把改写写回源数据
# (G2B-009 兼容性条款「fid.com 等后端生成文件始终实体写入,不受影响」)。
WRITABLE_RAW_NAMES = {"fid.com", "profYZ.dat"}  # 转换会 touch/改写 profYZ.dat,实体复制以保护源


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


def _link_one(src: Path, dst: Path) -> str:
    """链接单个文件:符号链接 → 硬链接 → 复制回退(G2B-009,用户指定软链接优先)。"""
    try:
        os.symlink(src, dst)
        return "symlink"
    except OSError:
        pass
    try:
        os.link(src, dst)
        return "hardlink"
    except OSError:
        pass
    shutil.copy2(src, dst)
    return "copy"


def _link_tree(src: Path, dst: Path, stats: dict[str, int]) -> None:
    """按源相对结构建立链接树:目录 mkdir,文件链接(G2B-009)。

    后端可写文件(WRITABLE_RAW_NAMES,如 fid.com)实体复制,不链接。
    """
    dst.mkdir(parents=True, exist_ok=True)
    for item in sorted(src.iterdir()):
        source_item = src / item.name
        dest_item = dst / item.name
        if source_item.is_dir():
            _link_tree(source_item, dest_item, stats)
        elif item.name in WRITABLE_RAW_NAMES:
            shutil.copy2(source_item, dest_item)
            stats["writable"] += 1
        else:
            stats[_link_one(source_item, dest_item)] += 1


def import_data(
    manager: ProjectManager,
    exp_id: str,
    source: Path | str,
    *,
    segments: list[Path | str] | None = None,
    copy: bool = True,
) -> ImportResult:
    """导入数据到实验:只读参数 + 链接 raw/<exp_id>/<data_id>/ + metadata + import run。

    G2B-009:raw 只读文件默认符号链接(用户要求),失败回退硬链接/复制。

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
    link_stats = {"hardlink": 0, "symlink": 0, "copy": 0, "writable": 0}
    should_copy = copy
    if should_copy and src.is_relative_to(manager.root):
        warnings.append(f"源目录已在项目内,跳过复制(引用原路径): {src}")
        should_copy = False

    data_entry = manager.import_data(exp_id, str(src), segments=segment_paths)
    data_id = data_entry.id

    run: WorkflowRun | None = None
    copied_dir: Path | None = None
    metadata_path = manager.data_metadata_path(exp_id, data_id)
    try:
        effective_root = src
        if should_copy:
            copied_dir = manager.data_dir(exp_id, data_id, "raw")
            _link_tree(src, copied_dir, link_stats)
            data_entry.raw_dir = copied_dir.relative_to(manager.root).as_posix()
            if segment_paths:
                seg_base = copied_dir / "segments"
                seg_base.mkdir()
                copied_segments: list[str] = []
                for index, seg in enumerate(segment_paths, start=1):
                    dest = seg_base / f"{index:02d}"
                    _link_tree(seg, dest, link_stats)
                    copied_segments.append(str(dest))
                data_entry.segments = copied_segments
            effective_root = copied_dir
            if link_stats["copy"]:
                warnings.append(
                    f"{link_stats['copy']} 个文件无法建立链接,已回退复制"
                )

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
                "link_stats": dict(link_stats),
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
            "link_stats": dict(link_stats),  # G2B-009:hardlink/symlink/copy/writable
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
