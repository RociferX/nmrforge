"""峰表模型与持久化(Shared,契约 §6 / G2B-005;移植自旧项目 NMRFlow)。

- `.list`:内部峰文件即 Poky/Sparky 格式(0.2.199-补29ar,用户:峰文件全程
  Poky,不要 CSV);"Assignment w1 w2 [w3] Data Height Volume",
  2D w1=15N(N_shift) w2=1H(H_shift),3D 按外部约定 w1=15N/w2=13C/w3=1H
  (0.2.199-补29dk,用户:.list 与峰表显示都和外部一致,内部按 F1/F2/F3 逻辑
  解读——导出/导入经 nuclei 做外部 w 列 ↔ 内部 F 列置换);
  未命名峰 ?-?(2D)/?-?-?(3D),Height=Intensity %.3g,Data/Volume=0,双空格;
- 旧 CSV 仅兼容读取(load_peaks 自动判别),不再写入。
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PEAK_COLUMNS = ("Peak_ID", "H_shift", "N_shift", "Intensity", "SN", "label")
PEAK_3D_COLUMNS = (
    "Peak_ID",
    "F1_shift",
    "F2_shift",
    "F3_shift",
    "Intensity",
    "SN",
    "label",
)

_NUMERIC_KEYS = {
    "Peak_ID",
    "H_shift",
    "N_shift",
    "F1_shift",
    "F2_shift",
    "F3_shift",
    "Intensity",
    "SN",
    "Reliability(%)",
}

# Poky assignment 单字母氨基酸(0.2.199-补29cn/补29co,用户:格式按 Poky)。
# 查证(2026-08-29,ADAPT-NMR/PINE/relax 峰表样例):assignment 按维度分段、
# 连字符连接——2D 两段(未指认 ?-?)、3D 三段(未指认 ?-?-?);每段 =
# 单字母氨基酸+残基号+核名,如 C16H-K15CB-C16N、G1H-G1N。
# Poky/Sparky 外部 .list 约定(0.2.199-补29dk,用户):2D w1=15N/w2=1H;
# 3D w1=15N/w2=13C/w3=1H。内部行键(F1/F2/F3_shift)按谱轴序,导出/导入
# 经 nuclei(每 F 轴核名,如 ["15N","1H","13C"])做外部 ↔ 内部置换。
_EXTERNAL_2D_NUCLEI = ("15N", "1H")
_EXTERNAL_3D_NUCLEI = ("15N", "13C", "1H")


def _external_w_to_internal_axes(
    nuclei: list[str] | None, external: tuple[str, ...], ndim: int
) -> list[int]:
    """外部 w 列 → 内部 F 轴下标(0-based)排列。

    核信息完整、维度一致且与外部核集合一致时按核匹配(如
    nuclei=["15N","1H","13C"] → [0, 2, 1],w1=F1/w2=F3/w3=F2);
    否则回退位置式(F{j} ↔ w{j+1}),保持旧行为。
    """
    if (
        nuclei
        and len(nuclei) == len(external)
        and len(nuclei) == ndim
        and all(nuclei)
        and set(nuclei) == set(external)
    ):
        try:
            return [nuclei.index(n) for n in external]
        except ValueError:
            return list(range(ndim))
    return list(range(ndim))


_AA_LETTERS = "ACDEFGHIKLMNPQRSTVWY"


def _poky_part(part: str) -> str | None:
    """单段指认规范化:单字母氨基酸+残基号+核名,统一大写;不匹配返回 None。"""
    m = re.fullmatch(r"([A-Za-z])(\d+)([A-Za-z]+)", part)
    if m and m.group(1).upper() in _AA_LETTERS:
        return f"{m.group(1).upper()}{m.group(2)}{m.group(3).upper()}"
    return None


def normalize_poky_label(text: str | None, ndim: int = 2) -> str:
    """把 assignment 规范为 Poky 格式(按维度分段、连字符连接)。

    - ndim:谱图维度——2D 两段(如 G1H-G1N)、3D 三段(如 G1H-G1N-G1CA);
    - None/空串 → ""(导出时写 ?-?/?-?-?);
    - 逐段统一大写(如 c16h-k15cb-c16n → C16H-K15CB-C16N),逐段 ? 保留;
    - 段数与 ndim 不一致或其它内容原样返回(由调用方决定是否提示)。
    """
    if text is None:
        return ""
    raw = str(text).strip()
    if not raw:
        return ""
    parts: list[str] = []
    for part in (p.strip() for p in raw.split("-")):
        if part == "?":
            parts.append("?")
            continue
        norm = _poky_part(part)
        parts.append(norm if norm is not None else part)
    return "-".join(parts)


def poky_label_is_valid(text: str | None, ndim: int = 2) -> bool:
    """判断文本是否为当前维度下的 Poky assignment(段数=ndim,每段格式正确)。"""
    if text is None:
        return True
    raw = str(text).strip()
    if not raw:
        return True
    parts = [p.strip() for p in raw.split("-")]
    if len(parts) != ndim:
        return False
    for part in parts:
        if part == "?":
            continue
        if _poky_part(part) is None:
            return False
    return True


@dataclass
class PeakTable:
    """内存峰表:experiment_id + 拾峰参数 + 峰行。"""

    experiment_id: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    rows: list[dict[str, Any]] = field(default_factory=list)

    def add(self, peak: dict[str, Any]) -> int:
        """追加一行,Peak_ID 数字自动编号(未给时 len(rows)+1)。"""
        peak_id = int(peak.get("Peak_ID", 0) or 0) or (len(self.rows) + 1)
        row = dict(peak)
        row["Peak_ID"] = peak_id
        self.rows.append(row)
        return peak_id

    def remove(self, peak_id: int) -> None:
        """按 Peak_ID 移除一行。"""
        self.rows = [
            r for r in self.rows if int(r.get("Peak_ID", -1)) != peak_id
        ]


def save_peaks(
    path: Path | str,
    peaks: list[dict[str, Any]],
    extra_columns: tuple[str, ...] = (),
    *,
    nuclei: list[str] | None = None,
) -> Path:
    """把峰列表写为 Poky/Sparky `.list`(契约 §6:峰文件即 .list)。

    Poky 格式无附加列,extra_columns 仅兼容旧调用方(忽略);
    nuclei 为每 F 轴核名(F1/F2/F3 序),3D 导出按外部约定排 w 列
    (0.2.199-补29dk);返回实际写入路径(自动 .list 后缀)。
    """
    path = Path(path)
    if path.suffix.lower() != ".list":
        path = path.with_suffix(".list")
    is_3d = bool(peaks) and "F1_shift" in peaks[0]
    return export_peaks_poky(
        path, peaks, ndim=3 if is_3d else 2, nuclei=nuclei
    )


def _load_csv_rows(path: Path) -> list[dict[str, Any]]:
    """旧 CSV 峰表兼容读取(0.2.199-补29ar 前产物)。"""
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8", newline="") as fh:
        for raw in csv.DictReader(fh):
            row: dict[str, Any] = {}
            for key, value in raw.items():
                if key in _NUMERIC_KEYS:
                    try:
                        if key == "Peak_ID" and value != "":
                            row[key] = int(value)
                        elif value != "":
                            row[key] = float(value)
                        else:
                            row[key] = 0.0
                    except (TypeError, ValueError):
                        row[key] = 0
                else:
                    row[key] = value
            rows.append(row)
    return rows


def load_peaks(path: Path | str) -> list[dict[str, Any]]:
    """读取峰表:`.list`(Poky)解析,旧 CSV 兼容读取。

    返回行含数字 Peak_ID(按行序 1..n)、label、N_shift/H_shift 或
    F1/F2/F3_shift、Intensity(Height)、Data/Volume;数值列还原为 float。
    """
    path = Path(path)
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    rows = (
        import_peaks_poky(path)
        if text.lstrip().startswith("Assignment")
        else _load_csv_rows(path)
    )
    for i, row in enumerate(rows, start=1):
        if not (row.get("Peak_ID") or ""):
            row["Peak_ID"] = i
        for key in _NUMERIC_KEYS:
            if key not in row:
                continue
            try:
                row[key] = int(row[key]) if key == "Peak_ID" else float(row[key])
            except (TypeError, ValueError):
                pass
    return rows


def _poky_num(value: Any) -> float:
    """Poky 导出数值宽容解析:空串/None/非法值回退 0.0(峰表单元格可留空)。"""
    if value in (None, ""):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def export_peaks_poky(
    path: Path | str,
    peaks: list[dict[str, Any]],
    ndim: int = 2,
    *,
    nuclei: list[str] | None = None,
) -> Path:
    """导出 Poky/Sparky 峰表(.list):"Assignment w1 w2 [w3] Data Height Volume"。

    2D w1=15N(N_shift)、w2=1H(H_shift);3D 按外部约定 w1=15N/w2=13C/w3=1H
    (0.2.199-补29dk,用户);nuclei 为每 F 轴核名(F1/F2/F3 序),缺失或无法
    构成排列时回退位置式 w1/w2/w3=F1/F2/F3_shift;未命名峰 ?-?(2D)/?-?-?(3D);
    Height=Intensity %.3g;Data/Volume=0;双空格。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    is_3d = ndim >= 3 or (bool(peaks) and "F1_shift" in peaks[0])
    header = (
        "Assignment w1 w2 w3 Data Height Volume"
        if is_3d
        else "Assignment w1 w2 Data Height Volume"
    )
    lines = [header]
    order = (
        _external_w_to_internal_axes(nuclei, _EXTERNAL_3D_NUCLEI, 3)
        if is_3d
        else list(range(2))
    )
    for peak in peaks:
        label = str(peak.get("label", "") or "").strip().replace(" ", "_")
        if not label:
            label = "?-?-?" if is_3d else "?-?"
        if is_3d:
            shifts = [
                _poky_num(peak.get(f"F{order[j] + 1}_shift", 0.0))
                for j in range(3)
            ]
        else:
            shifts = [
                _poky_num(peak.get("N_shift", 0.0)),
                _poky_num(peak.get("H_shift", 0.0)),
            ]
        height = _poky_num(peak.get("Intensity", 0.0))
        row = [label] + [f"{s:.3f}" for s in shifts] + ["0", f"{height:.3g}", "0"]
        lines.append("  ".join(row))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def import_peaks_poky(
    path: Path | str, *, nuclei: list[str] | None = None
) -> list[dict[str, Any]]:
    """反向解析 Poky/Sparky .list(header 行 + 行解析),返回峰 dict 列表。

    3D 默认按外部约定 w1=15N/w2=13C/w3=1H 解读(0.2.199-补29dk,用户);
    nuclei 为每 F 轴核名(F1/F2/F3 序),据此把 w 列映射回内部
    F1/F2/F3_shift;缺失或无法构成排列时回退位置式(w1→F1 等)。
    """
    path = Path(path)
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("Assignment") or line.startswith("#"):
            continue
        tokens = line.split()
        if len(tokens) < 6:
            continue
        label = tokens[0]
        is_3d = len(tokens) >= 7
        try:
            if is_3d:
                order = _external_w_to_internal_axes(
                    nuclei, _EXTERNAL_3D_NUCLEI, 3
                )
                row: dict[str, Any] = {"label": label}
                for j in range(3):
                    row[f"F{order[j] + 1}_shift"] = float(tokens[j + 1])
                row.update(
                    {
                        "Data": float(tokens[4]),
                        "Height": float(tokens[5]),
                        "Volume": float(tokens[6]),
                        "Intensity": float(tokens[5]),
                    }
                )
            else:
                row = {
                    "label": label,
                    "N_shift": float(tokens[1]),
                    "H_shift": float(tokens[2]),
                    "Data": float(tokens[3]),
                    "Height": float(tokens[4]),
                    "Volume": float(tokens[5]),
                    "Intensity": float(tokens[4]),
                }
        except ValueError:
            continue
        rows.append(row)
    return rows


__all__ = [
    "PEAK_3D_COLUMNS",
    "PEAK_COLUMNS",
    "PeakTable",
    "export_peaks_poky",
    "import_peaks_poky",
    "load_peaks",
    "save_peaks",
]
