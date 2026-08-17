"""NMRPipe 后端：bruker -AUTO 转换 + NMRPipe 处理管道 + NUS SMILE 重构（Linux/csh）。

NMRPipe 语义只存在于本层（backend/）与生成的脚本；上层通过 ProcessingBackend 协议调用。
查找路径：csh 环境 ``source ~/.cshrc; which nmrPipe`` 优先（用户要求），可显式指定 bin 目录。

重要设计（真实数据验证）：
- NUS 时 bruker -AUTO 原生识别正确（按 NusTD 取间接维、nusExpand/ser_full/mask.fid、
  单文件 test.fid），单数据集不做切片追加，SMILE 直接从单文件走直接维处理；
- 多段实验（同实验拆多个数据集，如 61/63/65/67）：参考实验室脚本
  （Desktop/data/脚本/1stfid.com + 2ndAdd.com）——每段 bruker 转换后拆成 fid 切片，
  addNMR 逐对时间域合并，再统一 SMILE 重构；支持每段可选频移（-rs Hz，防场飘）。
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from backend.base import BackendCapabilities
from backend.bruker_workflow import patch_fid_com, patch_nus_expand_count
from backend.config import (
    resolve_ext_hi,
    resolve_ext_lo,
    resolve_nthread,
    resolve_points_per_line,
)
from backend.nmrpipe_finder import find_nmrpipe_bin, find_tool
from backend.runtime import CshRuntime
from backend.script_generator import (
    DEFAULT_POINTS_PER_LINE,
    _as_bool,
    effective_td,
    expand_baseline,
    generate_2d_nus_script,
    generate_3d_nus_script,
    generate_convert_script,
    generate_nus_finalize_script,
    generate_process_script,
    select_smile_params,
    zero_fill_plan,
    zero_fill_report,
)
from core.data.internal_data_model import Experiment, SamplingMode
from core.data.nus_reader import merge_nuslists, read_nuslist
from core.optimization.phase_search import (
    direct_ft_traces,
    search_direct_phase_on_spectrum,
    search_direct_spectrum_phase,
)
from core.planning.processing_plan import ProcessingPlan


def enforce_smile_thread_guardrail(nthread: int, grid_points: int) -> tuple[int, str]:
    """SMILE 线程护栏（D006）：间接网格 >5000 点时线程数上限 2。

    2026-08-11 sampleM 事故：宽窗口 SMILE 满核曾致宿主断电；大网格强制
    2 线程。返回 (线程数, 日志)；未超限时日志为空串。
    """
    if grid_points > 5000 and nthread > 2:
        return 2, f"大网格 {grid_points}：SMILE 线程数限制为 2（原 {nthread}）"
    return nthread, ""


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
        (暴力参考/选中相位写回生产用,准确性验证见 workflow.phase_optimize)。
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
        _progress("开始转换 fid")
        if experiment.segments:
            merged_ready = (
                (work / "merged" / "fid").is_dir()
                and list((work / "merged" / "fid").glob("test*.fid"))
            )
            in_file = "merged/fid/test%03d.fid"
            if merged_ready:
                logs.append("复用已转换 fid(跳过转换)")
            else:
                converted, convert_logs = self._convert_segments(
                    runtime, experiment, work, []
                )
                logs += convert_logs
        else:
            in_file = f"{experiment.dataset_id}.fid"
            if (work / in_file).is_file():
                logs.append("复用已转换 fid(跳过转换)")
            else:
                converted, convert_logs = self._convert(runtime, experiment, raw, work)
                logs += convert_logs
                # 0.2.81:bruker 切片式输出(fid/test%03d.fid,三维 TD 正确时),
                # process 同样走切片流(与 NUS 一致)
                if not (work / in_file).is_file():
                    slice_dir = work / "fid"
                    if slice_dir.is_dir() and list(slice_dir.glob("test*.fid")):
                        in_file = "fid/test%03d.fid"
                        logs.append("使用 bruker 切片式 fid（fid/test%03d.fid,流式处理）")
        if not converted:
            return {"success": False, "message": "Bruker→NMRPipe 转换失败", "logs": logs}
        proc_params = dict(params or {})
        sampling = proc_params.get("sampling") or {}
        # sampling.auto_phase=False → 关闭直接维自动相位(PS 保持 plan 默认 0/0)
        # 0.2.88:检查移到搜索前(此前在搜索之后才置位,实际关不掉自动相位)
        if sampling.get("auto_phase") is False:
            direct_phase_search = False
        direct_phase: dict[str, tuple[float, float]] | None = None
        if direct_phase_override:
            direct_phase = dict(direct_phase_override)
            logs.append(f"直接维相位覆盖: {direct_phase}")
        elif direct_phase_search:
            _progress("开始相位优化(直接维)")
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
                    work, phase_inputs, logs, is_nus=False
                )
                direct_axis = "F2" if experiment.ndim == 2 else "F3"
                direct_phase = {direct_axis: (p0, p1)}
                _progress(
                    f"完成相位优化(直接维 {direct_axis} "
                    f"p0={p0:g}° p1={p1:g}°)"
                )
        extract = _as_bool(proc_params.get("extract", True))
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
            },
        }

    def convert_to_fid(
        self,
        experiment: Experiment,
        data_dir: Path | str,
        progress: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        """独立阶段:bruker -AUTO/fid.com 把原始数据转换为 NMRPipe fid(不生成谱)。

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
        if progress is not None:
            progress("开始转换 fid")
        if experiment.segments:
            converted, convert_logs = self._convert_segments(
                runtime, experiment, work, []
            )
            logs += convert_logs
            fid_path = work / "merged" / "fid"
        else:
            converted, convert_logs = self._convert(runtime, experiment, raw, work)
            logs += convert_logs
            fid_path = work / f"{experiment.dataset_id}.fid"
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
                "work_dir": str(work),
                "fid_path": str(fid_path),
            },
        }

    def reconstruct_nus(
        self,
        experiment: Experiment,
        params: dict[str, Any] | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        """NUS 数据：bruker 原生转换（单段/多段合并）+ SMILE 重构输出终谱。"""
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
            merged_fid = work / "merged" / "fid"
            merged_ready = (
                merged_fid.is_dir()
                and list(merged_fid.glob("test*.fid"))
                and (work / "nuslist").is_file()
            )
            if not merged_ready:
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
                nuslist_count = self._write_merged_nuslist(
                    work, experiment.segments, experiment, logs
                )
            else:
                logs.append("复用已合并切片（跳过转换/合并）")
                nuslist_count = len(
                    (work / "nuslist").read_text(encoding="utf-8").splitlines()
                )
            in_file = "merged/fid/test%03d.fid"
        else:
            fid_file = work / f"{experiment.dataset_id}.fid"
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
            nuslist_count = len(
                (work / "nuslist").read_text(encoding="utf-8").splitlines()
            )
            # 0.2.80:bruker 切片式输出(fid/test%03d.fid)优先,否则单文件
            slice_dir = work / "fid"
            if slice_dir.is_dir() and list(slice_dir.glob("test*.fid")):
                in_file = "fid/test%03d.fid"
                logs.append("使用 bruker 切片式 fid（fid/test%03d.fid,流式处理）")
            else:
                in_file = fid_file.name

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
                nuslist_count=nuslist_count,
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
        smile_xq3 = float(params.get("smile_xq3", 2.0))
        smile_scaling = bool(params.get("smile_scaling", True))
        smile_report = int(params.get("smile_report", 1))
        nthread = resolve_nthread(params.get("nthread"))
        # 安全护栏（2026-08-11 sampleM 事故）：大网格 SMILE 满核曾致宿主断电，
        # 间接网格 >5000 点时线程数上限 2
        grid_points = int(td[1]) * (int(td[2]) if len(td) > 2 else 1)
        nthread, guard_log = enforce_smile_thread_guardrail(nthread, grid_points)
        if guard_log:
            logs.append(guard_log)
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

        out_file = f"{experiment.dataset_id}.{ext}"
        script = script_fn(
            experiment,
            in_file=in_file,
            nuslist="nuslist",
            out_file=out_file,
            nthread=nthread,
            nuslist_count=nuslist_count,
            ext_lo=ext_lo,
            ext_hi=ext_hi,
            nsigma=nsigma,
            thresh=thresh,
            smile_xq3=smile_xq3,
            smile_scaling=smile_scaling,
            smile_report=smile_report,
            direct_phase=smile_phase,
            extract=extract,
            baseline=baseline,
            zero_fill=zf_plan,
            linewidth_hz=linewidth_hz,
            points_per_line=points_per_line,
            sampling=sampling,
        )
        nus_com = work / f"{experiment.dataset_id}_nus.com"
        nus_com.write_text(script, encoding="utf-8", newline="\n")
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
                if abs(p1) > 20.0:
                    logs.append(
                        f"显示层相位 p1={p1:g}° 幅值异常(>20°),归零"
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
                "smile_xq3": smile_xq3,
                "smile_scaling": smile_scaling,
                "smile_report": smile_report,
                "nthread": nthread,
                "direct_phase": [direct_p0, direct_p1],
                "linewidth_hz": linewidth_hz,
                "points_per_line": points_per_line,
                "sampling": dict(sampling),
            },
        }

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
    ) -> dict[str, Any]:
        """从 SMILE 重构平面做间接维 FT 定稿(逐维相位候选,不重跑 SMILE)。

        phases:{轴 -> (p0, p1)},缺省 0;供逐维相位优化(用户方案)。
        planes:重构平面输入覆盖(默认 nus3d_rc/test%04d.ft1 或
        nus2d/recon.ft1;显示层填相位用 nus3d_rc_ph/ 副本)。
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
        out_ext = "ft3" if experiment.ndim >= 3 else "ft2"
        out_file = out_file or f"{experiment.dataset_id}.{out_ext}"
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

    def phase_ht_candidate_axis(
        self,
        spectrum_path: Path | str,
        axis: int,
        p0: float,
        p1: float,
        *,
        work_dir: Path | str | None = None,
        out_file: str | None = None,
        timeout: float = 600.0,
    ) -> dict[str, Any]:
        """把指定轴转置到管道轴后跑 nmrPipe PS -ht,生成该维候选显示谱。

        axis 为谱数组轴(0=F1,1=F2,2=F3);输出为转置布局,目标轴在最后轴。
        """
        import nmrglue as ng

        bin_dir = self._bin_dir()
        if bin_dir is None:
            return {"success": False, "message": "未找到 nmrPipe", "logs": []}
        work = Path(work_dir) if work_dir else Path(spectrum_path).parent
        work.mkdir(parents=True, exist_ok=True)
        src = Path(spectrum_path)
        if src.parent.resolve() != work.resolve():
            shutil.copy2(src, work / src.name)
        _dic, data = ng.pipe.read(str(src))
        ndim = int(_dic.get("FDDIMCOUNT", 2) or 2)
        # 目标轴 -> xyz2pipe 输出向量标志;最后轴(直接管道轴)无需转置
        if axis == ndim - 1:
            cmd = ["nmrPipe", "-in", src.name, "|", "nmrPipe", "-fn", "PS",
                   "-p0", f"{p0:g}", "-p1", f"{p1:g}", "-ht", "-di",
                   "-out", "", "-ov"]
        else:
            if ndim == 2:
                vec = "-y"
            else:
                vec = {0: "-z", 1: "-y", 2: "-x"}.get(axis, "-y")
            out_base = out_file or (
                f"{src.stem}_ax{axis}_ph_{p0:g}_{p1:g}.{src.suffix.lstrip('.')}"
            )
            cmd = ["xyz2pipe", "-in", src.name, vec,
                   "|", "nmrPipe", "-fn", "PS",
                   "-p0", f"{p0:g}", "-p1", f"{p1:g}", "-ht", "-di",
                   "|", "pipe2xyz", "-out", out_base, "-x"]
        out_name = out_file or (
            f"{src.stem}_ax{axis}_ph_{p0:g}_{p1:g}.{src.suffix.lstrip('.')}"
        )
        if axis == ndim - 1:
            cmd[-2] = out_name
        runtime = CshRuntime()
        result = runtime.run(cmd, cwd=str(work), timeout=timeout)
        logs = [f"nmrPipe PS -ht(axis {axis}): rc={result.returncode}"]
        out = work / out_name
        if result.returncode != 0 or not out.is_file() or out.stat().st_size == 0:
            return {"success": False, "message": "nmrPipe PS -ht(axis) 失败", "logs": logs}
        logs.append(f"候选显示谱 → {out}")
        return {"success": True, "spectrum_path": str(out), "logs": logs}

    def phase_ht_candidate(
        self,
        spectrum_path: Path | str,
        p0: float,
        p1: float,
        *,
        work_dir: Path | str | None = None,
        out_file: str | None = None,
        timeout: float = 600.0,
    ) -> dict[str, Any]:
        """用 nmrPipe PS -ht 对实型谱生成一个候选显示谱(轻后端)。

        这等价于 nmrDraw 在显示层的相位旋转 + 虚部重建,评分必须用该谱,
        不要用 numpy 模拟旋转。当前作用于谱文件管道轴(直接维)。
        """
        bin_dir = self._bin_dir()
        if bin_dir is None:
            return {"success": False, "message": "未找到 nmrPipe", "logs": []}
        work = Path(work_dir) if work_dir else Path(spectrum_path).parent
        work.mkdir(parents=True, exist_ok=True)
        src = Path(spectrum_path)
        if src.parent.resolve() != work.resolve():
            shutil.copy2(src, work / src.name)
        out_name = out_file or (
            f"{src.stem}_ph_{p0:g}_{p1:g}.{src.suffix.lstrip('.')}"
        )
        runtime = CshRuntime()
        result = runtime.run(
            [
                "nmrPipe", "-in", src.name,
                "|", "nmrPipe", "-fn", "PS",
                "-p0", f"{p0:g}", "-p1", f"{p1:g}", "-ht", "-di",
                "-out", out_name, "-ov",
            ],
            cwd=str(work),
            timeout=timeout,
        )
        logs = [f"nmrPipe PS -ht candidate: rc={result.returncode}"]
        out = work / out_name
        if result.returncode != 0 or not out.is_file() or out.stat().st_size == 0:
            return {"success": False, "message": "nmrPipe PS -ht 失败", "logs": logs}
        logs.append(f"候选显示谱 → {out}")
        return {"success": True, "spectrum_path": str(out), "logs": logs}

    def hilbert_spectrum(
        self,
        spectrum_path: Path | str,
        *,
        work_dir: Path | str | None = None,
        out_file: str | None = None,
        timeout: float = 600.0,
    ) -> dict[str, Any]:
        """对实型终谱跑 nmrPipe HT,重建虚部得到复型显示谱(轻后端)。

        nmrDraw 的显示层调相即使用 HT 重建虚部,因此简单途径以该复型谱
        为对象,与进阶版的真实后端 PS 保持同一数学运算。
        """
        bin_dir = self._bin_dir()
        if bin_dir is None:
            return {"success": False, "message": "未找到 nmrPipe", "logs": []}
        work = Path(work_dir) if work_dir else Path(spectrum_path).parent
        src = Path(spectrum_path)
        if src.parent.resolve() != work.resolve():
            shutil.copy2(src, work / src.name)
        out_name = out_file or f"{src.stem}_ht.{src.suffix.lstrip('.')}"
        runtime = CshRuntime()
        result = runtime.run(
            [
                "nmrPipe", "-in", src.name,
                "|", "nmrPipe", "-fn", "HT",
                "-out", out_name, "-ov",
            ],
            cwd=str(work),
            timeout=timeout,
        )
        logs = [f"nmrPipe HT: rc={result.returncode}"]
        out = work / out_name
        if result.returncode != 0 or not out.is_file() or out.stat().st_size == 0:
            return {"success": False, "message": "nmrPipe HT 失败", "logs": logs}
        logs.append(f"复型显示谱 → {out}")
        return {"success": True, "spectrum_path": str(out), "logs": logs}

    # ------------------------------------------------------------------ 转换

    def _finalize_converted_fid(
        self,
        raw_dir: Path,
        dest_work: Path,
        dataset_id: str,
        logs: list[str],
    ) -> bool:
        """把 bruker 转换产物归位:单文件 test.fid 或切片式 fid/test%03d.fid。

        acqu3s TD 修正后 bruker 输出切片式(每 F1 一个 fid 切片);
        0.2.80 起两种形式都接受,切片式保留为 work/fid/ 供流式处理。
        """
        source = raw_dir / "test.fid"
        if source.is_file():
            shutil.move(str(source), dest_work / f"{dataset_id}.fid")
            logs.append(f"{dataset_id}.fid 已就位（{raw_dir.name}）")
            return True
        slice_dir = raw_dir / "fid"
        slices = sorted(slice_dir.glob("test*.fid")) if slice_dir.is_dir() else []
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
    ) -> bool:
        """在 raw_dir 中 bruker -AUTO → patch fid.com → 执行 → 移动 test.fid 到 dest_work。

        NUS 时信任 bruker 原生识别（nusExpand/mask/单文件 test.fid）；bruker 失败时
        仅均匀采样走 bruk2pipe 回退。转换后清理 ser_full（可再生，避免占空间）。
        """
        fid_com = raw_dir / "fid.com"
        bruker_ok = False
        bruker = find_tool("bruker", self._bin_dir())
        if bruker is not None:
            result = runtime.run(["bruker", "-AUTO"], cwd=str(raw_dir), timeout=120)
            logs.append(f"bruker -AUTO ({raw_dir.name}): rc={result.returncode}")
            if result.returncode == 0 and fid_com.is_file():
                text = fid_com.read_text(encoding="utf-8", errors="replace")
                patched, corrections = patch_fid_com(text, experiment)
                if is_nus:
                    nuslist_path = raw_dir / "nuslist"
                    if nuslist_path.is_file():
                        nuslist_count = len(read_nuslist(nuslist_path))
                        patched, nus_corrections = patch_nus_expand_count(
                            patched, nuslist_count
                        )
                        corrections += nus_corrections
                for correction in corrections:
                    logs.append(f"参数修正: {correction}")
                # LF 行尾必须：CRLF 会让 csh 的 \ 续行失效
                fid_com.write_text(patched, encoding="utf-8", newline="\n")
                run_result = runtime.run(["csh", "fid.com"], cwd=str(raw_dir), timeout=900)
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
                ["csh", convert_script.name], cwd=str(raw_dir), timeout=600
            )
            logs.append(f"convert.com: rc={run_result.returncode}")
            if run_result.returncode != 0:
                return False
        if not self._finalize_converted_fid(
            raw_dir, dest_work, experiment.dataset_id, logs
        ):
            return False
        # SMILE 只需 nuslist;ser_full/mask.fid 等中间产物删除省空间
        for stale in ("ser_full", "mask.fid"):
            stale_path = raw_dir / stale
            if stale_path.is_file():
                stale_path.unlink()
        return True

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
        td = effective_td(experiment)
        grid_points = int(td[1]) * (int(td[2]) if len(td) > 2 else 1)
        nthread, guard_log = enforce_smile_thread_guardrail(
            nthread, grid_points
        )
        if guard_log:
            logs.append(guard_log)
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
        out_light = f"{experiment.dataset_id}_light.{ext}"
        script = script_fn(
            experiment,
            in_file=in_file,
            nuslist="nuslist",
            out_file=out_light,
            nthread=nthread,
            nuslist_count=len(sub),
            ext_lo=ext_lo,
            ext_hi=ext_hi,
            nsigma=5.0,
            thresh=0.95,
            smile_xq3=2.0,
            smile_scaling=True,
            smile_report=1,
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
            est = search_direct_phase_on_spectrum(np.asarray(data))
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
        """显示层相位搜索(0.2.96,nmrDraw 思路):在复型重构平面上做对称性
        评分(直接维在 axis 0),无需 Hilbert/额外后端。返回 (p0, p1, score);
        无干净信号峰返回 None。

        0.2.98:3D 平面文件为「第一轴实/虚交错」实型存储(nmrglue 读成翻倍
        实型),此前直接当复型旋转/评分是错误约定——现用 read_pipe_complex
        拆包复型后再搜索;2D recon.ft1 为 nmrglue 直接可读的复型。3D 按
        间接维增量均布子采样(≤8 个平面,直接维 1×TD),控制搜索成本。
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
                if len(paths) > 8:
                    index = np.linspace(0, len(paths) - 1, 8).astype(int)
                    paths = [paths[i] for i in index]
                arrays = [read_pipe_complex(path) for path in paths]
                arr = (
                    np.stack(arrays, axis=-1)
                    if len(arrays) > 1
                    else arrays[0]
                )
                logs.append(
                    f"显示层相位搜索: 3D 复型平面 {len(arrays)} 个"
                    f"(增量子采样,直接维 axis 0)"
                )
            else:
                recon = work / "nus2d" / "recon.ft1"
                if not recon.is_file():
                    return None
                _dic, data = ng.pipe.read(str(recon))
                arr = np.asarray(data)
            est = search_direct_phase_on_spectrum(
                arr, axis=0, metric="symmetry"
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
    ) -> bool:
        """最后一步填相位:旋转复型重构平面直接维(axis 0)后重跑 stage-2
        finalize(便宜,非 SMILE),终谱带正确直接维相位。

        0.2.98:旋转结果写入副本(nus3d_rc_ph/ 或 recon_ph.ft1)而不是原地
        改写源平面——源平面保持 PS(0,0) 复型供后续复用/重搜;3D 平面为
        第一轴实/虚交错实型存储,旋转前必须 read_pipe_complex 拆包复型。
        """
        try:
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
                for path in paths:
                    dic, _data = ng.pipe.read(str(path))
                    arr = read_pipe_complex(path)
                    n = arr.shape[0]  # 直接维在 axis 0
                    k = np.arange(n, dtype=float)
                    ramp = np.exp(
                        1j * np.deg2rad(p0 + p1 * k / max(n - 1, 1))
                    ).reshape(n, *([1] * (arr.ndim - 1)))
                    rot = arr * ramp
                    ng.pipe.write(
                        str(out_dir / path.name),
                        dic,
                        rot.astype(np.complex64),
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
    ) -> tuple[float, float]:
        """直接维相位搜索:直接维 FT 谱上 (p0, p1) 频域搜索,结果缓存 phase.json。

        0.2.88:从「原始 FID p1 共识(p0 恒 0)」升级为「直接维 FT 谱频域
        搜索」:增量 i 的直接维相位 = 公共相位 + ω1·t1(i)(t1 调制,
        t1(0)=0);p1 用多峰迹线相位集中度拟合取中位数(t1 只是逐峰常数
        偏置,不影响斜坡),p0 锚定首条有峰的迹线(增量 0)的峰相位圆均值
        取反(PS 校正约定),±180 消歧取正峰解——直接维公共 p0 不再丢失。
        估计在 PS 应用尺寸上做(NUS 直接维 1×TD、均匀 2×TD),p1 语义与
        脚本 PS 一致,无需缩放。fid_files 支持单个文件或切片列表。

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
                if data.get("version") != 2:
                    raise ValueError("旧版缓存(0.2.87 前 p0 恒 0),需重搜")
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
                    zf_size = None  # NUS 直接维 PS 在 1×TD 上应用
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
            if gain < min_gain or score < 0.55:
                logs.append(
                    f"直接维相位信息弱(gain={gain:.3f}, score={score:.3f}),"
                    "保持 p0=p1=0"
                )
                return 0.0, 0.0
            logs.append(
                f"直接维相位搜索: p0={p0:g} p1={p1:g} "
                f"(score={score:.3f}, gain={gain:.3f}, {spectra.shape[0]} 迹线)"
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
    ) -> tuple[bool, list[str]]:
        logs: list[str] = []
        is_nus = experiment.sampling.mode is SamplingMode.NUS
        ok = self._convert_dir(runtime, experiment, raw, work, is_nus, logs)
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
        """把单文件 test.fid 拆成 3D 平面切片（合并前必需，参考实验室 1stfid.com）。"""
        out_dir.mkdir(parents=True, exist_ok=True)
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

    def _merge_slices(
        self, runtime: CshRuntime, work: Path, n_segments: int, logs: list[str]
    ) -> bool:
        """addNMR 逐对时间域合并各段切片（参考实验室 2ndAdd.com）。"""
        merged = work / "merged" / "fid"
        shutil.copytree(work / "seg_001" / "fid", merged)
        for index in range(2, n_segments + 1):
            tmp = work / "merge_tmp"
            if tmp.exists():
                shutil.rmtree(tmp)
            tmp.mkdir(parents=True)
            result = runtime.run(
                [
                    "addNMR",
                    "-in1",
                    f"seg_{index:03d}/fid/test%03d.fid",
                    "-in2",
                    "merged/fid/test%03d.fid",
                    "-out",
                    "merge_tmp/test%03d.fid",
                    "-verb",
                ],
                cwd=str(work),
                timeout=600,
            )
            logs.append(f"addNMR seg_{index:03d}: rc={result.returncode}")
            if result.returncode != 0 or not list(tmp.glob("test*.fid")):
                return False
            shutil.rmtree(merged)
            shutil.move(str(tmp), str(merged))
        logs.append(f"多段合并完成 → merged/fid/（{n_segments} 段）")
        return True

    def _convert_segments(
        self,
        runtime: CshRuntime,
        experiment: Experiment,
        work: Path,
        shifts: list[float],
    ) -> tuple[bool, list[str]]:
        """多段实验：每段 bruker 转换 → 拆切片 → addNMR 合并。"""
        logs: list[str] = []
        is_nus = experiment.sampling.mode is SamplingMode.NUS
        for index, seg_dir in enumerate(experiment.segments, start=1):
            seg_work = work / f"seg_{index:03d}"
            seg_work.mkdir(parents=True, exist_ok=True)
            if not self._convert_dir(
                runtime, experiment, Path(seg_dir), seg_work, is_nus, logs
            ):
                return False, logs + [f"数据段 {index}（{Path(seg_dir).name}）转换失败"]
            shift_hz = shifts[index - 1] if index - 1 < len(shifts) else 0.0
            if not self._split_slices(
                runtime,
                seg_work,
                f"{experiment.dataset_id}.fid",
                seg_work / "fid",
                shift_hz,
                logs,
            ):
                return False, logs + [f"数据段 {index} 切片失败"]
        if not self._merge_slices(runtime, work, len(experiment.segments), logs):
            return False, logs + ["多段切片合并失败"]
        return True, logs

    def _write_merged_nuslist(
        self,
        work: Path,
        segment_dirs: list[Path],
        experiment: Experiment,
        logs: list[str],
    ) -> int:
        points = merge_nuslists([Path(d) / "nuslist" for d in segment_dirs])
        # 3D 校验：nuslist 列为复点索引（上限 NusTD//2），越界点属数据录入错误，丢弃并警告
        if experiment.ndim >= 3:
            td = effective_td(experiment)
            bounds = [int(td[1]) // 2, int(td[2]) // 2] if len(td) > 2 else []
            valid: list[tuple[int, ...]] = []
            dropped = 0
            for point in points:
                if len(point) >= 2 and (
                    point[0] >= bounds[0] or point[1] >= bounds[1]
                ):
                    dropped += 1
                else:
                    valid.append(point)
            if dropped:
                logs.append(f"nuslist 越界点 {dropped} 个已丢弃（网格 {bounds}）")
            points = valid
        text = "".join(" ".join(str(v) for v in point) + "\n" for point in points)
        (work / "nuslist").write_text(text, encoding="utf-8")
        logs.append(f"合并 nuslist：{len(points)} 采样点")
        return len(points)

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
    ) -> tuple[bool, list[str], Path]:
        """生成并执行 NMRPipe 处理管道（输出 ft2/ft3）。"""
        logs: list[str] = []
        ext = "ft3" if experiment.ndim >= 3 else "ft2"
        in_file = in_file or f"{experiment.dataset_id}.fid"
        out_file = out_file or f"{experiment.dataset_id}.{ext}"
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
            keep_direct_complex=keep_direct_complex,
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

