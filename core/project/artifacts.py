"""项目数据产物的统一查找规则。

这里集中处理“主频谱”与 3D 派生投影的区别，避免 GUI、状态机和
项目状态各自按 ``*.ft2`` 扫描而得出互相矛盾的结果。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def is_projection_spectrum_file(name: str, data_id: str) -> bool:
    """判断文件名是否表示 3D 投影，而不是活动主谱。

    已知投影命名包括 ``d_001_proj_F1.ft2`` 和
    ``d_001_15N-1H.ft2``。旧主谱名 ``<experiment>-<data>.ft2``
    不应被后一个规则误伤。
    """
    stem = Path(str(name)).stem
    if stem == str(data_id):
        return False
    if "_proj_" in stem:
        return True
    head, separator, tail = stem.rpartition("-")
    if not separator:
        return False

    def _nucleus_tag(text: str) -> bool:
        digits = "".join(character for character in text if character.isdigit())
        letters = "".join(character for character in text if character.isalpha())
        return bool(digits) and bool(letters) and len(letters) <= 2

    return _nucleus_tag(head.rsplit("_", 1)[-1]) and _nucleus_tag(tail)


def find_primary_spectrum(manager: Any, exp_id: str, data_id: str) -> Path | None:
    """返回数据条目的活动主谱；只有投影文件时返回 ``None``。

    ``DataEntry.spectrum_path`` 是显式登记结果，优先且视为权威。
    兼容旧项目时才扫描数据的 ``spectra`` 目录，并在回退扫描中排除
    3D 投影文件。
    """
    try:
        entry = manager.data(exp_id, data_id)
    except Exception:  # noqa: BLE001 - 查找失败按无产物处理
        return None

    registered = str(getattr(entry, "spectrum_path", "") or "")
    if registered:
        path = Path(registered)
        if not path.is_absolute():
            path = manager.root / path
        if path.is_file():
            return path

    try:
        spectra = manager.data_dir(exp_id, data_id, "spectra")
    except Exception:  # noqa: BLE001 - 目录映射异常按无产物处理
        return None

    # 新旧两种确定性命名优先于通配回退。
    for stem in (f"{exp_id}-{data_id}", data_id):
        for extension in ("ft2", "ft3"):
            path = spectra / f"{stem}.{extension}"
            if path.is_file():
                return path

    # 后端也可能按原始 dataset_id 命名；此时只接受非投影谱。
    for extension in ("ft2", "ft3"):
        try:
            matches = sorted(spectra.glob(f"*.{extension}"))
        except OSError:
            matches = []
        for path in matches:
            if not is_projection_spectrum_file(path.name, data_id):
                return path
    return None
