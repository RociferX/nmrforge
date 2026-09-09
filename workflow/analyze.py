"""HSQC CSP 分析(0.2.199-补29er)。

当前数据(扰动态/bound)对比比对数据(自由态/free/apo)的 HSQC 谱与峰表,
按 Assignment 优先、最近邻兜底匹配峰,计算化学位移扰动:
    Δδ = sqrt(ΔδH² + (csp_n_weight·ΔδN)²),csp_n_weight 默认 0.2(1/5,
    15N-HSQC 常用加权)。

输出到 <项目根>/analysis/<exp_id>/<data_id>/:
    csp_data.csv         逐匹配峰:Assignment,H_ref,N_ref,H_cur,N_cur,
                         ΔH,ΔN,Δδ(ppm)
    csp_plot.svg         Δδ 条形图(按残基/峰序)+ 均值线 + 均值+1σ 阈值线
    overlay_spectra.svg  两谱等高线叠加(自由=蓝,扰动=红)+ 匹配峰位移矢量
图为 matplotlib SVG(矢量,Inkscape/Illustrator 可继续编辑调整)。
"""

from __future__ import annotations

import csv
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

_H_TOL = 0.05  # ppm,未指认峰最近邻匹配的 1H 容差
_N_TOL = 0.5  # ppm,15N 容差
_DEFAULT_N_WEIGHT = 0.2  # Δδ = sqrt(ΔH² + (0.2·ΔN)²)


class AnalyzeError(RuntimeError):
    """分析步骤失败(消息面向用户)。"""


def _peaks_path(manager: Any, exp_id: str, data_id: str) -> Path | None:
    try:
        peaks_dir = manager.data_dir(exp_id, data_id, "peaks")
    except Exception:  # noqa: BLE001 - 目录不可用视为无峰表
        return None
    for suffix in (".list", ".csv"):
        cand = peaks_dir / f"{exp_id}-{data_id}{suffix}"
        if cand.is_file():
            return cand
    return None


def _spectrum_path(manager: Any, exp_id: str, data_id: str) -> str:
    entry = manager.data(exp_id, data_id)
    return str(getattr(entry, "spectrum_path", "") or "")


def _load_peaks(path: Path) -> list[dict[str, Any]]:
    """读取选峰得到的峰表(.list 优先,旧 CSV 兼容;0.2.199-补29er)。"""
    from core.peaks.peak_table import load_peaks

    rows = load_peaks(path)
    return [
        r
        for r in rows
        if r.get("H_shift") not in (None, "")
        or r.get("N_shift") not in (None, "")
    ]


def _label(peak: dict[str, Any]) -> str:
    lab = str(peak.get("label", "") or "").strip()
    return "" if lab in ("", "?-?", "?-?-?") else lab


def _assignment_residue(label: str) -> int | None:
    """从 assignment 提取残基号(首个字母段后的数字)。

    Poky 标签如 G1H-G1N / A45N / C16H-K15CB-C16N——取首个连字符段的数字
    作为氨基酸序列位置;无数字返回 None。两份峰表都指认时按残基号(序列)
    匹配,而非位置最近邻(0.2.199-补29es-修3)。
    """
    seg = str(label or "").split("-", 1)[0].strip()
    digits = "".join(ch for ch in seg if ch.isdigit())
    return int(digits) if digits else None


def _match_peaks(
    cur_peaks: list[dict[str, Any]],
    ref_peaks: list[dict[str, Any]],
    *,
    h_tol: float = _H_TOL,
    n_tol: float = _N_TOL,
    sequence_only: bool = False,
) -> list[tuple[int, int]]:
    """返回 [(cur_idx, ref_idx)]:先按 assignment 残基号(氨基酸序列)匹配
    指认峰;sequence_only=True 时只做序列匹配(两份都充分指认时不退化为
    位置最近邻),否则剩余峰最近邻一对一匹配(1H/15N 容差内组合距离最小)。"""
    pairs: list[tuple[int, int]] = []
    used_ref: set[int] = set()
    matched_cur: set[int] = set()
    by_residue: dict[int, list[int]] = {}
    for i, p in enumerate(ref_peaks):
        lab = _label(p)
        r = _assignment_residue(lab) if lab else None
        if r is not None:
            by_residue.setdefault(r, []).append(i)
    for ci, p in enumerate(cur_peaks):
        lab = _label(p)
        r = _assignment_residue(lab) if lab else None
        if r is not None and by_residue.get(r):
            ri = by_residue[r].pop(0)
            pairs.append((ci, ri))
            used_ref.add(ri)
            matched_cur.add(ci)
    if sequence_only:
        return pairs
    for ci, p in enumerate(cur_peaks):
        if ci in matched_cur:
            continue
        hc = float(p.get("H_shift") or np.nan)
        nc = float(p.get("N_shift") or np.nan)
        if not (np.isfinite(hc) and np.isfinite(nc)):
            continue
        best: int | None = None
        best_d = float("inf")
        for ri, q in enumerate(ref_peaks):
            if ri in used_ref:
                continue
            hr = float(q.get("H_shift") or np.nan)
            nr = float(q.get("N_shift") or np.nan)
            if not (np.isfinite(hr) and np.isfinite(nr)):
                continue
            dh = abs(hc - hr)
            dn = abs(nc - nr)
            if dh <= h_tol and dn <= n_tol:
                d = dh / h_tol + dn / n_tol
                if d < best_d:
                    best_d = d
                    best = ri
        if best is not None:
            pairs.append((ci, best))
            used_ref.add(best)
    return pairs


def _residue_key(assignment: str) -> int:
    digits = "".join(ch for ch in assignment if ch.isdigit())
    return int(digits) if digits else 10**9


def _publication_style() -> None:
    """发表级 matplotlib 样式(0.2.199-补29es-修5)。

    svg.fonttype="none" 让 SVG 文字保持文本(可在 Inkscape/Illustrator
    直接编辑),不转成路径。
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["DejaVu Sans", "Arial", "Helvetica"],
            "font.size": 9.5,
            "axes.titlesize": 11,
            "axes.labelsize": 10.5,
            "xtick.labelsize": 8.5,
            "ytick.labelsize": 8.5,
            "legend.fontsize": 8.5,
            "axes.linewidth": 0.8,
            "axes.edgecolor": "#333333",
            "axes.spines.top": False,
            "axes.spines.right": False,
            "svg.fonttype": "none",
        }
    )


def _make_csp_plot(
    rows: list[dict[str, Any]],
    path: Path,
) -> None:
    """Δδ 条形图(发表级,可编辑 SVG):残基号横轴、显著残基着色、
    均值与 mean+1σ 阈值标注。"""
    _publication_style()
    import matplotlib.pyplot as plt

    vals = np.array([float(r["dCSP"]) for r in rows], dtype=float)
    xnums: list[int] = []
    has_assign = False
    for i, r in enumerate(rows):
        lab = str(r.get("Assignment") or "")
        digits = "".join(ch for ch in lab if ch.isdigit())
        if digits:
            xnums.append(int(digits))
            has_assign = True
        else:
            xnums.append(i + 1)
    mean = float(np.mean(vals))
    th = mean + float(np.std(vals))
    n = len(vals)
    fig, ax = plt.subplots(figsize=(max(7.0, 0.22 * n), 4.0))
    colors = ["#c0392b" if v >= th else "#2980b9" for v in vals]
    ax.bar(range(n), vals, color=colors, width=0.8, edgecolor="none")
    ax.axhline(mean, color="#7f8c8d", ls="--", lw=0.9)
    ax.axhline(th, color="#c0392b", ls=":", lw=1.0)
    ax.text(
        n - 0.3, mean, f"  mean = {mean:.3f} ppm",
        va="center", ha="right", fontsize=8, color="#555555",
    )
    ax.text(
        n - 0.3, th, f"  mean+1σ = {th:.3f} ppm",
        va="center", ha="right", fontsize=8, color="#c0392b",
    )
    step = max(1, int(np.ceil(n / 24)))
    ticks = list(range(0, n, step))
    ax.set_xticks(ticks)
    ax.set_xticklabels([xnums[t] for t in ticks], fontsize=8)
    ax.set_xlim(-0.6, n - 0.4)
    ax.set_xlabel("Residue number" if has_assign else "Peak index")
    ax.set_ylabel("Δδ (ppm)")
    ax.set_title("HSQC chemical shift perturbation (CSP)")
    ax.yaxis.grid(True, ls=":", lw=0.5, alpha=0.4)
    ax.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(path, format="svg")
    plt.close(fig)


def _read_spectrum(path: str) -> tuple[np.ndarray, list[np.ndarray]]:
    """读谱实部 + 每数据轴 ppm 数组(与 pick_peaks 同 ORIG/CAR 语义)。"""
    import nmrglue as ng

    dic, data = ng.pipe.read(str(path))
    arr = np.asarray(data)
    if np.iscomplexobj(arr):
        arr = arr.real
    axes: list[np.ndarray] = []
    for i in range(arr.ndim):
        prefix = f"FDF{i + 1}"
        obs = float(dic.get(prefix + "OBS", 0.0) or 0.0)
        sw = float(dic.get(prefix + "SW", 0.0) or 0.0)
        orig = float(dic.get(prefix + "ORIG", 0.0) or 0.0)
        car = float(dic.get(prefix + "CAR", 0.0) or 0.0)
        idx = np.arange(arr.shape[i])
        if obs and orig:
            axes.append(orig / obs + (arr.shape[i] - 1 - idx) * sw / (arr.shape[i] * obs))
        elif obs and sw:
            axes.append(car + (arr.shape[i] / 2 - idx) * sw / (arr.shape[i] * obs))
        else:
            axes.append(idx.astype(float))
    return arr, axes


def _make_overlay(
    cur_path: str,
    ref_path: str,
    cur_pts: list[tuple[float, float]],
    ref_pts: list[tuple[float, float]],
    pairs: list[tuple[int, int]],
    path: Path,
) -> None:
    """两谱等高线叠加(发表级,可编辑 SVG):自由=蓝、扰动=红,
    匹配峰位移矢量。"""
    _publication_style()
    import matplotlib.pyplot as plt

    cur, cur_axes = _read_spectrum(cur_path)
    ref, ref_axes = _read_spectrum(ref_path)
    if cur.ndim != 2 or ref.ndim != 2:
        raise AnalyzeError("叠加图仅支持 2D 谱(HSQC)")
    # 存储轴:(F1=间接=15N, F2=直接=1H);横轴=1H(axis1),纵轴=15N(axis0)
    fig, ax = plt.subplots(figsize=(6.8, 5.4))

    def _draw(
        arr: np.ndarray, axes: list[np.ndarray], color: str, alpha: float
    ) -> None:
        h = axes[1]
        n = axes[0]
        # 降采样控制 SVG 体积(≤480px)
        step = max(1, int(np.ceil(max(arr.shape) / 480)))
        a = arr[::step, ::step]
        gx, gy = np.meshgrid(h[::step], n[::step])
        vmax = float(np.max(a))
        if vmax <= 0:
            return
        pos = a[a > 0]
        lo = float(np.min(pos)) if pos.size else vmax * 0.02
        lo = max(lo, vmax * 0.02)
        levels = np.geomspace(lo, vmax * 0.95, 6)
        ax.contour(
            gx, gy, a, levels=levels, colors=[color],
            linewidths=0.7, alpha=alpha,
        )

    _draw(ref, ref_axes, "#1f4e9c", alpha=0.9)
    _draw(cur, cur_axes, "#b02318", alpha=0.9)
    # 匹配峰位移矢量(自由 → 扰动)
    for ci, ri in pairs:
        if ci < len(cur_pts) and ri < len(ref_pts):
            hc, nc = cur_pts[ci]
            hr, nr = ref_pts[ri]
            ax.annotate(
                "",
                xy=(hc, nc),
                xytext=(hr, nr),
                arrowprops=dict(
                    arrowstyle="-|>",
                    color="#555555",
                    lw=0.7,
                    shrinkA=0,
                    shrinkB=0,
                    mutation_scale=9,
                ),
            )
    if ref_pts:
        ax.plot(
            [p[0] for p in ref_pts], [p[1] for p in ref_pts],
            "x", color="#1f4e9c", ms=5, mew=1.2, label="free",
        )
    if cur_pts:
        ax.plot(
            [p[0] for p in cur_pts], [p[1] for p in cur_pts],
            "o", color="#b02318", ms=4, mfc="none", mew=1.2,
            label="perturbed",
        )
    ax.set_xlabel("1H (ppm)")
    ax.set_ylabel("15N (ppm)")
    ax.set_title("HSQC overlay (free x / perturbed o)")
    ax.invert_xaxis()
    ax.invert_yaxis()
    ax.legend(frameon=False, loc="upper right")
    fig.tight_layout()
    fig.savefig(path, format="svg")
    plt.close(fig)


def _make_symlink(target: Path, link: Path) -> bool:
    """在 link 处建指向 target 的相对软链接(0.2.199-补29es)。

    比对数据与当前数据共享同一份 CSP 产物——文件只放当前数据,
    比对数据对应位置放软链接;失败(如平台无权限)不阻断,返回 False。
    """
    try:
        if link.is_symlink() or link.exists():
            link.unlink()
        link.parent.mkdir(parents=True, exist_ok=True)
        rel = os.path.relpath(target, link.parent)
        link.symlink_to(rel)
        return True
    except OSError:
        return False


def _write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    cols = [
        "Assignment", "H_ref", "N_ref", "H_cur", "N_cur",
        "dH", "dN", "dCSP",
    ]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols)
        writer.writeheader()
        for r in rows:
            writer.writerow({c: r[c] for c in cols})


def analyze(
    manager: Any,
    exp_id: str,
    data_id: str,
    *,
    reference_data_id: str = "",
    csp_n_weight: float = _DEFAULT_N_WEIGHT,
    h_tol: float = _H_TOL,
    n_tol: float = _N_TOL,
    progress: Callable[[str], None] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """HSQC CSP 分析:当前数据(扰动态) vs 比对数据(自由态)。

    返回 {"status","message","analysis_dir","csp_data","csp_plot",
    "overlay_spectra","peak_count","logs"};失败抛 AnalyzeError。
    h_tol/n_tol 为未指认峰最近邻匹配容差(ppm),位移大的体系(如光态转换)
    可放宽。
    """
    logs: list[str] = []

    def _log(msg: str) -> None:
        logs.append(msg)
        if progress is not None:
            progress(msg)

    if not reference_data_id:
        # 未选比对谱:返回 pending 不抛错(自动/批量路径不阻断;GUI 已在
        # 调用前拦截并提示选择)
        return {
            "status": "pending",
            "message": "HSQC CSP 分析需要选择比对谱(自由态 HSQC):"
            "请在分析步骤点「比对谱」选择",
            "logs": logs,
        }
    if reference_data_id == data_id:
        raise AnalyzeError("比对谱不能是当前数据本身")
    cur_spectrum = _spectrum_path(manager, exp_id, data_id)
    ref_spectrum = _spectrum_path(manager, exp_id, reference_data_id)
    cur_peaks_path = _peaks_path(manager, exp_id, data_id)
    ref_peaks_path = _peaks_path(manager, exp_id, reference_data_id)
    if not cur_spectrum or not Path(cur_spectrum).is_file():
        raise AnalyzeError(f"当前数据谱图缺失: {exp_id}/{data_id}")
    if not ref_spectrum or not Path(ref_spectrum).is_file():
        raise AnalyzeError(f"比对数据谱图缺失: {exp_id}/{reference_data_id}")
    if cur_peaks_path is None or ref_peaks_path is None:
        raise AnalyzeError("当前或比对数据缺少峰表(.list),请先选峰")
    _log(f"HSQC CSP: 当前(扰动) {data_id} ↔ 比对(自由) {reference_data_id}")
    cur_peaks = _load_peaks(cur_peaks_path)
    ref_peaks = _load_peaks(ref_peaks_path)
    if not cur_peaks or not ref_peaks:
        raise AnalyzeError("当前或比对峰表为空")
    if any("H_shift" not in p or "N_shift" not in p for p in cur_peaks + ref_peaks):
        raise AnalyzeError("CSP 分析目前仅支持 2D HSQC 峰表(N_shift/H_shift)")
    _log(f"使用选峰峰表: 当前 {cur_peaks_path.name}({len(cur_peaks)} 峰)")
    _log(f"使用选峰峰表: 比对 {ref_peaks_path.name}({len(ref_peaks)} 峰)")
    # 0.2.199-补29es-修3(用户):两份峰表都有 assignment 时按氨基酸序列
    # (残基号)匹配,不做位置最近邻
    cur_assigned = sum(
        1 for p in cur_peaks if _assignment_residue(_label(p)) is not None
    ) / max(len(cur_peaks), 1)
    ref_assigned = sum(
        1 for p in ref_peaks if _assignment_residue(_label(p)) is not None
    ) / max(len(ref_peaks), 1)
    if cur_assigned >= 0.5 and ref_assigned >= 0.5:
        _log(
            f"两份峰表均有 assignment(当前 {cur_assigned:.0%}/比对 "
            f"{ref_assigned:.0%}),按氨基酸序列(残基号)匹配,未指认峰不参与"
        )
        pairs = _match_peaks(cur_peaks, ref_peaks, sequence_only=True)
    else:
        _log(
            f"峰表指认不完整(当前 {cur_assigned:.0%}/比对 {ref_assigned:.0%}),"
            "指认峰按序列匹配,其余按位置最近邻"
        )
        pairs = _match_peaks(cur_peaks, ref_peaks, h_tol=h_tol, n_tol=n_tol)
    if not pairs:
        raise AnalyzeError(
            "未找到匹配峰(两份都有 assignment 时按残基号序列匹配;"
            "否则按 1H±0.05/15N±0.5 ppm 最近邻)"
        )
    _log(f"匹配峰: {len(pairs)} 个")
    rows: list[dict[str, Any]] = []
    for ci, ri in pairs:
        cp = cur_peaks[ci]
        rp = ref_peaks[ri]
        hc = float(cp["H_shift"])
        nc = float(cp["N_shift"])
        hr = float(rp["H_shift"])
        nr = float(rp["N_shift"])
        dh = hc - hr
        dn = nc - nr
        d = float(np.sqrt(dh * dh + (csp_n_weight * dn) ** 2))
        assignment = _label(cp) or _label(rp) or ""
        rows.append(
            {
                "Assignment": assignment,
                "H_ref": round(hr, 4),
                "N_ref": round(nr, 4),
                "H_cur": round(hc, 4),
                "N_cur": round(nc, 4),
                "dH": round(dh, 4),
                "dN": round(dn, 4),
                "dCSP": round(d, 4),
            }
        )
    rows.sort(key=lambda r: (_residue_key(r["Assignment"]), r["dCSP"]))
    out_dir = manager.dir_path("analysis") / exp_id / data_id
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "csp_data.csv"
    # 0.2.199-补29es:图片输出到当前数据的 figures/ 目录
    figures_dir = manager.data_dir(exp_id, data_id, "figures")
    figures_dir.mkdir(parents=True, exist_ok=True)
    plot_path = figures_dir / "csp_plot.svg"
    overlay_path = figures_dir / "overlay_spectra.svg"
    _write_csv(rows, csv_path)
    _log("CSP 数据文件已写: csp_data.csv")
    _make_csp_plot(rows, plot_path)
    _log("CSP 图已生成: csp_plot.svg")
    cur_pts = [(float(p["H_shift"]), float(p["N_shift"])) for p in cur_peaks]
    ref_pts = [(float(p["H_shift"]), float(p["N_shift"])) for p in ref_peaks]
    _make_overlay(cur_spectrum, ref_spectrum, cur_pts, ref_pts, pairs, overlay_path)
    _log("叠加图已生成: overlay_spectra.svg")
    # 0.2.199-补29es:比对数据对应位置放软链接,表示两份数据共享 CSP 产物
    ref_figures = manager.data_dir(exp_id, reference_data_id, "figures")
    ref_out_dir = manager.dir_path("analysis") / exp_id / reference_data_id
    symlink_ok = 0
    for _target, _link in (
        (csv_path, ref_out_dir / "csp_data.csv"),
        (plot_path, ref_figures / "csp_plot.svg"),
        (overlay_path, ref_figures / "overlay_spectra.svg"),
    ):
        if _make_symlink(_target, _link):
            symlink_ok += 1
    if symlink_ok:
        _log(f"比对数据 {reference_data_id} 对应位置已建 {symlink_ok} 个软链接(共享 CSP 产物)")
    else:
        _log("比对数据软链接创建失败(平台无权限等),仅当前数据持有产物")
    run = manager.start_run(
        exp_id,
        workflow_ref="analyze",
        inputs={
            "data_id": data_id,
            "spectrum_path": cur_spectrum,
            "reference_spectrum_path": ref_spectrum,
            "reference_data_id": reference_data_id,
        },
        params={"csp_n_weight": csp_n_weight, "peak_count": len(rows)},
    )
    manager.finish_run(
        run.run_id,
        "success",
        outputs={
            "analysis_dir": str(out_dir),
            "csp_data": str(csv_path),
            "csp_plot": str(plot_path),
            "overlay_spectra": str(overlay_path),
        },
    )
    return {
        "status": "success",
        "message": f"HSQC CSP 分析完成: {len(rows)} 个匹配峰",
        "analysis_dir": str(out_dir),
        "csp_data": str(csv_path),
        "csp_plot": str(plot_path),
        "overlay_spectra": str(overlay_path),
        "peak_count": len(rows),
        "logs": logs,
    }


__all__ = ["AnalyzeError", "analyze"]
