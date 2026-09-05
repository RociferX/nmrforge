"""项目/实验/样品数据三级注释读写 helper(GUI 侧约定)。

- 项目注释:ProjectInfo.protein.notes(JSON 字段串,兼容旧纯文本);
- 实验注释:ExperimentEntry.metadata["note_fields"](dict,约定键);
- 样品数据注释:ExperimentEntry.metadata["data_notes"][data_id](dict,约定键)。
各级字段为「常规信息列表」,由用户按表单逐行填写;写操作由调用方在
manager.save() 前调用。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from viewer.axis_labels import infer_nucleus

# 各级注释字段(键 / 显示名),0.2.79 起按层级区分:
# 项目=蛋白样品基本信息;实验类型=实验类型(指认实验/动力学实验);
# 样品数据=重复/条件/pH/温度 + 维度/数据类型(presets)/核。
SAMPLE_FIELDS: tuple[tuple[str, str], ...] = (
    ("protein_name", "蛋白名称"),
    ("expression_system", "表达系统"),
    ("concentration", "浓度"),
    ("buffer", "Buffer"),
    ("notes", "备注"),
)
EXPERIMENT_FIELDS: tuple[tuple[str, str], ...] = (
    ("experiment_type", "实验类型"),
    ("sample_name", "样品名称"),
    ("instrument", "仪器"),
    ("temperature", "温度(°C)"),
    ("notes", "备注"),
)
DATA_FIELDS: tuple[tuple[str, str], ...] = (
    ("repeat", "重复号"),
    ("condition", "Buffer 组分"),
    ("buffer_ph", "Buffer pH"),
    ("temperature", "温度(°C)"),
    ("dimension", "维度"),
    ("experiment_type", "数据类型"),
    ("nuclei", "核"),
    ("notes", "备注"),
)

_FIELD_BY_KIND: dict[str, tuple[tuple[str, str], ...]] = {
    "project": SAMPLE_FIELDS,
    "experiment": EXPERIMENT_FIELDS,
    "data": DATA_FIELDS,
}

# 仅有几种取值的字段,表单直接给下拉选项(0.2.85)
DIMENSION_OPTIONS: tuple[str, ...] = ("2D", "3D")
NUCLEI_OPTIONS: tuple[str, ...] = (
    "1H-15N",
    "1H-13C",
    "1H-1H",
    "1H-15N-13C",
    "1H-13C-15N",
    "1H-13C-1H",
    "1H-15N-1H",
    "13C-13C-1H",
)
# 实验注释:「实验类型」仅两种取值:指认实验 / 动力学实验
EXPERIMENT_CATEGORY_OPTIONS: tuple[str, ...] = ("指认实验", "动力学实验")
_GENERIC_PRESET_NAMES = {"Generic1D", "Generic2D", "Generic3D"}
_PRESET_OPTIONS: list[tuple[str, int]] | None = None


def _preset_options() -> list[tuple[str, int]]:
    """presets/*.yaml 的 (name, ndim) 列表(惰性加载 + 缓存)。"""
    global _PRESET_OPTIONS
    if _PRESET_OPTIONS is not None:
        return _PRESET_OPTIONS
    import yaml

    from core.app_paths import resource_path

    options: list[tuple[str, int]] = []
    presets_dir = resource_path("presets")
    try:
        files = sorted(presets_dir.glob("*.yaml")) if presets_dir.is_dir() else []
    except OSError:
        files = []
    for file in files:
        try:
            data = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001 - 单个模板损坏不影响其它选项
            continue
        name = str(data.get("name", "")).strip()
        ndim = int(data.get("constraints", {}).get("ndim", 0) or 0)
        if name and name not in _GENERIC_PRESET_NAMES and (name, ndim) not in options:
            options.append((name, ndim))
    _PRESET_OPTIONS = options
    return options


def _ndim_int(ndim: str | int) -> int:
    """把 '2D'/'3D' 或 2/3 归一化为维度数;无法解析返回 0。"""
    digits = "".join(ch for ch in str(ndim) if ch.isdigit())
    try:
        return int(digits) if digits else 0
    except (TypeError, ValueError):
        return 0


def experiment_type_options(ndim: str | int = "") -> list[str]:
    """常见数据类型选项(来自 presets 模板,HSQC 等);ndim 非空时按维度过滤。"""
    if ndim in ("", None):
        return [name for name, _ in _preset_options()]
    target = _ndim_int(ndim)
    if target <= 0:
        return []
    return [name for name, n in _preset_options() if n == target]

_FIELD_LABELS: dict[str, str] = {
    key: label
    for schema in (SAMPLE_FIELDS, DATA_FIELDS, EXPERIMENT_FIELDS)
    for key, label in schema
}
# 样品数据注释中 presets 实验类型字段显示为「数据类型」(2026-08-18)
_DATA_FIELD_LABELS: dict[str, str] = dict(_FIELD_LABELS)
_DATA_FIELD_LABELS["experiment_type"] = "数据类型"


def note_fields(kind: str) -> tuple[tuple[str, str], ...]:
    """某层级的注释字段列表(键, 显示名)。"""
    return _FIELD_BY_KIND.get(kind, ())


def format_fields(fields: dict, labels: dict | None = None) -> str:
    """字段 dict → 多行「显示名: 值」(跳过空值);labels 覆盖显示名。"""
    label_map = labels if labels is not None else _FIELD_LABELS
    lines: list[str] = []
    for key, value in (fields or {}).items():
        text = str(value or "").strip()
        if not text:
            continue
        lines.append(f"{label_map.get(key, key)}: {text}")
    return "\n".join(lines)


def _clean(fields: dict) -> dict[str, str]:
    return {str(key): str(value or "").strip() for key, value in (fields or {}).items()}


def _loads(value: str) -> dict:
    try:
        data = json.loads(value or "")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------- 项目
def sample_note_fields(project) -> dict:
    """项目结构化注释:protein.notes 中 JSON 字段。"""
    protein = getattr(project, "protein", None)
    return _loads(str(getattr(protein, "notes", "") or ""))


def set_sample_note_fields(project, fields: dict) -> None:
    protein = getattr(project, "protein", None)
    if protein is not None:
        protein.notes = json.dumps(_clean(fields), ensure_ascii=False)


def sample_note(project) -> str:
    """项目注释展示文本(结构化字段多行;旧纯文本直接返回)。"""
    fields = sample_note_fields(project)
    if fields:
        return format_fields(fields)
    protein = getattr(project, "protein", None)
    return str(getattr(protein, "notes", "") or "")


# ---------------------------------------------------------------- 实验类型
def experiment_note_fields(project, exp_id: str) -> dict:
    entry = project.experiment(exp_id) if project is not None else None
    if entry is None:
        return {}
    fields = (entry.metadata or {}).get("note_fields")
    return dict(fields) if isinstance(fields, dict) else {}


def set_experiment_note_fields(project, exp_id: str, fields: dict) -> None:
    entry = project.experiment(exp_id) if project is not None else None
    if entry is None:
        return
    meta = dict(entry.metadata or {})
    meta["note_fields"] = _clean(fields)
    entry.metadata = meta


def experiment_note(project, exp_id: str) -> str:
    """实验注释展示文本(结构化字段优先,兼容 entry.notes 纯文本)。"""
    fields = experiment_note_fields(project, exp_id)
    if fields:
        return format_fields(fields)
    entry = project.experiment(exp_id) if project is not None else None
    return str(getattr(entry, "notes", "") or "") if entry is not None else ""


# ---------------------------------------------------------------- 数据
def data_note_fields(project, exp_id: str, data_id: str) -> dict:
    if project is None:
        return {}
    entry = project.experiment(exp_id)
    if entry is None:
        return {}
    value = ((entry.metadata or {}).get("data_notes") or {}).get(data_id)
    return dict(value) if isinstance(value, dict) else {}


def set_data_note_fields(project, exp_id: str, data_id: str, fields: dict) -> None:
    if project is None:
        return
    entry = project.experiment(exp_id)
    if entry is None:
        return
    meta = dict(entry.metadata or {})
    data_notes = dict(meta.get("data_notes") or {})
    data_notes[data_id] = _clean(fields)
    meta["data_notes"] = data_notes
    entry.metadata = meta


def data_note(project, exp_id: str, data_id: str) -> str:
    """样品数据注释展示文本(结构化字段优先;兼容旧纯文本字符串)。"""
    fields = data_note_fields(project, exp_id, data_id)
    if fields:
        return format_fields(fields, _DATA_FIELD_LABELS)
    if project is None:
        return ""
    entry = project.experiment(exp_id)
    if entry is None:
        return ""
    raw = ((entry.metadata or {}).get("data_notes") or {}).get(data_id)
    return str(raw or "") if isinstance(raw, str) else ""


# ---------------------------------------------------------------- 导入自动填充
def _acqus_value(raw_dir, key: str) -> str:
    """从 Bruker acqus 读单个参数值(如 TE)。"""
    acqus = Path(raw_dir) / "acqus"
    try:
        text = acqus.read_text(encoding="latin-1", errors="replace")
    except OSError:
        return ""
    match = re.search(rf"##\${key}=([^\n]+)", text)
    return match.group(1).strip() if match else ""


def temperature_from_acqus(raw_dir) -> str:
    """Bruker TE → 摄氏温度字符串;自动识别 0.1 K / K / °C,无法解析返回空串。

    - TE 惯例为 0.1 K(如 2980 → 298.0 K → 24.9 °C);
    - 部分数据直接存 K(如 298.0)或 °C(如 25),按数值范围判定。
    """
    value = _acqus_value(raw_dir, "TE")
    if not value:
        return ""
    try:
        number = float(value.split()[0])
    except (TypeError, ValueError):
        return ""
    kelvin_tenths = number / 10.0
    celsius: float | None = None
    if 240.0 <= kelvin_tenths <= 340.0:
        celsius = kelvin_tenths - 273.15
    elif 240.0 <= number <= 340.0:
        celsius = number - 273.15
    elif -40.0 <= number <= 100.0:
        celsius = number
    return f"{celsius:.1f}" if celsius is not None else ""


def _raw_dir_for(project, exp_id: str, data_id: str) -> Path | None:
    try:
        raw = project.data_dir(exp_id, data_id, "raw")
        if raw.is_dir():
            return raw
    except Exception:  # noqa: BLE001 - 布局不可用按无原始目录处理
        pass
    return None


def auto_fill_notes_from_metadata(
    manager, exp_id: str, data_id: str, metadata: dict
) -> dict:
    """导入后按 Bruker 文件/元数据自动填充样品数据注释里能填的字段(不覆盖已有值)。

    样品数据注释:维度 / 数据类型(presets 名)/ 核 / 温度(acqus TE)。
    返回本次实际填充的 {字段: 值} 摘要。
    """
    filled: dict[str, str] = {}
    project = manager.project if manager is not None else None
    if project is None:
        return filled
    dataset = (metadata or {}).get("dataset") or {}

    data_fields = dict(data_note_fields(project, exp_id, data_id))
    ndim = dataset.get("ndim")
    if ndim and not data_fields.get("dimension"):
        data_fields["dimension"] = f"{int(ndim)}D"
    exptype = str((dataset.get("experiment_type") or {}).get("name", "") or "")
    if (
        exptype
        and not data_fields.get("experiment_type")
        and exptype.lower() not in ("unknown", "generic", "generic2d", "generic3d")
    ):
        data_fields["experiment_type"] = exptype
    nuclei: list[str] = []
    for dim in dataset.get("dimensions") or []:
        try:
            sf = float(dim.get("sf", 0) or 0)
        except (TypeError, ValueError):
            sf = 0.0
        # 0.2.89:优先按化学位移(观测频率 sf)推断核,失败回退存储字段
        nucleus = infer_nucleus(sf) or str(dim.get("nucleus", "") or "").strip()
        if nucleus:
            nuclei.append(nucleus)
    if nuclei and not data_fields.get("nuclei"):
        data_fields["nuclei"] = "-".join(nuclei)
    if not data_fields.get("temperature"):
        raw_dir = _raw_dir_for(manager, exp_id, data_id)
        temp = temperature_from_acqus(raw_dir) if raw_dir is not None else ""
        if temp:
            data_fields["temperature"] = temp
    set_data_note_fields(project, exp_id, data_id, data_fields)
    for key in ("dimension", "experiment_type", "nuclei", "temperature"):
        value = data_fields.get(key, "")
        if value:
            filled[f"data.{key}"] = value
    return filled
