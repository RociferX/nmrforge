"""窗函数优化:直接维 + 间接维,统一内存评分引擎(不重跑 SMILE/process)。

用户规则(0.2.139)适用于每维:
1. 评估只做该维傅里叶(窗在 FT 前),候选全部在原始 FID/重构平面迹上内存评分;
2. 分辨率优先:先按 FWHM(点数)过滤,只保留线宽 <= 最优 1.25x 的候选;
3. 达标池内信噪比与线形平衡:score = 0.5*snr_norm + 0.5*shape_norm。
4. 无窗(none)是一等候选:自然衰减/FID 尾部已充分采样的轴应能正确选到无窗;
   需要抑制截断振铃/提噪的轴由评分自动选择合适窗。

0.2.190(恢复真实选窗):0.2.189 曾把间接维硬编码固定无窗——那是上个窗口
对需求的误读。正确行为是优化器把无窗作为候选参与评分,并能在无窗确实
最优时(自然衰减、加窗仅展宽)正确选出;直接维同样恢复 0.5-0.98 等候选
参与评分(分辨率过滤放宽到 1.25x,避免把用户偏好的温和窗提前排除)。

0.2.192(加入 GM):GM(Lorentz-to-Gauss)公式已与 NMRPipe 实测逐点对齐
(0.2.191,k=1/(2*sqrt(ln2))),重新加入直接维缺省候选池(GM g1=8 g2=15)。
GM/EM 依赖谱宽 SW,评分未提供 SW 时跳过这些候选(避免 sw=1.0 的数值
垃圾虚高);间接维候选池不加 GM——分辨率受限的间接维加窗信噪比虚高会
翻盘自然衰减轴的无窗选择(0.2.190 要求保留)。

选出的配置写回 window[axis](type=none/sine_bell/gaussian/exp 等),由终跑
完整脚本应用;任何失败降级返回当前配置,不阻断自动处理。
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
    """单轴窗优化结果。"""

    choice: dict[str, Any]
    changed: bool
    scores: list[dict[str, Any]] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    optimal_label: str = ""


@dataclass
class MultiWindowOptimizeResult:
    """多间接维窗优化结果:choice = {逻辑轴: 配置}。"""

    choice: dict[str, dict[str, Any]]
    changed: bool
    per_axis: dict[str, WindowOptimizeResult] = field(default_factory=dict)
    logs: list[str] = field(default_factory=list)


# 直接维候选:用户规则(0.2.189)0.5-0.98 列为首选,其余常用组合对比;
# c 保持 0.5(NMRPipe SP -c,内存窗模型与 direct_ft_traces 一致只建模
# sin 项);gaussian(GM)0.2.192 重新加入(0.2.191 已与 NMRPipe 实测逐点
# 对齐,k=1/(2*sqrt(ln2))),GM/EM 依赖谱宽 SW,评分时未提供 SW 自动跳过;
# 间接维候选池不加 GM(分辨率受限间接维会因 GM 信噪比虚高翻盘无窗)
DEFAULT_CANDIDATES: list[dict[str, Any]] = [
    {"type": "sine_bell", "off": 0.50, "end": 0.98, "pow": 2, "c": 0.5},
    {"type": "none"},
    {"type": "sine_bell", "off": 0.30, "end": 0.98, "pow": 1, "c": 0.5},
    {"type": "sine_bell", "off": 0.45, "end": 0.98, "pow": 1, "c": 0.5},
    {"type": "sine_bell", "off": 0.45, "end": 0.90, "pow": 1, "c": 0.5},
    {"type": "sine_bell", "off": 0.45, "end": 0.98, "pow": 2, "c": 0.5},
    {"type": "gaussian", "g1": 8.0, "g2": 15.0, "g3": 0.0, "c": 1.0},
]

# 间接维候选:无窗列为首选(自然衰减间接维应能正确选出无窗),其余同族
INDIRECT_CANDIDATES: list[dict[str, Any]] = [
    {"type": "none"},
    {"type": "sine_bell", "off": 0.30, "end": 0.98, "pow": 1, "c": 0.5},
    {"type": "sine_bell", "off": 0.45, "end": 0.98, "pow": 1, "c": 0.5},
    {"type": "sine_bell", "off": 0.45, "end": 0.90, "pow": 1, "c": 0.5},
    {"type": "sine_bell", "off": 0.45, "end": 0.98, "pow": 2, "c": 0.5},
    {"type": "sine_bell", "off": 0.50, "end": 0.98, "pow": 2, "c": 0.5},
]

# 分辨率优先的线宽容忍度(1.25x 最优;1.15x 会把用户偏好的 0.5-0.98/pow2
# 温和窗提前排除,0.2.190 放宽)
_RES_TOL = 1.25

# 间接维分辨率池更紧(1.15x):间接维分辨率受限,加窗只展宽时应正确落
# 无窗。0.2.191 窗公式与 NMRPipe 逐点一致后(SP 首点乘 -c),加窗的信噪比
# 虚高会被 1.25x 池放大而翻盘,收紧池保证自然衰减轴确定性只留无窗。
_INDIRECT_RES_TOL = 1.15


def _label(cfg: dict[str, Any]) -> str:
    wtype = str(cfg.get("type", "sine_bell"))
    if wtype == "none":
        return "无窗(线性)"
    if wtype == "gaussian":
        return f"GM g1={cfg.get('g1', 8.0):g} g2={cfg.get('g2', 15.0):g}"
    if wtype == "exp":
        return f"EM lb={cfg.get('lb', 5.0):g}"
    return (
        f"SP off={cfg.get('off', 0.45):g} end={cfg.get('end', 0.95):g} "
        f"pow={cfg.get('pow', 1):g} c={cfg.get('c', 0.5):g}"
    )


def _window_vector(
    cfg: dict[str, Any], n: int, sw: float = 0.0
) -> np.ndarray:
    """NMRPipe 语义窗向量(0.2.191 与 nmrPipe 实测/源码逐点一致)。

    公式来源:VM nmrPipe 全 1 FID 实测 + nmrglue pipe_proc/proc_base
    (与 NMRPipe 同语义);首点均乘 -c(SP 脚本显式 -c 0.5 缺省,GM/EM
    脚本不写 -c 按 NMRPipe 缺省 1.0):
    - SP/sine_bell: w[i] = sin(pi*off + pi*(end-off)*i/(n-1))^pow;
    - GM(Lorentz-to-Gauss): w[i] = exp(pi*g1p*i - (k*pi*g2p*(g3*(n-1)-i))^2),
      k=1/(2*sqrt(ln2))=0.6005612...(VM 实测,非 nmrglue 的 0.6 近似),
      g1p=g1/SW、g2p=g2/SW(SW 为该轴谱宽 Hz,取 fid 头 FDFxSW);
    - EM: w[i] = exp(-pi*(lb/SW)*i);
    none/off=全 1。
    """
    wtype = str(cfg.get("type", "sine_bell"))
    if wtype in ("none", "off"):
        return np.ones(n, dtype=float)
    i = np.arange(n, dtype=float)
    sw_eff = sw if sw > 0.0 else 1.0
    if wtype == "gaussian":
        g1 = float(cfg.get("g1", 0.0))
        g2 = float(cfg.get("g2", 0.0))
        g3 = float(cfg.get("g3", 0.0))
        g1p = g1 / sw_eff
        g2p = g2 / sw_eff
        e = np.pi * i * g1p
        g = (1.0 / (2.0 * np.sqrt(np.log(2.0)))) * np.pi * g2p * (g3 * (n - 1) - i)
        w = np.exp(e - g * g)
        c = float(cfg.get("c", 1.0))
    elif wtype == "exp":
        lb = float(cfg.get("lb", 5.0))
        w = np.exp(-np.pi * (lb / sw_eff) * i)
        c = float(cfg.get("c", 1.0))
    else:
        off = float(cfg.get("off", 0.45))
        end = float(cfg.get("end", 0.95))
        powv = 2 if wtype == "sine_bell_squared" else float(cfg.get("pow", 1))
        w = np.sin(np.pi * off + np.pi * (end - off) * i / max(n - 1, 1)) ** powv
        c = float(cfg.get("c", 0.5))
    w[0] *= c
    return w



def _measure_trace(amp: np.ndarray) -> dict[str, float]:
    """单条迹线指标:分辨率(FWHM 点数)、SNR、线形(对称+旁瓣)。"""
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


def _score_axis(
    arr: np.ndarray,
    axis: int,
    candidates: list[dict[str, Any]],
    *,
    zf_size: int | None = None,
    resolution_penalty: float = 0.0,
    sw: float = 0.0,
    res_tol: float = _RES_TOL,
) -> tuple[list[WindowChoice], WindowChoice | None, str]:
    """沿指定时间轴评分候选窗,返回 (choices, 最优, 日志行)。

    resolution_penalty>0 时对达标池内相对最优 FWHM 的展宽施加指数惩罚
    (score *= exp(-k*max(fwhm/min_fwhm-1,0))):分辨率受限的间接维用它
    让自然衰减轴正确落到无窗,截断轴仍保留温和窗(0.2.190)。
    """
    n = arr.shape[axis]
    moved = np.moveaxis(arr, axis, -1)
    flat = moved.reshape(-1, n)
    energy = np.sum(np.abs(flat) ** 2, axis=-1)
    order = np.argsort(energy)[::-1]
    keep = min(max(int(np.ceil(order.size * 0.1)), 4), 12)
    picked = flat[order[:keep]]
    n_zf = zf_size or n
    # GM/EM 依赖谱宽 SW;未提供 SW 时跳过,避免 sw=1.0 数值垃圾虚高
    # (0.2.192 加入 GM 后必需)
    sw_dependent = {"gaussian", "exp"}
    skipped_sw = 0
    scorable: list[dict[str, Any]] = []
    for cfg in candidates:
        if str(cfg.get("type", "sine_bell")) in sw_dependent and sw <= 0.0:
            skipped_sw += 1
            continue
        scorable.append(cfg)
    measured: list[WindowChoice] = []
    for cfg in scorable:
        win = _window_vector(cfg, n, sw=sw)
        work = picked * win
        if n_zf > n:
            work = np.pad(work, [(0, 0), (0, n_zf - n)])
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
        return measured, None, "迹线无有效信号"
    min_fwhm = min(m.fwhm for m in valid)
    pool = [m for m in valid if m.fwhm <= min_fwhm * res_tol] or valid
    max_snr = max(m.snr for m in pool)
    max_shape = max(m.shape for m in pool)
    for m in pool:
        snr_norm = m.snr / max(max_snr, 1e-12)
        shape_norm = m.shape / max(max_shape, 1e-12)
        m.score = 0.5 * snr_norm + 0.5 * shape_norm
        if resolution_penalty > 0.0:
            widen = max(m.fwhm / min_fwhm - 1.0, 0.0)
            m.score *= float(np.exp(-resolution_penalty * widen))
    best = max(pool, key=lambda m: m.score)
    for m in measured:
        m.selected = m is best
    log = (
        f"最优 {best.label} "
        f"(FWHM {best.fwhm:.2f}点, SNR {best.snr:.1f}, "
        f"线形 {best.shape:.3f}, score {best.score:.3f}); "
        f"达标池 {len(pool)}/{len(measured)} 候选(分辨率 >= "
        f"{min_fwhm * res_tol:.2f}点)"
    )
    if skipped_sw:
        log += f"; {skipped_sw} 个依赖谱宽的候选(GM/EM)未提供 SW 跳过"
    return measured, best, log


def optimize_axis_window(
    arr: np.ndarray,
    axis: int,
    candidates: list[dict[str, Any]] | None = None,
    *,
    zf_size: int | None = None,
    current: dict[str, Any] | None = None,
    axis_label: str = "",
    resolution_penalty: float = 0.0,
    sw: float = 0.0,
    res_tol: float = _RES_TOL,
) -> WindowOptimizeResult:
    """沿 arr 的 axis 时间轴评分候选窗(共享引擎,直接/间接维通用)。"""
    data = np.asarray(arr)
    if data.ndim < 1 or data.shape[axis] < 16:
        return WindowOptimizeResult(
            choice=dict(current or {}),
            changed=False,
            logs=[f"{axis_label}: 窗优化跳过:该轴点数不足"],
        )
    cands = candidates if candidates is not None else DEFAULT_CANDIDATES
    measured, best, message = _score_axis(
        data, axis, cands, zf_size=zf_size,
        resolution_penalty=resolution_penalty,
        sw=sw,
        res_tol=res_tol,
    )
    if best is None:
        return WindowOptimizeResult(
            choice=dict(current or {}),
            changed=False,
            logs=[f"{axis_label}: 窗优化跳过:{message}"],
        )
    changed = best.cfg != (current or {})
    logs = [f"{axis_label}: 窗(内存评分): {message}"]
    if not changed:
        logs.append(f"{axis_label}: 窗: 最优与现有配置一致,保持")
    return WindowOptimizeResult(
        choice=dict(best.cfg),
        changed=changed,
        scores=[
            {
                "label": m.label,
                "fwhm": m.fwhm,
                "snr": m.snr,
                "shape": m.shape,
                "score": m.score,
                "selected": m.selected,
            }
            for m in measured
        ],
        logs=logs,
        optimal_label=best.label,
    )


def optimize_direct_window(
    fid: np.ndarray,
    *,
    zf_size: int | None = None,
    candidates: list[dict[str, Any]] | None = None,
    current: dict[str, Any] | None = None,
    sw: float = 0.0,
) -> WindowOptimizeResult:
    """在原始 fid 直接维迹(最后轴)上评分候选窗,返回最优配置。"""
    return optimize_axis_window(
        fid,
        -1,
        candidates=candidates,
        zf_size=zf_size,
        current=current,
        axis_label="直接维",
        sw=sw,
    )


def optimize_indirect_windows(
    arr: np.ndarray,
    axis_map: dict[str, int],
    *,
    candidates: list[dict[str, Any]] | None = None,
    current: dict[str, dict[str, Any]] | None = None,
    zf_mult: float = 2.0,
    sw_map: dict[str, float] | None = None,
) -> MultiWindowOptimizeResult:
    """对每间接维时间轴评分候选窗(含无窗),写回每轴最优。

    axis_map: 逻辑轴 -> 数组中的时间轴索引(数组其余轴可为时间或频率,
    不影响逐轴评分)。zf_mult: 评分用零填倍数(间接维点数少,2x 提高
    FWHM 分辨)。
    """
    data = np.asarray(arr)
    cands = candidates if candidates is not None else INDIRECT_CANDIDATES
    current = dict(current or {})
    choice: dict[str, dict[str, Any]] = dict(current)
    per_axis: dict[str, WindowOptimizeResult] = {}
    logs: list[str] = []
    changed = False
    for axis_name, axis in axis_map.items():
        if axis >= data.ndim or data.shape[axis] < 16:
            logs.append(f"{axis_name}: 间接维窗优化跳过:点数不足")
            continue
        n = int(data.shape[axis])
        zf_size = max(int(round(n * max(zf_mult, 1.0))), n)
        res = optimize_axis_window(
            data,
            axis,
            cands,
            zf_size=zf_size,
            current=current.get(axis_name),
            axis_label=f"{axis_name}(间接维)",
            resolution_penalty=1.0,
            sw=(sw_map or {}).get(axis_name, 0.0),
            res_tol=_INDIRECT_RES_TOL,
        )
        per_axis[axis_name] = res
        logs += res.logs
        if res.changed:
            choice[axis_name] = dict(res.choice)
            changed = True
    if not per_axis:
        logs.append("间接维窗优化跳过:无可用时间轴")
    return MultiWindowOptimizeResult(
        choice=choice, changed=changed, per_axis=per_axis, logs=logs
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


def _load_fid(work: Path, experiment: Experiment) -> tuple[np.ndarray, dict] | None:
    """加载转换后 fid(单文件或 3D 切片流堆叠)+ 头部(取首文件 dic)。"""
    paths = _fid_paths(work, experiment)
    if not paths:
        return None
    import nmrglue as ng

    arrays: list[np.ndarray] = []
    dic: dict = {}
    for index, path in enumerate(paths):
        d, fid = ng.pipe.read(str(path))
        if index == 0:
            dic = d
        arrays.append(fid)
    fid = arrays[0] if len(arrays) == 1 else np.concatenate(arrays, axis=0)
    return fid, dic



def _load_recon_planes(
    work: Path, experiment: Experiment
) -> tuple[np.ndarray, dict] | None:
    """加载 SMILE 重构平面(间接维时间域)+ 头部。

    0.2.199-补29(实测 sampleB + sampleJ 手工切片):
    3D nus3d_rc/test%04d.ft1 每个文件=一个直接维(F3 频)点,平面数组为
    (F1 时, F2 时):13C 轴为 hypercomplex 4×TD(300 real),15N 轴为
    States 实型(TD)。窗函数评分须与后端一致地作用在原始实型轴
    (SP 直接作用于该轴),故**不**做简单轴 0 交错拆包(会拆错
    hypercomplex 数据);堆叠后 (F1 时, F2 时, F3)。只读首平面头部
    FDFILECOUNT 个平面,避免陈旧 test*.ft1 混入(补29)。
    2D nus2d/recon.ft1 单文件 (F2 频, F1 时),F1 复型在最后轴——nmrglue
    已直接读为复型 (F2, F1) complex,不能再用 read_pipe_complex 拆轴 0
    (会把直接维砍半;0.2.199-补29b 实证 sampleF 制造的 2D recon)。
    """
    import nmrglue as ng

    from core.data.pipe_io import read_pipe_complex

    if experiment.ndim >= 3:
        plane_dir = work / "nus3d_rc"
        paths = sorted(plane_dir.glob("test*.ft1"))
        if not paths:
            return None
        dic: dict = {}
        arrays: list[np.ndarray] = []
        count: int | None = None
        for index, path in enumerate(paths):
            d, raw = ng.pipe.read(str(path))
            if index == 0:
                dic = d
                try:
                    count = int(float(dic.get("FDFILECOUNT") or 0))
                except (TypeError, ValueError):
                    count = None
            if count is not None and len(arrays) >= count:
                break
            arrays.append(np.asarray(raw))
        if not arrays:
            return None
        return np.stack(arrays, axis=-1), dic
    recon = work / "nus2d" / "recon.ft1"
    if not recon.is_file():
        return None
    dic, raw = ng.pipe.read(str(recon))
    arr = np.asarray(raw)
    if np.iscomplexobj(arr):
        planes: np.ndarray = arr.astype(np.complex128)
    else:
        planes = read_pipe_complex(recon)
    return planes, dic



def _uniform_axis_map(experiment: Experiment) -> dict[str, int]:
    """uniform fid 布局:2D (F1,F2)、3D (F1,F2,F3),间接维按内部约定取轴。"""
    from core.data.internal_data_model import AxisRole
    from core.processing.axes import axis_index

    return {
        dim.logical_axis: axis_index(dim.logical_axis, experiment.ndim)
        for dim in experiment.dimensions
        if dim.role is not AxisRole.DIRECT
    }


def _nus_axis_map(experiment: Experiment) -> dict[str, int]:
    """NUS 重构平面布局(0.2.199-补29 修正):2D (F2 频, F1 时) → F1=1;
    3D 堆叠 (F1 时, F2 时, F3) → F1=0, F2=1。旧代码 F1=2 指向直接维轴。"""
    if experiment.ndim >= 3:
        return {"F1": 0, "F2": 1}
    return {"F1": 1}


def _axis_sw(
    dic: dict[str, Any], axis: str, experiment: Experiment | None = None
) -> float:
    """从 fid/平面头部取逻辑轴谱宽(SW Hz)。

    0.2.199-补29:优先按头部核标签(FDF{n}LABEL)匹配逻辑轴核(3D 头部
    FDF1=15N/FDF2=1H/FDF3=13C,与逻辑 F2/F3/F1 不同号,按数字后缀取会
    拿错轴的 SW);实验未知时回退数字后缀(2D uniform 头部与逻辑同号)。
    """
    if experiment is not None:
        dim = next(
            (d for d in experiment.dimensions if d.logical_axis == axis), None
        )
        nucleus = (dim.nucleus or "").strip() if dim is not None else ""
        # 头部 LABEL 为 "15N",Bruker NUC1 为 "<15N>",只留字母数字比较
        norm = lambda v: "".join(ch for ch in v if ch.isalnum())  # noqa: E731
        if nucleus:
            for i in (1, 2, 3):
                label = str(dic.get(f"FDF{i}LABEL") or "").strip()
                if norm(label) == norm(nucleus):
                    try:
                        return float(dic.get(f"FDF{i}SW") or 0.0)
                    except (TypeError, ValueError):
                        return 0.0
    suffix = axis[1:] if axis.startswith("F") else axis
    try:
        return float(dic.get(f"FDF{suffix}SW") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def optimize_direct_window_from_work(
    work_dir: Path | str,
    experiment: Experiment,
    *,
    current: dict[str, Any] | None = None,
    zf_size: int | None = None,
    candidates: list[dict[str, Any]] | None = None,
) -> WindowOptimizeResult:
    """从转换后 fid(work 目录,支持切片流)加载并优化直接维窗,
    不重跑 SMILE/process。SW(谱宽)从 fid 头读取,供 GM/EM 精确建模。"""
    work = Path(work_dir)
    try:
        loaded = _load_fid(work, experiment)
    except Exception as exc:  # noqa: BLE001
        return WindowOptimizeResult(
            choice=dict(current or {}),
            changed=False,
            logs=[f"直接维窗优化失败(读取 fid): {exc}"],
        )
    if loaded is None:
        return WindowOptimizeResult(
            choice=dict(current or {}),
            changed=False,
            logs=["直接维窗优化跳过:未找到转换后 .fid"],
        )
    fid, dic = loaded
    direct_axis = f"F{experiment.ndim}"
    return optimize_direct_window(
        fid,
        zf_size=zf_size,
        candidates=candidates,
        current=current,
        sw=_axis_sw(dic, direct_axis, experiment),
    )



def optimize_indirect_windows_from_work(
    work_dir: Path | str,
    experiment: Experiment,
    *,
    current: dict[str, dict[str, Any]] | None = None,
    candidates: list[dict[str, Any]] | None = None,
) -> MultiWindowOptimizeResult:
    """从转换后 fid 优化 uniform 各间接维窗(内存评分,不重跑 process)。
    各轴 SW 从 fid 头读取,供 GM/EM 精确建模。"""
    work = Path(work_dir)
    try:
        loaded = _load_fid(work, experiment)
    except Exception as exc:  # noqa: BLE001
        return MultiWindowOptimizeResult(
            choice=dict(current or {}),
            changed=False,
            logs=[f"间接维窗优化失败(读取 fid): {exc}"],
        )
    if loaded is None:
        return MultiWindowOptimizeResult(
            choice=dict(current or {}),
            changed=False,
            logs=["间接维窗优化跳过:未找到转换后 .fid"],
        )
    fid, dic = loaded
    axis_map = _uniform_axis_map(experiment)
    sw_map = {axis: _axis_sw(dic, axis, experiment) for axis in axis_map}
    return optimize_indirect_windows(
        fid, axis_map, candidates=candidates, current=current, sw_map=sw_map
    )



def optimize_indirect_windows_from_recon(
    work_dir: Path | str,
    experiment: Experiment,
    *,
    current: dict[str, dict[str, Any]] | None = None,
    candidates: list[dict[str, Any]] | None = None,
) -> MultiWindowOptimizeResult:
    """从 SMILE 重构平面优化 NUS 各间接维窗(内存评分,不重跑 SMILE)。
    各轴 SW 从平面头部读取,供 GM/EM 精确建模。"""
    work = Path(work_dir)
    try:
        loaded = _load_recon_planes(work, experiment)
    except Exception as exc:  # noqa: BLE001
        return MultiWindowOptimizeResult(
            choice=dict(current or {}),
            changed=False,
            logs=[f"间接维窗优化失败(读取重构平面): {exc}"],
        )
    if loaded is None:
        return MultiWindowOptimizeResult(
            choice=dict(current or {}),
            changed=False,
            logs=["间接维窗优化跳过:未找到 SMILE 重构平面"],
        )
    planes, dic = loaded
    axis_map = _nus_axis_map(experiment)
    sw_map = {axis: _axis_sw(dic, axis, experiment) for axis in axis_map}
    return optimize_indirect_windows(
        planes, axis_map, candidates=candidates, current=current, sw_map=sw_map
    )



__all__ = [
    "DEFAULT_CANDIDATES",
    "INDIRECT_CANDIDATES",
    "MultiWindowOptimizeResult",
    "WindowChoice",
    "WindowOptimizeResult",
    "optimize_axis_window",
    "optimize_direct_window",
    "optimize_direct_window_from_work",
    "optimize_indirect_windows",
    "optimize_indirect_windows_from_recon",
    "optimize_indirect_windows_from_work",
]
