"""按数据(d_xxx 文件夹)持久化的 GUI 记录文件(0.2.199-补29ga)。

- ui_state.json:峰挑选阈值 + 谱图显示调节(contour start/levels/aspect/
  峰标记尺寸),存于 <项目>/<exp_id>/<data_id>/ui_state.json;
- log.txt:LogPanel 单数据作用域日志的镜像,同数据文件夹下 log.txt。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

UI_STATE_FILENAME = "ui_state.json"
DATA_LOG_FILENAME = "log.txt"
_UI_STATE_VERSION = 1


def data_log_path(manager: Any, exp_id: str, data_id: str) -> Path:
    """d_xxx 数据文件夹 report/ 下的日志记录文件(0.2.199-补29ge)。"""
    return (
        manager.data_base(exp_id, data_id) / "report" / DATA_LOG_FILENAME
    )


def group_log_path(manager: Any, exp_id: str, group_id: str) -> Path:
    """数据组日志记录文件:<项目>/<exp_id>/groups/<group_id>/report/log.txt。

    0.2.199-补29gb(用户:数据组的也落盘)/补29ge(log.txt 进 report)。"""
    root = manager.root
    if root is None:
        raise ValueError("项目未加载")
    return (
        Path(root)
        / exp_id
        / "groups"
        / group_id
        / "report"
        / DATA_LOG_FILENAME
    )


def ui_state_path(manager: Any, exp_id: str, data_id: str) -> Path:
    """d_xxx 数据文件夹下的界面调节状态文件。"""
    return manager.data_base(exp_id, data_id) / UI_STATE_FILENAME


def data_is_trashed(manager: Any, exp_id: str, data_id: str) -> bool:
    """该数据是否已删除/入回收站(不存在也算)。

    0.2.199-补29hz:删除数据只移走产物目录,但界面写回
    (阈值/contour 调节)会 mkdir 重建目录并写 ui_state.json;
    manager.recover_trashed 见到该目录就把数据「复活」,因此
    写入前必须先判断数据是否还在(与 gui/log_panel 的日志守卫同源)。
    """
    project = getattr(manager, "project", None)
    if project is None:
        return False
    try:
        exp = project.experiment(exp_id)
    except Exception:  # noqa: BLE001 - 读取失败按不可写处理
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
    """读取该数据的 ui_state;缺失/损坏返回空结构。"""
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
    """整份写回该数据的 ui_state;数据已删除/不存在时不写(返回 False)。"""
    if data_is_trashed(manager, exp_id, data_id):
        return False
    path = ui_state_path(manager, exp_id, data_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    out = dict(payload)
    out.setdefault("version", _UI_STATE_VERSION)
    path.write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return True


def update_ui_state(
    manager: Any,
    exp_id: str,
    data_id: str,
    section: str,
    values: dict[str, Any],
) -> bool:
    """按分区(peaks/spectrum)更新,避免两个面板互相覆盖。

    返回是否真的落盘(数据已删除/不存在时不写,见 data_is_trashed)。
    """
    if data_is_trashed(manager, exp_id, data_id):
        return False
    payload = load_ui_state(manager, exp_id, data_id)
    payload[section] = dict(values)
    return save_ui_state(manager, exp_id, data_id, payload)
