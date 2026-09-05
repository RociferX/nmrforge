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
) -> None:
    """整份写回该数据的 ui_state。"""
    path = ui_state_path(manager, exp_id, data_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    out = dict(payload)
    out.setdefault("version", _UI_STATE_VERSION)
    path.write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def update_ui_state(
    manager: Any,
    exp_id: str,
    data_id: str,
    section: str,
    values: dict[str, Any],
) -> None:
    """按分区(peaks/spectrum)更新,避免两个面板互相覆盖。"""
    payload = load_ui_state(manager, exp_id, data_id)
    payload[section] = dict(values)
    save_ui_state(manager, exp_id, data_id, payload)
