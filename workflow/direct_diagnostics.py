"""直接维数据质量诊断门控(0.2.140)。

在生成谱图步骤的最开始运行(基于转换后 fid,内存评估,不重跑 SMILE):
检出可纠正问题(直流偏置→POLY -time、尖峰坏点→自动替换)自动处理;
难以通过数据处理消除的问题(频率漂移、宽带溶剂残留、采样点能量严重不均)
明确报告并给出建议(检查温控/溶剂压制/增益或重新采谱)。

诊断报告随处理日志呈现给用户,格式:
  1. 直接维存在直流偏置(DC 峰为最强信号的 X.X 倍),已启用 POLY -time 纠正
  2. 检测到 N 处尖峰坏点,已自动替换(备份见 fid_diag_bak/)
  3. 采样前/后周期存在频率漂移,数据处理无法完全消除,建议检查温控并考虑重新采谱

fid 字节布局(nmrPipe 标准):512 字节参数头 + 每迹 [实部块(fdsize 复点数×4B),
虚部块(fdsize×4B)],complex64。读写均按该布局直接进行,不依赖 nmrglue
的写回路径(其 complex 写回存在形状解析问题)。
"""

from __future__ import annotations

import json
import shutil
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from core.data.internal_data_model import Experiment, SamplingMode

# 阈值(初版,经验值)
DC_RATIO_THRESHOLD = 0.03          # DC 峰高于最强信号的 3% 即启用 POLY -time
BADPOINT_MAD = 12.0                # 孤立尖峰 = 幅度超出邻域中值 12×MAD
FIRST_POINT_RATIO = 1.6            # 首点幅/次点幅超 1.6× 提示群延迟重建
BROAD_PEAK_FRACTION = 0.08         # 最强峰 FWHM 超过谱宽 8% 视为宽带包(疑似溶剂)
DRIFT_FWHM_MULT = 1.0              # 首尾采样周期峰位漂移超过 1 个 FWHM 提示重采
ENERGY_TOP20_THRESHOLD = 0.92      # 前 20% 迹能量占比超 92% 提示分布不均

# nmrPipe fid 头长度因 2D(2048B)/3D 流(512B)等而异,解析时动态判定
COMPLEX_BYTES = 8  # complex64 每复点
HEADER_CANDIDATES = (512, 1024, 2048)


@dataclass
class DirectDiagnosticsResult:
    """直接维诊断结果。"""

    reports: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    apply_poly_time: bool = False
    repaired_badpoints: int = 0
    backup_dir: str = ""


def _collect_fid_paths(
    work: Path, experiment: Experiment
) -> list[Path]:
    """定位转换后 fid:单文件(dataset.fid)或多段 fid/testNNN.fid。"""
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


def _read_fid_raw(
    path: Path,
) -> tuple[np.ndarray, int, int, int] | None:
    """按字节布局读取 fid → (complex64 (nrows, fdsize), nrows, fdsize, header)。

    以 nmrglue read 的复型数组为基准,试候选头长度(512/1024/2048),
    逐元素全等者即为该文件的真实布局。支持:
    - 旧切片式 2D 复型平面:(specnum, fdsize),实虚交错沿第二轴;
    - 0.2.199-补16 单文件 aq2D 伪 3D:(a, b, c) 3D 复型,直接维在最后
      一轴,reshape 为 (a*b, c)(0.2.199-补25)。
    """
    raw = path.read_bytes()
    if len(raw) <= 2048:
        return None
    try:
        import nmrglue as ng

        dic, d = ng.pipe.read(str(path))
        arr = np.asarray(d)
        fdsize = int(float(dic["FDSIZE"]))
        specnum = int(float(dic["FDSPECNUM"]))
    except Exception:  # noqa: BLE001
        return None
    # 单文件 aq2D:nmrglue 读为 3D 复型(a, b, c),直接维 = 最后一轴
    if arr.ndim == 3:
        nrows = int(arr.shape[0] * arr.shape[1])
        fdsize3 = int(arr.shape[2])
        target = arr.reshape(nrows, fdsize3).astype(np.complex64)
        for header in HEADER_CANDIDATES + (0,):
            expect = header + nrows * fdsize3 * COMPLEX_BYTES
            if len(raw) != expect:
                continue
            flat = np.frombuffer(
                raw, dtype="<f4", count=nrows * fdsize3 * 2, offset=header
            ).astype(np.float32)
            rows = flat.reshape(nrows, fdsize3 * 2)
            cand = rows[:, :fdsize3] + 1j * rows[:, fdsize3:]
            if cand.shape == target.shape and np.array_equal(
                cand, target, equal_nan=True
            ):
                return target, fdsize3, nrows, header
        # 头长度不在候选内:按文件尺寸反推,仍返回 nmrglue 读值
        for header in HEADER_CANDIDATES:
            if len(raw) >= header + nrows * fdsize3 * COMPLEX_BYTES:
                return target, fdsize3, nrows, header
        return target, fdsize3, nrows, 0
    if arr.ndim != 2 or arr.shape != (specnum, fdsize):
        return None
    target = arr.astype(np.complex64)
    for header in HEADER_CANDIDATES:
        expect = header + specnum * fdsize * COMPLEX_BYTES
        if len(raw) != expect:
            continue
        flat = np.frombuffer(
            raw, dtype="<f4", count=specnum * fdsize * 2, offset=header
        ).astype(np.float32)
        rows = flat.reshape(specnum, fdsize * 2)
        cand = rows[:, :fdsize] + 1j * rows[:, fdsize:]
        if cand.shape == target.shape and np.array_equal(cand, target, equal_nan=True):
            return cand, fdsize, specnum, header
    return None


def _trace_metrics(
    traces: np.ndarray, n: int
) -> dict[str, float]:
    """top 迹上计算直流/首点/宽带峰/漂移指标。"""
    energy = np.sum(np.abs(traces) ** 2, axis=-1)
    order = np.argsort(energy)[::-1]
    keep = min(max(int(np.ceil(len(order) * 0.05)), 4), 12)
    top = traces[order[:keep]]
    n_pad = n
    work = np.zeros((len(top), n_pad), dtype=complex)
    work[:, : min(n, n_pad)] = top[:, : min(n, n_pad)]
    spec = np.fft.fft(work, axis=-1)
    amp = np.abs(spec)
    dc = np.median(amp[:, 0])
    side = np.max(amp[:, 3 : n_pad // 2], axis=1)
    dc_ratio = float(np.median(dc / np.maximum(side, 1e-12)))
    p_side = np.argmax(amp[:, 3 : n_pad // 2], axis=1) + 3
    avg = np.median(amp, axis=0)
    pk = int(np.median(p_side))
    peak = avg[pk]
    half = peak / 2
    left = pk
    while left > 0 and avg[left] > half:
        left -= 1
    r = pk
    while r < n_pad - 1 and avg[r] > half:
        r += 1
    fwhm_pts = max(float(r - left), 1.0)
    broad = fwhm_pts / n_pad > BROAD_PEAK_FRACTION
    med_amp = np.median(np.abs(top), axis=0)
    first_ratio = (
        float(med_amp[0] / max(med_amp[1], 1e-12))
        if n > 2 and med_amp[1] > 0
        else 0.0
    )
    split = max(int(len(order) * 0.2), 2)
    head = traces[order[:split]]
    tail = traces[order[-split:]]
    pos_head, pos_tail = [], []
    for arr2 in (head, tail):
        w2 = np.zeros((len(arr2), n_pad), dtype=complex)
        w2[:, : min(n, n_pad)] = arr2[:, : min(n, n_pad)]
        a2 = np.abs(np.fft.fft(w2, axis=-1))
        a2[:, :3] = 0.0
        pos = np.argmax(a2[:, 3 : n_pad // 2], axis=1)
        (pos_head if arr2 is head else pos_tail).append(float(np.median(pos)))
    drift_pts = abs(float(np.median(pos_head)) - float(np.median(pos_tail)))
    return {
        "dc_ratio": dc_ratio,
        "first_point_ratio": first_ratio,
        "broad_peak": broad,
        "fwhm_pts": fwhm_pts,
        "drift_pts": drift_pts,
        "n_direct": n,
    }


def _find_bad_points(row: np.ndarray) -> np.ndarray:
    """孤立尖峰掩码:幅度 >> 邻域中值且两侧同时陡降。

    0.2.199-补29u:逐点 Python 循环向量化——3D NUS 数千条迹 × 2048 点
    的原实现是数据质量诊断的主要耗时。
    """
    amp = np.abs(row)
    med = float(np.median(amp))
    mad = float(np.median(np.abs(amp - med)))
    n = amp.size
    cand = np.zeros(n, dtype=bool)
    if mad <= 0 or n < 5:
        return cand
    thr = med + BADPOINT_MAD * 1.4826 * mad
    center = amp[2:-2]
    left = np.maximum(amp[1:-3], amp[0:-4])
    right = np.maximum(amp[3:-1], amp[4:])
    cand[2:-2] = (
        (center > thr)
        & (center > 3.0 * np.maximum(left, 1e-12))
        & (center > 3.0 * np.maximum(right, 1e-12))
    )
    return cand


def _patch_point(
    raw: bytearray, fdsize: int, header: int, row: int, col: int, value: complex
) -> None:
    """写回单点(复值)到字节缓冲:实部块/虚部块布局。"""
    re_off = header + row * fdsize * COMPLEX_BYTES + col * 4
    struct.pack_into("<f", raw, re_off, float(value.real))
    struct.pack_into("<f", raw, re_off + fdsize * 4, float(value.imag))


def run_direct_diagnostics(
    work_dir: Path | str,
    experiment: Experiment,
    *,
    repair: bool = True,
) -> DirectDiagnosticsResult:
    """生成谱图最开始的直接维诊断门控。返回报告/指标/处理决定。"""
    work = Path(work_dir)
    paths = _collect_fid_paths(work, experiment)
    res = DirectDiagnosticsResult()
    if not paths:
        res.reports = ["数据质量诊断:未找到转换后 fid,跳过(诊断不阻断处理)"]
        return res
    blocks: list[np.ndarray] = []
    parsed: list[tuple[Path, int, int, int]] = []
    for path in paths:
        got = _read_fid_raw(path)
        if got is None:
            continue
        data, fdsize, specnum, header = got
        blocks.append(data)
        parsed.append((path, fdsize, specnum, header))
    if not blocks:
        res.reports = ["数据质量诊断:转换后 fid 布局无法解析,跳过"]
        return res
    traces = np.concatenate(blocks, axis=0)
    n = int(traces.shape[-1])
    m = _trace_metrics(traces, n)
    res.metrics = {
        k: (float(v) if isinstance(v, (int, float, np.floating)) else bool(v))
        for k, v in m.items()
    }
    reports: list[str] = []

    if m["dc_ratio"] > DC_RATIO_THRESHOLD:
        res.apply_poly_time = True
        reports.append(
            f"直接维存在直流偏置(DC 峰为最强信号的 {m['dc_ratio']:.2f} 倍),"
            "已启用 POLY -time 自动纠正"
        )

    repaired = 0
    backup = work / "fid_diag_bak"
    made_backup = False
    if repair:
        repaired = 0
        for path, fdsize, specnum, header in parsed:
            got = _read_fid_raw(path)
            if got is None:
                continue
            data, _fs, _sn, _hdr = got
            raw = bytearray(path.read_bytes())
            for row in range(specnum):
                energy = float(np.sum(np.abs(data[row]) ** 2))
                if energy <= 0:  # NUS 未采集平面
                    continue
                mask = _find_bad_points(data[row])
                if not np.any(mask):
                    continue
                if not made_backup:
                    backup.mkdir(parents=True, exist_ok=True)
                    for p in paths:
                        try:
                            shutil.copy2(p, backup / p.name)
                        except OSError:
                            pass
                    made_backup = True
                for col in np.where(mask)[0]:
                    if 0 < col < fdsize - 1:
                        val = 0.5 * (data[row, col - 1] + data[row, col + 1])
                        data[row, col] = val
                        _patch_point(raw, fdsize, header, row, col, val)
                        repaired += 1
            if made_backup:
                path.write_bytes(bytes(raw))
        res.repaired_badpoints = repaired
    if repaired:
        reports.append(
            f"检测到 {repaired} 处尖峰坏点,已自动替换"
            "(原始 fid 备份于 fid_diag_bak/,重新转换 fid.com 亦可恢复)"
        )
        nonzero = int(np.sum(np.sum(np.abs(traces) ** 2, axis=-1) > 0))
        if repaired / max(nonzero * n, 1) > 0.005:
            reports.append("坏点占比偏高,建议同时检查采集端 ADC/增益稳定性")

    if m["first_point_ratio"] > FIRST_POINT_RATIO:
        reports.append(
            f"采样首点幅度偏高(为次点 {m['first_point_ratio']:.2f} 倍),"
            "群延迟/首点重建一般已由转换参数处理;如高场基线仍翘,"
            "建议检查 acqus 的 GRPDLY/DSPFVS 参数"
        )
    if m["broad_peak"]:
        reports.append(
            "检测到宽带包峰(FWHM 超过谱宽 8%),疑似溶剂/化学交换残留,"
            "数据处理难以完全消除,建议核对溶剂压制条件"
        )
    if m["drift_pts"] > DRIFT_FWHM_MULT * m["fwhm_pts"]:
        reports.append(
            f"采样前/后周期直接维频率漂移约 {m['drift_pts']:.1f} 点"
            f"({m['drift_pts'] * m['fwhm_pts']:.1f} 个线宽量级),"
            "数据处理无法完全消除,建议检查温控/锁场并考虑重新采谱"
        )
    energy = np.sum(np.abs(traces) ** 2, axis=-1)
    # 0.2.199-补29z:NUS 大部分迹为空(未采集),前 20% 全迹必然占 100%
    # 能量导致每个谱都误报——只统计非空迹的能量分布
    nz = energy[energy > 0]
    frac = 0.0
    if nz.size >= 8:
        order = np.argsort(nz)[::-1]
        top20 = max(int(np.ceil(nz.size * 0.2)), 1)
        frac = float(np.sum(nz[order[:top20]]) / max(np.sum(nz), 1e-12))
    res.metrics["energy_top20_frac"] = frac
    if frac > ENERGY_TOP20_THRESHOLD:
        reports.append(
            f"采样点能量分布不均(非空迹前 20% 占 {frac * 100:.0f}% 能量),"
            "可能是增益步长/脉冲不稳定;不影响重构时可继续,否则建议核查采集"
        )
    # 0.2.196:潜在问题只报告不自动处理——NaN/Inf、全零迹、持续异常能量
    n_nan_inf = int(np.isnan(traces).sum()) + int(np.isinf(traces).sum())
    res.metrics["nan_inf_count"] = n_nan_inf
    if n_nan_inf:
        reports.append(
            f"检测到 {n_nan_inf} 处 NaN/Inf 值,未自动处理"
            "(建议核查采集端与转换参数)"
        )
    zero_traces = int(np.sum(energy == 0))
    res.metrics["zero_traces"] = zero_traces
    if experiment.sampling.mode is SamplingMode.UNIFORM and zero_traces:
        reports.append(
            f"检测到 {zero_traces} 条全零迹线,未自动处理"
            "(均匀采样不应存在全零迹,采集可能缺失)"
        )
    nz_energy = energy[energy > 0]
    high_energy = 0
    if nz_energy.size >= 4:
        med_nz = float(np.median(nz_energy))
        if med_nz > 0:
            high_energy = int(np.sum(nz_energy > 100.0 * med_nz))
            res.metrics["high_energy_traces"] = high_energy
    if high_energy:
        reports.append(
            f"{high_energy} 条迹线能量异常偏高(>100×中位),非孤立尖峰,"
            "未自动处理(建议核查增益/脉冲稳定性)"
        )
    if not reports:
        reports = ["数据质量诊断:未检出直流偏置、尖峰坏点、首点异常、宽带峰或漂移"]
    res.reports = reports
    if made_backup:
        res.backup_dir = str(backup)
    try:
        (work / "diagnostics.json").write_text(
            json.dumps(
                {
                    "reports": reports,
                    "metrics": res.metrics,
                    "apply_poly_time": res.apply_poly_time,
                    "repaired_badpoints": repaired,
                    "backup_dir": res.backup_dir or str(backup),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass
    return res


__all__ = [
    "DirectDiagnosticsResult",
    "run_direct_diagnostics",
]
