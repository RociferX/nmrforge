"""GUI record file (0.2.199-patch29ga) persisted by data (d_xxx file folder). - ui_state.json: peak
selection threshold + spectrum display adjustment (contour start/levels/aspect/ peak mark size),
stored in <project>/<exp_id>/<data_id>/ui_state.json; - log.txt: LogPanel single data scope log
mirror, the same as the data file folder log.txt."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ui_support.i18n import tr

UI_STATE_FILENAME = "ui_state.json"
DATA_LOG_FILENAME = "log.txt"
_UI_STATE_VERSION = 1


def data_log_path(manager: Any, exp_id: str, data_id: str) -> Path:
    """D_xxx The log record file (0.2.199-patch29ge) under the data file folder report/."""
    return (
        manager.data_base(exp_id, data_id) / "report" / DATA_LOG_FILENAME
    )


def group_log_path(manager: Any, exp_id: str, group_id: str) -> Path:
    """Data group log record file:<project>/<exp_id>/groups/<group_id>/report/log.txt.
    0.2.199-patch29gb(user: the data group is also logged)/patch29ge(log.txt enter report)."""
    root = manager.root
    if root is None:
        raise ValueError(tr("project not loaded"))
    return (
        Path(root)
        / exp_id
        / "groups"
        / group_id
        / "report"
        / DATA_LOG_FILENAME
    )


def ui_state_path(manager: Any, exp_id: str, data_id: str) -> Path:
    """D_xxx The interface adjustment status under the data file folder file."""
    return manager.data_base(exp_id, data_id) / UI_STATE_FILENAME


def data_is_trashed(manager: Any, exp_id: str, data_id: str) -> bool:
    """Has the data been deleted?/into recycle bin (does not exist). 0.2.199-patch29hz: Deleting
    data only removes the product directory, but interface writeback (threshold/contour
    adjustment) will cause mkdir to rebuild the directory and write ui_state.json;
    manager.recover_trashed "resurrects" the data when it sees the directory, so it must be
    judged whether the data is still there before writing (same origin as the log guard of
    gui/log_panel)."""
    project = getattr(manager, "project", None)
    if project is None:
        return False
    try:
        exp = project.experiment(exp_id)
    except Exception:  # noqa: BLE001 - If the read fails, it will be treated as unwritable.
        return True
    if exp is None:
        return True
    node = next(
        (d for d in (getattr(exp, "data", None) or []) if getattr(d, "id", "") == data_id),
        None,
    )
    if node is None:
        return True
    return bool(getattr(node, "trashed", False))


def load_ui_state(
    manager: Any, exp_id: str, data_id: str
) -> dict[str, Any]:
    """Read ui_state;Missing/Damage returns empty structure. of this data."""
    path = ui_state_path(manager, exp_id, data_id)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return raw
    except (OSError, ValueError):
        pass
    return {"version": _UI_STATE_VERSION}


def save_ui_state(
    manager: Any,
    exp_id: str,
    data_id: str,
    payload: dict[str, Any],
) -> bool:
    """Write back the entire ui_state;Data deleted/Do not write if it does not exist of the data
    (return False)."""
    if data_is_trashed(manager, exp_id, data_id):
        return False
    from core.project.manager import atomic_write_json

    path = ui_state_path(manager, exp_id, data_id)
    out = dict(payload)
    out.setdefault("version", _UI_STATE_VERSION)
    # Atomic replacement; the serialisation stays byte-identical to the previous
    # json.dumps(..., indent=2) + "\n", it just no longer overwrites in place (a half-written
    # JSON file loses the whole ui_state on the next read).
    atomic_write_json(path, out)
    return True


def update_ui_state(
    manager: Any,
    exp_id: str,
    data_id: str,
    section: str,
    values: dict[str, Any],
) -> bool:
    """Update by partition (peaks/spectrum) to prevent the two panels from covering each other.
    Return whether the disk is really placed (Data deleted/Do not write if it does not exist,
    see data_is_trashed)."""
    if data_is_trashed(manager, exp_id, data_id):
        return False
    payload = load_ui_state(manager, exp_id, data_id)
    payload[section] = dict(values)
    return save_ui_state(manager, exp_id, data_id, payload)
