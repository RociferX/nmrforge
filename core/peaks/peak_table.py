"""峰表模型与持久化(Shared,契约 §6 / G2B-005;移植自旧项目 NMRFlow)。

- `.list`:内部峰文件即 Poky/Sparky 格式(0.2.199-补29ar,用户:峰文件全程
  Poky,不要 CSV);"Assignment w1 w2 [w3] Data Height Volume",
  2D w1=15N(N_shift) w2=1H(H_shift),3D w1/w2/w3=F1/F2/F3_shift,
  未命名峰 ?-?(2D)/?-?-?(3D),Height=Intensity %.3g,Data/Volume=0,双空格;
- 旧 CSV 仅兼容读取(load_peaks 自动判别),不再写入。
"""

from __future__ import annotations

import csv
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
) -> Path:
    """把峰列表写为 Poky/Sparky `.list`(契约 §6:峰文件即 .list)。

    Poky 格式无附加列,extra_columns 仅兼容旧调用方(忽略);
    返回实际写入路径(自动 .list 后缀)。
    """
    path = Path(path)
    if path.suffix.lower() != ".list":
        path = path.with_suffix(".list")
    is_3d = bool(peaks) and "F1_shift" in peaks[0]
    return export_peaks_poky(path, peaks, ndim=3 if is_3d else 2)


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
) -> Path:
    """导出 Poky/Sparky 峰表(.list):"Assignment w1 w2 [w3] Data Height Volume"。

    2D w1=15N(N_shift)、w2=1H(H_shift);3D w1/w2/w3=F1/F2/F3_shift;
    未命名峰 ?-?(2D)/?-?-?(3D);Height=Intensity %.3g;Data/Volume=0;双空格。
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
    for peak in peaks:
        label = str(peak.get("label", "") or "").strip().replace(" ", "_")
        if not label:
            label = "?-?-?" if is_3d else "?-?"
        if is_3d:
            shifts = [
                _poky_num(peak.get("F1_shift", 0.0)),
                _poky_num(peak.get("F2_shift", 0.0)),
                _poky_num(peak.get("F3_shift", 0.0)),
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


def import_peaks_poky(path: Path | str) -> list[dict[str, Any]]:
    """反向解析 Poky/Sparky .list(header 行 + 行解析),返回峰 dict 列表。"""
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
                row = {
                    "label": label,
                    "F1_shift": float(tokens[1]),
                    "F2_shift": float(tokens[2]),
                    "F3_shift": float(tokens[3]),
                    "Data": float(tokens[4]),
                    "Height": float(tokens[5]),
                    "Volume": float(tokens[6]),
                    "Intensity": float(tokens[5]),
                }
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
