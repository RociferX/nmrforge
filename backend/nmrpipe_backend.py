"""NMRPipe 后端：bruker -AUTO 转换 + NMRPipe 处理管道 + NUS SMILE 重构（Linux/csh）。

NMRPipe 语义只存在于本层（backend/）与生成的脚本；上层通过 ProcessingBackend 协议调用。
查找路径：csh 环境 ``source ~/.cshrc; which nmrPipe`` 优先（用户要求），可显式指定 bin 目录。

重要设计（真实数据验证，0.2.199-补16 单文件化）：
- 四种途径（自动/人工 × 单数据集/分段）的 fid.com 一律生成单文件
  {dataset_id}.fid（bruker -AUTO 按 NusTD 网格 + -aq2D Complex 输出，与实验室
  fid.com 一致）；不再修正 acqu3s TD（TD=1 时 bruker 同样输出单文件全网格 fid，
  已实测 sampleK 验证）；3D NUS 的切片在 SMILE 脚本 step1 直接维处理后产生
  （nus3d_1/test%04d.ft1）；
- 多段实验（同实验拆多个数据集，如 61/63/65/67）：参考实验室脚本
  （1stfid.com + 2ndAdd.com）——每段 bruker 转换输出单文件，addNMR 逐对
  时域合并为 merged/{dataset_id}.fid，再统一 SMILE 重构；支持每段可选频移
  （-rs Hz，防场飘）。
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from backend.base import BackendCapabilities
from backend.bruker_workflow import (
    apply_fid_com_overrides,
    patch_fid_com,
    patch_nus_expand_count,
)
from backend.config import (
    resolve_ext_hi,
    resolve_ext_lo,
    resolve_nthread,
    resolve_points_per_line,
)
from backend.memory_disk import INTERMEDIATE_SUBDIR
from backend.nmrpipe_finder import find_nmrpipe_bin, find_tool
from backend.runtime import CshRuntime, cancel_requested
from backend.script_generator import (
    DEFAULT_POINTS_PER_LINE,
    _as_bool,
    effective_td,
    expand_baseline,
    generate_2d_nus_script,
    generate_3d_nus_script,
    generate_convert_script,
    generate_nus_finalize_script,
    generate_preview_script,
    generate_process_script,
    select_smile_params,
    zero_fill_plan,
    zero_fill_report,
)
from core.data.internal_data_model import Experiment, SamplingMode
from core.data.nus_reader import read_nuslist
from core.optimization.phase_search import (
    direct_ft_traces,
    dominant_absorption_ratio,
    orient_dominant_positive,
    search_direct_phase_on_spectrum,
    search_direct_spectrum_phase,
)
from core.planning.processing_plan import ProcessingPlan


def _slice_candidates(directory: Path, dataset_id: str) -> list[Path]:
    """目录内切片 fid 候选:新命名 {dataset_id}*.fid 优先,兼容旧 test*.fid。"""
    if not directory.is_dir():
        return []
    new_style = sorted(directory.glob(f"{dataset_id}*.fid"))
    legacy = sorted(directory.glob("test*.fid"))
    seen = {p.name for p in new_style}
    return new_style + [p for p in legacy if p.name not in seen]


def _slice_in_file(directory: Path, dataset_id: str) -> str | None:
    """目录内切片流的 in_file 模式(fid/test%03d.fid 或 fid/{dataset_id}%03d.fid)。"""
    slices = _slice_candidates(directory, dataset_id)
    if not slices:
        return None
    new_style = any(p.name.startswith(dataset_id) for p in slices)
    return f"fid/{dataset_id}%03d.fid" if new_style else "fid/test%03d.fid"


def _nus_grid_bounds(experiment: Experiment) -> list[int] | None:
    """NUS nuslist 索引上限。

    2D:nuslist 单列 = F1 复点索引,上限 = NUS 网格 td[1](如 nus20_25 索引到
    126、网格 128,不能按 NusTD//2 判);
    3D:nuslist 列为复点索引,上限 = NusTD//2(cc F2 索引到 84、NusTD 170)。
    """
    td = effective_td(experiment)
    if experiment.ndim == 2 and len(td) > 1:
        return [int(td[1])]
    if experiment.ndim >= 3 and len(td) > 2:
        return [int(td[1]) // 2, int(td[2]) // 2]
    return None


def _validate_nus_points(
    points: list[tuple[int, ...]],
    experiment: Experiment,
) -> tuple[list[tuple[int, ...]], list[tuple[int, ...]], dict[tuple[int, ...], list[str]]]:
    """NUS 采样点坏点校验:越界点 + 重复点。返回 (有效点, 坏点, 坏点原因)。"""
    from collections import Counter

    counts = Counter(points)
    bounds = _nus_grid_bounds(experiment)
    valid: list[tuple[int, ...]] = []
    bad: list[tuple[int, ...]] = []
    reasons: dict[tuple[int, ...], list[str]] = {}
    seen: set[tuple[int, ...]] = set()
    for raw_point in points:
        point = tuple(raw_point)
        oob = bounds is not None and any(
            point[i] >= bounds[i] for i in range(min(len(point), len(bounds)))
        )
        dup = counts[point] > 1
        if oob or dup:
            if point not in bad:
                bad.append(point)
                r: list[str] = []
                if oob:
                    r.append(f"越界(网格 {bounds})")
                if dup:
                    r.append(f"重复({counts[point]})")
                reasons[point] = r
            continue
        if point in seen:
            continue
        seen.add(point)
        valid.append(point)
    return valid, bad, reasons


def _ser_point_layout(
    experiment: Experiment, data_size: int, n_rows: int
) -> tuple[int, int, int] | None:
    """按采样参数推导 ser 布局,返回 (每点字节块, 每向量字节, 冗余数)。

    ser 字节随采样参数变化(0.2.195):每向量 = serPadSize 补齐后的直接维
    复点数 × 2 × 字长(nusExpand:字长 8 → 128 对齐、字长 4 → 256 对齐);
    每采样点含冗余向量数(NS 重复,-avg 平均)= ser 大小/点数/每向量字节,
    要求整除。无法确定(参数缺失/不整除)返回 None——调用方回退生成 FID
    清理,不做可能错位的整块删除。
    """
    td = effective_td(experiment)
    if not td:
        return None
    direct_td = int(td[0])
    per_point = data_size // n_rows if n_rows else 0
    for word_bytes, ser_pad in ((8, 128), (4, 256)):
        padded = ((direct_td + ser_pad - 1) // ser_pad) * ser_pad
        vec_bytes = (padded // 2) * 2 * word_bytes
        if vec_bytes <= 0 or per_point % vec_bytes != 0:
            continue
        redundancy = per_point // vec_bytes
        if redundancy >= 1 and per_point * n_rows == data_size:
            return per_point, vec_bytes, redundancy
    return None


def _nus_grid_from_points(
    points: list[tuple[int, ...]],
) -> list[int] | None:
    """坏点移除后按 nuslist 实际采样范围推导网格(每维 max+1)。

    2D 单列 → [f1_grid];3D 两列 → [f2_grid, f1_grid]。
    """
    if not points:
        return None
    n_cols = len(points[0])
    if n_cols == 1:
        return [max(p[0] for p in points) + 1]
    if n_cols == 2:
        return [max(p[0] for p in points) + 1, max(p[1] for p in points) + 1]
    return None


def _apply_nus_grid_after_clean(
    experiment: Experiment, points: list[tuple[int, ...]]
) -> list[str]:
    """坏点移除后按实际采样范围更新 NusTD(0.2.197)。

    此前交叉验证(参数修正)用静态 NusTD 把坏点清理后调整的网格改回,
    导致 fid.com 网格与清理后数据不一致;这里把 acqu2s/acqu3s 的 NusTD
    缩到实际范围(只缩小),使 _effective_td、fid.com 参数修正、nusExpand
    网格、重构全部一致。返回日志行。
    """
    grid = _nus_grid_from_points(points)
    if not grid:
        return []
    logs: list[str] = []
    params = experiment.acquisition_parameters
    if len(grid) == 1:
        block = params.setdefault("acqu2s", {})
        old = int(block.get("NusTD", 0) or 0)
        new = grid[0]
        if old and 0 < new < old:
            block["NusTD"] = new
            logs.append(f"采样坏点移除后网格调整: acqu2s NusTD {old}→{new}")
    elif len(grid) == 2:
        for key, g in (("acqu2s", grid[0]), ("acqu3s", grid[1])):
            block = params.setdefault(key, {})
            old = int(block.get("NusTD", 0) or 0)
            new = 2 * g
            if old and 0 < new < old:
                block["NusTD"] = new
                logs.append(f"采样坏点移除后网格调整: {key} NusTD {old}→{new}")
    return logs


def zf_summary(plan: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """填零计划摘要(WorkflowRun params 用):{轴: {"mode", "size"}}。"""
    return {
        axis: {"mode": str(cfg.get("mode", "auto")), "size": cfg.get("size")}
        for axis, cfg in plan.items()
    }

def _effective_params_base(
    extract: bool,
    ext_lo: str,
    ext_hi: str,
    zf_plan: dict[str, Any],
    baseline: dict[str, Any] | None,
    linewidth_hz: dict[str, float] | None,
    points_per_line: float,
    sampling: dict[str, Any] | None,
) -> dict[str, Any]:
    """process/reconstruct_nus 共用的 effective_params 基础键(0.2.46)。"""
    return {
        "extract": extract,
        "ext_lo": ext_lo,
        "ext_hi": ext_hi,
        "zero_fill": zf_summary(zf_plan),
        "baseline": baseline,
        "linewidth_hz": linewidth_hz,
        "points_per_line": points_per_line,
        "sampling": dict(sampling),
    }


def _segment_kind_info(segments: list[Path | str]) -> tuple[str | None, list[str]]:
    """识别多段容器类型并生成说明日志(0.2.199-补29cv)。

    - repeat_uniform / repeat_nus:重复实验叠加——各段 FID 按 TopSpin
      fidadd 语义时域逐点相加(co-addition,addNMR 默认不归一化),用于
      提高信噪比;NUS 各段采样点相同,同网格叠加后单次重构。
    - segmented_nus:NUS 分段——各段采样点互补,合并 nuslist 补全网格
      后单次重构。
    分类失败返回 (None, [警告]),不阻断 FID 生成(调用方仍按分段合并)。
    """
    from core.data.bruker_reader import classify_segment_kind

    try:
        kind = classify_segment_kind(list(segments))
    except Exception as exc:  # noqa: BLE001 - 分类失败不阻断转换
        return None, [f"⚠ 多段类型识别失败({exc}),按分段合并处理"]
    label = {
        "repeat_uniform": "重复实验叠加(uniform 同参数)",
        "repeat_nus": "重复实验叠加(NUS 同采样点)",
        "segmented_nus": "分段(NUS 互补采样点)",
    }.get(kind, kind)
    if kind == "repeat_uniform":
        detail = (
            f"识别为{label}:{len(segments)} 段 FID 按 TopSpin fidadd 语义"
            "时域逐点相加(co-addition,addNMR 不归一化),提高信噪比"
        )
    elif kind == "repeat_nus":
        detail = (
            f"识别为{label}:各段采样点相同,同网格叠加后单次重构"
            "(TopSpin fidadd 语义,addNMR 不归一化)"
        )
    elif kind == "segmented_nus":
        detail = (
            f"识别为{label}:各段采样点互补,合并 nuslist 补全网格后单次重构"
        )
    else:
        detail = f"多段类型: {label}"
    return kind, [detail]


@dataclass


class NMRPipeBackend:
    """NMRPipe 实现（Linux：bruker -AUTO + fid.com + NMRPipe 管道 + SMILE + 多段合并）。"""

    nmrpipe_bin: str = ""
    work_dir: str = ""
    capabilities: BackendCapabilities = field(
        default_factory=lambda: BackendCapabilities(provider="nmrpipe")
    )

    def _bin_dir(self) -> Path | None:
        return find_nmrpipe_bin(self.nmrpipe_bin)

    def _work_path(self, experiment: Experiment) -> Path:
        raw = Path(experiment.source_path)
        if self.work_dir:
            return Path(self.work_dir)
        return raw.parent / f"{experiment.dataset_id}.nmrpipe"

    def health_check(self) -> dict[str, Any]:
        bin_dir = self._bin_dir()
        if bin_dir is None:
            return {
                "ok": False,
                "nmrpipe": None,
                "bruker": None,
                "message": "未找到 nmrPipe（csh: source ~/.cshrc; which nmrPipe）",
            }
        bruker = find_tool("bruker", bin_dir)
        return {
            "ok": True,
            "nmrpipe": str(bin_dir / "nmrPipe"),
            "bruker": str(bruker) if bruker else "",
            "message": f"找到 nmrPipe: {bin_dir / 'nmrPipe'}",
        }

    def process(
        self,
        experiment: Experiment,
        plan: ProcessingPlan,
        *,
        params: dict[str, Any] | None = None,
        direct_phase_search: bool = True,
        direct_phase_override: dict[str, tuple[float, float]] | None = None,
        progress: Callable[[str], None] | None = None,
        out_file: str | None = None,
        script_name: str | None = None,
    ) -> dict[str, Any]:
        """均匀采样：转换（含多段合并）+ NMRPipe 处理管道（NUS 请用 reconstruct_nus）。

        direct_phase_override 非空时跳过相位搜索,直接以给定相位写 PS
        (参考/选中相位写回生产用;统一内存搜索见 workflow.memory_phase_search)。
        """
        if experiment.sampling.mode is SamplingMode.NUS:
            return {
                "success": False,
                "message": "NUS 数据请调用 reconstruct_nus（SMILE）",
                "logs": [],
            }
        bin_dir = self._bin_dir()
        if bin_dir is None:
            return {
                "success": False,
                "message": "未找到 nmrPipe（csh: which nmrPipe）",
                "logs": [],
            }
        runtime = CshRuntime()
        raw = Path(experiment.source_path)
        work = self._work_path(experiment)
        work.mkdir(parents=True, exist_ok=True)
        logs: list[str] = []

        def _progress(msg: str) -> None:
            if progress is not None:
                progress(msg)

        converted = True
        # 0.2.165:只有真正执行转换才发「开始转换 fid」;复用已转换产物时
        # 发「复用已转换 fid」,避免 preview/joint/候选等多次 process 调用
        # 反复显示误导性的转换进度
        if experiment.segments:
            merged_in = self._merged_fid_in(
                work, experiment.dataset_id
            )
            merged_ready = merged_in is not None
            in_file = merged_in or f"merged/{experiment.dataset_id}.fid"
            if merged_ready and not (params or {}).get("segment_shift_hz"):
                logs.append("复用已转换 fid(跳过转换)")
                _progress("复用已转换 fid(跳过转换)")
            else:
                _progress("开始转换 fid")
                # 0.2.166:分段频移与 NUS 对齐(有 segment_shift_hz 必须重转)
                shifts = [
                    float(v)
                    for v in (params or {}).get("segment_shift_hz", [])
                ]
                converted, convert_logs = self._convert_segments(
                    runtime, experiment, work, shifts
                )
                logs += convert_logs
        else:
            in_file = f"{experiment.dataset_id}.fid"
            if (work / in_file).is_file():
                logs.append("复用已转换 fid(跳过转换)")
                _progress("复用已转换 fid(跳过转换)")
            else:
                _progress("开始转换 fid")
                converted, convert_logs = self._convert(runtime, experiment, raw, work)
                logs += convert_logs
                # 0.2.81:bruker 切片式输出(fid/test%03d.fid,三维 TD 正确时),
                # process 同样走切片流(与 NUS 一致)
                if not (work / in_file).is_file():
                    slice_dir = work / "fid"
                    slice_in = _slice_in_file(slice_dir, experiment.dataset_id)
                    if slice_in:
                        in_file = slice_in
                        logs.append(f"使用 bruker 切片式 fid（{slice_in},流式处理）")
        if not converted:
            return {"success": False, "message": "Bruker→NMRPipe 转换失败", "logs": logs}
        proc_params = dict(params or {})
        sampling = proc_params.get("sampling") or {}
        # sampling.auto_phase=False → 关闭直接维自动相位(PS 保持 plan 默认 0/0)
        # 0.2.88:检查移到搜索前(此前在搜索之后才置位,实际关不掉自动相位)
        # 0.2.167:仅 route=none 逃生口直连时有效;unified 自动路径按实验
        # 类型 presets processing_hints.auto_phase 决定(见 phase_routes)
        if sampling.get("auto_phase") is False:
            direct_phase_search = False
        direct_phase: dict[str, tuple[float, float]] | None = None
        # 0.2.106:逐维复型预览模式——仅 preview_axis 的 PS 不加 -di
        # (其它轴按 direct_phase_override 固定相位加 -di),零填零;
        # 预览数据保持 PS(0,0),关闭直接维相位搜索
        preview_axis = proc_params.get("preview_axis")
        if preview_axis:
            direct_phase_search = False
        # 0.2.160:首遍复型预览不加 POLY -time(避免带偏直接维相位搜索);
        # POLY -time 只进终跑完整脚本(params_final 的 direct_poly_time)
        direct_poly_time = _as_bool(proc_params.get("direct_poly_time", False))
        if preview_axis:
            direct_poly_time = False
        if direct_phase_override:
            direct_phase = dict(direct_phase_override)
            logs.append(f"直接维相位覆盖: {direct_phase}")
        elif direct_phase_search:
            _progress(
                "开始相位优化(1D)" if experiment.ndim == 1
                else "开始相位优化(直接维)"
            )
            phase_inputs: Path | list[Path]
            if experiment.segments:
                phase_inputs = work / "seg_001" / f"{experiment.dataset_id}.fid"
            else:
                slice_dir = work / "fid"
                slices = (
                    sorted(slice_dir.glob("test*.fid"))
                    if slice_dir.is_dir()
                    else []
                )
                if slices:
                    phase_inputs = slices
                    logs.append(
                        f"切片式 fid:直接维相位搜索用 {len(slices)} 个切片"
                    )
                else:
                    phase_inputs = work / f"{experiment.dataset_id}.fid"
            if isinstance(phase_inputs, Path) and not phase_inputs.is_file():
                logs.append("直接维相位搜索:未找到 fid/切片,保持 p0=p1=0")
            else:
                p0, p1 = self._search_direct_phase(
                    work,
                    phase_inputs,
                    logs,
                    is_nus=False,
                    is_1d=(experiment.ndim == 1),
                )
                direct_axis = "F2" if experiment.ndim <= 2 else "F3"
                direct_phase = {direct_axis: (p0, p1)}
                if experiment.ndim == 1:
                    _progress(f"完成 1D 相位优化: p0={p0:g}° p1={p1:g}°")
                else:
                    _progress(
                        f"完成相位优化(直接维 {direct_axis} "
                        f"p0={p0:g}° p1={p1:g}°)"
                    )
        extract = _as_bool(proc_params.get("extract", experiment.ndim > 1))
        ext_lo = resolve_ext_lo(proc_params.get("ext_lo"))
        ext_hi = resolve_ext_hi(proc_params.get("ext_hi"))
        baseline = expand_baseline(experiment, proc_params.get("baseline"))
        window = proc_params.get("window")
        zero_fill = proc_params.get("zero_fill")
        linewidth_hz = proc_params.get("linewidth_hz")
        points_per_line = resolve_points_per_line(
            proc_params.get("points_per_line")
        )
        zf_plan = zero_fill_plan(
            experiment,
            zero_fill,
            linewidth_hz=linewidth_hz,
            points_per_line=points_per_line,
        )
        logs += zero_fill_report(zf_plan)
        processed, process_logs, spectrum = self._process(
            runtime,
            experiment,
            plan,
            work,
            in_file=in_file,
            direct_phase=direct_phase,
            baseline=baseline,
            window=window,
            zero_fill=zf_plan,
            linewidth_hz=linewidth_hz,
            points_per_line=points_per_line,
            extract=extract,
            ext_lo=ext_lo,
            ext_hi=ext_hi,
            sampling=sampling,
            progress=progress,
            out_file=out_file,
            script_name=script_name,
            keep_direct_complex=_as_bool(proc_params.get("keep_direct_complex", False)),
            keep_complex_all=_as_bool(proc_params.get("keep_complex_all", False)),
            preview_axis=preview_axis,
            direct_poly_time=direct_poly_time,
        )
        logs += process_logs
        if not processed:
            return {"success": False, "message": "NMRPipe 处理失败", "logs": logs}
        _progress("终谱已就位")
        return {
            "success": True,
            "message": "NMRPipe 处理成功",
            "spectrum_path": str(spectrum),
            "logs": logs,
            "effective_params": {
                **_effective_params_base(
                    extract,
                    ext_lo,
                    ext_hi,
                    zf_plan,
                    baseline,
                    linewidth_hz,
                    points_per_line,
                    sampling,
                ),
                "window": window,
                "direct_phase": direct_phase,
                "direct_poly_time": direct_poly_time,
            },
        }

    def convert_to_fid(
        self,
        experiment: Experiment,
        data_dir: Path | str,
        progress: Callable[[str], None] | None = None,
        fid_com_overrides: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """独立阶段:bruker -AUTO/fid.com 把原始数据转换为 NMRPipe fid(不生成谱)。

        fid_com_overrides:人工途径参数覆盖(0.2.163-补13),分段数据逐段应用,
        转换/切片/合并/坏点清理仍按自动路径执行。
        返回稳定键 {success, fid_path, message, logs}(API_CONTRACT §8.3);
        供步骤化流程「生成 FID」调用,process/reconstruct_nus 会复用其结果。
        """
        bin_dir = self._bin_dir()
        if bin_dir is None:
            return {
                "success": False,
                "message": "未找到 nmrPipe（csh: which nmrPipe）",
                "logs": [],
            }
        runtime = CshRuntime()
        raw = Path(data_dir)
        work = self._work_path(experiment)
        work.mkdir(parents=True, exist_ok=True)
        logs: list[str] = []

        # 0.2.199-补29dz(用户):缺关键输入文件直接提示,避免 bruker 挂起/卡住
        def _missing_input(seg_dir: Path) -> str:
            if not (seg_dir / "acqus").is_file():
                return f"{seg_dir}/acqus"
            has_ser = (seg_dir / "ser").is_file()
            # 0.2.199-补29gj-修:1D Bruker 数据文件是 fid(文件),不是 ser/或 fid 目录
            has_fid_file = (seg_dir / "fid").is_file()
            has_fid_dir = (
                any((seg_dir / "fid").glob("*.fid"))
                if (seg_dir / "fid").is_dir()
                else False
            )
            if not has_ser and not has_fid_file and not has_fid_dir:
                return f"{seg_dir}/fid" if experiment.ndim == 1 else f"{seg_dir}/ser"
            return ""

        if experiment.segments:
            for _seg in [Path(s) for s in experiment.segments]:
                _miss = _missing_input(_seg)
                if _miss:
                    return {
                        "success": False,
                        "message": f"缺少输入文件: {_miss}",
                        "logs": logs,
                    }
        else:
            _miss = _missing_input(raw)
            if _miss:
                return {
                    "success": False,
                    "message": f"缺少输入文件: {_miss}",
                    "logs": logs,
                }
        segment_kind: str | None = None
        if progress is not None:
            progress("开始转换 fid")
        if experiment.segments:
            # 0.2.199-补29cv:识别重复实验叠加/分段,日志注明 TopSpin
            # fidadd 语义的时域相加;分类失败不阻断转换
            segment_kind, kind_logs = _segment_kind_info(experiment.segments)
            logs += kind_logs
            # 0.2.124:坏点在源头 ser/nuslist 删除并备份(用户要求)
            _count, _bad, source_removed = self._clean_source_nus(
                experiment, [Path(s) for s in experiment.segments], logs
            )
            if _bad and source_removed:
                # 0.2.197/0.2.199:分段也按清理后合并 nuslist 实际范围调整
                # 网格,交叉验证(参数修正)不再把各段网格改回静态 NusTD
                merged_points: list[tuple[int, ...]] = []
                for seg in experiment.segments:
                    merged_points += [
                        tuple(p) for p in read_nuslist(Path(seg) / "nuslist")
                    ]
                logs += _apply_nus_grid_after_clean(experiment, merged_points)
            converted, convert_logs = self._convert_segments(
                runtime, experiment, work, [], fid_com_overrides=fid_com_overrides
            )
            logs += convert_logs
            merged_in = self._merged_fid_in(
                work, experiment.dataset_id
            )
            if merged_in and merged_in.endswith(".fid"):
                fid_path = work / merged_in
            elif merged_in:
                fid_path = work / "merged" / "fid"
            else:
                fid_path = work / "merged"
            if converted:
                _count, bad_points = self._write_merged_nuslist(
                    work, experiment.segments, experiment, logs
                )
                if bad_points:
                    # 源头删除不可行时回退到生成 FID 清零
                    self._zero_bad_point_fid(
                        work, bad_points, logs, dataset_id=experiment.dataset_id
                    )
        else:
            converted, convert_logs = self._convert(
                runtime, experiment, raw, work, fid_com_overrides=fid_com_overrides
            )
            logs += convert_logs
            fid_path = self._converted_fid_path(work, experiment.dataset_id)
        if not converted:
            return {
                "success": False,
                "message": "Bruker→NMRPipe 转换失败",
                "logs": logs,
            }
        logs.append(f"fid → {fid_path}")
        return {
            "success": True,
            "fid_path": str(fid_path),
            "message": "转换完成",
            "logs": logs,
            "effective_params": {
                "dataset_id": experiment.dataset_id,
                "ndim": experiment.ndim,
                "segments": len(experiment.segments),
                "segment_kind": segment_kind,
                "work_dir": str(work),
                "fid_path": str(fid_path),
            },
        }

    def reconstruct_nus(
        self,
        experiment: Experiment,
        params: dict[str, Any] | None = None,
        progress: Callable[[str], None] | None = None,
        script_only: bool = False,
    ) -> dict[str, Any]:
        """NUS 数据：bruker 原生转换（单段/多段合并）+ SMILE 重构输出终谱。

        script_only=True 时只生成脚本并返回文本（0.2.199-补29hz-修3:SMILE
        参数扫描需要先拿到脚本文本再决定怎么跑），不执行 NMRPipe。
        """
        params = dict(params or {})
        if experiment.sampling.mode is not SamplingMode.NUS:
            return {"success": False, "message": "非 NUS 数据，请使用 process()", "logs": []}
        bin_dir = self._bin_dir()
        if bin_dir is None:
            return {
                "success": False,
                "message": "未找到 nmrPipe（csh: which nmrPipe）",
                "logs": [],
            }
        runtime = CshRuntime()
        raw = Path(experiment.source_path)
        work = self._work_path(experiment)
        work.mkdir(parents=True, exist_ok=True)
        logs: list[str] = []

        if experiment.segments:
            # 0.2.124:坏点在源头 ser/nuslist 删除并备份(用户要求)
            _count, _bad, source_removed = self._clean_source_nus(
                experiment, [Path(s) for s in experiment.segments], logs
            )
            if _bad and source_removed:
                # 0.2.197:坏点移除后按清理后 nuslist 实际范围调整 NusTD,
                # 交叉验证(参数修正)使用调整后的值,不再改回
                merged_points: list[tuple[int, ...]] = []
                for seg in experiment.segments:
                    merged_points += [
                        tuple(p) for p in read_nuslist(Path(seg) / "nuslist")
                    ]
                logs += _apply_nus_grid_after_clean(experiment, merged_points)
            merged_in = self._merged_fid_in(
                work, experiment.dataset_id
            )
            merged_ready = (
                merged_in is not None
                and (work / "nuslist").is_file()
                and not params.get("segment_shift_hz")  # 有频移必须重转
            )
            if source_removed:
                # 源头已变:旧合并产物失效,强制重转
                if merged_in is not None:
                    merged_path = work / merged_in.replace("%03d", "000")
                    if merged_path.is_file():
                        merged_path.unlink()
                merged_ready = False
            if not merged_ready:
                # 0.2.199-补29cv:本路径自行转换/合并时同样注明多段类型
                _kind, kind_logs = _segment_kind_info(experiment.segments)
                logs += kind_logs
                shifts = [float(v) for v in params.get("segment_shift_hz", [])]
                converted, convert_logs = self._convert_segments(
                    runtime, experiment, work, shifts
                )
                logs += convert_logs
                if not converted:
                    return {
                        "success": False,
                        "message": "多段 NUS 转换/合并失败",
                        "logs": logs,
                    }
                nuslist_count, bad_points = self._write_merged_nuslist(
                    work, experiment.segments, experiment, logs
                )
                if bad_points:
                    # 源头删除不可行时回退到生成 FID 清零
                    self._zero_bad_point_fid(
                        work, bad_points, logs, dataset_id=experiment.dataset_id
                    )
            else:
                logs.append(f"复用已合并 fid（{merged_in},跳过转换/合并）")
                nuslist_count = len(
                    (work / "nuslist").read_text(encoding="utf-8").splitlines()
                )
            in_file = self._merged_fid_in(
                work, experiment.dataset_id
            ) or f"merged/{experiment.dataset_id}.fid"
        else:
            # 0.2.124:坏点在源头 ser/nuslist 删除并备份(用户要求),转换前执行
            nuslist_count, bad_points, source_removed = self._clean_source_nus(
                experiment, [raw], logs
            )
            if bad_points and source_removed:
                # 0.2.197:坏点移除后按清理后 nuslist 实际范围调整 NusTD,
                # 交叉验证(参数修正)使用调整后的值,不再改回
                cleaned = [tuple(p) for p in read_nuslist(raw / "nuslist")]
                logs += _apply_nus_grid_after_clean(experiment, cleaned)
            fid_file = work / f"{experiment.dataset_id}.fid"
            if source_removed:
                # 源头已变:旧的已转换 fid 失效,强制重转
                if fid_file.is_file():
                    fid_file.unlink()
                stale_slice = work / "fid"
                if stale_slice.is_dir():
                    shutil.rmtree(stale_slice)
            if not fid_file.is_file():
                converted, convert_logs = self._convert(runtime, experiment, raw, work)
                logs += convert_logs
                if not converted:
                    return {
                        "success": False,
                        "message": "NUS 转换失败（bruker 原生识别失败）",
                        "logs": logs,
                    }
            raw_nuslist = raw / "nuslist"
            if not raw_nuslist.is_file():
                return {"success": False, "message": "缺少 nuslist 采样表", "logs": logs}
            shutil.copy2(raw_nuslist, work / "nuslist")
            # 安全网:工作 nuslist 再校验(源头已清理时应为 0 坏点)
            nuslist_count, _leftover = self._clean_work_nuslist(work, experiment, logs)
            # 0.2.199-补27:兼容 bruker 自动输出——单文件优先,找不到单文件
            # 回退切片流(fid/test%03d.fid 或 fid/{dataset_id}%03d.fid);
            # nus 脚本 xyz2pipe 两种输入都支持
            slice_dir = work / "fid"
            slice_in = _slice_in_file(slice_dir, experiment.dataset_id)
            if fid_file.is_file():
                in_file = fid_file.name
            elif slice_in:
                in_file = slice_in
                logs.append(f"使用 bruker 切片式 fid（{slice_in},流式处理）")
            else:
                in_file = fid_file.name  # 兜底:转换应已产出其一
                logs.append("fid 未找到(单文件/切片均无),终跑将失败")
            if bad_points and not source_removed:
                # 源头删除不可行(ser 缺失/大小不符)时回退到生成 FID 清零
                self._zero_bad_point_fid(
                    work, bad_points, logs, in_file=in_file,
                    dataset_id=experiment.dataset_id,
                )

        direct_p0, direct_p1 = 0.0, 0.0
        override = params.get("direct_phase_override")
        if override is not None:
            direct_p0, direct_p1 = float(override[0]), float(override[1])
            logs.append(f"直接维相位覆盖: p0={direct_p0:g} p1={direct_p1:g}")
        sampling = params.get("sampling") or {}
        direct_phase_search = bool(params.get("direct_phase_search", True))
        # 0.2.95:显示层相位搜索(nmrDraw 思路,默认开启)——正式重构终谱上
        # 频域旋转对称性评分;开启时跳过 NU-DFT/轻量(它们不可靠/实验性)
        display_phase_search = bool(params.get("display_phase_search", True))
        light_phase = bool(params.get("light_phase_search", False))
        if sampling.get("auto_phase") is False:
            direct_phase_search = False
        # 0.2.94:轻量 SMILE 相位搜索(实验性,默认关闭——VM 实测重构伪影会
        # 把固定迹线评分最优值带偏:16/32 点子采样 → F2 偏 55°)
        if (
            direct_phase_search
            and not display_phase_search
            and params.get("direct_phase_override") is None
            and light_phase
        ):
            light_result = self._light_phase_search(
                experiment,
                work,
                in_file,
                runtime,
                logs,
                params,
                nuslist_count=int(params.get("nuslist_count") or nuslist_count),
                sampling=sampling,
            )
            if light_result is not None:
                direct_p0, direct_p1 = light_result
                direct_phase_search = False
            else:
                logs.append("轻量 SMILE 相位搜索失败,回退 NU-DFT")
        if (
            direct_phase_search
            and not display_phase_search
            and not light_phase
            and params.get("direct_phase_override") is None
        ):
            phase_inputs: Path | list[Path]
            if experiment.segments:
                phase_inputs = work / "seg_001" / f"{experiment.dataset_id}.fid"
            else:
                slice_dir = work / "fid"
                slices = (
                    sorted(slice_dir.glob("test*.fid"))
                    if slice_dir.is_dir()
                    else []
                )
                if slices:
                    phase_inputs = slices
                    logs.append(
                        f"切片式 fid:直接维相位搜索用 {len(slices)} 个切片"
                    )
                else:
                    phase_inputs = work / f"{experiment.dataset_id}.fid"
            if isinstance(phase_inputs, Path) and not phase_inputs.is_file():
                logs.append("直接维相位搜索:未找到 fid/切片,保持 p0=p1=0")
            else:
                _td = effective_td(experiment)
                direct_p0, direct_p1 = self._search_direct_phase(
                    work,
                    phase_inputs,
                    logs,
                    is_nus=True,
                    n_f1=int(_td[1]) if len(_td) > 1 else 0,
                    n_f2=int(_td[2]) if len(_td) > 2 else 1,
                    is_1d=(experiment.ndim == 1),
                )

        td = effective_td(experiment)
        if experiment.ndim >= 3:
            grid = max(int(td[1]) * int(td[2]), 1)
            ext = "ft3"
            script_fn = generate_3d_nus_script
        else:
            grid = max(int(td[1]), 1)
            ext = "ft2"
            script_fn = generate_2d_nus_script
        fraction = nuslist_count / grid if grid else 0.0
        tier_nsigma, tier_thresh = select_smile_params(fraction)
        nsigma = float(params.get("nsigma", tier_nsigma))
        thresh = float(params.get("thresh", tier_thresh))
        smile_scaling = bool(params.get("smile_scaling", True))
        smile_report = int(params.get("smile_report", 1))
        nthread = resolve_nthread(params.get("nthread"))
        # 0.2.113:不再按网格限线程——sampleM 事故根因是直接维内存
        # (非切片流/直接维填零过多),由 0.2.112 内存护栏兜底
        ext_lo = resolve_ext_lo(params.get("ext_lo"))
        ext_hi = resolve_ext_hi(params.get("ext_hi"))
        extract = _as_bool(params.get("extract", True))
        baseline = expand_baseline(experiment, params.get("baseline"))
        zero_fill = params.get("zero_fill")
        linewidth_hz = params.get("linewidth_hz")
        points_per_line = resolve_points_per_line(params.get("points_per_line"))
        zf_plan = zero_fill_plan(
            experiment,
            zero_fill,
            linewidth_hz=linewidth_hz,
            points_per_line=points_per_line,
        )
        logs += zero_fill_report(zf_plan)
        # 0.2.112:内存护栏——SMILE 峰值估计(0.2.199-补15 对齐 SMILE 自报:
        # 直接维点数 × 间接维迭代 FT 尺寸积 × 16B);超限先降直接维填零
        # 1×TD,仍超则报错
        from backend.memory_guard import (
            MEM_SAFETY,
            available_memory_mb,
            direct_points_after_ext,
            estimate_smile_peak_mb,
        )

        direct_axis = (
            experiment.dimensions[0].logical_axis
            if experiment.dimensions
            else ""
        )
        zf_direct = int((zf_plan.get(direct_axis) or {}).get("size") or td[0])
        direct_pts = direct_points_after_ext(experiment, zf_direct, ext_lo, ext_hi)
        peak_mb = estimate_smile_peak_mb(
            experiment.ndim, direct_pts, td[1:]
        )
        avail_mb = available_memory_mb()
        if peak_mb > avail_mb * MEM_SAFETY:
            td0 = max(int(td[0]), 1)
            one_x = 1 << (td0 - 1).bit_length()  # 1×TD 的 next_pow2
            if zf_direct > one_x:
                logs.append(
                    f"内存护栏:峰值约 {peak_mb:.0f}MB > 可用 {avail_mb}MB×"
                    f"{MEM_SAFETY:.2f},直接维填零降为 1×TD({zf_direct}→{one_x})"
                )
                if progress is not None:
                    progress("内存不足:直接维填零已降为 1×TD 以降低 SMILE 内存")
                zf_plan[direct_axis] = {
                    "mode": "size",
                    "size": one_x,
                    "note": "内存护栏:直接维填零降为 1×TD",
                }
                direct_pts = direct_points_after_ext(
                    experiment, one_x, ext_lo, ext_hi
                )
                peak_mb = estimate_smile_peak_mb(
                    experiment.ndim, direct_pts, td[1:]
                )
            if peak_mb > avail_mb * MEM_SAFETY:
                import math

                needed_gb = math.ceil(peak_mb / 1024.0)
                return {
                    "success": False,
                    "message": (
                        f"当前内存无法处理该谱(可用约 {avail_mb} MB,SMILE "
                        f"峰值约 {peak_mb:.0f} MB),请至少提供 {needed_gb} GB 内存。"
                        "也可以尽可能变窄直接维范围并开启「应用此范围到优化过程」"
                        "(直接维窗口越窄,SMILE 峰值内存越低)"
                    ),
                    "logs": logs,
                }
        # 0.2.199-补14:按当前可用内存实时设置 SMILE -maxMem(与护栏同一预算,
        # 防预估偏差/并发占用导致峰值超限硬扛)
        max_mem_gb = max(avail_mb * MEM_SAFETY / 1024.0, 1.0)
        logs.append(
            f"SMILE 内存上限(-maxMem): {max_mem_gb:.1f} GB"
            f"(可用 {avail_mb} MB)"
        )
        # 0.2.96:显示层相位搜索(1× SMILE,无额外后端)——主重构用 PS(0,0)
        # (或缓存相位);重构后在复型 recon 平面上对称性评分,最后一步把相位
        # 旋转应用到 recon 并便宜重渲 stage-2(非 SMILE)
        smile_phase = (direct_p0, direct_p1)
        run_display_search = False
        if (
            display_phase_search
            and direct_phase_search
            and params.get("direct_phase_override") is None
        ):
            if (work / "phase.json").is_file():
                try:
                    data = json.loads(
                        (work / "phase.json").read_text(encoding="utf-8")
                    )
                    if data.get("version") == 2:
                        smile_phase = (float(data["p0"]), float(data["p1"]))
                        logs.append(
                            f"直接维相位(缓存): p0={smile_phase[0]:g} "
                            f"p1={smile_phase[1]:g}"
                        )
                except (OSError, TypeError, ValueError, KeyError):
                    pass
            else:
                smile_phase = (0.0, 0.0)
                run_display_search = True
                logs.append(
                    "显示层相位搜索: 主重构 PS(0,0),重构后对称性评分"
                )

        # 0.2.162:SMILE 优化稳定性——对输入 fid 注入小幅噪声生成临时副本,
        # 使同参数多次重构存在运行间差异(去伪峰用);只影响本次重构,用完即删
        fid_noise = float(params.get("fid_noise", 0.0) or 0.0)
        noise_seed = int(params.get("fid_noise_seed", 0) or 0)
        if fid_noise > 0:
            noisy_in = self._make_noisy_fid_input(
                work, in_file, fid_noise, noise_seed, logs
            )
            if noisy_in is not None:
                in_file = noisy_in
        out_file = f"{experiment.dataset_id}.{ext}"
        script = script_fn(
            experiment,
            in_file=in_file,
            nuslist=str(params.get("nuslist_file") or "nuslist"),
            out_file=out_file,
            nthread=nthread,
            nuslist_count=int(params.get("nuslist_count") or nuslist_count),
            ext_lo=ext_lo,
            ext_hi=ext_hi,
            nsigma=nsigma,
            thresh=thresh,
            smile_scaling=smile_scaling,
            smile_report=smile_report,
            max_mem=max_mem_gb,
            direct_phase=smile_phase,
            phases=params.get("phases"),
            window=params.get("window"),
            extract=extract,
            baseline=baseline,
            zero_fill=zf_plan,
            linewidth_hz=linewidth_hz,
            points_per_line=points_per_line,
            sampling=sampling,
            direct_poly_time=bool(params.get("direct_poly_time", False)),
        )
        nus_com = work / f"{experiment.dataset_id}_nus.com"
        nus_com.write_text(script, encoding="utf-8", newline="\n")
        if script_only:
            return {
                "success": True,
                "message": "仅生成脚本(script_only)",
                "logs": logs,
                "script": script,
                "script_path": str(nus_com),
                "work_dir": str(work),
            }
        logs.append(
            f"SMILE 重构（{nuslist_count} 采样点，{fraction * 100:.1f}%，"
            f"1H {ext_lo}-{ext_hi} ppm，nSigma={nsigma:g} thresh={thresh:g}）"
        )
        timeout = float(params.get("timeout_s", 3600))
        # 注意：不在 tcsh -c 包装内叠加 nice（实测会让 tcsh 脚本结束后挂起空转）
        if progress is not None:
            progress("开始 SMILE 重构")
        run_result = runtime.run(
            ["csh", nus_com.name],
            cwd=str(work),
            timeout=timeout,
            on_line=(lambda line: progress(line) if progress else None),
        )
        logs.append(f"nus.com: rc={run_result.returncode}")
        if fid_noise > 0:
            shutil.rmtree(work / f".smile_noise_{noise_seed}", ignore_errors=True)
        # 0.2.199-补11:SMILE 内部错误(如直接维未加窗)在 csh 管道里可能
        # rc=0,显式检测输出,避免把失败重构当成功出谱
        smile_err = "SMILE Error" in (
            (run_result.stdout or "") + (run_result.stderr or "")
        )
        if smile_err:
            logs.append(
                "检测到 SMILE 内部错误(直接维未加窗/输入状态错误),重构失败"
            )
            return {
                "success": False,
                "message": "SMILE 重构失败(内部错误:直接维需加窗)",
                "logs": logs,
            }
        spectrum = work / out_file
        if (
            run_result.returncode != 0
            or not spectrum.is_file()
            or spectrum.stat().st_size == 0
        ):
            return {
                "success": False,
                "message": f"SMILE 重构失败/未生成 {out_file}",
                "logs": logs,
            }
        try:
            (work / ".nus_params.json").write_text(
                json.dumps(params, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass  # 参数指纹写盘失败不影响重构结果
        logs.append(f"终谱 → {spectrum}")
        # 0.2.96:最后一步填相位(显示层搜索 + recon 旋转 + 便宜 finalize 重渲)
        if run_display_search:
            est = self._display_phase_search(experiment, work, logs)
            if est is not None and est[2] >= 30.0:
                p0, p1, score = est
                logs.append(
                    f"显示层相位: F2=({p0:g}, {p1:g}) score={score:.2f}"
                )
                # 0.2.199-补22:放宽 p1 归零护栏(20→170,高场真实大 p1)
                if abs(p1) > 170.0:
                    logs.append(
                        f"显示层相位 p1={p1:g}° 幅值异常(>170°),归零"
                    )
                    p1 = 0.0
                (work / "phase.json").write_text(
                    json.dumps(
                        {
                            "version": 2,
                            "source": "display_recon",
                            "p0": p0,
                            "p1": p1,
                            "score": score,
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                if (
                    abs(((p0 + 180.0) % 360.0) - 180.0) > 2.0
                    or abs(p1) > 2.0
                ):
                    if self._apply_direct_phase(
                        experiment, work, p0, p1, logs
                    ):
                        logs.append(
                            "终谱已按显示层相位重渲(stage-2 重跑,无 SMILE)"
                        )
            else:
                logs.append("显示层相位置信度不足或搜索失败,保持默认相位")
        if progress is not None:
            progress("完成 SMILE 重构;终谱已就位")
        return {
            "success": True,
            "message": "SMILE 重构成功",
            "spectrum_path": str(spectrum),
            "logs": logs,
            "effective_params": {
                **_effective_params_base(
                    extract,
                    ext_lo,
                    ext_hi,
                    zf_plan,
                    baseline,
                    linewidth_hz,
                    points_per_line,
                    sampling,
                ),
                "nSigma": nsigma,
                "thresh": thresh,
                "smile_scaling": smile_scaling,
                "smile_report": smile_report,
                "nthread": nthread,
                "direct_phase": [direct_p0, direct_p1],
                "linewidth_hz": linewidth_hz,
                "points_per_line": points_per_line,
                "sampling": dict(sampling),
            },
        }

    def _make_noisy_fid_input(
        self, work: Path, in_file: str, noise_scale: float, seed: int, logs: list[str]
    ) -> str | None:
        """把输入 fid(单文件或切片)复制到临时目录并注入高斯噪声。

        返回新的 in_file 相对路径;失败返回 None(不影响主流程)。SMILE 为
        确定性算法,同参数重构逐位一致;注入小幅测量噪声使多次重构存在
        差异,供 SMILE 优化做峰稳定性去伪(0.2.162)。"""
        import numpy as np

        rng = np.random.default_rng(seed)
        tmp = work / f".smile_noise_{seed}"
        if in_file.startswith("fid/"):
            src_dir = work / "fid"
            if not src_dir.is_dir():
                return None
            try:
                tmp.mkdir(parents=True, exist_ok=True)
                for src in sorted(src_dir.glob("test*.fid")):
                    self._write_noisy_fid(src, tmp / src.name, noise_scale, rng)
                return f".smile_noise_{seed}/test%03d.fid"
            except Exception as exc:  # noqa: BLE001
                logs.append(f"fid 噪声注入(切片)失败: {exc}")
                return None
        src = work / in_file
        if not src.is_file():
            return None
        try:
            tmp.mkdir(parents=True, exist_ok=True)
            target = tmp / src.name
            self._write_noisy_fid(src, target, noise_scale, rng)
            return f".smile_noise_{seed}/{src.name}"
        except Exception as exc:  # noqa: BLE001
            logs.append(f"fid 噪声注入(单文件)失败: {exc}")
            return None

    @staticmethod
    def _write_noisy_fid(
        src: Path, target: Path, noise_scale: float, rng: Any
    ) -> None:
        """按 nmrPipe fid 字节布局读取→注入噪声→写回副本(实/虚分块)。"""
        import nmrglue as ng
        import numpy as np

        raw = src.read_bytes()
        dic, data = ng.pipe.read(str(src))
        arr = np.asarray(data).astype(np.complex64)
        fdsize = int(float(dic["FDSIZE"]))
        specnum = int(float(dic["FDSPECNUM"]))
        header_len = next(
            (
                header
                for header in (512, 1024, 2048)
                if len(raw) == header + specnum * fdsize * 8
            ),
            None,
        )
        if header_len is None or arr.shape != (specnum, fdsize):
            raise ValueError("fid 布局无法解析")
        noisy = arr.copy()
        for row in range(specnum):
            sigma = float(np.std(np.imag(arr[row]))) if fdsize > 1 else 0.0
            if sigma <= 0:
                continue
            noisy[row] = arr[row] + rng.normal(
                0.0, sigma * noise_scale, fdsize
            ) + 1j * rng.normal(0.0, sigma * noise_scale, fdsize)
        out = bytearray(raw[:header_len])
        for row in range(specnum):
            re = np.ascontiguousarray(noisy[row].real, dtype="<f4")
            im = np.ascontiguousarray(noisy[row].imag, dtype="<f4")
            out += re.tobytes() + im.tobytes()
        target.write_bytes(bytes(out))

    def smile_scan(
        self,
        experiment: Experiment,
        params: dict[str, Any] | None = None,
        combos: list[dict[str, Any]] | None = None,
        *,
        work_dir: Path | str,
        evaluate: Callable[[str], dict[str, Any]] | None = None,
        progress: Callable[[int, int, str], None] | None = None,
        delete_spectra: bool = True,
        holdout_ratio: float = 0.0,
    ) -> dict[str, Any]:
        """SMILE 参数扫描:直接维跑一次,候选谱评估后即删(0.2.199-补29hz-修3)。

        以终跑脚本为模板只换 SMILE 参数:① 直接维段跑一次得到切片文件;
        ② 每组参数跑一次「SMILE + 间接维」得到终谱;③ evaluate(path) 取指标;
        ④ 立刻删除该谱。候选各自独立命名、互不覆盖,也不触碰数据 process/
        下的终跑脚本。返回 {success, message, logs, candidates}。
        """
        from backend.script_generator import (
            rename_nus_scan_output,
            split_nus_script,
        )

        runtime = CshRuntime()
        combos = list(combos or [])
        base = dict(params or {})
        scan_dir = Path(work_dir)
        scan_dir.mkdir(parents=True, exist_ok=True)
        logs: list[str] = []
        holdout_file = ""
        holdout_coords: list[tuple[int, int]] = []
        if holdout_ratio and float(holdout_ratio) > 0:
            # A 方案(0.2.199-补29hz-修5):留出一部分**已采集**的采样点,
            # 只用其余点重建;留出点用于数据一致性残差(无需全采样参考)
            # 扫描目录里的 nuslist 由 reconstruct_nus 稍后拷入,这里必须先用原始数据的
            src = None
            for _cand in (
                Path(str(experiment.source_path)) / "nuslist",
                scan_dir / "nuslist",
            ):
                if _cand.is_file():
                    src = _cand
                    break
            src = src or (scan_dir / "nuslist")
            if src.is_file():
                lines = [
                    ln.strip()
                    for ln in src.read_text(encoding="utf-8").splitlines()
                    if ln.strip() and not ln.strip().startswith("#")
                ]
                step = max(2, int(round(1.0 / float(holdout_ratio))))
                holdout = [ln for i, ln in enumerate(lines) if i % step == step - 1]
                train = [ln for i, ln in enumerate(lines) if i % step != step - 1]
                if train and holdout:
                    train_path = scan_dir / "nuslist_train"
                    holdout_path = scan_dir / "nuslist_holdout"
                    train_path.write_text("\n".join(train) + "\n", encoding="utf-8")
                    holdout_path.write_text("\n".join(holdout) + "\n", encoding="utf-8")
                    holdout_file = str(holdout_path)
                    base["nuslist_file"] = "nuslist_train"
                    base["nuslist_count"] = len(train)
                    logs.append(
                        f"留出采样点: train={len(train)} holdout={len(holdout)}"
                    )
                    holdout_coords = [
                        tuple(int(v) for v in ln.split()[:2])
                        for ln in holdout
                        if len(ln.split()) >= 2
                    ]
        old_work_dir = self.work_dir
        self.work_dir = str(scan_dir)
        try:
            scripts: list[str] = []
            for combo in combos:
                resp = self.reconstruct_nus(
                    experiment, {**base, **combo}, script_only=True
                )
                if not resp.get("success") or not resp.get("script"):
                    return {
                        "success": False,
                        "message": str(resp.get("message", "无法生成扫描脚本")),
                        "logs": logs + list(resp.get("logs", [])),
                        "candidates": [],
                    }
                scripts.append(str(resp["script"]))
                logs.extend(str(line) for line in resp.get("logs", []))
            prefix, _ = split_nus_script(scripts[0])
            # 无切片切点(2D 单文件脚本)→ 回退:整脚本逐组跑,输出各自命名
            split_available = bool(prefix)
            timeout = float(base.get("timeout_s", 7200))
            if split_available:
                step1 = scan_dir / "step1_direct.com"
                step1.write_text(prefix, encoding="utf-8", newline="\n")
                if progress is not None:
                    progress(0, len(combos), "直接维处理(生成切片)…")
                run1 = runtime.run(
                    ["csh", step1.name], cwd=str(scan_dir), timeout=timeout
                )
                logs.append(f"step1 直接维: rc={run1.returncode}")
                if run1.returncode != 0:
                    return {
                        "success": False,
                        "message": f"直接维处理失败(rc={run1.returncode})",
                        "logs": logs,
                        "candidates": [],
                    }
            out_ext = {1: "ft1", 2: "ft2"}.get(experiment.ndim, "ft3")
            candidates: list[dict[str, Any]] = []
            for index, (combo, script) in enumerate(
                zip(combos, scripts), start=1
            ):
                tag = f"cand{index:02d}"
                if split_available:
                    _, suffix = split_nus_script(script)
                else:
                    suffix = script
                suffix = rename_nus_scan_output(suffix, f"{tag}.{out_ext}")
                task = scan_dir / f"step2_{tag}.com"
                task.write_text(suffix, encoding="utf-8", newline="\n")
                if progress is not None:
                    progress(index, len(combos), f"扫描 {index}/{len(combos)}: {combo}")
                _smile_log = scan_dir / "smile.log"
                try:
                    _log_offset = _smile_log.stat().st_size
                except OSError:
                    _log_offset = 0
                run2 = runtime.run(
                    ["csh", task.name], cwd=str(scan_dir), timeout=timeout
                )
                spectrum = scan_dir / f"{tag}.{out_ext}"
                ok = (
                    run2.returncode == 0
                    and spectrum.is_file()
                    and spectrum.stat().st_size > 0
                )
                metrics: dict[str, Any] = {}
                # 0.2.199-补29hz-修6:SMILE 每平面 RMS 报告 → 训练点拟合优度
                # (FINAL/INITIAL 的中位数;无需平面↔网格映射,跨参数可比)
                try:
                    with _smile_log.open("r", encoding="utf-8", errors="ignore") as _fh:
                        _fh.seek(_log_offset)
                        _tail = _fh.read()
                    _ratios: list[float] = []
                    for _line in _tail.splitlines():
                        if "INITIAL_RMS" not in _line or "FINAL_RMS" not in _line:
                            continue
                        try:
                            _ini = float(_line.split("INITIAL_RMS")[1].split()[0])
                            _fin = float(_line.split("FINAL_RMS")[1].split()[0])
                        except (IndexError, ValueError):
                            continue
                        if _ini > 0:
                            _ratios.append(_fin / _ini)
                    if _ratios:
                        _ratios.sort()
                        metrics["smile_rms_ratio"] = round(
                            _ratios[len(_ratios) // 2], 4
                        )
                        metrics["smile_planes"] = len(_ratios)
                except OSError:
                    pass
                # A 方案(0.2.199-补29hz-修6):留出采样点的数据一致性残差。
                # 索引映射由实物相关性实测确定(scale=1.0/offset=0):
                #   留出 (k0,k1) → 平面内 [k1, k0](平面=直接维点,两轴=间接维)
                if holdout_file and holdout_coords:
                    def _holdout_residual() -> dict[str, float]:
                        import nmrglue as ng
                        import numpy as np

                        if experiment.ndim >= 3:
                            acq_dir, rc_dir, name = (
                                scan_dir / "nus3d_1",
                                scan_dir / "nus3d_rc",
                                "test%04d.ft1",
                            )
                        else:
                            return {}
                        planes = [
                            i for i in range(1, 1203) if i % 40 == 1
                        ][:30]
                        meas: list[complex] = []
                        pred: list[complex] = []
                        for p in planes:
                            fa = acq_dir / (name % p)
                            fr = rc_dir / (name % p)
                            if not (fa.is_file() and fr.is_file()):
                                continue
                            _da, A = ng.pipe.read(str(fa))
                            _dr, R = ng.pipe.read(str(fr))
                            A = np.asarray(A)
                            R = np.asarray(R)
                            for (k0, k1) in holdout_coords:
                                if not (
                                    0 <= k1 < A.shape[0]
                                    and 0 <= k0 < A.shape[1]
                                    and 0 <= k1 < R.shape[0]
                                    and 0 <= k0 < R.shape[1]
                                ):
                                    continue
                                meas.append(complex(A[k1, k0]))
                                pred.append(complex(R[k1, k0]))
                        if len(meas) < 10:
                            return {}
                        m = np.array(meas)
                        q = np.array(pred)
                        scale = float(np.sqrt(np.mean(np.abs(m) ** 2))) or 1.0
                        resid = np.abs(q - m) / scale
                        denom = float(np.linalg.norm(m) * np.linalg.norm(q))
                        corr = (
                            float(abs(np.vdot(m, q)) / denom) if denom else 0.0
                        )
                        return {
                            "holdout_rmse": round(float(np.median(resid)), 4),
                            "holdout_rmse_p90": round(float(np.percentile(resid, 90)), 4),
                            "holdout_corr": round(corr, 4),
                            "holdout_points": len(meas),
                            "holdout_planes": len(planes),
                        }

                    try:
                        metrics.update(_holdout_residual())
                    except Exception as exc:  # noqa: BLE001 - 残差失败不阻断扫描
                        logs.append(f"留出残差计算失败: {exc}")
                if ok and evaluate is not None:
                    try:
                        metrics.update(dict(evaluate(str(spectrum)) or {}))
                    except Exception as exc:  # noqa: BLE001 - 单组失败不阻断其余
                        metrics = {"error": str(exc)}
                elif not ok:
                    metrics = {"error": f"重构失败(rc={run2.returncode})"}
                if delete_spectra:
                    spectrum.unlink(missing_ok=True)
                candidates.append(
                    {
                        "index": index,
                        "params": dict(combo),
                        "metrics": metrics,
                        "script": script,
                        "ok": bool(ok),
                    }
                )
                logs.append(f"{tag}: rc={run2.returncode} 指标={metrics}")
            return {
                "success": True,
                "message": f"完成 {len(candidates)} 组扫描",
                "logs": logs,
                "candidates": candidates,
                "scan_dir": str(scan_dir),
                "holdout_file": holdout_file,
            }
        finally:
            self.work_dir = old_work_dir

    def finalize_nus(
        self,
        experiment: Experiment,
        *,
        phases: dict[str, tuple[float, float]] | None = None,
        work_dir: Path | str | None = None,
        timeout: float = 1800.0,
        baseline: dict[str, dict[str, Any]] | None = None,
        params: dict[str, Any] | None = None,
        sampling: dict[str, Any] | None = None,
        out_file: str | None = None,
        script_name: str | None = None,
        planes: str | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        """从 SMILE 重构平面做间接维 FT 定稿(逐维相位候选,不重跑 SMILE)。

        phases:{轴 -> (p0, p1)},缺省 0;供逐维相位优化(用户方案)。
        planes:重构平面输入覆盖(默认 nus3d_rc/test%04d.ft1 或
        nus2d/recon.ft1;显示层填相位用 nus3d_rc_ph/ 副本)。
        window:{轴 -> 窗函数配置},缺省不插窗(间接维 FT 前)。
        """
        bin_dir = self._bin_dir()
        if bin_dir is None:
            return {
                "success": False,
                "message": "未找到 nmrPipe（csh: which nmrPipe）",
                "logs": [],
            }
        work = Path(work_dir) if work_dir else self._work_path(experiment)
        if planes is None:
            planes = (
                "nus3d_rc/test%04d.ft1"
                if experiment.ndim >= 3
                else "nus2d/recon.ft1"
            )
        if experiment.ndim >= 3:
            if not (work / "nus3d_rc").is_dir():
                return {
                    "success": False,
                    "message": f"缺少重构平面 nus3d_rc: {work}",
                    "logs": [],
                }
        else:
            if not (work / "nus2d" / "recon.ft1").is_file():
                return {
                    "success": False,
                    "message": f"缺少重构平面 nus2d/recon.ft1: {work}",
                    "logs": [],
                }
        out_ext = {1: "ft1", 2: "ft2"}.get(experiment.ndim, "ft3")
        # 0.2.199-补29ez:显式 out_file = 中间渲染(预览/joint/候选),统一写入
        # work/_intermediate;终跑默认名保持原位。
        _render_out = out_file not in (None, "")
        out_file = out_file or f"{experiment.dataset_id}.{out_ext}"
        if _render_out:
            _render_dir = work / INTERMEDIATE_SUBDIR
            _render_dir.mkdir(parents=True, exist_ok=True)
            out_file = f"{INTERMEDIATE_SUBDIR}/{out_file}"
        zf_params = dict(params or {})
        zf_plan = zero_fill_plan(
            experiment,
            zf_params.get("zero_fill"),
            linewidth_hz=zf_params.get("linewidth_hz"),
            points_per_line=resolve_points_per_line(
                zf_params.get("points_per_line")
            ),
        )
        script = generate_nus_finalize_script(
            experiment,
            planes=planes,
            out_file=out_file,
            phases=phases,
            baseline=baseline,
            zero_fill=zf_plan,
            sampling=sampling,
            preview_axis=zf_params.get("preview_axis"),
            window=zf_params.get("window"),
            keep_complex=bool(zf_params.get("keep_complex")),
        )
        finalize_com = work / (
            script_name or f"{experiment.dataset_id}_finalize.com"
        )
        finalize_com.write_text(script, encoding="utf-8", newline="\n")
        runtime = CshRuntime()
        result = runtime.run(
            ["csh", finalize_com.name], cwd=str(work), timeout=timeout
        )
        logs = zero_fill_report(zf_plan) + [f"finalize.com: rc={result.returncode}"]
        spectrum = work / out_file
        if (
            result.returncode != 0
            or not spectrum.is_file()
            or spectrum.stat().st_size == 0
        ):
            return {
                "success": False,
                "message": f"finalize 失败/未生成 {out_file}",
                "logs": logs,
            }
        logs.append(f"谱图 → {spectrum}")
        return {
            "success": True,
            "spectrum_path": str(spectrum),
            "message": "finalize 完成",
            "logs": logs,
        }

    def project_3d(
        self,
        spectrum_path: Path | str,
        out_dir: Path | str,
        *,
        prefix: str = "proj",
        timeout: float = 900,
        labels: list[str] | None = None,
    ) -> dict[str, dict[str, object]]:
        """用 NMRPipe 自带 proj3D.tcl 从 3D 终谱生成三个 2D 投影(沿轴求和)。

        0.2.133 正确用法:直接把 3D 谱交给 proj3D.tcl,不预拆平面、不
        重写任何输出头(proj3D 自动按轴标签命名输出 ``{核A}.{核B}.dat``,
        输出头在 NMRPipe 语义下正确,showhdr 验证)。返回:
          {"paths": {"核A-核B": path, ...},
           "labels": {"核A-核B": 固定轴核, ...},
           "nuclei": {"核A-核B": [核A, 核B], ...}};
        固定轴核 = 源谱三个 FDF 标签中不在该平面两核内的那一个
        (例如 13C-15N 平面固定 1H)。输出文件名中的核顺序即 proj3D
        的 X.Y 轴序,不做字符推断以外的任何猜测。投影失败抛
        ToolError(由调用方降级,不阻断谱图生成)。
        """
        import nmrglue as ng

        from backend.nmrpipe_finder import find_tool
        from backend.runtime import ToolError

        runtime = CshRuntime()
        src = Path(spectrum_path)
        dest = Path(out_dir)
        dest.mkdir(parents=True, exist_ok=True)
        proj3d = find_tool("proj3D.tcl", self._bin_dir())
        if proj3d is None:
            raise ToolError("未找到 proj3D.tcl(NMRPipe 投影工具)")
        dic, _ = ng.pipe.read(str(src))
        if labels is None:
            labels = [
                str(dic.get(k, "") or "")
                for k in ("FDF1LABEL", "FDF2LABEL", "FDF3LABEL")
            ]
        # 清除残留 .dat,只保留本次输出
        for stale in dest.glob("*.dat"):
            stale.unlink(missing_ok=True)
        run = runtime.run(
            [
                str(proj3d),
                "-in",
                str(src),
                "-outDir",
                str(dest.resolve()),
                "-sum",
                "-noverb",
            ],
            timeout=timeout,
        )
        if run.returncode != 0:
            # 0.2.199-补29w:重复标签(HNN 两个 15N)proj3D 无法按标签
            # 选轴(实测 bad axis name Y),回退 numpy 内存投影
            return self._project_3d_numpy(
                str(src), dest, prefix=prefix, labels=labels
            )
        dat_files = sorted(dest.glob("*.dat"))
        outputs: dict[str, str] = {}
        nuclei: dict[str, list[str]] = {}
        fixed: dict[str, str] = {}
        for dat in dat_files:
            parts = dat.stem.split(".")
            if len(parts) != 2 or not all(parts):
                continue
            n1, n2 = parts[0], parts[1]
            key = f"{n1}-{n2}"
            outputs[key] = str(dat)
            nuclei[key] = [n1, n2]
            fixed[key] = next(
                (str(lbl or "") for lbl in labels if str(lbl or "") not in (n1, n2)),
                "",
            )
        if len(outputs) != 3:
            raise ToolError(
                f"proj3D 输出解析异常(期望 3 个 *.dat,实际 {len(outputs)}): "
                f"{[p.name for p in dat_files]}"
            )
        return {"paths": outputs, "labels": fixed, "nuclei": nuclei}

    def _project_3d_numpy(
        self,
        spectrum_path: str,
        out_dir: Path,
        *,
        prefix: str = "proj",
        labels: list[str] | None = None,
    ) -> dict[str, dict[str, object]]:
        """numpy 内存投影回退(0.2.199-补29w):HNN 等重复核标签。

        proj3D.tcl 按轴标签选轴,两个 15N 无法消歧(实测 bad axis name Y);
        这里读终谱按存储轴(axis0=F2, axis1=F1, axis2=F3)分别求和生成
        三个 2D 投影 FDF,命名 {prefix}_F{n}.ft2(固定逻辑轴),GUI 的
        _proj_F{n} 兼容解析;数据取向 (b,a) 与 GUI _load_projection_ft2
        的 _proj_F{n} 约定一致(axis0=b, axis1=a)。轴参数从源头 FDF 块
        复制(GUI 优先取已加载 3D 谱对应核的轴,文件头仅兜底)。
        """
        import nmrglue as ng
        from nmrglue.fileio import pipe as ngpipe

        from backend.runtime import ToolError

        dic, data = ng.pipe.read(spectrum_path)
        data = np.asarray(data)
        if np.iscomplexobj(data):
            data = data.real
        if data.ndim != 3:
            raise ToolError(
                f"投影需要 3D 谱: {spectrum_path} shape={data.shape}"
            )

        def _src_fdf(axis_idx: int) -> str:
            order = [int(v) for v in dic.get("FDDIMORDER") or []]
            if len(order) >= 3:
                dim = order[2 - axis_idx]
                if 1 <= dim <= 4:
                    return f"FDF{dim}"
            return f"FDF{axis_idx + 1}"

        def _write(path: Path, plane: np.ndarray, fdf0: str, fdf1: str) -> None:
            # 保留源头全部 FDF1/2/3 键(nmrglue dic2fdata 需完整 512 字头),
            # 仅把 FDF1/FDF2 覆盖为平面两轴;FDDIMCOUNT=2 时读取端忽略 FDF3
            out: dict[str, object] = dict(dic)
            out["FDDIMCOUNT"] = 2
            out["FDSIZE"] = float(plane.shape[1])
            out["FDSPECNUM"] = float(plane.shape[0])
            out["FDQUADFLAG"] = 1
            out["FDF1QUADFLAG"] = 1
            out["FDF2QUADFLAG"] = 1
            out["FDDIMORDER"] = [2.0, 1.0]
            for out_pref, src_pref, size in (
                ("FDF1", fdf1, plane.shape[1]),
                ("FDF2", fdf0, plane.shape[0]),
            ):
                for k, v in dic.items():
                    if str(k).startswith(src_pref):
                        out[out_pref + str(k)[len(src_pref):]] = v
                out[out_pref + "SIZE"] = float(size)
            ngpipe.write(
                str(path),
                out,
                np.ascontiguousarray(plane, dtype=np.float32),
                overwrite=True,
            )

        out_dir.mkdir(parents=True, exist_ok=True)
        # 存储轴序 (F2, F1, F3):三投影分别固定 F1/F2/F3
        p23 = out_dir / f"{prefix}_F1.ft2"  # F2-F3 平面,固定 F1
        p13 = out_dir / f"{prefix}_F2.ft2"  # F1-F3 平面,固定 F2
        p12 = out_dir / f"{prefix}_F3.ft2"  # F1-F2 平面,固定 F3
        _write(p23, data.sum(axis=1), _src_fdf(0), _src_fdf(2))
        _write(p13, data.sum(axis=0), _src_fdf(1), _src_fdf(2))
        _write(p12, data.sum(axis=2).T, _src_fdf(1), _src_fdf(0))
        return {
            "paths": {"F1": str(p23), "F2": str(p13), "F3": str(p12)},
            "labels": {"F1": "", "F2": "", "F3": ""},
            "nuclei": {"F1": None, "F2": None, "F3": None},
            "numpy_fallback": True,
        }


    @staticmethod
    def _converted_fid_path(work: Path, dataset_id: str) -> Path:
        """转换产物 fid 路径:单文件优先;切片式 work/fid/ 兼容。"""
        single = work / f"{dataset_id}.fid"
        if single.is_file():
            return single
        slice_dir = work / "fid"
        if _slice_candidates(slice_dir, dataset_id):
            return slice_dir
        return single

    @staticmethod
    def _merged_fid_in(work: Path, dataset_id: str) -> str | None:
        """分段合并产物 in_file:单文件优先,否则切片流(0.2.199-补28)。"""
        if (work / "merged" / f"{dataset_id}.fid").is_file():
            return f"merged/{dataset_id}.fid"
        slice_dir = work / "merged" / "fid"
        slice_in = _slice_in_file(slice_dir, dataset_id)
        if slice_in:
            return f"merged/{slice_in}"
        return None

    def _finalize_converted_fid(
        self,
        raw_dir: Path,
        dest_work: Path,
        dataset_id: str,
        logs: list[str],
    ) -> bool:
        """把 bruker 转换产物归位:单文件 {dataset_id}.fid(0.2.199-补16 统一)。

        fid.com 输出名已被 patch_fid_com 改写为 {dataset_id}.fid
        （0.2.163-补13）;test.fid/test*.fid 仅作旧数据/人工改名的兼容。
        旧切片式 fid/*.fid(acqu3s TD 修正时代产物)仅作兼容保留;
        0.2.80 起两种形式都接受,切片式保留为 work/fid/ 供流式处理。
        """
        source = raw_dir / f"{dataset_id}.fid"
        if not source.is_file():
            source = raw_dir / "test.fid"  # 旧命名兼容(fid.com 手动改回)
        if source.is_file():
            shutil.move(str(source), dest_work / f"{dataset_id}.fid")
            logs.append(f"{dataset_id}.fid 已就位（{raw_dir.name}）")
            return True
        slice_dir = raw_dir / "fid"
        slices = _slice_candidates(slice_dir, dataset_id)
        if not slices:
            return False
        dest_slice = dest_work / "fid"
        if dest_slice.exists():
            shutil.rmtree(dest_slice)
        shutil.copytree(slice_dir, dest_slice)
        logs.append(f"切片式 fid → {dest_slice.name}/（{len(slices)} 个切片）")
        return True

    def _convert_dir(
        self,
        runtime: CshRuntime,
        experiment: Experiment,
        raw_dir: Path,
        dest_work: Path,
        is_nus: bool,
        logs: list[str],
        fid_com_overrides: dict[str, str] | None = None,
    ) -> bool:
        """在 raw_dir 中 bruker -AUTO → fid.com 归位 dest_work → patch → 执行
        （脚本在 work 目录,相对路径以转换目录为 cwd 解析）→ 产物
        （单文件 {dataset_id}.fid,0.2.199-补16 统一;旧切片式兼容归位）。

        fid_com_overrides:人工途径的参数覆盖(0.2.163-补13)——分段数据
        用户在参考段 fid.com 上改的参数逐段应用,转换/合并仍由本层保证。
        不再修正 acqu3s TD:bruker -AUTO 按 NusTD 网格 + -aq2D Complex 直接
        输出单文件全网格 fid(实测 sampleK acqu3s TD=1 验证),3D 切片在
        SMILE 脚本 step1 直接维处理后产生。多段路径每段单文件 + addNMR 合并。
        bruker 失败时仅均匀采样走 bruk2pipe 回退。转换后清理 ser_full（可再生）。
        """
        convert_dir = raw_dir
        if not (convert_dir / "acqus").is_file():
            logs.append(f"缺少输入文件: {convert_dir}/acqus")
            return False
        try:
            raw_fid = convert_dir / "fid.com"
            fid_com = dest_work / "fid.com"
            bruker_ok = False
            bruker = find_tool("bruker", self._bin_dir())
            if bruker is not None:
                result = runtime.run(
                    ["bruker", "-AUTO"], cwd=str(convert_dir), timeout=120
                )
                logs.append(f"bruker -AUTO ({convert_dir.name}): rc={result.returncode}")
                if result.returncode == 0 and raw_fid.is_file():
                    # 0.2.91:fid.com 归位 process/(dest_work),raw 不再保留生成脚本
                    if raw_fid != fid_com:
                        fid_com.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(raw_fid), str(fid_com))
                    text = fid_com.read_text(encoding="utf-8", errors="replace")
                    patched, corrections = patch_fid_com(text, experiment)
                    if fid_com_overrides:
                        # 人工调参只覆盖参数;输出名/结构仍由后端保证
                        patched, override_corrections = apply_fid_com_overrides(
                            patched, fid_com_overrides
                        )
                        corrections += override_corrections
                    if is_nus:
                        nuslist_path = convert_dir / "nuslist"
                        if nuslist_path.is_file():
                            nuslist_count = len(read_nuslist(nuslist_path))
                            patched, nus_corrections = patch_nus_expand_count(
                                patched, nuslist_count
                            )
                            corrections += nus_corrections
                    for correction in corrections:
                        logs.append(f"参数修正: {correction}")
                    # LF 行尾必须：CRLF 会让 csh 的 \ 续行失效;脚本在 work 目录,
                    # 内部相对路径(./ser)以转换目录为 cwd 解析
                    fid_com.write_text(patched, encoding="utf-8", newline="\n")
                    # 0.2.199-补29gk:直接维无效 TD(0) 会让 bruk2pipe 无限转换卡死,
                    # 直接明确中止而非挂起(与补29dz 缺文件口径一致)。
                    if experiment.dimensions and experiment.dimensions[0].td == 0:
                        logs.append(
                            "直接维 TD=0(acqus/acqu 均未提供有效 TD),中止转换避免卡死"
                        )
                        return False
                    run_result = runtime.run(
                        ["csh", str(fid_com)], cwd=str(convert_dir), timeout=900
                    )
                    logs.append(f"fid.com: rc={run_result.returncode}")
                    bruker_ok = run_result.returncode == 0
            if not bruker_ok:
                if is_nus:
                    return False
                logs.append("回退：使用内置 bruk2pipe 参数转换")
                script = generate_convert_script(experiment)
                convert_script = dest_work / f"{experiment.dataset_id}_convert.com"
                convert_script.write_text(script, encoding="utf-8", newline="\n")
                run_result = runtime.run(
                    ["csh", str(convert_script)], cwd=str(convert_dir), timeout=600
                )
                logs.append(f"convert.com: rc={run_result.returncode}")
                if run_result.returncode != 0:
                    return False
            if not self._finalize_converted_fid(
                convert_dir, dest_work, experiment.dataset_id, logs
            ):
                return False
            # SMILE 只需 nuslist;ser_full/mask.fid/mask/ 等中间产物删除省空间
            # (mask/ 是 fid.com 中 nusExpand.tcl -mask 输出的采样掩码,可再生成)
            for stale in ("ser_full", "mask.fid"):
                stale_path = convert_dir / stale
                if stale_path.is_file():
                    stale_path.unlink()
            mask_dir = convert_dir / "mask"
            if mask_dir.is_dir():
                shutil.rmtree(mask_dir, ignore_errors=True)
            return True
        finally:
            pass  # 0.2.199-补16:不再有暂存副本需要清理

    def _light_phase_search(
        self,
        experiment: Experiment,
        work: Path,
        in_file: str,
        runtime: Any,
        logs: list[str],
        params: dict[str, Any],
        *,
        nuslist_count: int,
        sampling: dict[str, Any],
    ) -> tuple[float, float] | None:
        """轻量 SMILE 相位搜索(0.2.94,用户方案):子采样 nuslist + PS(0,0)
        亚秒轻量重构,再用现有固定迹线评分在轻量终谱上估直接维 (p0, p1)。

        单文件/多文件统一处理:轻量子目录 work/light/ 里放子采样 nuslist
        (SMILE -sample None 时读默认文件 nuslist),转换产物符号链接复用。
        成功写 phase.json(v2, source=phase_only_recon)并返回 (p0, p1);
        失败返回 None(调用方保留 NU-DFT 结果或 (0,0))。
        """
        phase_file = work / "phase.json"
        if phase_file.is_file():
            try:
                data = json.loads(phase_file.read_text(encoding="utf-8"))
                if data.get("version") == 2:
                    logs.append(
                        f"直接维相位(缓存): p0={data['p0']:g} "
                        f"p1={data['p1']:g}"
                    )
                    return float(data["p0"]), float(data["p1"])
            except (OSError, TypeError, ValueError, KeyError):
                pass
        points = read_nuslist(work / "nuslist")
        if len(points) < 8:
            return None
        target = max(16, len(points) // 4)
        if target >= len(points):
            return None  # 采样点太少,轻量无意义
        step = max(1, len(points) // target)
        sub = points[::step][:target]
        if not sub or 0 not in [int(p[0]) for p in sub]:
            sub[0] = points[0]
        light_dir = work / "light"
        if light_dir.exists():
            shutil.rmtree(light_dir)
        light_dir.mkdir()
        (light_dir / "nuslist").write_text(
            "".join(" ".join(str(v) for v in p) + "\n" for p in sub),
            encoding="utf-8",
        )
        try:
            if "%" in in_file:
                (light_dir / "fid").symlink_to(
                    work / "fid", target_is_directory=True
                )
            else:
                (light_dir / Path(in_file).name).symlink_to(
                    work / Path(in_file).name
                )
        except OSError:
            logs.append("轻量 SMILE 相位搜索:符号链接失败,跳过")
            return None
        nthread = resolve_nthread(params.get("nthread"))
        ext_lo = resolve_ext_lo(params.get("ext_lo"))
        ext_hi = resolve_ext_hi(params.get("ext_hi"))
        extract = _as_bool(params.get("extract", True))
        baseline = expand_baseline(experiment, params.get("baseline"))
        zero_fill = params.get("zero_fill")
        linewidth_hz = params.get("linewidth_hz")
        points_per_line = resolve_points_per_line(
            params.get("points_per_line")
        )
        if experiment.ndim >= 3:
            script_fn = generate_3d_nus_script
            ext = "ft3"
        else:
            script_fn = generate_2d_nus_script
            ext = "ft2"
        from backend.memory_guard import MEM_SAFETY, available_memory_mb

        max_mem_gb = max(available_memory_mb() * MEM_SAFETY / 1024.0, 1.0)
        out_light = f"{experiment.dataset_id}_light.{ext}"
        script = script_fn(
            experiment,
            in_file=in_file,
            nuslist=str(params.get("nuslist_file") or "nuslist"),
            out_file=out_light,
            nthread=nthread,
            nuslist_count=len(sub),
            ext_lo=ext_lo,
            ext_hi=ext_hi,
            nsigma=5.0,
            thresh=0.95,
            smile_scaling=True,
            smile_report=1,
            max_mem=max_mem_gb,
            direct_phase=(0.0, 0.0),
            extract=extract,
            baseline=baseline,
            zero_fill=zero_fill,
            linewidth_hz=linewidth_hz,
            points_per_line=points_per_line,
            sampling=sampling,
        )
        light_com = light_dir / "light.com"
        light_com.write_text(script, encoding="utf-8", newline="\n")
        logs.append(
            f"轻量 SMILE 相位搜索: {len(sub)}/{len(points)} 采样点,"
            f"窗口 {ext_lo}-{ext_hi} ppm,PS(0,0) 重构"
        )
        result = runtime.run(["csh", "light.com"], cwd=str(light_dir), timeout=600)
        logs.append(f"light.com: rc={result.returncode}")
        light_ft = light_dir / out_light
        if (
            result.returncode != 0
            or not light_ft.is_file()
            or light_ft.stat().st_size == 0
        ):
            logs.append("轻量 SMILE 相位搜索失败(回退 NU-DFT/默认)")
            return None
        try:
            import nmrglue as ng

            _dic, data = ng.pipe.read(str(light_ft))
            est = search_direct_phase_on_spectrum(
                np.asarray(data), cancel=cancel_requested
            )
        except Exception as exc:  # noqa: BLE001
            logs.append(f"轻量谱评分失败(回退): {exc}")
            return None
        if est is None:
            logs.append("轻量谱无信号(回退)")
            return None
        p0, p1, score = est
        phase_file.write_text(
            json.dumps(
                {
                    "version": 2,
                    "source": "phase_only_recon",
                    "p0": p0,
                    "p1": p1,
                    "score": score,
                    "n": len(sub),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        logs.append(
            f"轻量 SMILE 相位: F2=({p0:g}, {p1:g}) score={score:.2f}"
            "(已写 phase.json,正式重构复用)"
        )
        return p0, p1

    def _display_phase_search(
        self,
        experiment: Experiment,
        work: Path,
        logs: list[str],
    ) -> tuple[float, float, float] | None:
        """显示层相位搜索(0.2.96,nmrDraw 思路):在复型重构数据上做对称性
        评分,无需 Hilbert/额外后端。返回 (p0, p1, score);无干净信号峰
        返回 None。

        0.2.98:3D 平面文件为「第一轴实/虚交错」实型存储(nmrglue 读成翻倍
        实型),此前直接当复型旋转/评分是错误约定——现用 read_pipe_complex
        拆包复型后再搜索;2D recon.ft1 为 nmrglue 直接可读的复型(直接维
        在 axis 0)。

        0.2.199-补17:3D 的 nus3d_rc 平面为「每直接维点一个平面」(168 个,
        平面内是 (F1,F2) 间接轴)——直接维是**平面序号**,不是平面内轴;
        此前沿 axis 0(F1 相关,434 点)评分导致评分面平、相位搜索结果
        无意义(实测 sampleK:返回 (0,25) score=28.3 被门槛拒绝)。现改
        为把平面堆叠后沿 axis=-1(直接维平面轴)评分。
        """
        try:
            import nmrglue as ng

            from core.data.pipe_io import read_pipe_complex
            from core.optimization.phase_search import (
                search_direct_phase_on_spectrum,
            )

            if experiment.ndim >= 3:
                plane_dir = work / "nus3d_rc"
                paths = sorted(plane_dir.glob("test*.ft1"))
                if not paths:
                    return None
                # 0.2.199-补17:平面 = 直接维点,不得子采样(直接维分辨率
                # 必须完整;168 平面 × 434×219 × 8B ≈ 127MB,可接受)
                # 0.2.199-补29:按首平面 FDFILECOUNT 截断,陈旧 test*.ft1
                # (旧运行残留)不参与,避免直接维轴混入垃圾平面
                try:
                    first_dic = ng.pipe.read(str(paths[0]))[0]
                    count = int(float(first_dic.get("FDFILECOUNT") or 0))
                except (TypeError, ValueError):
                    count = 0
                if count > 0:
                    paths = paths[:count]
                arrays = [read_pipe_complex(path) for path in paths]
                arr = (
                    np.stack(arrays, axis=-1)
                    if len(arrays) > 1
                    else arrays[0]
                )
                logs.append(
                    f"显示层相位搜索: 3D 复型平面 {len(arrays)} 个全部参与"
                    f"(直接维=平面序号,axis=-1)"
                )
                search_axis = -1
            else:
                recon = work / "nus2d" / "recon.ft1"
                if not recon.is_file():
                    return None
                _dic, data = ng.pipe.read(str(recon))
                arr = np.asarray(data)
                search_axis = 0
            est = search_direct_phase_on_spectrum(
                arr,
                axis=search_axis,
                metric="symmetry",
                cancel=cancel_requested,
            )
            if est is None:
                logs.append("显示层相位搜索:无干净信号峰")
                return None
            logs.append(
                f"显示层相位搜索: F2=({est[0]:.1f}, {est[1]:.1f}) "
                f"score={est[2]:.1f}"
            )
            return est
        except Exception as exc:  # noqa: BLE001
            logs.append(f"显示层相位搜索失败: {exc}")
            return None

    def _apply_direct_phase(
        self,
        experiment: Experiment,
        work: Path,
        p0: float,
        p1: float,
        logs: list[str],
        progress: Callable[[str], None] | None = None,
    ) -> bool:
        """最后一步填相位:对复型重构数据施加直接维相位后重跑 stage-2
        finalize(便宜,非 SMILE),终谱带正确直接维相位。

        0.2.98:旋转结果写入副本(nus3d_rc_ph/ 或 recon_ph.ft1)而不是原地
        改写源平面——源平面保持 PS(0,0) 复型供后续复用/重搜;3D 平面为
        第一轴实/虚交错实型存储,读取前必须 read_pipe_complex 拆包复型。

        0.2.199-补17:3D 直接维 = 平面序号(每直接维点一个平面),相位斜坡
        按平面序号逐元素乘(p0 + p1·k/(n-1)),不再旋转平面内轴。
        """
        try:
            if progress is not None:
                progress("应用直接维相位(recon 平面旋转)")
            import nmrglue as ng

            from core.data.pipe_io import read_pipe_complex

            if experiment.ndim >= 3:
                plane_dir = work / "nus3d_rc"
                paths = sorted(plane_dir.glob("test*.ft1"))
                if not paths or not paths[0].is_file():
                    return False
                out_dir = work / "nus3d_rc_ph"
                if out_dir.exists():
                    shutil.rmtree(out_dir)
                out_dir.mkdir()
                # 0.2.199-补17:直接维 = 平面序号,逐平面元素乘相位斜坡
                arrays = [read_pipe_complex(path) for path in paths]
                stack = np.stack(arrays, axis=-1)  # (i0, i1, k=直接维)
                n = stack.shape[-1]
                k = np.arange(n, dtype=float)
                ramp = np.exp(
                    1j * np.deg2rad(p0 + p1 * k / max(n - 1, 1))
                ).reshape(*([1] * (stack.ndim - 1)), n)
                rotated = stack * ramp
                for path, plane in zip(paths, np.moveaxis(rotated, -1, 0)):
                    dic, _data = ng.pipe.read(str(path))
                    ng.pipe.write(
                        str(out_dir / path.name),
                        dic,
                        plane.astype(np.complex64),
                        overwrite=True,
                    )
                planes = "nus3d_rc_ph/test%04d.ft1"
            else:
                recon = work / "nus2d" / "recon.ft1"
                if not recon.is_file():
                    return False
                dic, data = ng.pipe.read(str(recon))
                arr = np.asarray(data)
                n = arr.shape[0]
                k = np.arange(n, dtype=float)
                ramp = np.exp(
                    1j * np.deg2rad(p0 + p1 * k / max(n - 1, 1))
                ).reshape(n, *([1] * (arr.ndim - 1)))
                rot = arr * ramp
                out_file = work / "nus2d" / "recon_ph.ft1"
                ng.pipe.write(
                    str(out_file),
                    dic,
                    rot.astype(np.complex64),
                    overwrite=True,
                )
                planes = "nus2d/recon_ph.ft1"
            resp = self.finalize_nus(
                experiment, work_dir=work, planes=planes
            )
            if not resp.get("success"):
                logs.append(f"finalize 重渲失败: {resp.get('message')}")
                return False
            return True
        except Exception as exc:  # noqa: BLE001
            logs.append(f"应用直接维相位失败: {exc}")
            return False

    def _search_direct_phase(
        self,
        work: Path,
        fid_files: Path | list[Path],
        logs: list[str],
        min_gain: float = 0.02,
        is_nus: bool = False,
        n_f1: int = 0,
        n_f2: int = 1,
        is_1d: bool = False,
    ) -> tuple[float, float]:
        """直接维相位搜索:直接维 FT 谱上 (p0, p1) 频域搜索,结果缓存 phase.json。

        0.2.88:从「原始 FID p1 共识(p0 恒 0)」升级为「直接维 FT 谱频域
        搜索」:增量 i 的直接维相位 = 公共相位 + ω1·t1(i)(t1 调制,
        t1(0)=0);p1 用多峰迹线相位集中度拟合取中位数(t1 只是逐峰常数
        偏置,不影响斜坡),p0 锚定首条有峰的迹线(增量 0)的峰相位圆均值
        取反(PS 校正约定),±180 消歧取正峰解——直接维公共 p0 不再丢失。
        估计在 PS 应用尺寸上做(p1 为跨轴总度数,与填零尺寸无关;
        0.2.199-补29dq NUS 直接维默认 2×TD,内存不足护栏降 1×TD),
        p1 语义与脚本 PS 一致,无需缩放。fid_files 支持单个文件或切片列表。

        0.2.91(NUS 自动路径):有 nuslist + 切片时优先走「非均匀 DFT 最强
        峰相位」(core.optimization.phase_search.nus_direct_phase)——沿增量
        对最强直接峰复值做 NU-DFT,在真实 F1/F2 频率处 t1 调制精确抵消,
        峰相位 = φ(k*),直接维 (p0, p1) 校正可靠,无需人工确认;失败回退
        逐切片锚定。n_f1/n_f2 为间接维网格尺寸(effective_td)。
        """
        phase_file = work / "phase.json"
        if phase_file.is_file():
            try:
                data = json.loads(phase_file.read_text(encoding="utf-8"))
                if data.get("version") != 2 or (
                    is_1d and data.get("source") != "1d_self"
                ):
                    raise ValueError("旧版缓存(算法已更新),需重搜")
                logs.append(
                    f"直接维相位(缓存): p0={data['p0']:g} p1={data['p1']:g}"
                )
                return float(data["p0"]), float(data["p1"])
            except (OSError, TypeError, ValueError, KeyError):
                pass  # 缓存损坏/旧版则重新搜索
        paths = [fid_files] if isinstance(fid_files, Path) else list(fid_files)
        if not paths:
            return 0.0, 0.0
        # 0.2.91:NUS 自动路径——非均匀 DFT 最强峰相位(用全部切片,无需人工确认)
        if is_nus and n_f1 > 0:
            nuslist_file = work / "nuslist"
            if nuslist_file.is_file():
                try:
                    import nmrglue as ng

                    from core.optimization.phase_search import nus_direct_phase

                    points = read_nuslist(nuslist_file)
                    fids: list[np.ndarray] = []
                    for path in paths:
                        _dic, fid = ng.pipe.read(str(path))
                        arr = np.asarray(fid)
                        if arr.ndim < 1 or arr.shape[-1] < 8:
                            continue
                        fids.append(arr.reshape(-1, arr.shape[-1]))
                    if fids and len(fids) == len(points):
                        est = nus_direct_phase(
                            np.concatenate(fids, axis=0),
                            points,
                            n_f1,
                            n_f2,
                        )
                        if est is not None:
                            p0, p1, score, gain, kstar = est
                            phase_file.write_text(
                                json.dumps(
                                    {
                                        "version": 2,
                                        "source": "direct_nudft",
                                        "p0": p0,
                                        "p1": p1,
                                        "score": score,
                                        "gain": gain,
                                        "n": int(len(fids)),
                                        "kstar": kstar,
                                    },
                                    indent=2,
                                ),
                                encoding="utf-8",
                            )
                            if score < 2.0:
                                logs.append(
                                    f"直接维相位信息弱(相干 SNR="
                                    f"{score:.2f} < 2),保持 p0=p1=0"
                                )
                                return 0.0, 0.0
                            logs.append(
                                f"直接维相位搜索(NU-DFT): p0={p0:g} "
                                f"p1={p1:g} (相干 SNR={score:.2f}, "
                                f"峰 k*={kstar}, {len(fids)} 切片)"
                            )
                            return p0, p1
                except Exception as exc:  # noqa: BLE001
                    logs.append(
                        f"直接维相位搜索(NU-DFT)失败,回退逐切片: {exc}"
                    )
        # 逐切片回退:均匀子采样 ≤16
        if len(paths) > 16:
            index = np.linspace(0, len(paths) - 1, 16).astype(int)
            paths = [paths[i] for i in index]
        try:
            import nmrglue as ng

            rows: list[np.ndarray] = []
            for path in paths:
                _dic, fid = ng.pipe.read(str(path))
                arr = np.asarray(fid)
                n_points = arr.shape[-1] if arr.ndim >= 1 else 0
                if n_points < 8:
                    continue
                if is_nus:
                    zf_size = None  # NUS 直接维搜索用原始 FID 尺寸(不填零)
                else:
                    zf_size = 1
                    while zf_size < 2 * n_points:
                        zf_size *= 2
                traces = direct_ft_traces(
                    arr,
                    zf_size=zf_size,
                    sp_off=0.45,
                    sp_end=0.95,
                    sp_pow=1,
                )
                rows.append(traces.reshape(-1, traces.shape[-1]))
            if not rows:
                logs.append("直接维相位搜索:无可用切片,保持 p0=p1=0")
                return 0.0, 0.0
            spectra = np.concatenate(rows, axis=0)
            if is_1d:
                # 0.2.199-补29gk: 1D 窗宽,group-delay 引入显著线性相位,须先定
                # p1 再定 p0(2D/3D 窄窗 p1 影响小,仍走 search_direct_spectrum_phase,
                # 见 D-2026-09-05 用户口径)。用对称性 (p0,p1) 联合搜索与旧
                # p0-only 结果择优:若对称性主峰吸收不及旧结果(如 1H 以水峰
                # 为主),回退旧结果,保证窄谱不回归。
                est_old = search_direct_spectrum_phase(spectra)
                est_sym = search_direct_phase_on_spectrum(
                    spectra, axis=-1, metric="symmetry",
                    coarse_p0_step=15.0, radius=12, min_windows=3,
                    prefer_p1_zero=False, sign_mode="uniform",
                )
                cands: list[tuple[float, float, float, float, float]] = []
                if est_old is not None:
                    cands.append(
                        (
                            dominant_absorption_ratio(spectra[0], est_old[0], 0.0),
                            est_old[0], 0.0, float(est_old[2]), float(est_old[3]),
                        )
                    )
                if est_sym is not None:
                    cands.append(
                        (
                            dominant_absorption_ratio(
                                spectra[0], float(est_sym[0]), float(est_sym[1])
                            ),
                            float(est_sym[0]), float(est_sym[1]),
                            float(est_sym[2]), 0.0,
                        )
                    )
                if not cands:
                    logs.append("1D 相位搜索:无可用结果,保持 p0=p1=0")
                    return 0.0, 0.0
                _da, p0, p1, score, gain = max(cands, key=lambda c: c[0])
                # 0.2.199-补29gk(用户:峰要向上而不是向下吸收):主峰为负则
                # p0 翻转 180,保证吸收峰向上(正)。
                p0 = orient_dominant_positive(spectra[0], p0, p1)
                phase_file.write_text(
                    json.dumps(
                        {
                            "version": 2,
                            "source": "1d_self",
                            "p0": p0,
                            "p1": p1,
                            "score": score,
                            "gain": gain,
                            "n": int(spectra.shape[0]),
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                logs.append(f"1D 相位搜索: p0={p0:g} p1={p1:g} (score={score:.3f})")
                return p0, p1
            est = search_direct_spectrum_phase(spectra)
            if est is None:
                logs.append("直接维相位搜索:直接维谱无信号,保持 p0=p1=0")
                return 0.0, 0.0
            p0, p1, score, gain = est
            phase_file.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "p0": p0,
                        "p1": p1,
                        "score": score,
                        "gain": gain,
                        "n": int(spectra.shape[0]),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            if (gain < min_gain or score < 0.55) and not is_1d:
                logs.append(
                    f"直接维相位信息弱(gain={gain:.3f}, score={score:.3f}),"
                    "保持 p0=p1=0"
                )
                return 0.0, 0.0
            logs.append(
                f"直接维相位搜索: p0={p0:g} p1={p1:g} "
                f"(score={score:.3f}, gain={gain:.3f}, {spectra.shape[0]} 迹线"
                + (", 1D 弱信号采用估计值)" if is_1d else ")")
            )
            return p0, p1
        except Exception as exc:  # noqa: BLE001
            logs.append(f"直接维相位搜索失败(回退 p0=p1=0): {exc}")
            return 0.0, 0.0

    def _convert(
        self,
        runtime: CshRuntime,
        experiment: Experiment,
        raw: Path,
        work: Path,
        fid_com_overrides: dict[str, str] | None = None,
    ) -> tuple[bool, list[str]]:
        logs: list[str] = []
        is_nus = experiment.sampling.mode is SamplingMode.NUS
        ok = self._convert_dir(
            runtime, experiment, raw, work, is_nus, logs,
            fid_com_overrides=fid_com_overrides,
        )
        if not ok and is_nus:
            logs.append("NUS 转换需要 bruker 原生识别（不做 bruk2pipe 回退）")
        return ok, logs

    def _split_slices(
        self,
        runtime: CshRuntime,
        work: Path,
        in_file: str,
        out_dir: Path,
        shift_hz: float,
        logs: list[str],
    ) -> bool:
        """把单文件 fid 拆成 3D 平面切片（旧切片合并时代遗留工具）。

        0.2.199-补16 起转换/合并均用单文件,不再调用;保留供旧工作目录
        兼容与调试。out_dir 已有切片时跳过拆分(避免重复/失败)。"""
        out_dir.mkdir(parents=True, exist_ok=True)
        if _slice_candidates(out_dir, Path(in_file).stem):
            existing = _slice_candidates(out_dir, Path(in_file).stem)
            logs.append(
                f"切片 {out_dir.name}: 已有切片式输出({len(existing)} 个),跳过拆分"
            )
            return True
        pattern = f"{out_dir.relative_to(work)}/test%03d.fid"
        cmd = ["xyz2pipe", "-in", in_file, "-x"]
        if shift_hz:
            cmd += ["|", "nmrPipe", "-fn", "PS", "-rs", f"{shift_hz}Hz"]
        cmd += ["|", "pipe2xyz", "-out", pattern, "-x"]
        result = runtime.run(cmd, cwd=str(work), timeout=600)
        logs.append(f"切片 {out_dir.name}: rc={result.returncode}")
        if result.returncode != 0 or not list(out_dir.glob("test*.fid")):
            return False
        logs.append(f"切片数: {len(list(out_dir.glob('test*.fid')))}")
        return True

    def _merge_single_fid(
        self,
        runtime: CshRuntime,
        work: Path,
        n_segments: int,
        dataset_id: str,
        logs: list[str],
    ) -> bool:
        """addNMR 逐对时域合并各段单文件全网格 fid（参考实验室 2ndAdd.com）。"""
        merged = work / "merged"
        if merged.exists():
            shutil.rmtree(merged)  # 幂等:旧合并(如 generate_fid 产物)先清
        merged.mkdir(parents=True)
        shutil.copy2(
            work / "seg_001" / f"{dataset_id}.fid", merged / f"{dataset_id}.fid"
        )
        for index in range(2, n_segments + 1):
            tmp = work / "merge_tmp"
            if tmp.exists():
                shutil.rmtree(tmp)
            tmp.mkdir(parents=True)
            result = runtime.run(
                [
                    "addNMR",
                    "-in1",
                    f"seg_{index:03d}/{dataset_id}.fid",
                    "-in2",
                    f"merged/{dataset_id}.fid",
                    "-out",
                    f"merge_tmp/{dataset_id}.fid",
                    "-verb",
                ],
                cwd=str(work),
                timeout=600,
            )
            logs.append(f"addNMR seg_{index:03d}: rc={result.returncode}")
            if result.returncode != 0 or not (
                tmp / f"{dataset_id}.fid"
            ).is_file():
                return False
            shutil.rmtree(merged)
            shutil.move(str(tmp), str(merged))
        logs.append(f"多段合并完成 → merged/{dataset_id}.fid（{n_segments} 段）")
        return True

    def _merge_slices(
        self,
        runtime: CshRuntime,
        work: Path,
        n_segments: int,
        logs: list[str],
    ) -> bool:
        """addNMR 逐对时域合并各段切片式 fid(merged/fid/test%03d.fid)。

        0.2.199-补28:任一段为切片时,全部归一为切片后按切片索引逐对合并;
        与 _merge_single_fid 对称(单文件合并走 merged/{dataset_id}.fid)。
        0.2.199-补29ct:补实现(此前调用点存在但方法缺失,生成 FID 报
        AttributeError)。"""
        merged = work / "merged"
        if merged.exists():
            shutil.rmtree(merged)  # 幂等:旧合并先清
        merged.mkdir(parents=True)
        (merged / "fid").mkdir(parents=True)
        first_slices = sorted((work / "seg_001" / "fid").glob("test*.fid"))
        if not first_slices:
            return False
        for sl in first_slices:
            shutil.copy2(sl, merged / "fid" / sl.name)
        for index in range(2, n_segments + 1):
            seg_slices = sorted(
                (work / f"seg_{index:03d}" / "fid").glob("test*.fid")
            )
            if len(seg_slices) != len(first_slices):
                logs.append(
                    f"段 {index} 切片数({len(seg_slices)})与首段"
                    f"({len(first_slices)})不一致"
                )
                return False
            tmp = work / "merge_tmp"
            if tmp.exists():
                shutil.rmtree(tmp)
            (tmp / "fid").mkdir(parents=True)
            ok = True
            for sl in seg_slices:
                result = runtime.run(
                    [
                        "addNMR",
                        "-in1",
                        f"seg_{index:03d}/fid/{sl.name}",
                        "-in2",
                        f"merged/fid/{sl.name}",
                        "-out",
                        f"merge_tmp/fid/{sl.name}",
                        "-verb",
                    ],
                    cwd=str(work),
                    timeout=600,
                )
                if result.returncode != 0 or not (
                    tmp / "fid" / sl.name
                ).is_file():
                    ok = False
                    break
            logs.append(
                f"addNMR seg_{index:03d} 切片合并: "
                f"rc={'ok' if ok else 'fail'}"
            )
            if not ok:
                return False
            shutil.rmtree(merged)
            shutil.move(str(tmp), str(merged))
        logs.append(f"多段切片合并完成 → merged/fid/（{n_segments} 段）")
        return True

    def _convert_segments(
        self,
        runtime: CshRuntime,
        experiment: Experiment,
        work: Path,
        shifts: list[float],
        fid_com_overrides: dict[str, str] | None = None,
    ) -> tuple[bool, list[str]]:
        """多段实验：每段 bruker 转换 → 频移(可选) → 合并。

        参考实验室 1stfid.com/2ndAdd.com 流程：分段实验是同一实验按采样时间拆
        段,各段 bruker -AUTO 生成的 fid.com 参数一致(均用 NusTD 网格;acqu2s
        TD 只反映各自采样点数),逐段独立转换后 addNMR 逐对时域合并。
        0.2.199-补28:兼容 bruker 自动输出——各段全为单文件时合并单文件
        (merged/{dataset_id}.fid);任一段为切片时全部归一为切片再合并
        (merged/fid/test%03d.fid)。每段可带 -rs 频移(单文件直接 PS -rs;
        切片式经 xyz2pipe 流 PS -rs)。
        """
        logs: list[str] = []
        is_nus = experiment.sampling.mode is SamplingMode.NUS
        if is_nus:
            # 0.2.124:坏点在源头 ser/nuslist 删除并备份(用户要求,幂等)
            self._clean_source_nus(
                experiment, [Path(seg) for seg in experiment.segments], logs
            )
        slice_mode = False
        for index, seg_dir in enumerate(experiment.segments, start=1):
            seg_work = work / f"seg_{index:03d}"
            seg_work.mkdir(parents=True, exist_ok=True)
            if not self._convert_dir(
                runtime, experiment, Path(seg_dir), seg_work, is_nus, logs,
                fid_com_overrides=fid_com_overrides,
            ):
                return False, logs + [f"数据段 {index}（{Path(seg_dir).name}）转换失败"]
            seg_single = (
                seg_work / f"{experiment.dataset_id}.fid"
            ).is_file()
            if not seg_single:
                slice_mode = True
                logs.append(f"段 {index}: bruker 输出切片式 fid(兼容合并)")
            shift_hz = shifts[index - 1] if index - 1 < len(shifts) else 0.0
            if not shift_hz:
                continue
            if seg_single:
                fid_name = f"{experiment.dataset_id}.fid"
                shifted = seg_work / f"{experiment.dataset_id}_shifted.fid"
                result = runtime.run(
                    [
                        "nmrPipe",
                        "-in",
                        fid_name,
                        "|",
                        "nmrPipe",
                        "-fn",
                        "PS",
                        "-rs",
                        f"{shift_hz}Hz",
                        "-out",
                        shifted.name,
                        "-ov",
                    ],
                    cwd=str(seg_work),
                    timeout=600,
                )
                logs.append(
                    f"段 {index} 频移 -rs {shift_hz}Hz: rc={result.returncode}"
                )
                if result.returncode == 0 and shifted.is_file():
                    shifted.replace(seg_work / fid_name)
            else:
                # 切片式:xyz2pipe 流上 PS -rs,整批重写
                fid_dir = seg_work / "fid"
                shifted_dir = seg_work / "fid_shifted"
                if shifted_dir.exists():
                    shutil.rmtree(shifted_dir)
                result = runtime.run(
                    [
                        "xyz2pipe",
                        "-in",
                        "fid/test%03d.fid",
                        "-x",
                        "|",
                        "nmrPipe",
                        "-fn",
                        "PS",
                        "-rs",
                        f"{shift_hz}Hz",
                        "|",
                        "pipe2xyz",
                        "-out",
                        "fid_shifted/test%03d.fid",
                        "-x",
                    ],
                    cwd=str(seg_work),
                    timeout=600,
                )
                logs.append(
                    f"段 {index} 切片式频移 -rs {shift_hz}Hz: "
                    f"rc={result.returncode}"
                )
                if result.returncode == 0 and list(
                    shifted_dir.glob("test*.fid")
                ):
                    shutil.rmtree(fid_dir)
                    shifted_dir.replace(fid_dir)
        if not slice_mode:
            if not self._merge_single_fid(
                runtime, work, len(experiment.segments),
                experiment.dataset_id, logs,
            ):
                return False, logs + ["多段单文件合并失败"]
        else:
            # 单文件段拆成切片,统一切片合并
            for index in range(1, len(experiment.segments) + 1):
                seg_work = work / f"seg_{index:03d}"
                if (seg_work / f"{experiment.dataset_id}.fid").is_file():
                    if not self._split_slices(
                        runtime,
                        seg_work,
                        f"{experiment.dataset_id}.fid",
                        seg_work / "fid",
                        0.0,
                        logs,
                    ):
                        return False, logs + [f"数据段 {index} 切片失败"]
            if not self._merge_slices(
                runtime, work, len(experiment.segments), logs
            ):
                return False, logs + ["多段切片合并失败"]
        return True, logs

    def _clean_source_nus(
        self,
        experiment: Experiment,
        raw_dirs: list[Path],
        logs: list[str],
    ) -> tuple[int, list[tuple[int, ...]], bool]:
        """源头清理 NUS 坏点(用户要求,0.2.124):删除发生在最开始的 ser 文件,
        而不是生成 fid 上清零。按 nuslist 行整块删除 ser(每行字节 =
        ser_size / nuslist 行数,须整除),同步清理 nuslist;删除前备份
        ser/nuslist 为 .bak(仅首次,幂等);os.replace 断硬/软链接,外部
        原件不受影响。多段 raw_dirs 校验规则(0.2.199-补29cx 起按类型):
        - 重复实验叠加(NUS 同点,repeat_nus):跨段同点为正常叠加,保留;
          仅段内重复/越界为坏点;
        - 分段/混合/未知:跨段重复点视为坏点剔除(0.2.124 原规则)。

        返回 (有效点数, 坏点列表, 是否实际执行了源头删除);ser 缺失或
        大小不能按行整除时跳过源头删除并返回 removed=False(调用方回退
        到生成 FID 清零)。
        """
        per_dir: list[list[tuple[int, ...]]] = []
        entries: list[tuple[int, int, tuple[int, ...]]] = []
        for dir_idx, raw_dir in enumerate(raw_dirs):
            nuslist_path = Path(raw_dir) / "nuslist"
            pts = (
                [tuple(p) for p in read_nuslist(nuslist_path)]
                if nuslist_path.is_file()
                else []
            )
            per_dir.append(pts)
            for row_idx, point in enumerate(pts):
                entries.append((dir_idx, row_idx, point))
        if not entries:
            return 0, [], False
        points = [entry[2] for entry in entries]
        repeat = False
        if len(raw_dirs) > 1:
            try:
                from core.data.bruker_reader import classify_segment_kind

                repeat = (
                    classify_segment_kind([Path(p) for p in raw_dirs])
                    == "repeat_nus"
                )
            except Exception:  # noqa: BLE001 - 分类失败保守按分段
                repeat = False
        if repeat:
            # 逐段校验(段内重复/越界),跨段同点全部保留后去重
            valid, bad, reasons = [], [], {}
            for dir_pts in per_dir:
                v, b, r = _validate_nus_points(dir_pts, experiment)
                valid += v
                for point in b:
                    if point not in bad:
                        bad.append(point)
                    reasons.setdefault(point, r.get(point, []))
            seen: set[tuple[int, ...]] = set()
            unique: list[tuple[int, ...]] = []
            for point in valid:
                if point not in seen:
                    seen.add(point)
                    unique.append(point)
            valid = unique
        else:
            valid, bad, reasons = _validate_nus_points(points, experiment)
        if not bad:
            return len(valid), [], False
        kept = set(valid)
        drop_by_dir: dict[int, set[int]] = {}
        for dir_idx, row_idx, point in entries:
            if point not in kept:
                drop_by_dir.setdefault(dir_idx, set()).add(row_idx)
        removed_any = False
        for dir_idx, drop_rows in drop_by_dir.items():
            if not drop_rows:
                continue
            raw_dir = Path(raw_dirs[dir_idx])
            nuslist_path = raw_dir / "nuslist"
            data_file = raw_dir / "ser"
            if not data_file.is_file():
                logs.append(
                    f"⚠ 坏点需从源头 ser 删除,但 {raw_dir.name}/ser 缺失,"
                    "回退为生成 FID 清理"
                )
                continue
            n_rows = len(per_dir[dir_idx])
            data_size = data_file.stat().st_size
            if n_rows <= 0 or data_size % n_rows != 0:
                logs.append(
                    f"⚠ {raw_dir.name}/ser 大小 {data_size} 不能按 nuslist "
                    f"{n_rows} 行整除(ser 行数与 nuslist 点数可能不一致),"
                    "回退为生成 FID 清理,未自动处理"
                )
                continue
            # 0.2.195:ser 字节随采样参数变化(直接维 TD 补齐 + 字长 + 冗余
            # 数),按参数推导并校验,避免按错误块大小删除造成重构错位
            layout = _ser_point_layout(experiment, data_size, n_rows)
            if layout is None:
                td0 = effective_td(experiment)[0] if effective_td(experiment) else "?"
                logs.append(
                    f"⚠ {raw_dir.name}/ser 布局无法按采样参数确定"
                    f"(直接维 TD={td0}, 每点 {data_size // n_rows} 字节;"
                    "冗余数不一致或字长未知),回退为生成 FID 清理,未自动处理"
                )
                continue
            row_bytes, _vec_bytes, _redundancy = layout
            try:
                backup = raw_dir / "ser.bak"
                if not backup.exists():
                    shutil.copy2(data_file, backup)
                    logs.append(
                        f"源头 ser 已备份 → {raw_dir.name}/ser.bak({data_size} 字节)"
                    )
                raw = data_file.read_bytes()
                kept_bytes = b"".join(
                    raw[i * row_bytes : (i + 1) * row_bytes]
                    for i in range(n_rows)
                    if i not in drop_rows
                )
                tmp = raw_dir / "ser.tmp"
                tmp.write_bytes(kept_bytes)
                os.replace(tmp, data_file)  # 断硬/软链接,外部原件不受影响
                nus_backup = raw_dir / "nuslist.bak"
                if not nus_backup.exists():
                    shutil.copy2(nuslist_path, nus_backup)
                text = "".join(
                    " ".join(str(v) for v in p) + "\n"
                    for i, p in enumerate(per_dir[dir_idx])
                    if i not in drop_rows
                )
                nus_tmp = raw_dir / "nuslist.tmp"
                nus_tmp.write_text(text, encoding="utf-8", newline="\n")
                os.replace(nus_tmp, nuslist_path)
                removed_any = True
                logs.append(
                    f"源头清理 {raw_dir.name}:nuslist {n_rows} → "
                    f"{n_rows - len(drop_rows)} 行,ser {data_size} → "
                    f"{len(kept_bytes)} 字节(备份 .bak)"
                )
            except OSError as exc:  # noqa: BLE001 - 清理失败不阻断
                logs.append(f"⚠ {raw_dir.name} 源头清理失败({exc}),回退生成 FID 清理")
        for point in bad:
            logs.append(
                f"⚠ 检测到采样坏点 {point}:{'、'.join(reasons.get(point, []) or ['未知'])},"
                "已从源头 ser/nuslist 删除(备份 .bak)"
            )
        return len(valid), bad, removed_any

    def _write_merged_nuslist(
        self,
        work: Path,
        segment_dirs: list[Path],
        experiment: Experiment,
        logs: list[str],
    ) -> tuple[int, list[tuple[int, ...]]]:
        """合并各段 nuslist 并检测采样坏点（越界/重复），返回 (有效点数, 坏点列表)。

        分段采样可能有个别「写错并采错」的点（如 cc/63 的 27 2350：F1 索引远超
        网格上限）。坏点从合并 nuslist 剔除并由调用方清理对应 FID，同时以 ⚠ 提示用户。
        0.2.199-补29cx:重复实验叠加(NUS 同点)跨段同点为正常叠加,合并去重
        保留唯一点;分段/未知保守按原规则(跨段重复视为坏点剔除)。
        """
        all_points: list[tuple[int, ...]] = []
        for seg_dir in segment_dirs:
            nuslist_path = Path(seg_dir) / "nuslist"
            if nuslist_path.is_file():
                all_points += [tuple(p) for p in read_nuslist(nuslist_path)]
        repeat = False
        if len(segment_dirs) > 1:
            try:
                from core.data.bruker_reader import classify_segment_kind

                repeat = (
                    classify_segment_kind([Path(p) for p in segment_dirs])
                    == "repeat_nus"
                )
            except Exception:  # noqa: BLE001 - 分类失败保守按分段
                repeat = False
        if repeat:
            valid, bad, reasons = [], [], {}
            for seg_dir in segment_dirs:
                nl_path = Path(seg_dir) / "nuslist"
                seg_pts = (
                    [tuple(p) for p in read_nuslist(nl_path)]
                    if nl_path.is_file()
                    else []
                )
                v, b, r = _validate_nus_points(seg_pts, experiment)
                valid += v
                for point in b:
                    if point not in bad:
                        bad.append(point)
                    reasons[point] = r.get(point, [])
            seen: set[tuple[int, ...]] = set()
            unique: list[tuple[int, ...]] = []
            for point in valid:
                if point not in seen:
                    seen.add(point)
                    unique.append(point)
            valid = unique
        else:
            valid, bad, reasons = _validate_nus_points(all_points, experiment)
        for point in bad:
            logs.append(
                f"⚠ 检测到采样坏点 {point}:{'、'.join(reasons.get(point, []) or ['未知'])},"
                f"已从合并 nuslist 丢弃"
            )
        text = "".join(" ".join(str(v) for v in point) + "\n" for point in valid)
        (work / "nuslist").write_text(text, encoding="utf-8")
        logs.append(f"合并 nuslist：{len(valid)} 采样点（坏点 {len(bad)}）")
        return len(valid), bad

    def _zero_bad_point_fid(
        self,
        work: Path,
        bad_points: list[tuple[int, ...]],
        logs: list[str],
        in_file: str | None = None,
        dataset_id: str | None = None,
    ) -> None:
        """清理坏点对应的 FID 数据(0.2.124 起仅作源头删除不可行时的回退)。

        有效网格内的坏点(越界重复等)对应 States 双实行(2y, 2y+1):
        3D 在切片 test{z:03d}.fid / {dataset_id}{z:03d}.fid、2D 在单 fid
        文件的这些行清零;越界点无对应槽位,记录即可。
        """
        if not bad_points:
            return
        import nmrglue as ng

        if in_file and "%" in in_file:
            base = work / in_file.replace("%03d", "{z:03d}").replace("%04d", "{z:03d}")
        else:
            base = work / (in_file or f"{Path(in_file or '').name}") if in_file else None
        slice_dir = work / "merged" / "fid"
        if not slice_dir.is_dir():
            slice_dir = work / "fid"
        for point in bad_points:
            if not point:
                continue
            f2 = int(point[0])
            f1 = int(point[1]) if len(point) > 1 else None
            targets: list[Path] = []
            if f1 is not None and slice_dir.is_dir():
                # States 布局:复点 (f2, f1) 落在切片 2*f1+1 / 2*f1+2 的
                # 行 2*f2 / 2*f2+1(0.2.195 修正:此前误用 test{f1},清零
                # 会打在错误切片上造成合并/重构错误)
                for zz in (2 * f1 + 1, 2 * f1 + 2):
                    t = slice_dir / f"test{zz:03d}.fid"
                    if not t.is_file() and dataset_id:
                        t = slice_dir / f"{dataset_id}{zz:03d}.fid"
                    if t.is_file():
                        targets.append(t)
                if not targets:
                    logs.append(
                        f"⚠ 坏点 {point}:越界,无对应切片,合并 FID 无需清理"
                    )
                    continue
            elif f1 is None and base is not None and base.is_file():
                targets = [base]
            if not targets:
                logs.append(f"⚠ 坏点 {point}:无对应 FID 文件,无需清理")
                continue
            for target in targets:
                try:
                    dic, data = ng.pipe.read(str(target))
                    arr = np.asarray(data)
                    if arr.ndim < 2:
                        continue
                    rows = [r for r in (2 * f2, 2 * f2 + 1) if r < arr.shape[0]]
                    if not rows:
                        logs.append(f"⚠ 坏点 {point}:行越界,无需清理")
                        continue
                    arr[rows, :] = 0
                    ng.pipe.write(str(target), dic, arr, overwrite=True)
                    logs.append(
                        f"⚠ 坏点 {point}:对应 FID 增量已清零"
                        f"({target.name} 行 {rows})"
                    )
                except Exception as exc:  # noqa: BLE001 - 清理失败不阻断
                    logs.append(f"⚠ 坏点 {point}:FID 清理失败 {exc}")

    def _clean_work_nuslist(
        self, work: Path, experiment: Experiment, logs: list[str]
    ) -> tuple[int, list[tuple[int, ...]]]:
        """校验并清理工作目录 nuslist(单 NUS 数据):坏点剔除 + ⚠ 提示。
        返回 (有效点数, 坏点列表)。"""
        nuslist_path = work / "nuslist"
        if not nuslist_path.is_file():
            return 0, []
        points = [tuple(p) for p in read_nuslist(nuslist_path)]
        valid, bad, reasons = _validate_nus_points(points, experiment)
        if bad:
            text = "".join(" ".join(str(v) for v in point) + "\n" for point in valid)
            nuslist_path.write_text(text, encoding="utf-8")
            for point in bad:
                logs.append(
                    f"⚠ 检测到采样坏点 {point}:{'、'.join(reasons.get(point, []) or ['未知'])},"
                    f"已从 nuslist 丢弃并清理对应 FID"
                )
            logs.append(f"nuslist 清理: {len(points)} → {len(valid)} 采样点(坏点 {len(bad)})")
        return len(valid), bad

    # ------------------------------------------------------------------ 处理

    def _process(
        self,
        runtime: CshRuntime,
        experiment: Experiment,
        plan: ProcessingPlan,
        work: Path,
        *,
        in_file: str | None = None,
        direct_phase: dict[str, tuple[float, float]] | None = None,
        baseline: dict[str, dict[str, Any]] | None = None,
        window: dict[str, dict[str, Any]] | None = None,
        zero_fill: dict[str, dict[str, Any]] | None = None,
        linewidth_hz: dict[str, float] | None = None,
        points_per_line: float = DEFAULT_POINTS_PER_LINE,
        extract: bool = True,
        ext_lo: str = "10.5",
        ext_hi: str = "6.5",
        sampling: dict[str, Any] | None = None,
        progress: Callable[[str], None] | None = None,
        out_file: str | None = None,
        script_name: str | None = None,
        keep_direct_complex: bool = False,
        keep_complex_all: bool = False,
        preview_axis: str | None = None,
        direct_poly_time: bool = False,
    ) -> tuple[bool, list[str], Path]:
        """生成并执行 NMRPipe 处理管道（输出 ft2/ft3）。"""
        logs: list[str] = []
        ext = {1: "ft1", 2: "ft2"}.get(experiment.ndim, "ft3")
        in_file = in_file or f"{experiment.dataset_id}.fid"
        # 0.2.199-补29ez:显式 out_file = 中间渲染(预览/joint/候选),统一写入
        # work/_intermediate(可被内存盘接管);终跑默认名保持原位。
        _render_out = out_file not in (None, "")
        out_file = out_file or f"{experiment.dataset_id}.{ext}"
        if _render_out:
            _render_dir = work / INTERMEDIATE_SUBDIR
            _render_dir.mkdir(parents=True, exist_ok=True)
            out_file = f"{INTERMEDIATE_SUBDIR}/{out_file}"
        if preview_axis:
            script = generate_preview_script(
                experiment,
                plan,
                in_file=in_file,
                out_file=out_file,
                preview_axis=preview_axis,
                fixed_phases=direct_phase,
                baseline=baseline,
                window=window,
                zero_fill=zero_fill,
                ext_lo=ext_lo,
                ext_hi=ext_hi,
                extract=extract,
                sampling=sampling,
            )
        else:
            script = generate_process_script(
                experiment,
                plan,
                in_file=in_file,
                out_file=out_file,
                direct_phase=direct_phase,
                baseline=baseline,
                window=window,
                zero_fill=zero_fill,
                linewidth_hz=linewidth_hz,
                points_per_line=points_per_line,
                extract=extract,
                ext_lo=ext_lo,
                ext_hi=ext_hi,
                sampling=sampling,
                keep_direct_complex=keep_complex_all or keep_direct_complex,
                complex_axes=(frozenset(dim.logical_axis for dim in experiment.dimensions)
                              if keep_complex_all else None),
                direct_poly_time=direct_poly_time,
            )
        process_com = work / (
            script_name or f"{experiment.dataset_id}_process.com"
        )
        process_com.write_text(script, encoding="utf-8", newline="\n")
        run_result = runtime.run(
            ["csh", process_com.name],
            cwd=str(work),
            timeout=7200,
            on_line=(lambda line: progress(line) if progress else None),
        )
        logs.append(f"process.com: rc={run_result.returncode}")
        spectrum = work / out_file
        if (
            run_result.returncode != 0
            or not spectrum.is_file()
            or spectrum.stat().st_size == 0
        ):
            return False, logs + [f"未生成 {out_file}"], spectrum
        logs.append(f"谱图 → {spectrum}")
        return True, logs, spectrum

