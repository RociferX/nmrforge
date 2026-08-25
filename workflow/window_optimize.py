"""直接维窗函数优化:对转换后 fid 的直接维迹做内存 SP+FT 评分,不重跑 SMILE。

用户规则(0.2.139):
1. 评估只做直接维傅里叶(窗在 SMILE 重构 step1 的 FT 前),不改窗就无需
   重跑 SMILE——窗候选全部在原始 FID 迹上内存评分;
2. 分辨率优先:先按 FWHM(点数)过滤,只保留线宽 <= 最优 1.15x 的候选;
3. 在达标池内信噪比与线形平衡:score = 0.5*snr_norm + 0.5*shape_norm。

选出的配置写回 window[direct_axis](type=sine_bell/off/end/pow/c 或
type=none),由终跑完整脚本 step1 应用;任何失败降级返回当前配置,不阻断
自动处理。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from core.data.internal_data_model import Experiment


@dataclass
class WindowChoice:
    """候选窗评估结果。"""

    cfg: dict[str, Any]
    label: str
    fwhm: float
    snr: float
    shape: float
    score: float
    selected: bool = False


@dataclass
class WindowOptimizeResult:
    """直接维窗优化结果。"""

    choice: dict[str, Any]
    changed: bool
    scores: list[dict[str, Any]] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    optimal_label: str = ""


# 候选窗:用户规则(0.2.189)——直接维 0.5-0.98 更好,列为首选;
# 其余保留常用组合(off/end/pow/c)作对比
DEFAULT_CANDIDATES: list[dict[str, Any]] = [
    {"type": "sine_bell", "off": 0.50, "end": 0.98, "pow": 2, "c": 0.5},
    {"type": "none"},
    {"type": "sine_bell", "off": 0.30, "end": 0.98, "pow": 1, "c": 0.5},
    {"type": "sine_bell", "off": 0.45, "end": 0.98, "pow": 1, "c": 0.5},
    {"type": "sine_bell", "off": 0.45, "end": 0.90, "pow": 1, "c": 0.5},
    {"type": "sine_bell", "off": 0.45, "end": 0.98, "pow": 2, "c": 0.5},
    {"type": "sine_bell", "off": 0.45, "end": 0.95, "pow": 1, "c": 1.0},
]

# 分辨率优先的线宽容忍度(1.15x 最优)
_RES_TOL = 1.15


def _label(cfg: dict[str, Any]) -> str:
    wtype = str(cfg.get("type", "sine_bell"))
    if wtype == "none":
        return "无窗(线性)"
    return (
        f"SP off={cfg.get('off', 0.45):g} end={cfg.get('end', 0.95):g} "
        f"pow={cfg.get('pow', 1):g} c={cfg.get('c', 0.5):g}"
    )


def _window_vector(cfg: dict[str, Any], n: int) -> np.ndarray:
    """NMRPipe SP 语义窗向量(与 direct_ft_traces 一致);none=全 1。"""
    if str(cfg.get("type", "sine_bell")) == "none":
        return np.ones(n, dtype=float)
    t = np.linspace(0.0, 1.0, n)
    off = float(cfg.get("off", 0.45))
    end = float(cfg.get("end", 0.95))
    powv = float(cfg.get("pow", 1))
    return np.sin(np.pi * (off + (end - off) * t)) ** powv


def _measure_trace(amp: np.ndarray) -> dict[str, float]:
    """单条直接维迹线指标:分辨率(FWHM 点数)、SNR、线形(对称+旁瓣)。"""
    n = amp.size
    if n < 16:
        return {"fwhm": float(n), "snr": 0.0, "shape": 0.0, "ok": False}
    p = int(np.argmax(amp[2 : n - 3])) + 2
    peak = float(amp[p])
    if peak <= 0.0:
        return {"fwhm": float(n), "snr": 0.0, "shape": 0.0, "ok": False}
    half = peak * 0.5
    left = p
    while left > 0 and amp[left] > half:
        left -= 1
    if left > 0:
        frac = (half - amp[left]) / max(amp[left + 1] - amp[left], 1e-12)
        left = left + max(min(frac, 1.0), 0.0)
    right = p
    while right < n - 1 and amp[right] > half:
        right += 1
    if right < n - 1:
        frac = (half - amp[right]) / max(amp[right - 1] - amp[right], 1e-12)
        right = right - max(min(frac, 1.0), 0.0)
    fwhm = max(float(right - left), 1.0)
    lo = max(int(p - 3 * fwhm), 0)
    hi = min(int(p + 3 * fwhm), n)
    noise = amp[list(range(0, lo)) + list(range(hi, n))]
    if noise.size < 4:
        noise = amp[np.concatenate([np.arange(0, max(lo, 4)), np.arange(min(hi, n - 4), n)])]
    med = float(np.median(noise))
    mad = float(np.median(np.abs(noise - med)))
    rms = max(1.4826 * mad, med, np.finfo(float).eps)
    snr = peak / rms
    half_l = float(p - left)
    half_r = float(right - p)
    sym = min(half_l, half_r) / max(max(half_l, half_r), 1e-9)
    span = max(int(fwhm), 2)
    left_sw = amp[max(p - 6 * span, 0) : max(p - 2 * span, 0)]
    right_sw = amp[min(p + 2 * span, n) : min(p + 6 * span, n)]
    swell = 0.0
    if left_sw.size and right_sw.size:
        swell = float(max(np.max(left_sw), np.max(right_sw))) / peak
    shape = sym / (1.0 + 5.0 * max(swell, 0.0))
    return {"fwhm": fwhm, "snr": snr, "shape": shape, "ok": True}


def _aggregate(amp_traces: np.ndarray, cfg: dict[str, Any]) -> dict[str, float]:
    """取能量 top 迹的中位数指标。"""
    per = [_measure_trace(row) for row in amp_traces]
    ok = [m for m in per if m["ok"] and m["snr"] > 3.0]
    if not ok:
        return {"fwhm": float(amp_traces.shape[-1]), "snr": 0.0, "shape": 0.0}
    return {
        "fwhm": float(np.median([m["fwhm"] for m in ok])),
        "snr": float(np.median([m["snr"] for m in ok])),
        "shape": float(np.median([m["shape"] for m in ok])),
    }


def optimize_direct_window(
    fid: np.ndarray,
    *,
    zf_size: int | None = None,
    candidates: list[dict[str, Any]] | None = None,
    current: dict[str, Any] | None = None,
) -> WindowOptimizeResult:
    """在原始 fid 直接维迹上评分候选窗,返回最优配置(全程不跑 SMILE)。"""
    arr = np.asarray(fid)
    if arr.ndim < 1 or arr.shape[-1] < 16:
        return WindowOptimizeResult(
            choice=dict(current or {}),
            changed=False,
            logs=["直接维窗优化跳过:直接维点数不足"],
        )
    cands = candidates if candidates is not None else DEFAULT_CANDIDATES
    n_direct = arr.shape[-1]
    n_zf = zf_size or n_direct
    traces = arr.reshape(-1, n_direct)
    energy = np.sum(np.abs(traces) ** 2, axis=-1)
    order = np.argsort(energy)[::-1]
    keep = min(max(int(np.ceil(order.size * 0.1)), 4), 12)
    picked = traces[order[:keep]]
    measured: list[WindowChoice] = []
    for cfg in cands:
        win = _window_vector(cfg, n_direct)
        work = picked * win
        if n_zf > n_direct:
            pad = [(0, 0)] * work.ndim
            pad[-1] = (0, n_zf - n_direct)
            work = np.pad(work, pad)
        amp = np.abs(np.fft.fft(work, axis=-1))
        # 排除低频截断区与镜像端
        amp[:, : max(2, n_zf // 80)] = 0.0
        amp[:, n_zf - 3 :] = 0.0
        agg = _aggregate(amp, cfg)
        measured.append(
            WindowChoice(
                cfg=cfg,
                label=_label(cfg),
                fwhm=agg["fwhm"],
                snr=agg["snr"],
                shape=agg["shape"],
                score=0.0,
            )
        )
    valid = [m for m in measured if m.snr > 0.0]
    if not valid:
        return WindowOptimizeResult(
            choice=dict(current or {}),
            changed=False,
            logs=["直接维窗优化跳过:迹线无有效信号"],
        )
    min_fwhm = min(m.fwhm for m in valid)
    pool = [m for m in valid if m.fwhm <= min_fwhm * _RES_TOL] or valid
    max_snr = max(m.snr for m in pool)
    max_shape = max(m.shape for m in pool)
    for m in pool:
        snr_norm = m.snr / max(max_snr, 1e-12)
        shape_norm = m.shape / max(max_shape, 1e-12)
        m.score = 0.5 * snr_norm + 0.5 * shape_norm
    best = max(pool, key=lambda m: m.score)
    for m in measured:
        m.selected = m is best
    changed = best.cfg != (current or {})
    logs = [
        f"直接维窗(内存评分): 最优 {best.label} "
        f"(FWHM {best.fwhm:.2f}点, SNR {best.snr:.1f}, "
        f"线形 {best.shape:.3f}, score {best.score:.3f}); "
        f"达标池 {len(pool)}/{len(measured)} 候选(分辨率 >= "
        f"{min_fwhm * _RES_TOL:.2f}点)"
    ]
    if not changed:
        logs.append("直接维窗: 最优与现有配置一致,保持")
    return WindowOptimizeResult(
        choice=dict(best.cfg),
        changed=changed,
        scores=[
            {"label": m.label, "fwhm": m.fwhm, "snr": m.snr,
             "shape": m.shape, "score": m.score, "selected": m.selected}
            for m in measured
        ],
        logs=logs,
        optimal_label=best.label,
    )


def _fid_paths(work: Path, experiment: Experiment) -> list[Path]:
    """转换后 fid 路径:切片流(fid/test*.fid)或单文件(dataset.fid),
    与 workflow.direct_diagnostics._collect_fid_paths 同语义(0.2.163-补6:
    3D uniform/NUS 为切片流)。"""
    if experiment.segments:
        for base in (work / "merged", work):
            d = base / "fid"
            if d.is_dir():
                fs = sorted(d.glob("test*.fid"))
                if fs:
                    return fs
    d2 = work / "fid"
    if d2.is_dir():
        fs = sorted(d2.glob("test*.fid"))
        if fs:
            return fs
    single = work / f"{experiment.dataset_id}.fid"
    if single.is_file():
        return [single]
    return sorted(work.glob("test*.fid"))


def optimize_direct_window_from_work(
    work_dir: Path | str,
    experiment: Experiment,
    *,
    current: dict[str, Any] | None = None,
    zf_size: int | None = None,
) -> WindowOptimizeResult:
    """从转换后 fid(work 目录,支持切片流)加载并优化直接维窗,
    不重跑 SMILE/process。"""
    work = Path(work_dir)
    paths = _fid_paths(work, experiment)
    if not paths:
        return WindowOptimizeResult(
            choice=dict(current or {}),
            changed=False,
            logs=["直接维窗优化跳过:未找到转换后 .fid"],
        )
    try:
        import nmrglue as ng

        arrays: list = []
        for path in paths:
            _dic, fid = ng.pipe.read(str(path))
            arrays.append(fid)
        import numpy as np

        fid = arrays[0] if len(arrays) == 1 else np.concatenate(arrays, axis=0)
    except Exception as exc:  # noqa: BLE001
        return WindowOptimizeResult(
            choice=dict(current or {}),
            changed=False,
            logs=[f"直接维窗优化失败(读取 fid): {exc}"],
        )
    res = optimize_direct_window(fid, zf_size=zf_size, current=current)
    return res
