"""峰表 IO 与 Poky 导出(G2B-005)。

优先使用 Shared Contract 的 core.peaks(Backend 实现);未落地时提供本地
等价实现,保证 GUI 导出/加载可用。
"""

from __future__ import annotations

import csv
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


def _use_core_peaks() -> bool:
    try:
        import core.peaks.peak_table  # type: ignore[import-not-found]  # noqa: F401

        return True
    except ImportError:
        return False


def load_peaks(path: Path | str) -> list[dict[str, Any]]:
    """读取峰表 CSV(优先 core.peaks.load_peaks,缺失时本地解析)。"""
    if _use_core_peaks():
        try:
            from core.peaks.peak_table import load_peaks as core_load

            return list(core_load(path))
        except Exception:  # noqa: BLE001
            pass
    peaks: list[dict[str, Any]] = []
    try:
        with Path(path).open(encoding="utf-8", newline="") as fh:
            peaks = [dict(row) for row in csv.DictReader(fh)]
    except (OSError, csv.Error):
        return []
    return peaks


def save_peaks(path: Path | str, peaks: list[dict[str, Any]]) -> Path:
    """写回峰表 CSV(优先 core.peaks.save_peaks,缺失时本地写)。"""
    if _use_core_peaks():
        try:
            from core.peaks.peak_table import save_peaks as core_save

            return Path(core_save(path, peaks))
        except Exception:  # noqa: BLE001
            pass
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    columns = [c for c in PEAK_COLUMNS if any(c in p for p in peaks)] or list(PEAK_COLUMNS)
    with target.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(peaks)
    return target


def export_peaks_poky(
    path: Path | str, peaks: list[dict[str, Any]], ndim: int = 2
) -> Path:
    """导出 Poky/Sparky .list(优先 core.peaks,缺失时本地实现)。

    格式: "Assignment w1 w2 [w3] Data Height Volume",双空格分隔;
    2D w1=15N(N_shift) w2=1H(H_shift);Height=Intensity %.3g;Data/Volume=0;
    未命名峰 "?-?"(2D) / "?-?-?"(3D)。
    """
    if _use_core_peaks():
        try:
            from core.peaks.peak_table import export_peaks_poky as core_export

            return Path(core_export(path, peaks, ndim=ndim))
        except Exception:  # noqa: BLE001
            pass
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for peak in peaks:
        if ndim == 3:
            w1 = str(peak.get("F1_shift", ""))
            w2 = str(peak.get("F2_shift", ""))
            w3 = str(peak.get("F3_shift", ""))
            assignment = str(peak.get("label") or "?-?-?")
        else:
            w1 = str(peak.get("N_shift", ""))
            w2 = str(peak.get("H_shift", ""))
            w3 = None
            assignment = str(peak.get("label") or "?-?")
        height = f"{float(peak.get('Intensity') or 0):.3g}"
        if w3 is None:
            lines.append(f"{assignment}  {w1}  {w2}  0  {height}  0")
        else:
            lines.append(f"{assignment}  {w1}  {w2}  {w3}  0  {height}  0")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target
