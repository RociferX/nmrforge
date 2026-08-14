"""样本/实验/数据三级注释读写 helper(GUI 侧约定)。

- 样本注释:ProjectInfo.protein.notes(项目即样本,0.2.55 术语统一);
- 实验注释:ExperimentEntry.notes;
- 数据注释:ExperimentEntry.metadata["data_notes"][data_id](约定键,
  metadata 为模型自由 dict,不改 Shared Contract 结构)。
写操作由调用方在 manager.save() 前调用。
"""

from __future__ import annotations


def sample_note(project) -> str:
    """样本(项目)注释:protein.notes。"""
    protein = getattr(project, "protein", None)
    return str(getattr(protein, "notes", "") or "")


def set_sample_note(project, text: str) -> None:
    protein = getattr(project, "protein", None)
    if protein is not None:
        protein.notes = str(text or "")


def experiment_note(project, exp_id: str) -> str:
    entry = project.experiment(exp_id) if project is not None else None
    return str(getattr(entry, "notes", "") or "") if entry is not None else ""


def set_experiment_note(project, exp_id: str, text: str) -> None:
    entry = project.experiment(exp_id) if project is not None else None
    if entry is not None:
        entry.notes = str(text or "")


def data_note(project, exp_id: str, data_id: str) -> str:
    if project is None:
        return ""
    entry = project.experiment(exp_id)
    if entry is None:
        return ""
    data_notes = (entry.metadata or {}).get("data_notes") or {}
    return str(data_notes.get(data_id, "") or "")


def set_data_note(project, exp_id: str, data_id: str, text: str) -> None:
    if project is None:
        return
    entry = project.experiment(exp_id)
    if entry is None:
        return
    meta = dict(entry.metadata or {})
    data_notes = dict(meta.get("data_notes") or {})
    data_notes[data_id] = str(text or "")
    meta["data_notes"] = data_notes
    entry.metadata = meta
