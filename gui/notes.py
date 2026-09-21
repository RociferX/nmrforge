"""Project/experiment/Sample data three-level annotation reading and writing helper(GUI side
convention). - Project comment: ProjectInfo.protein.notes(JSON field string, compatible with old
plain text); - Experiment comment: ExperimentEntry.metadata["note_fields"](dict, convention
key); - Sample data comment: ExperimentEntry.metadata["data_notes"][data_id](dict, convention
key). Fields at all levels are "regular information list", determined by user The form is filled
in line by line; the write operation is called by the caller before manager.save()."""

from __future__ import annotations

import json
import re
from pathlib import Path

from ui_support.i18n import tr
from viewer.axis_labels import infer_nucleus

# Annotation fields at all levels (key/display name), divided by level starting from 0.2.79: project
# = basic information of protein sample; experiment type = currently supported experimental
# category; sample data = repeat/condition/pH/temperature + dimension /data type (presets)/core.
SAMPLE_FIELDS: tuple[tuple[str, str], ...] = (
    ("protein_name", tr("Protein name")),
    ("expression_system", tr("Expression system")),
    ("concentration", tr("Concentration")),
    ("buffer", "Buffer"),
    ("notes", tr("Remark")),
)
EXPERIMENT_FIELDS: tuple[tuple[str, str], ...] = (
    ("experiment_type", tr("Experiment type")),
    ("sample_name", tr("Sample name")),
    ("instrument", tr("Instrument")),
    ("temperature", tr("Temperature (°C)")),
    ("notes", tr("Remark")),
)
DATA_FIELDS: tuple[tuple[str, str], ...] = (
    ("repeat", tr("Repeat number")),
    ("condition", tr("Buffer component")),
    ("buffer_ph", "Buffer pH"),
    ("temperature", tr("Temperature (°C)")),
    ("dimension", tr("Dimensions")),
    ("experiment_type", tr("Data type")),
    ("nuclei", tr("Nuclei")),
    ("peak_sign", tr("Peak symbol")),
    ("notes", tr("Remark")),
)

_FIELD_BY_KIND: dict[str, tuple[tuple[str, str], ...]] = {
    "project": SAMPLE_FIELDS,
    "experiment": EXPERIMENT_FIELDS,
    "data": DATA_FIELDS,
}

# There are only a few fields with values, and the form directly gives drop-down options (0.2.85).
DIMENSION_OPTIONS: tuple[str, ...] = ("1D", "2D", "3D")
NUCLEI_OPTIONS: tuple[str, ...] = (
    "1H",
    "13C",
    "19F",
    "31P",
    "1H-15N",
    "1H-13C",
    "1H-1H",
    "1H-15N-13C",
    "1H-13C-15N",
    "1H-13C-1H",
    "1H-15N-1H",
    "13C-13C-1H",
)
# IMPORT-007 Plan A: The import of kinetic data is prohibited and will not be displayed as an
# optional product capability. Editable dropdowns will still retain customizations already in the
# old project/dynamics text.
EXPERIMENT_CATEGORY_OPTIONS: tuple[str, ...] = (tr("identification experiment"),)
_GENERIC_PRESET_NAMES = {"Generic1D", "Generic2D", "Generic3D"}
_PRESET_OPTIONS: list[tuple[str, int]] | None = None


def _preset_options() -> list[tuple[str, int]]:
    """List of (name, ndim) in presets/*.yaml (lazy loading + cache)."""
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
        # Damage to a single template does not affect other options.
        except Exception:  # noqa: BLE001 -
            continue
        name = str(data.get("name", "")).strip()
        ndim = int(data.get("constraints", {}).get("ndim", 0) or 0)
        if name and name not in _GENERIC_PRESET_NAMES and (name, ndim) not in options:
            options.append((name, ndim))
    _PRESET_OPTIONS = options
    return options


def _ndim_int(ndim: str | int) -> int:
    """Normalizes '2D'/'3D' or 2/3 to dimension number; cannot parse and returns 0."""
    digits = "".join(ch for ch in str(ndim) if ch.isdigit())
    try:
        return int(digits) if digits else 0
    except (TypeError, ValueError):
        return 0


def experiment_type_options(ndim: str | int = "") -> list[str]:
    """Common data type options (from presets template, HSQC, etc.); filter by dimension when ndim
    is non-empty."""
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
# The presets experiment type field in the sample data annotation is displayed as "data type"
# (2026-08-18).
_DATA_FIELD_LABELS: dict[str, str] = dict(_FIELD_LABELS)
_DATA_FIELD_LABELS["experiment_type"] = tr("Data type")


def note_fields(kind: str) -> tuple[tuple[str, str], ...]:
    """A list of annotation fields at a certain level (key, display name)."""
    return _FIELD_BY_KIND.get(kind, ())


def format_fields(fields: dict, labels: dict | None = None) -> str:
    """Field dict -> multi-line "display name: value" (skip empty values); labels overwrite the
    display name."""
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


# ---------------------------------------------------------------- project.
def sample_note_fields(project) -> dict:
    """Project structured comments: protein.notes in JSON fields."""
    protein = getattr(project, "protein", None)
    return _loads(str(getattr(protein, "notes", "") or ""))


def set_sample_note_fields(project, fields: dict) -> None:
    protein = getattr(project, "protein", None)
    if protein is not None:
        protein.notes = json.dumps(_clean(fields), ensure_ascii=False)


def sample_note(project) -> str:
    """Project comment display text (structured fields have multiple lines; old plain text is
    returned directly)."""
    fields = sample_note_fields(project)
    if fields:
        return format_fields(fields)
    protein = getattr(project, "protein", None)
    return str(getattr(protein, "notes", "") or "")


# -------------------------------------------------------------------------- experiment type.
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
    """Experiment annotation display text (structured fields take precedence, compatible with
    entry.notes plain text)."""
    fields = experiment_note_fields(project, exp_id)
    if fields:
        return format_fields(fields)
    entry = project.experiment(exp_id) if project is not None else None
    return str(getattr(entry, "notes", "") or "") if entry is not None else ""


# ---------------------------------------------------------------- data.
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


def group_note(project, exp_id: str, group_id: str) -> str:
    """Data group comments: List the comments for each data in the group."""
    if project is None:
        return ""
    exp = project.experiment(exp_id)
    if exp is None:
        return ""
    group = next((g for g in getattr(exp, "groups", None) or [] if g.id == group_id), None)
    if group is None:
        return ""
    lines: list[str] = []
    for data_id in (group.data_ids or []):
        d = next((x for x in exp.data if x.id == data_id), None)
        label = (d.title or f"Data {data_id}") if d else f"Data {data_id}"
        note = data_note(project, exp_id, data_id)
        lines.append(f"◆ {label} ({data_id})")
        lines.append("   " + (note if note else tr("(not filled in)")))
    return "\n".join(lines)


def data_note(project, exp_id: str, data_id: str) -> str:
    """Sample data annotation display text (structured fields take precedence; compatible with old
    plain text strings)."""
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


# --------------------------------------------------------------------- Import AutoFill.
def _acqus_value(raw_dir, key: str) -> str:
    """Read a single parameter value (such as TE) from Bruker acqus."""
    acqus = Path(raw_dir) / "acqus"
    try:
        text = acqus.read_text(encoding="latin-1", errors="replace")
    except OSError:
        return ""
    match = re.search(rf"##\${key}=([^\n]+)", text)
    return match.group(1).strip() if match else ""


def temperature_from_acqus(raw_dir) -> str:
    """Bruker TE -> Celsius temperature string; automatically recognizes 0.1 K / K / °C, cannot be
    parsed and returns an empty string. - TE The convention is 0.1 K (such as 2980 -> 298.0 K ->
    24.9 °C); - Some data are directly stored in K (such as 298.0) or °C (such as 25), and are
    judged according to the numerical range."""
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
    # If the layout is not available, it will be processed as no original directory.
    except Exception:  # noqa: BLE001 -
        pass
    return None


def auto_fill_notes_from_metadata(
    manager, exp_id: str, data_id: str, metadata: dict
) -> dict:
    """After importing, press Bruker file / metadata to automatically fill in the fields that can
    be filled in the sample data annotation (without overwriting existing values). Sample data
    annotation: dimension / data type (presets name) / core / temperature (acqus TE). Return the
    summary of the {field: value} actually filled this time."""
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
        # 0.2.89: Give priority to inferring the core by chemical shift (observation frequency sf),
        # and fall back to the storage field if it fails.
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
