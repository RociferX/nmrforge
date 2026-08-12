"""峰挑选步骤(契约 §6 峰表格式 / G2B-004)。

输入谱图(spectra/<exp_id>-<data_id>.ft2|ft3)→ core/qc/peak_detection 检测
→ 写峰表 CSV(data_dir(..., "peaks")/<exp_id>-<data_id>.csv)→ WorkflowRun
(workflow_ref="pick_peaks") 登记;失败 finish_run("failed") 并抛带信息异常。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from core.peaks.peak_table import save_peaks
from core.project import ProjectManager
from core.qc import peak_detection


class PickPeaksError(Exception):
    """峰挑选错误(谱缺失/读取失败/检出失败)。"""


def _ppm_axis(dic: dict[str, Any], prefix: str, size: int) -> np.ndarray:
    """NMRPipe 头部构造 ppm 轴(ORIG 优先回退 CAR,与 viewer/spectrum 契约一致)。"""
    obs = float(dic.get(prefix + "OBS", 0.0) or 0.0)
    sw = float(dic.get(prefix + "SW", 0.0) or 0.0)
    orig = float(dic.get(prefix + "ORIG", 0.0) or 0.0)
    carrier = float(dic.get(prefix + "CAR", 0.0) or 0.0)
    idx = np.arange(size)
    if obs and orig:
        return orig / obs + (size - 1 - idx) * (sw / (size * obs))
    if obs and sw:
        return carrier + (size / 2 - idx) * sw / (size * obs)
    return np.zeros(size)


def _axes_ppm(dic: dict[str, Any], data: np.ndarray) -> list[np.ndarray]:
    """按数据轴序构造 ppm 轴(FDF1/FDF2/FDF3 ↔ axis0/1/2)。"""
    prefixes = ("FDF1", "FDF2", "FDF3")
    return [_ppm_axis(dic, prefixes[i], data.shape[i]) for i in range(data.ndim)]


def _write_peaks_csv(
    manager: ProjectManager,
    exp_id: str,
    data_id: str,
    data: np.ndarray,
    dic: dict[str, Any],
    peaks: list[peak_detection.Peak],
) -> Path:
    """把检测峰写为契约 §6 峰表 CSV(数字 Peak_ID,经 PeakTable.save_peaks)。"""
    peaks_dir = manager.data_dir(exp_id, data_id, "peaks")
    peaks_dir.mkdir(parents=True, exist_ok=True)
    path = peaks_dir / f"{exp_id}-{data_id}.csv"
    axes = _axes_ppm(dic, data)
    rows: list[dict[str, Any]] = []
    for i, peak in enumerate(peaks, start=1):
        row: dict[str, Any] = {
            "Peak_ID": i,
            "Intensity": float(peak.height),
            "SN": float(peak.snr),
            "label": "",
        }
        if data.ndim == 2:
            row["H_shift"] = (
                float(axes[1][int(peak.position[1])]) if len(axes) > 1 else 0.0
            )
            row["N_shift"] = float(axes[0][int(peak.position[0])])
        else:
            for k in range(3):
                row[f"F{k + 1}_shift"] = (
                    float(axes[k][int(peak.position[k])])
                    if k < len(axes)
                    else 0.0
                )
        rows.append(row)
    return save_peaks(path, rows)


def pick_peaks(
    manager: ProjectManager,
    exp_id: str,
    data_id: str,
    backend: Any | None = None,
) -> dict[str, Any]:
    """峰挑选:检测谱峰并写 CSV,登记 WorkflowRun。

    backend 保留为接口占位(generate_* 系列签名统一);返回
    {"status", "peak_path", "peak_count", "logs"}。
    """
    data_entry = manager.data(exp_id, data_id)
    spectrum_path = data_entry.spectrum_path
    if not spectrum_path or not Path(spectrum_path).is_file():
        run = manager.start_run(exp_id, workflow_ref="pick_peaks", inputs={})
        manager.finish_run(
            run.run_id, "failed", message=f"谱图缺失: {exp_id}/{data_id}"
        )
        raise PickPeaksError(f"谱图缺失,无法挑峰: {exp_id}/{data_id}")

    run = manager.start_run(
        exp_id,
        workflow_ref="pick_peaks",
        inputs={"spectrum_path": spectrum_path},
    )
    try:
        import nmrglue as ng

        dic, data = ng.pipe.read(str(spectrum_path))
        arr = np.asarray(data)
        if np.iscomplexobj(arr):
            arr = arr.real
        peaks = peak_detection.detect(arr)
        peak_path = _write_peaks_csv(
            manager, exp_id, data_id, arr, dict(dic), peaks
        )
    except Exception as exc:  # noqa: BLE001 - 统一失败登记
        manager.finish_run(run.run_id, "failed", message=str(exc))
        raise PickPeaksError(f"峰挑选失败: {exc}") from exc

    manager.finish_run(
        run.run_id,
        "success",
        outputs={"peak_path": str(peak_path)},
        message="峰挑选完成",
    )
    return {
        "status": "success",
        "peak_path": str(peak_path),
        "peak_count": len(peaks),
        "logs": [f"峰挑选: {len(peaks)} 个峰 → {peak_path}"],
    }


__all__ = ["PickPeaksError", "pick_peaks"]
