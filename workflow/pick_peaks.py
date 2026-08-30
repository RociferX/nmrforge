"""峰挑选步骤(契约 §6 峰表格式 / G2B-004)。

输入谱图(spectra/<exp_id>-<data_id>.ft2|ft3)→ core/qc/peak_detection 检测
→ 写峰表 Poky .list(data_dir(..., "peaks")/<exp_id>-<data_id>.list)→
WorkflowRun(workflow_ref="pick_peaks") 登记;失败 finish_run("failed") 并抛
带信息异常。

峰符号规则(0.2.199-补29ap,用户):实验类型单符号(uniform,presets
peak_sign=uniform)只选占据主符号的峰(不关心正负,以候选峰计数多的符号
为准);实验类型正负共存(mixed)正负都选。实验类型名取自已导入 metadata
的 experiment_type.name,模板缺失回退 uniform。
阈值(0.2.199-补29aq/补29ar/补29cm,用户):默认 15σ(补29aq 5σ → 补29ar 6σ
→ 补29cm 15σ),可经 sigma_multiplier 参数由 GUI 阈值条调整。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from core.peaks.peak_table import save_peaks
from core.project import ProjectManager
from core.qc import peak_detection

# 0.2.199-补29df:核名集合(轴映射用,与 viewer/spectrum 同源)
_NUCLEUS_SET = {"1H", "2H", "13C", "15N", "19F", "31P", "23Na", "29Si"}


class PickPeaksError(Exception):
    """峰挑选错误(谱缺失/读取失败/检出失败)。"""


# 选峰默认阈值(0.2.199-补29aq 5σ → 补29ar 6σ → 补29cm 15σ,用户)。
# 检测算法默认 3σ 供 QC 使用,选峰步骤用更严阈值。
_PICK_THRESHOLD_SIGMA = 15.0
# 轴峰排除边缘点数(0.2.199-补29at/补29bf,用户):上下边缘横条内的峰不选;
# 补29bf 从 2 加到 5,靠近边缘的轴峰残余一并排除。
_PICK_EDGE_MARGIN = 5


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


def _fdf_prefix(dic: dict[str, Any], ndim: int, axis_idx: int) -> str:
    """数据轴 axis_idx 对应的 FDF 块前缀('FDF1'/'FDF2'/...)。

    与 viewer/spectrum._fdf_prefix_for_axis 同源(0.2.151):nmrglue
    pipe.read 数据轴序与存储序相反,每轴逻辑维号由 FDDIMORDER 给出
    (axis i ↔ FDF{FDDIMORDER[ndim-1-i]});缺失/非法回退旧位置式。
    """
    try:
        order = [int(v) for v in dic.get("FDDIMORDER") or []]
    except (TypeError, ValueError):
        order = []
    if len(order) >= ndim:
        dim = order[ndim - 1 - axis_idx]
        if 1 <= dim <= 4:
            return f"FDF{dim}"
    return f"FDF{axis_idx + 1}"


def _logical_axis_indices(dic: dict[str, Any], ndim: int) -> list[int]:
    """逻辑维 F{k+1}(k=0..ndim-1) 对应的数据轴下标;FDDIMORDER 非法回退位置式。

    3D 常见 ORDER 2 3 1 时数据轴序为 (F1,F3,F2),峰表 F1/F2/F3_shift
    必须按逻辑维取对应数据轴的 ppm,否则 F2/F3 互换(0.2.199-补29ap 修)。
    """
    try:
        order = [int(v) for v in dic.get("FDDIMORDER") or []]
    except (TypeError, ValueError):
        order = []
    if len(order) >= ndim and sorted(order[:ndim]) == list(range(1, ndim + 1)):
        indices: list[int] = [0] * ndim
        for axis_idx in range(ndim):
            dim = order[ndim - 1 - axis_idx]
            indices[dim - 1] = axis_idx
        return indices
    return list(range(ndim))

def _parse_nmrpipe_label(label: str) -> str:
    """NMRPipe FDF*LABEL('N15'/'H1'/'C13',同核下标'15Nx') → 核名;失败返回 ''。"""
    text = str(label or "").strip().upper()
    if not text:
        return ""
    if text in _NUCLEUS_SET:
        return text
    if text[-1:] in ("X", "Y", "Z") and text[:-1] in _NUCLEUS_SET:
        return text[:-1]
    digits = "".join(ch for ch in text if ch.isdigit())
    letters = "".join(ch for ch in text if ch.isalpha())
    candidate = f"{digits}{letters}" if digits and letters else ""
    return candidate if candidate in _NUCLEUS_SET else ""


def _infer_nucleus_obs(obs: float) -> str:
    """按观测频率 OBS(MHz)与核旋磁比推断核(1H≈600/15N≈60/13C≈150)。"""
    if not obs or obs <= 0:
        return ""
    base = 600.13
    best, best_err = "", 1.0
    for nucleus, ratio in (("1H", 1.0), ("15N", base / 60.81), ("13C", base / 150.9)):
        implied = obs / ratio
        err = abs(implied - base) / base
        if err < best_err:
            best, best_err = nucleus, err
    return best if best_err < 0.05 else ""


def _storage_nuclei(dic: dict[str, Any], prefixes: tuple[str, ...]) -> list[str]:
    """按 NMRPipe 头部推断各存储轴的核:LABEL 优先,OBS 兜底。"""
    nuclei: list[str] = []
    for prefix in prefixes:
        nucleus = _parse_nmrpipe_label(dic.get(prefix + "LABEL", ""))
        if not nucleus:
            try:
                obs = float(dic.get(prefix + "OBS", 0) or 0)
            except (TypeError, ValueError):
                obs = 0.0
            nucleus = _infer_nucleus_obs(obs)
        nuclei.append(nucleus)
    return nuclei


def _permutation_to_logical(
    storage_nuclei: list[str], logical_nuclei: list[str]
) -> list[int] | None:
    """storage 轴 → 逻辑位置排列;无法构成排列返回 None(与 viewer 同源)。"""
    n = len(storage_nuclei)
    if n != len(logical_nuclei) or n == 0:
        return None
    if any(not s for s in storage_nuclei) or any(not t for t in logical_nuclei):
        return None
    perm: list[int | None] = [None] * n
    used = [False] * n
    for lpos, target in enumerate(logical_nuclei):
        for spos, source in enumerate(storage_nuclei):
            if source == target and not used[spos]:
                perm[spos] = lpos
                used[spos] = True
                break
        else:
            return None
    return [int(p) for p in perm]


def _metadata_nuclei(
    manager: ProjectManager, exp_id: str, data_id: str
) -> list[str] | None:
    """按导入 metadata 返回逻辑轴核(F1/F2/F3 序);无则 None。

    0.2.199-补29df:viewer 显示按 metadata 核重排逻辑轴;选峰必须用同一
    逻辑序写 F1/F2/F3_shift,否则峰表列与谱图轴错位(如 15N 列出现 1H 值)。
    """
    try:
        meta_path = manager.data_metadata_path(exp_id, data_id)
    except Exception:  # noqa: BLE001
        return None
    if meta_path is None or not Path(meta_path).is_file():
        return None
    try:
        import json

        metadata = json.loads(Path(meta_path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    dims = ((metadata or {}).get("dataset") or {}).get("dimensions") or []
    by_axis: dict[int, str] = {}
    for dim in dims:
        axis = str((dim or {}).get("logical_axis", "") or "")
        if axis[:1] != "F" or not axis[1:].isdigit():
            continue
        try:
            sf = float((dim or {}).get("sf", 0) or 0)
        except (TypeError, ValueError):
            sf = 0.0
        nucleus = _infer_nucleus_obs(sf) or str((dim or {}).get("nucleus", "") or "")
        if nucleus:
            by_axis[int(axis[1:])] = nucleus
    if not by_axis:
        return None
    return [by_axis[i] for i in sorted(by_axis)]



def _axes_ppm(dic: dict[str, Any], data: np.ndarray) -> list[np.ndarray]:
    """按数据轴序构造 ppm 轴(每轴 FDF 块按 FDDIMORDER 定位)。"""
    return [
        _ppm_axis(dic, _fdf_prefix(dic, data.ndim, i), data.shape[i])
        for i in range(data.ndim)
    ]


def _write_peaks_list(
    manager: ProjectManager,
    exp_id: str,
    data_id: str,
    data: np.ndarray,
    dic: dict[str, Any],
    peaks: list[peak_detection.Peak],
    *,
    logical_nuclei: list[str] | None = None,
) -> Path:
    """把检测峰写为 Poky/Sparky `.list`(契约 §6,峰文件即 .list)。"""
    peaks_dir = manager.data_dir(exp_id, data_id, "peaks")
    peaks_dir.mkdir(parents=True, exist_ok=True)
    path = peaks_dir / f"{exp_id}-{data_id}.list"
    axes = _axes_ppm(dic, data)
    logical_axes = _logical_axis_indices(dic, data.ndim)
    # 0.2.199-补29df:有 metadata 核时按核匹配逻辑轴(与 viewer 显示一致),
    # 避免峰表 F 列与谱图轴错位(如 15N 列出现 1H 值)
    if logical_nuclei and data.ndim >= 3:
        prefixes = tuple(_fdf_prefix(dic, data.ndim, i) for i in range(data.ndim))
        storage_nuclei = _storage_nuclei(dic, prefixes)
        perm = _permutation_to_logical(storage_nuclei, logical_nuclei)
        if perm is not None and perm != list(range(data.ndim)):
            logical_axes = [0] * data.ndim
            for spos, lpos in enumerate(perm):
                logical_axes[lpos] = spos
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
                if k >= len(axes):
                    row[f"F{k + 1}_shift"] = 0.0
                else:
                    ax = logical_axes[k] if k < len(logical_axes) else k
                    row[f"F{k + 1}_shift"] = float(
                        axes[ax][int(peak.position[ax])]
                    )
        rows.append(row)
    save_peaks(path, rows)
    return path


def _experiment_type_name(
    manager: ProjectManager, exp_id: str, data_id: str
) -> str:
    """从数据 metadata(experiment_type.name)取实验类型名;缺失返回 ''。"""
    data_entry = manager.data(exp_id, data_id)
    candidates: list[Path] = []
    try:
        candidates.append(manager.data_metadata_path(exp_id, data_id))
    except Exception:  # noqa: BLE001 - 路径构造失败不阻断挑峰
        pass
    if data_entry.metadata_path:
        p = Path(data_entry.metadata_path)
        candidates.append(p if p.is_absolute() else manager.root / p)
    for path in candidates:
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        name = (payload.get("experiment_type") or {}).get("name", "")
        if name:
            return str(name)
    return ""


def _sign_mode_for(
    manager: ProjectManager, exp_id: str, data_id: str
) -> str:
    """峰符号模式:presets peak_sign=mixed → both;uniform/未知 → dominant。"""
    from core.experiments.registry import get as get_template

    name = _experiment_type_name(manager, exp_id, data_id)
    template = get_template(name) if name else None
    peak_sign = template.peak_sign if template else "uniform"
    return "both" if peak_sign == "mixed" else "dominant"


def pick_peaks(
    manager: ProjectManager,
    exp_id: str,
    data_id: str,
    backend: Any | None = None,
    *,
    sigma_multiplier: float | None = None,
) -> dict[str, Any]:
    """峰挑选:检测谱峰并写 Poky .list,登记 WorkflowRun。

    backend 保留为接口占位;sigma_multiplier 为噪声倍数阈值(默认 15σ,
    min_snr 同步);返回 {"status", "peak_path", "peak_count", "logs"}。
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
        sign_mode = _sign_mode_for(manager, exp_id, data_id)
        threshold = (
            float(sigma_multiplier)
            if sigma_multiplier and float(sigma_multiplier) > 0
            else _PICK_THRESHOLD_SIGMA
        )
        peaks = peak_detection.detect(
            arr,
            peak_detection.PeakDetectionParams(
                sign_mode=sign_mode,
                sigma_multiplier=threshold,
                min_snr=threshold,
                edge_margin=_PICK_EDGE_MARGIN,
            ),
        )
        logical_nuclei = _metadata_nuclei(manager, exp_id, data_id)
        peak_path = _write_peaks_list(
            manager, exp_id, data_id, arr, dict(dic), peaks,
            logical_nuclei=logical_nuclei,
        )
    except Exception as exc:  # noqa: BLE001 - 统一失败登记
        manager.finish_run(run.run_id, "failed", message=str(exc))
        raise PickPeaksError(f"峰挑选失败: {exc}") from exc

    manager.finish_run(
        run.run_id,
        "success",
        outputs={"peak_path": str(peak_path)},
        message=f"峰挑选完成({len(peaks)} 峰)",
    )
    sign_label = {
        "both": "正负峰都选(mixed)",
        "dominant": "仅主符号峰(uniform)",
        "positive": "仅正峰",
        "negative": "仅负峰",
    }.get(sign_mode, sign_mode)
    logs = [
        f"峰挑选: {len(peaks)} 个峰 → {peak_path}"
        f"(符号模式: {sign_label},阈值: {threshold:.1f}σ)"
    ]
    return {
        "status": "success",
        "peak_path": str(peak_path),
        "peak_count": len(peaks),
        "logs": logs,
    }


__all__ = ["PickPeaksError", "pick_peaks"]
