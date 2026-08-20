"""3D 投影输出头重写(NMRForge 0.2.133,Backend 侧)。

NMRPipe proj3D.tcl 的输出头实测不可靠(见 docs/backend/state.md 0.2.133
验证记录):三个输出文件的 FDF1/FDF2 头都原样复制输入平面的头,与各输出
实际数据轴不一致。本项目在 project_3d 生成后调用本模块按「实际数据形状
匹配源谱各轴尺寸」重写每个投影的头(LABEL/OBS/CAR/ORIG/SW),并修正
FDSIZE/FDSPECNUM,保证 GUI 按头显示正确的核与 ppm。

源谱 3D 头尺寸-轴对应(实测):FDSPECNUM ↔ FDF1、FDSIZE ↔ FDF2、
FDF3SIZE ↔ FDF3;输出投影的形状 (rows, cols) 通过尺寸唯一确定实际两轴。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

_SIZE_KEYS = ("FDF1", "FDF2", "FDF3")
_SIZE_FIELDS = ("FDSPECNUM", "FDSIZE", "FDF3SIZE")
_AXIS_FIELDS = ("LABEL", "OBS", "CAR", "ORIG", "SW")


def _axis_params(dic: dict[str, Any], prefix: str) -> dict[str, Any]:
    """取 FDF* 轴头参数子集(LABEL/OBS/CAR/ORIG/SW)。"""
    return {field: dic.get(prefix + field) for field in _AXIS_FIELDS}


def source_axis_table(dic: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """源 3D 头 → {轴点数: (FDFn 前缀, 轴参数)},按尺寸索引。

    点数相同(退化谱)时先到先得,调用方应通过返回的前缀察觉歧义。
    """
    table: dict[int, dict[str, Any]] = {}
    for prefix, size_key in zip(_SIZE_KEYS, _SIZE_FIELDS, strict=True):
        try:
            size = int(float(dic.get(size_key, 0) or 0))
        except (TypeError, ValueError):
            size = 0
        if size > 0:
            table.setdefault(
                size, {"prefix": prefix, "params": _axis_params(dic, prefix)}
            )
    return table


def _axes_for_shape(
    rows: int, cols: int, table: dict[int, dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """按输出形状 (rows, cols) 匹配源轴上/列参数;缺尺寸或歧义返回 None。"""
    if rows not in table or cols not in table or rows == cols:
        return None
    return table[rows], table[cols]


def rewrite_projection_headers(
    outputs: dict[str, Path | str], src_dic: dict[str, Any]
) -> dict[str, list[str]]:
    """重写每个投影输出的头为实际两轴,返回 {tag: [FDF1 核, FDF2 核]}。

    - 每个输出按 (rows, cols) 尺寸匹配源轴,写 FDF1*/FDF2* 参数;
    - FDSIZE/FDSPECNUM 与实际数据形状一致,FDDIMCOUNT=2,FDF3SIZE 清零;
    - 尺寸缺失/歧义时保持原头(FDF1/FDF2 原样),仅修正尺寸计数,
      核从原头读取(GUI 仍能按头显示,但可能如旧版标签错误)。
    """
    import nmrglue as ng

    table = source_axis_table(src_dic)
    result: dict[str, list[str]] = {}
    for tag, path in outputs.items():
        path = Path(path)
        if not path.is_file():
            continue
        dic, data = ng.pipe.read(str(path))
        data = np.asarray(data)
        new = dict(dic)
        axes = _axes_for_shape(int(data.shape[0]), int(data.shape[1]), table)
        if axes is not None:
            row_params, col_params = axes
            for field in _AXIS_FIELDS:
                new["FDF1" + field] = row_params["params"].get(field)
                new["FDF2" + field] = col_params["params"].get(field)
            # FDF3* 头标记保留(无关紧要),仅清 FDF3SIZE 避免被当作 3D
            # 读;不能删 FDF3APOD 等数值槽(nmrglue dic2fdata 需要)。
            new["FDF3SIZE"] = 0.0
        new["FDDIMCOUNT"] = 2.0
        new["FDSIZE"] = float(data.shape[1])
        new["FDSPECNUM"] = float(data.shape[0])
        ng.pipe.write(
            str(path), new, data.astype(np.float32, copy=False), overwrite=True
        )
        dic2, data2 = ng.pipe.read(str(path))
        if np.asarray(data2).shape != data.shape:
            raise RuntimeError(
                f"投影头重写后读回形状不一致({tag}: {data.shape} -> "
                f"{np.asarray(data2).shape})"
            )
        f1 = str(dic2.get("FDF1LABEL", "") or "")
        f2 = str(dic2.get("FDF2LABEL", "") or "")
        result[tag] = [f1, f2]
    return result


def fixed_nucleus_for(
    tag: str,
    nuclei: dict[str, list[str]],
    src_labels: list[str],
) -> str:
    """求投影固定轴(被求和的第三轴)核:输出两核之外的那一个。"""
    plane = nuclei.get(tag) or []
    if len(plane) == 2 and len(src_labels) >= 3:
        for label in src_labels:
            if str(label or "") not in plane:
                return str(label or "")
    return ""
