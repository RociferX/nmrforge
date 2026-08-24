"""相位质量：吸收度比例 / 连续负面积 / 谱熵 / 负峰比例 / 峰对称性。

评分依据（成熟文献方案）：
- 负面积最小化（de Brouwer et al., JMR 201 (2009) 230-238）：先做稳健基线
  校正再统计负值面积——相位误差把吸收能量摊到色散边瓣，负面积随误差一阶
  上升，是 ±5° 邻域区分度最高的连续指标；
- 谱熵最小化（Ernst 1966；Chen 2002 高效版）：相位正确时谱最集中，
  正部熵最低，方向与负面积一致；
- 吸收度（实虚平衡）与负峰计数（180° 反相兜底）作为补充。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from core.qc import peak_detection


@dataclass
class PhaseQuality:
    absorption_fraction: float = 0.0
    negative_peak_fraction: float = 0.0
    negative_area_fraction: float = 0.0
    entropy: float = 0.0
    symmetry: float = 0.0
    score: float = 0.0


def negative_area_fraction(
    real: Any, radius: int = 8, *, symmetry_gated: bool = True
) -> float:
    """峰窗负面积比例：检测峰后在峰窗口内统计负值面积占比（峰高加权）。

    0.2.63 起从全谱统计改为峰窗统计（VM 真实数据校准）：全谱负面积被
    噪声/基线稀释，对相位几乎不敏感（sampleF 实测全谱评分在 ±5° 内仅
    0.02 分）；相位误差的色散负边瓣集中在峰窗内，峰窗负面积占比对相位
    敏感且尖锐（sampleF 粗网格判别力 ~2 分/100 分制）。
    返回 [0, 1]，0 = 峰窗无负值区域，1 = 全部为负。
    """
    arr = np.asarray(np.real(real), dtype=float)
    # 正峰 + 负峰(180° 反相谱 detect 找不到正峰,必须分别检测)
    _pos = peak_detection.detect(arr)
    _neg = peak_detection.detect(-arr)
    _total = len(_pos) + len(_neg)
    # 0.2.171:反相谱(负峰占绝对多数)不 gate,照常惩罚
    neg_majority = _total > 0 and len(_neg) > 0.6 * _total
    peaks = list(_pos) + list(_neg)
    if not peaks:
        return 0.0  # 无峰：无负面积信息，不惩罚
    # 只统计强峰：噪声峰无数目性负瓣，会稀释相位敏感信号（实测 501 个
    # 峰里真实信号峰占少数，全峰平均后负面积恒≈0，相位判别失效）
    heights = np.asarray([float(p.height) for p in peaks])
    threshold = max(float(np.percentile(np.abs(arr), 99.5)), 0.0)
    strong = [p for p, h in zip(peaks, heights) if h >= threshold]
    # 跳过边缘峰(窗口不完整,统计无意义;FFT 边界伪影峰常落在数组两端)
    strong = [
        p
        for p in strong
        if all(
            radius <= int(round(float(v))) < s - radius
            for v, s in zip(np.atleast_1d(p.position), arr.shape)
        )
    ]
    if not strong:
        return 0.0  # 强峰全在边缘/窗口不完整:无有效信号峰,不惩罚
    # 只取峰高 top-5 强峰(0.2.63,VM sampleF 校准):全强峰加权聚合会把
    # 大量弱峰/伪影纳入,与主峰观感不一致(顺序搜索收敛到局部最优);
    # 主峰(最强峰)是相位最可靠指示,top-N 与用户看到的一维谱峰形一致
    strong.sort(key=lambda p: p.height, reverse=True)
    strong = strong[:5]
    total_abs = 0.0
    total_neg = 0.0
    for peak in strong:
        pos = np.round(np.asarray(peak.position)).astype(int)
        slices = tuple(
            slice(max(0, i - radius), min(s, i + radius + 1))
            for i, s in zip(pos, arr.shape)
        )
        win = arr[slices]
        if symmetry_gated and not neg_majority:
            # 0.2.171:吸收型(峰窗对称)负成分——真实负峰(mixed 谱 Cβ)、
            # 噪声负瓣——不算相位错误;仅不对称的色散负瓣计罚
            flipped = np.flip(win)
            denom = float(np.sum(win * win) * np.sum(flipped * flipped))
            corr = (
                float(np.sum(win * flipped) / np.sqrt(denom))
                if denom > 1e-12
                else 0.0
            )
            if corr >= 0.0:
                continue
        total_abs += float(np.sum(np.abs(win)))
        total_neg += float(-np.sum(np.minimum(win, 0.0)))
    return float(total_neg / (total_abs + 1e-12))


def negative_area_axis(real: Any, axis: int, radius: int = 8) -> float:
    """沿指定轴的一维剖面峰窗负面积(旧项目 NMRFlow 逐轴调相方式)。

    相位误差的色散负瓣沿被调轴方向展开;沿该轴取峰位置的一维剖面,
    统计负值占比(正/负峰分别检测,强峰过滤,峰高加权)。二维窗口会
    把已调好方向的峰形(如 F2 已吸收)纳入,稀释目标轴相位信号——
    VM sampleF 实测二维聚合与一维主峰剖面排序不一致,一维剖面与
    用户看到的一维谱峰形一致。
    """
    arr = np.asarray(np.real(real), dtype=float)
    peaks = list(peak_detection.detect(arr)) + list(
        peak_detection.detect(-arr)
    )
    if not peaks:
        return 0.0
    heights = np.asarray([float(p.height) for p in peaks])
    threshold = max(float(np.percentile(np.abs(arr), 99.5)), 0.0)
    strong = [p for p, h in zip(peaks, heights) if h >= threshold]
    strong = [
        p
        for p in strong
        if 0 <= axis < arr.ndim
        and radius
        <= int(round(float(np.atleast_1d(p.position)[axis])))
        < arr.shape[axis] - radius
    ]
    if not strong:
        return 0.0
    # 只取峰高 top-5 强峰(与 negative_area_fraction 一致,主峰优先)
    strong.sort(key=lambda p: p.height, reverse=True)
    strong = strong[:5]
    total_abs = 0.0
    total_neg = 0.0
    for peak in strong:
        pos = np.round(np.asarray(peak.position)).astype(int)
        lo = max(0, int(pos[axis]) - radius)
        hi = min(arr.shape[axis], int(pos[axis]) + radius + 1)
        sl = tuple(
            slice(lo, hi) if i == axis else slice(int(pos[i]), int(pos[i]) + 1)
            for i in range(arr.ndim)
        )
        win = arr[sl]
        total_abs += float(np.sum(np.abs(win)))
        total_neg += float(-np.sum(np.minimum(win, 0.0)))
    return float(total_neg / (total_abs + 1e-12))


def profile_symmetry_axis(real: Any, axis: int, radius: int = 6) -> float:
    """峰窗一维剖面镜像对称性(吸收≈+1,色散≈-1),top-5 强峰中位数。

    旧项目(NMRFlow)用一维剖面与镜像的相关系数做 ±90° 相位歧义消解:
    吸收峰为偶函数(镜像相关≈+1),色散峰为奇函数(镜像相关≈-1)。
    注意:全谱翻转相关(旧 0.2.38 方案)会把 90° 色散 2D 谱误判为对称,
    一维峰窗剖面才是正确的奇偶性判别。
    """
    arr = np.asarray(np.real(real), dtype=float)
    peaks = list(peak_detection.detect(arr)) + list(
        peak_detection.detect(-arr)
    )
    if not peaks:
        return 0.0
    heights = np.asarray([float(p.height) for p in peaks])
    threshold = max(float(np.percentile(np.abs(arr), 99.5)), 0.0)
    strong = [p for p, h in zip(peaks, heights) if h >= threshold]
    strong = [
        p
        for p in strong
        if 0 <= axis < arr.ndim
        and radius
        <= int(round(float(np.atleast_1d(p.position)[axis])))
        < arr.shape[axis] - radius
    ]
    strong.sort(key=lambda p: p.height, reverse=True)
    strong = strong[:5]
    if not strong:
        return 0.0
    corrs: list[float] = []
    for peak in strong:
        pos = np.round(np.asarray(peak.position)).astype(int)
        lo = max(0, int(pos[axis]) - radius)
        hi = min(arr.shape[axis], int(pos[axis]) + radius + 1)
        sl = tuple(
            slice(lo, hi) if i == axis else slice(int(pos[i]), int(pos[i]) + 1)
            for i in range(arr.ndim)
        )
        profile = arr[sl].astype(float).ravel()
        if profile.size < 5 or float(np.std(profile)) == 0.0:
            continue
        corr = float(np.corrcoef(profile, profile[::-1])[0, 1])
        if corr == corr:
            corrs.append(corr)
    return float(np.median(corrs)) if corrs else 0.0


def _peak_window_nets(real: Any, radius: int = 8) -> list[float]:
    """强峰窗签名净吸收列表(与优化 score_axis_memory 同公式,0.2.172)。

    检测正/负峰,取峰高 top-5 强峰(避开边缘),在 ±radius 峰窗内统计
    净吸收 (正+负)/|总|。吸收峰(无论正负)净吸收约 ±1,色散峰约 0。
    """
    arr = np.asarray(np.real(real), dtype=float)
    peaks = list(peak_detection.detect(arr)) + list(
        peak_detection.detect(-arr)
    )
    if not peaks:
        return []
    heights = np.asarray([float(p.height) for p in peaks])
    # 阈值降级(0.2.172):FFT 边缘伪影常占 99.5 分位且被边缘过滤剔除,
    # 导致无有效强峰——逐级降阈值(95/75)让真实信号峰入选,与优化
    # _lock_discrete_traces 的降级策略一致
    strong: list[Any] = []
    for pct in (99.5, 95.0, 75.0):
        threshold = max(float(np.percentile(np.abs(arr), pct)), 0.0)
        strong = [p for p, h in zip(peaks, heights) if h >= threshold]
        strong = [
            p
            for p in strong
            if all(
                radius <= int(round(float(v))) < s - radius
                for v, s in zip(np.atleast_1d(p.position), arr.shape)
            )
        ]
        if strong:
            break
    strong.sort(key=lambda p: p.height, reverse=True)
    nets: list[float] = []
    for peak in strong[:5]:
        pos = np.round(np.asarray(peak.position)).astype(int)
        # 沿各轴取一维峰剖面(与优化迹线窗口一致,±radius)。相位误差沿
        # 被调轴展开,其它轴保持吸收——取 |净吸收| 最小的剖面(最差方向)
        # 代表该峰相位状态,避免取最强剖面时漏掉被调轴的色散(2D 谱 45°
        # 未校正候选在 F1 方向仍吸收,此前误判高分)
        best: tuple[float, float] | None = None
        for axis in range(arr.ndim):
            sl = tuple(
                slice(max(0, i - radius), min(s, i + radius + 1))
                if a == axis
                else slice(i, i + 1)
                for a, (i, s) in enumerate(zip(pos, arr.shape))
            )
            profile = arr[sl].ravel()
            total = float(np.abs(profile).sum())
            if total <= 1e-12:
                continue
            net = float(
                (
                    np.clip(profile, 0.0, None).sum()
                    + np.clip(profile, None, 0.0).sum()
                )
                / total
            )
            if best is None or abs(net) < best[1]:
                best = (net, abs(net))
        if best is not None:
            nets.append(best[0])
    return nets


def spectral_entropy(real: Any) -> float:
    """正部谱熵（Ernst 最小熵，归一化到 [0, 1]）。

    基线扣除后取正部为能量分布：均匀分布熵=1，单点集中=0。
    相位错误把能量摊开 → 熵上升；实型终谱下方向与负面积一致。
    """
    arr = np.asarray(np.real(real), dtype=float)
    pos = np.maximum(arr - float(np.median(arr)), 0.0)
    total = float(np.sum(pos))
    if total <= 1e-12 or pos.size < 2:
        return 1.0
    p = pos.ravel() / total
    p = p[p > 0]
    n = p.size
    if n < 2:
        return 0.0
    return float(-np.sum(p * np.log(p)) / np.log(n))


def evaluate(data: Any, *, sign_mode: str = "uniform") -> PhaseQuality:
    """评估相位质量（吸收度 + 连续负面积 + 谱熵 + 负峰 + 对称性）。

    评分 = 100 × (0.25×吸收度 + 0.40×(1−负面积) + 0.20×(1−熵)
    + 0.15×(1−负峰比例))。终谱为实型（D005）时吸收度恒 1，区分主要靠
    负面积与熵；负峰计数兜底 180° 反相。镜像对称性不再计分：90° 色散谱
    反而高度对称，旧公式会把它误判为高相位质量（字段保留供调用方读取）。
    """
    arr = np.asarray(data)
    real = np.real(arr)
    imag = np.imag(arr)
    abs_real = float(np.mean(np.abs(real))) + 1e-12
    abs_imag = float(np.mean(np.abs(imag))) + 1e-12
    absorption = abs_real / (abs_real + abs_imag)

    positive = peak_detection.detect(arr)
    negative = peak_detection.detect(-real)
    total = len(positive) + len(negative)
    neg_fraction = len(negative) / total if total else 0.0
    na = negative_area_fraction(real)
    ent = spectral_entropy(real)
    # 0.2.172:相位主评分与优化同源——峰窗签名净吸收
    # (memory_phase_search.score_axis_memory 同公式);修复"谱图看着
    # 相位正常但相位分低"的误报
    nets = _peak_window_nets(real)

    symmetry = 0.5
    if real.size > 1 and float(np.std(real)) > 1e-12:
        mirrored = np.flip(real)
        corr = float(np.corrcoef(real.ravel(), mirrored.ravel())[0, 1])
        if not np.isnan(corr):
            symmetry = max(0.0, min(1.0, (corr + 1.0) / 2.0))

    # 0.2.172:与优化同源(峰窗签名净吸收,0-100)为主,叠加 10% 谱熵
    # 微调保证弱峰窗/1D 谱也随相位误差单调;无有效峰窗时中性 50
    # (与优化 score_axis_memory 无迹线时返回 50 一致)
    if nets:
        if sign_mode == "mixed":
            net_score = 50.0 * (float(np.median(np.abs(nets))) + 1.0)
            strong = [n for n in nets if abs(n) > 0.35]
            if strong:
                has_pos = any(n > 0 for n in strong)
                has_neg = any(n < 0 for n in strong)
                if not (has_pos and has_neg):
                    net_score *= 0.7
        else:
            net_score = 50.0 * (float(np.median(nets)) + 1.0)
        phase_score = 0.9 * net_score + 0.1 * (100.0 * (1.0 - ent))
    else:
        phase_score = 50.0
    score = float(np.clip(phase_score, 0.0, 100.0))
    return PhaseQuality(
        absorption_fraction=float(absorption),
        negative_peak_fraction=float(neg_fraction),
        negative_area_fraction=float(na),
        entropy=float(ent),
        symmetry=float(symmetry),
        score=score,
    )
