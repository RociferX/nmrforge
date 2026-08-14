"""谱轴显示名:把 F1/F2/F3 替换为真实核符号(H/N/C...),同核加 x/y/z 下标。

数据来源:导入生成的 metadata.json 的 dataset.dimensions[].logical_axis/nucleus
(如 logical_axis=F1, nucleus=15N)。映射后 HSQC 显示 N-H,同核 2D COSY 显示
Hx-Hy,3D 显示如 C-N-H。缺失元数据时调用方回退 F1/F2/F3。
"""

from __future__ import annotations

_NUCLEUS_ALIASES = {
    "1H": "H",
    "2H": "D",
    "13C": "C",
    "15N": "N",
    "19F": "F",
    "31P": "P",
    "23Na": "Na",
    "29Si": "Si",
}
_SUBSCRIPT = "xyz"


def nucleus_symbol(nucleus: str) -> str:
    """核字符串 → 显示符号:15N→N、1H→H;未知时去掉前导数字。"""
    n = (nucleus or "").strip()
    if n in _NUCLEUS_ALIASES:
        return _NUCLEUS_ALIASES[n]
    body = n
    while body and body[0].isdigit():
        body = body[1:]
    return body or n


def axis_labels_from_nuclei(nuclei: list[str]) -> tuple[str, ...]:
    """nuclei[i] 为第 i 维(F1/F2/F3)的核;同核出现多次时全部加 x/y/z 下标。
    例:["15N","1H"]→("N","H");["1H","1H"]→("Hx","Hy");
    ["13C","15N","1H"]→("C","N","H");["1H","1H","15N"]→("Hx","Hy","N")。
    """
    symbols = [nucleus_symbol(n) for n in nuclei]
    counts = {s: symbols.count(s) for s in set(symbols)}
    seen: dict[str, int] = {}
    labels: list[str] = []
    for symbol in symbols:
        index = seen.get(symbol, 0)
        seen[symbol] = index + 1
        if counts[symbol] <= 1:
            labels.append(symbol)
        elif index < len(_SUBSCRIPT):
            labels.append(f"{symbol}{_SUBSCRIPT[index]}")
        else:
            labels.append(f"{symbol}{index + 1}")
    return tuple(labels)


def nuclei_from_metadata(metadata: dict | None) -> list[str] | None:
    """按 F1/F2/F3 顺序从导入 metadata 提取核列表;缺信息返回 None。"""
    dims = ((metadata or {}).get("dataset") or {}).get("dimensions") or []
    by_axis: dict[int, str] = {}
    for dim in dims:
        axis = str((dim or {}).get("logical_axis", "") or "")
        nucleus = str((dim or {}).get("nucleus", "") or "")
        if axis[:1] == "F" and axis[1:].isdigit() and nucleus:
            by_axis[int(axis[1:])] = nucleus
    if not by_axis:
        return None
    return [by_axis[i] for i in sorted(by_axis)]
