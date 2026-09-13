"""峰挑选步骤(契约 §6 峰表格式 / G2B-004)。

输入谱图(spectra/<exp_id>-<data_id>.ft2|ft3)→ core/qc/peak_detection 检测
→ 写峰表 Poky .list(data_dir(..., "peaks")/<exp_id>-<data_id>.list)→
WorkflowRun(workflow_ref="pick_peaks") 登记;失败 finish_run("failed") 并抛
带信息异常。

峰符号规则(0.2.199-补29ap,用户):实验类型单符号(uniform,presets
peak_sign=uniform)只选占据主符号的峰(不关心正负,以候选峰计数多的符号
为准);实验类型正负共存(mixed)正负都选。实验类型名取自已导入 metadata
的 experiment_type.name,模板缺失回退 uniform。
阈值(0.2.199-补29aq/补29ar/补29cm/补29gc,用户):默认 35σ(5σ → 6σ →
15σ → 25σ),可经 sigma_multiplier 参数由 GUI 阈值条调整。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from core.peaks import axis_units
from core.peaks.peak_table import save_peaks
from core.project import ProjectManager
from core.qc import peak_detection

# 0.2.199-补29df:核名集合(轴映射用,与 viewer/spectrum 同源)
_NUCLEUS_SET = {"1H", "2H", "13C", "15N", "19F", "31P", "23Na", "29Si"}


class PickPeaksError(Exception):
    """峰挑选错误(谱缺失/读取失败/检出失败)。"""


# 选峰默认阈值(0.2.199-补29aq 5σ → 补29ar 6σ → 补29cm 15σ →
# 补29gc 25σ → 补29hn 35σ;当前默认 35σ)。
# 检测算法默认 3σ 供 QC 使用,选峰步骤用更严阈值。
_PICK_THRESHOLD_SIGMA = 35.0
# 轴峰排除边缘点数(0.2.199-补29at/补29bf,用户):上下边缘横条内的峰不选;
# 补29bf 从 2 加到 5,靠近边缘的轴峰残余一并排除。
# 轴峰排除边距(点):选峰与 SMILE 候选评估共用(修24 起为公开常量)
PICK_EDGE_MARGIN = 5
_PICK_EDGE_MARGIN = PICK_EDGE_MARGIN  # 兼容旧名
# 2026-09-13(用户方案 A):边距默认按**物理宽度**定义(见 core.peaks.axis_units:
# 默认 = 3×该轴核素线宽(Hz) 折算 ppm),运行时按当前谱点距换算成点数;
# 上面的 PICK_EDGE_MARGIN(点数)只在无法换算(缺 OBS/点距)时作回退。
# 谱面 mixed 证据(0.2.199-补29fc,用户):类型低置信/未知时,若谱面正负峰
# 占比都高(少数符号 ≥ 主符号数 × 0.2 且 ≥3 个,总数 ≥6),按 mixed 正负都选;
# 高置信 uniform 模板(如 HSQC)仍尊重模板,避免噪声负峰带偏。
_MIXED_MIN_MINOR = 3
_MIXED_COUNT_SHARE = 0.20   # 少数符号数量 ≥ 主符号数 × 0.2
_MIXED_INTEN_SHARE = 0.15   # 少数符号绝对强度总和 ≥ 主符号 × 0.15
_MIXED_MIN_TOTAL = 6
# 抗污染:少数符号不能由单个极强峰主导——次强峰强度 ≥ 最强 × 0.25
_MIXED_OUTLIER_RATIO = 0.25


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


def _ppm_at_fraction(axis_ppm: np.ndarray, value: float) -> float:
    """亚像素索引 → ppm 线性插值(0.2.199-补29eo:峰位亚像素修正后
    写插值 ppm,与 viewer/spectrum.ppm_at_f 同语义)。"""
    n = axis_ppm.size
    if n < 2:
        return float(axis_ppm[0]) if n else 0.0
    i0 = max(0, int(np.floor(value)))
    i1 = min(i0 + 1, n - 1)
    i0 = min(i0, i1)
    frac = value - i0
    return float(axis_ppm[i0] * (1.0 - frac) + axis_ppm[i1] * frac)


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

# NMRPipe/Sparky 常见非标准 LABEL 别名(0.2.199-补29dh):真实数据 1H 轴
# 常写 "HN"(sampleC、cc 等 30.ft3/d_011.ft3 实测),单字母为常见简写。
_NMRPIPE_LABEL_ALIASES: dict[str, str] = {
    "HN": "1H",
    "H": "1H",
    "N": "15N",
    "C": "13C",
    "P": "31P",
    "F": "19F",
    "D": "2H",
    "NA": "23Na",
    "SI": "29Si",
}


def _parse_nmrpipe_label(label: str) -> str:
    """NMRPipe FDF*LABEL('N15'/'H1'/'C13',同核下标'15Nx',别名'HN') → 核名;
    失败返回 ''。"""
    text = str(label or "").strip().upper()
    if not text:
        return ""
    if text in _NUCLEUS_SET:
        return text
    if text in _NMRPIPE_LABEL_ALIASES:
        return _NMRPIPE_LABEL_ALIASES[text]
    if text[-1:] in ("X", "Y", "Z") and text[:-1] in _NUCLEUS_SET:
        return text[:-1]
    digits = "".join(ch for ch in text if ch.isdigit())
    letters = "".join(ch for ch in text if ch.isalpha())
    candidate = f"{digits}{letters}" if digits and letters else ""
    return candidate if candidate in _NUCLEUS_SET else ""


# 核旋磁比(相对 1H)与常见 1H 频率场强,按观测频率推断核
# (0.2.199-补29dh:与 viewer.axis_labels.infer_nucleus 同源;旧实现比值
# 方向取反且只认 600 MHz——非 600 MHz 谱与 15N/13C 的 OBS 推断全部失败)
_NUCLEUS_RATIOS: dict[str, float] = {
    "1H": 1.0,
    "2H": 0.15351,
    "13C": 0.25145,
    "15N": 0.10137,
    "19F": 0.94077,
    "31P": 0.40481,
    "23Na": 0.26452,
    "29Si": 0.19837,
}
_COMMON_B0_H1 = (
    300.0, 400.0, 500.0, 600.0, 700.0, 800.0, 850.0, 900.0, 950.0,
    1000.0, 1100.0, 1200.0, 1300.0, 1500.0, 2000.0,
)


def _infer_nucleus_obs(obs: float) -> str:
    """按观测频率 OBS(MHz)与核旋磁比推断核(与 viewer.axis_labels 同源)。"""
    if not obs or obs <= 0:
        return ""
    best, best_err = "", float("inf")
    for nucleus, ratio in _NUCLEUS_RATIOS.items():
        implied_1h = obs / ratio
        if not (300.0 <= implied_1h <= 2100.0):
            continue
        err = min(abs(implied_1h - b0) for b0 in _COMMON_B0_H1) / implied_1h
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


def _logical_nuclei_from_order(
    dic: dict[str, Any], ndim: int, storage_nuclei: list[str]
) -> list[str] | None:
    """按 FDDIMORDER 推断逻辑序核列表(F1/F2/F3 序);无法构成排列返回 None。

    与 viewer/spectrum 同源(0.2.152):nmrglue 数据轴 i 的逻辑维号 =
    FDDIMORDER[ndim-1-i],据此把存储序核映射回逻辑序。
    """
    try:
        order = [int(v) for v in dic.get("FDDIMORDER") or []]
    except (TypeError, ValueError):
        return None
    if len(order) < ndim:
        return None
    logical: list[str | None] = [None] * ndim
    for axis_idx, nucleus in enumerate(storage_nuclei):
        dim = order[ndim - 1 - axis_idx]
        if not (1 <= dim <= ndim) or logical[dim - 1] is not None:
            return None
        logical[dim - 1] = nucleus
    if any(n is None for n in logical):
        return None
    return [n for n in logical if n is not None]  # type: ignore[return-value]



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
) -> Path:
    """把检测峰写为 Poky/Sparky `.list`(契约 §6,峰文件即 .list)。"""
    peaks_dir = manager.data_dir(exp_id, data_id, "peaks")
    peaks_dir.mkdir(parents=True, exist_ok=True)
    path = peaks_dir / f"{exp_id}-{data_id}.list"
    axes = _axes_ppm(dic, data)
    # 0.2.199-补29df/补29dg/补29dh:逻辑序与 viewer load_from_ft3 完全一致。
    # 补29dh(用户):轴序只以 .ft3 文件头(FDDIMORDER+LABEL/OBS)为准,不使用
    # metadata 兜底——软件处理流程有轴重排,metadata 的 Bruker F1/F2/F3 是
    # 采集序,不代表最终 .ft3 轴序;头部无法构成排列时保持存储序。
    logical_axes = list(range(data.ndim))
    prefixes = tuple(_fdf_prefix(dic, data.ndim, i) for i in range(data.ndim))
    storage_nuclei = _storage_nuclei(dic, prefixes)
    target = _logical_nuclei_from_order(dic, data.ndim, storage_nuclei)
    if target:
        perm = _permutation_to_logical(storage_nuclei, target)
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
            # 0.2.199-补29dh:2D 按核匹配写 N/H 列(外部 (1H,15N) 存储序文件
            # 不再把 1H 值写进 N_shift);两核齐全时按核名定位,否则按逻辑
            # F1→N、F2→H 兜底(保持 HSQC 存储 (15N,1H) 的原有行为不变)。
            if "15N" in storage_nuclei and "1H" in storage_nuclei:
                n_axis = storage_nuclei.index("15N")
                h_axis = storage_nuclei.index("1H")
            else:
                n_axis = logical_axes[0]
                h_axis = logical_axes[1]
            row["H_shift"] = _ppm_at_fraction(axes[h_axis], peak.position[h_axis])
            row["N_shift"] = _ppm_at_fraction(axes[n_axis], peak.position[n_axis])
        else:
            for k in range(3):
                if k >= len(axes):
                    row[f"F{k + 1}_shift"] = 0.0
                else:
                    ax = logical_axes[k] if k < len(logical_axes) else k
                    row[f"F{k + 1}_shift"] = _ppm_at_fraction(
                        axes[ax], peak.position[ax]
                    )
        rows.append(row)
    # 0.2.199-补29dk:3D .list 按外部约定 w1=15N/w2=13C/w3=1H 写列;
    # nuclei=target(头部 FDDIMORDER 推导的 F1/F2/F3 逻辑核),缺失回退位置式
    save_peaks(path, rows, nuclei=target)
    return path


def _metadata_experiment_type(
    manager: ProjectManager, exp_id: str, data_id: str
) -> tuple[str, float]:
    """从数据 metadata 读 (实验类型名, 置信度);缺失返回 ("", 0.0)。"""
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
        # 0.2.199-补29fa(修复):真实 metadata.json 中实验类型在
        # dataset.experiment_type(导入时 _dataset_summary 写入);旧/兼容结构
        # 可能在顶层 experiment_type——两处都读,先 dataset 后顶层。
        entry = (
            ((payload.get("dataset") or {}).get("experiment_type") or {})
            or (payload.get("experiment_type") or {})
        )
        name = str(entry.get("name", "") or "")
        try:
            confidence = float(entry.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        if name:
            return name, confidence
    return "", 0.0


def _experiment_type_name(
    manager: ProjectManager, exp_id: str, data_id: str
) -> str:
    """从数据 metadata(experiment_type.name)取实验类型名;缺失返回 ''。"""
    return _metadata_experiment_type(manager, exp_id, data_id)[0]


def _type_uncertain(
    manager: ProjectManager, exp_id: str, data_id: str
) -> bool:
    """类型是否低置信/未知(可被谱面证据覆盖):无类型或置信度 < 0.6。"""
    name, confidence = _metadata_experiment_type(manager, exp_id, data_id)
    if not name:
        return True
    return confidence < 0.6


def _strong_two_sign(peaks: list[Any]) -> bool:
    """谱面是否明显正负共存(0.2.199-补29fc/补29fc-修)。

    数量与强度综合:少数符号数量占比 ≥ _MIXED_COUNT_SHARE 且少数符号绝对
    强度总和占比 ≥ _MIXED_INTEN_SHARE;抗污染——少数符号不能由一个极强峰
    主导(次强峰 ≥ 最强峰 × _MIXED_OUTLIER_RATIO),零星/单峰污染不会误判。
    """
    pos = [abs(p.height) for p in peaks if p.height > 0]
    neg = [abs(p.height) for p in peaks if p.height < 0]
    total = len(pos) + len(neg)
    if total < _MIXED_MIN_TOTAL:
        return False
    minor_abs, major_abs = (
        (pos, neg) if len(pos) <= len(neg) else (neg, pos)
    )
    minor = len(minor_abs)
    major = len(major_abs)
    if minor < _MIXED_MIN_MINOR or major <= 0:
        return False
    if minor / major < _MIXED_COUNT_SHARE:
        return False
    sum_minor = float(sum(minor_abs))
    sum_major = float(sum(major_abs))
    if sum_major <= 0 or sum_minor / sum_major < _MIXED_INTEN_SHARE:
        return False
    ordered = sorted(minor_abs, reverse=True)
    if len(ordered) >= 2 and ordered[1] < ordered[0] * _MIXED_OUTLIER_RATIO:
        return False  # 少数符号被单个极强峰主导(疑似污染)
    return True


def _sign_mode_for(
    manager: ProjectManager, exp_id: str, data_id: str
) -> str:
    """峰符号模式:presets peak_sign=mixed → both;uniform/未知 → dominant。"""
    from core.experiments.registry import get as get_template

    name = _experiment_type_name(manager, exp_id, data_id)
    template = get_template(name) if name else None
    peak_sign = template.peak_sign if template else "uniform"
    return "both" if peak_sign == "mixed" else "dominant"


def _peak_nucleus_ppm(
    peak: peak_detection.Peak,
    axes: list[np.ndarray],
    storage_nuclei: list[str],
) -> dict[str, float]:
    """检测峰(数据轴序) → {核名: ppm}(仅核已知的轴;0.2.199-补29dl)。"""
    coords: dict[str, float] = {}
    for ax, nucleus in enumerate(storage_nuclei):
        if not nucleus or ax >= len(axes):
            continue
        pos = float(peak.position[ax])
        if 0 <= pos < int(axes[ax].size):
            # 0.2.199-补29fx:与 _write_peaks_list 同源——亚像素插值而非
            # int() 截断(截断在 19.999… 这类浮点会差 1 个数据点,收紧
            # 到 Poky kr 容差后参考匹配会漏掉真峰)。
            coords[nucleus] = _ppm_at_fraction(axes[ax], pos)
    return coords


def _row_nucleus_ppm(
    row: dict[str, Any], nuclei: list[str] | None
) -> dict[str, float]:
    """参考峰表行 → {核名: ppm}(0.2.199-补29dl)。

    nuclei 为参考轴核名(F 序,3D 行 F1/F2/F3_shift 用);2D 行直接按键名
    (N_shift/H_shift/C_shift)取核。
    """
    coords: dict[str, float] = {}
    if nuclei:
        for i, nucleus in enumerate(nuclei):
            value = row.get(f"F{i + 1}_shift")
            if value is not None and str(value) != "":
                coords[nucleus] = float(value)
    for key, nucleus in (
        ("H_shift", "1H"), ("N_shift", "15N"), ("C_shift", "13C"),
    ):
        value = row.get(key)
        if value is not None and str(value) != "":
            coords.setdefault(nucleus, float(value))
    return coords


def _safe_figure_token(name: str) -> str:
    """参考显示名 → 单段安全文件名 token(0.2.199-补29fx:exp/data 显示名
    含 '/' 时不再在 figures/ 下生成嵌套目录)。"""
    token = "".join(
        c if c.isalnum() or c in "_-." else "_" for c in str(name)
    )
    return token.strip("_.") or "reference"


def _reference_match(
    peak_coords: dict[str, float],
    ref_coords: list[dict[str, float]],
    tol: dict[str, float],
) -> bool:
    """峰是否与任一参考峰匹配:参考的每个核,当前峰都须有且落在容差内。

    当前谱比参考多出的核(如 3D 对 2D 参考)不参与匹配,第三维自由——
    一个参考峰可保留多个峰(如 HNCA 的 CA/CB)。
    """
    for rc in ref_coords:
        ok = True
        for nucleus, rv in rc.items():
            cv = peak_coords.get(nucleus)
            if cv is None or abs(cv - rv) > tol.get(nucleus, 1.0):
                ok = False
                break
        if ok:
            return True
    return False


def pick_peaks(
    manager: ProjectManager,
    exp_id: str,
    data_id: str,
    backend: Any | None = None,
    *,
    sigma_multiplier: float | None = None,
    ref_peaks: list[dict[str, Any]] | None = None,
    ref_nuclei: list[str] | None = None,
    tolerance_ppm: dict[str, float] | None = None,
    ref_name: str = "",
    edge_margin_ppm: float | None = None,
    edge_margin_points: int | None = None,
) -> dict[str, Any]:
    """峰挑选:检测谱峰并写 Poky .list,登记 WorkflowRun。

    backend 保留为接口占位;sigma_multiplier 为噪声倍数阈值(默认 35σ,
    min_snr 同步);ref_peaks/ref_nuclei/tolerance_ppm 为参考峰表约束
    (0.2.199-补29dl,用户):只保留与参考峰表按核名匹配的峰——2D 参考匹配
    全部核;3D 当前谱 + 2D 参考时第三维自由,一个参考峰可保留多个峰。
    轴峰排除边距(第 0 轴上下)按**物理宽度**定义:``edge_margin_ppm`` 显式给
    ppm,或用默认(3×该轴核素线宽折算 ppm);运行时按当前谱点距换算成点数,
    因此零填零不会改变边距覆盖的 ppm 宽度(用户方案 A)。
    ``edge_margin_points`` 为显式点数逃生口(不推荐,跨分辨率不可比)。
    返回 {"status", "peak_path", "peak_count", "detection", "logs"};
    ``detection`` 记录边距来源、ppm/点数、各轴点距(供留档复算)。
    """
    data_entry = manager.data(exp_id, data_id)
    spectrum_path = data_entry.spectrum_path
    if not spectrum_path or not Path(spectrum_path).is_file():
        run = manager.start_run(
            exp_id, workflow_ref="pick_peaks", inputs={"data_id": data_id}
        )
        manager.finish_run(
            run.run_id, "failed", message=f"谱图缺失: {exp_id}/{data_id}"
        )
        raise PickPeaksError(f"谱图缺失,无法挑峰: {exp_id}/{data_id}")

    run = manager.start_run(
        exp_id,
        workflow_ref="pick_peaks",
        inputs={"data_id": data_id, "spectrum_path": spectrum_path},
    )
    try:
        import nmrglue as ng

        dic, data = ng.pipe.read(str(spectrum_path))
        arr = np.asarray(data)
        if np.iscomplexobj(arr):
            arr = arr.real
        # 2026-09-13(用户方案 A):边距按物理宽度(ppm)换算点数——固定点数会随
        # 零填零改变覆盖宽度,使峰集口径跟着处理参数漂移。
        prefixes = tuple(
            _fdf_prefix(dict(dic), arr.ndim, i) for i in range(arr.ndim)
        )
        storage_nuclei = _storage_nuclei(dict(dic), prefixes)
        axes_ppm = _axes_ppm(dict(dic), arr)
        axis0_nucleus = storage_nuclei[0] if storage_nuclei else ""
        axis0_obs = (
            float(dic.get(prefixes[0] + "OBS", 0.0) or 0.0)
            if prefixes
            else 0.0
        )
        try:
            from backend.config import load_processing_defaults

            _linewidths = load_processing_defaults().get("linewidth_hz") or {}
        except Exception:  # noqa: BLE001 - 配置不可读时用内置默认
            _linewidths = {}
        if edge_margin_points is not None:
            edge_points = max(0, int(edge_margin_points))
            edge_width_ppm = axis_units.ppm_for_points(axes_ppm[0], edge_points)
            edge_source = "points(显式)"
        else:
            edge_width_ppm = (
                float(edge_margin_ppm)
                if edge_margin_ppm is not None
                else axis_units.edge_margin_ppm(
                    axis0_nucleus,
                    axis0_obs,
                    linewidth_hz_by_nucleus=_linewidths,
                )
            )
            edge_points = axis_units.points_for_ppm(axes_ppm[0], edge_width_ppm)
            edge_source = "ppm(物理宽度)"
            if edge_points <= 0:
                edge_points = int(_PICK_EDGE_MARGIN)
                edge_width_ppm = axis_units.ppm_for_points(
                    axes_ppm[0], edge_points
                )
                edge_source = "points(回退:无法换算)"
        detection = {
            "edge_margin_source": edge_source,
            "edge_margin_ppm": round(float(edge_width_ppm), 6),
            "edge_margin_points": int(edge_points),
            "axis0_nucleus": axis0_nucleus,
            "linewidth_hz_by_nucleus": {
                str(k): float(v) for k, v in (_linewidths or {}).items()
            },
            "axes": [
                axis_units.describe_axis(
                    axes_ppm[i],
                    nucleus=(storage_nuclei[i] if i < len(storage_nuclei) else ""),
                    width_ppm=(edge_width_ppm if i == 0 else None),
                )
                for i in range(arr.ndim)
            ],
        }
        run.params["detection"] = detection
        sign_mode = _sign_mode_for(manager, exp_id, data_id)
        threshold = (
            float(sigma_multiplier)
            if sigma_multiplier and float(sigma_multiplier) > 0
            else _PICK_THRESHOLD_SIGMA
        )
        # 0.2.199-补29fc(用户):谱面回补——未知/低置信类型时先用 both 检出,
        # 若正负峰占比都高则按 mixed 正负都选;否则按模板规则(dominant 过滤)。
        evidence_log: str | None = None
        peaks = peak_detection.detect(
            arr,
            peak_detection.PeakDetectionParams(
                sign_mode="both",
                sigma_multiplier=threshold,
                min_snr=threshold,
                edge_margin=edge_points,
            ),
        )
        if sign_mode == "both":
            pass  # 模板已 mixed
        else:
            n_pos = sum(1 for p in peaks if p.height > 0)
            n_neg = sum(1 for p in peaks if p.height < 0)
            if (
                _type_uncertain(manager, exp_id, data_id)
                and _strong_two_sign(peaks)
            ):
                sign_mode = "both"
                evidence_log = (
                    f"谱面回补: 类型低置信/未知但正负峰占比都高"
                    f"(正 {n_pos} / 负 {n_neg}),按 mixed 正负都选"
                )
            else:
                peaks = peak_detection.keep_dominant(peaks)
        # 0.2.199-补29dl/补29fw(用户):参考峰表约束。参考峰文件足够多时
        # (>=5)先整体平移对齐(不同采集间可能有偏移,2026-09-04 用户裁定),
        # 再剔除参考中找不到对应峰的峰;参考峰很少时整体平移不可靠,
        # 退化为零平移直接按坐标匹配(保留补29dl 语义)。
        ref_log: str | None = None
        if ref_peaks:
            from workflow.peak_align import (
                MIN_ACCEPTABLE_RATIO,
                TOLERANCE_PPM,
                align_peak_files,
                filter_by_reference,
            )

            prefixes = tuple(
                _fdf_prefix(dict(dic), arr.ndim, i) for i in range(arr.ndim)
            )
            storage_nuclei = _storage_nuclei(dict(dic), prefixes)
            axes = _axes_ppm(dict(dic), arr)
            # 参考匹配容差(0.2.199-补29fx,用户:按 Poky kr 默认——1H
            # ±0.02 ppm、其它核 ±0.2 ppm;显式 tolerance_ppm 仍可覆盖)
            ref_tol = (
                tolerance_ppm if tolerance_ppm else dict(TOLERANCE_PPM)
            )
            # 检测峰 → {核: ppm}(数据轴序;与参考行同构)
            cur_coords = [
                _peak_nucleus_ppm(peak, axes, storage_nuclei)
                for peak in peaks
            ]
            cur_rows = [c for c in cur_coords if c]
            ref_rows = [
                c for c in (_row_nucleus_ppm(r, ref_nuclei) for r in ref_peaks)
                if c
            ]
            before = len(peaks)
            if len(ref_rows) >= 5:
                align = align_peak_files(
                    cur_rows,
                    ref_peaks,
                    cur_nuclei=None,
                    ref_nuclei=ref_nuclei,
                    tol_ppm=ref_tol,
                )
                if align["status"] == "no_common":
                    ref_log = (
                        "参考峰表约束: 当前谱与参考峰无共同核坐标,未过滤"
                    )
                else:
                    kept_rows, _stats = filter_by_reference(
                        cur_rows,
                        ref_peaks,
                        align["shift"],
                        nuclei=None,
                        ref_nuclei=ref_nuclei,
                        tol_ppm=ref_tol,
                    )
                    kept_keys = {
                        tuple(
                            round(v, 6)
                            for _, v in sorted(row.items())
                        )
                        for row in kept_rows
                    }
                    kept_peaks: list = []
                    for peak, coord in zip(peaks, cur_coords):
                        if not coord:
                            continue
                        key = tuple(
                            round(v, 6)
                            for _, v in sorted(coord.items())
                        )
                        if key in kept_keys:
                            kept_peaks.append(peak)
                    ratio = align.get("ratio", 0.0)
                    ref_log = (
                        f"参考峰表约束(对齐后): {before} → "
                        f"{len(kept_peaks)} 峰(整体偏移 "
                        f"{align['shift']},对齐率 {ratio:.0%}"
                    )
                    if ratio < MIN_ACCEPTABLE_RATIO:
                        ref_log += ",请检查参考谱是否与此谱相似"
                    ref_log += ")"
                    peaks = kept_peaks
                    try:
                        from workflow.peak_align import alignment_figure

                        figures_dir = manager.data_dir(
                            exp_id, data_id, "figures"
                        )
                        figures_dir.mkdir(parents=True, exist_ok=True)
                        ref_label = ref_name or "reference"
                        fig_name = (
                            f"{exp_id}-{data_id}_aligned_"
                            f"{_safe_figure_token(ref_label)}.png"
                        )
                        fig_path = figures_dir / fig_name
                        alignment_figure(
                            cur_rows,
                            ref_peaks,
                            align["shift"],
                            fig_path,
                            cur_nuclei=None,
                            ref_nuclei=ref_nuclei,
                            tol_ppm=ref_tol,
                            cur_label=f"{exp_id}-{data_id}",
                            ref_label=ref_label,
                        )
                        ref_log += (
                            f"(检查图 {fig_path};SVG "
                            f"{fig_path.with_suffix('.svg')})"
                        )
                    except Exception:  # noqa: BLE001 - 图失败不阻断选峰
                        pass
            else:
                # 参考峰很少:整体平移不可靠 → 零平移直接匹配(补29dl)
                zero_shift = {}
                kept_rows, _stats = filter_by_reference(
                    cur_rows,
                    ref_peaks,
                    zero_shift,
                    nuclei=None,
                    ref_nuclei=ref_nuclei,
                    tol_ppm=ref_tol,
                )
                kept_keys = {
                    tuple(
                        round(v, 6)
                        for _, v in sorted(row.items())
                    )
                    for row in kept_rows
                }
                kept_peaks = [
                    peak
                    for peak, coord in zip(peaks, cur_coords)
                    if not coord
                    or tuple(
                        round(v, 6) for _, v in sorted(coord.items())
                    )
                    in kept_keys
                ]
                ref_log = (
                    f"参考峰表约束(直匹配): {before} → "
                    f"{len(kept_peaks)} 峰(参考峰较少,未做整体平移)"
                )
                peaks = kept_peaks

        peak_path = _write_peaks_list(
            manager, exp_id, data_id, arr, dict(dic), peaks,
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
    logs.append(
        f"轴峰排除边距: {detection['edge_margin_ppm']:.3f} ppm"
        f"({detection['axis0_nucleus'] or '轴0'}) = "
        f"{detection['edge_margin_points']} 点"
        f"(点距 {detection['axes'][0]['ppm_per_point']:.4f} ppm/点,"
        f"来源 {detection['edge_margin_source']})"
    )
    if evidence_log:
        logs.append(evidence_log)
    if ref_log:
        logs.append(ref_log)
    return {
        "status": "success",
        "peak_path": str(peak_path),
        "peak_count": len(peaks),
        "detection": detection,
        "logs": logs,
    }


# ---------------------------------------------------------------------------
# 公开读谱入口(2026-09-12,参数敏感性接口复用)
#
# 选峰内部早已把「数据轴 ↔ 逻辑维 ↔ 核名 ↔ ppm」的口径收敛在这里;对外接口
# (nmrforge_api)要按同一口径定位峰位,因此把这一层显式公开,避免出现第二套
# 实现在 FDDIMORDER / ORIG-CAR 上再次分叉。


@dataclass(frozen=True)
class SpectrumAxes:
    """一份 NMRPipe 谱的读谱结果(数据轴序)+ 轴映射。

    - ``dic`` / ``data``:nmrglue 头部与数组(复型取实部);
    - ``ppm``:数据轴序 ppm 轴(ORIG 优先回退 CAR,与 viewer 同源);
    - ``nuclei``:数据轴序核名(无法判定为 ``""``);
    - ``logical_to_storage``:逻辑维 F{k+1}(k=0..ndim-1) → 数据轴下标
      (FDDIMORDER 非法时回退位置式)。
    """

    dic: dict[str, Any]
    data: np.ndarray
    ppm: list[np.ndarray]
    nuclei: list[str]
    logical_to_storage: list[int]
    obs: list[float] = field(default_factory=list)  # 数据轴序观测频率(MHz)

    @property
    def ndim(self) -> int:
        return int(self.data.ndim)

    def storage_of(self, nucleus: str) -> int | None:
        """核名 → 数据轴下标(同核重复取第一个;未知返回 None)。"""
        for axis, name in enumerate(self.nuclei):
            if name and name == nucleus:
                return axis
        return None

    def ppm_at_fraction(self, axis: int, fraction: float) -> float:
        """数据轴 ``axis`` 的亚像素索引 → ppm(与选峰写表同源)。"""
        return _ppm_at_fraction(self.ppm[axis], fraction)

    def fraction_from_ppm(self, axis: int, ppm: float) -> float:
        """ppm → 数据轴 ``axis`` 的分数索引(轴递增/递减均支持)。"""
        axis_ppm = np.asarray(self.ppm[axis], dtype=float)
        index = np.arange(axis_ppm.size, dtype=float)
        if axis_ppm.size < 2:
            return 0.0
        if axis_ppm[0] <= axis_ppm[-1]:
            return float(np.interp(float(ppm), axis_ppm, index))
        return float(np.interp(-float(ppm), -axis_ppm, index))


def read_spectrum_axes(path: Path | str) -> SpectrumAxes:
    """读 NMRPipe 谱(ft1/ft2/ft3),返回与选峰同口径的轴映射。

    与 ``pick_peaks`` 完全同源:同一 ORIG/CAR 公式、同一 FDDIMORDER 解析、
    同一核名别名表;复型数据取实部。``obs`` 为数据轴序观测频率(MHz),
    供「物理宽度 ↔ 点数」换算(见 core.peaks.axis_units)。
    """
    import nmrglue as ng

    dic, data = ng.pipe.read(str(path))
    arr = np.asarray(data)
    if np.iscomplexobj(arr):
        arr = arr.real
    ndim = int(arr.ndim)
    prefixes = tuple(_fdf_prefix(dic, ndim, axis) for axis in range(ndim))
    logical_to_storage = _logical_axis_indices(dic, ndim)
    return SpectrumAxes(
        dic=dict(dic),
        data=arr,
        ppm=_axes_ppm(dic, arr),
        nuclei=_storage_nuclei(dic, prefixes),
        logical_to_storage=list(logical_to_storage),
        obs=[float(dic.get(prefix + "OBS", 0.0) or 0.0) for prefix in prefixes],
    )


__all__ = ["PickPeaksError", "SpectrumAxes", "pick_peaks", "read_spectrum_axes"]
