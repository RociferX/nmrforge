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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.base import BackendCapabilities
from backend.bruker_workflow import patch_fid_com, patch_nus_expand_count
from backend.nmrpipe_finder import find_nmrpipe_bin, find_tool
from backend.runtime import CshRuntime
from backend.script_generator import (
    _as_bool,
    effective_td,
    generate_2d_nus_script,
    generate_3d_nus_script,
    generate_convert_script,
    generate_process_script,
    select_smile_params,
)
from core.data.internal_data_model import Experiment, SamplingMode
from core.data.nus_reader import merge_nuslists, read_nuslist
from core.optimization.phase_search import direct_ft_traces, search_phase
from core.planning.processing_plan import ProcessingPlan


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

        converted = True
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
        if not converted:
            return {"success": False, "message": "Bruker→NMRPipe 转换失败", "logs": logs}
        direct_phase: dict[str, tuple[float, float]] | None = None
        if direct_phase_override:
            direct_phase = dict(direct_phase_override)
            logs.append(f"直接维相位覆盖: {direct_phase}")
        elif direct_phase_search:
            fid_for_phase = (
                work / "seg_001" / f"{experiment.dataset_id}.fid"
                if experiment.segments
                else work / f"{experiment.dataset_id}.fid"
            )
            p0, p1 = self._search_direct_phase(work, fid_for_phase, logs)
            direct_axis = "F2" if experiment.ndim == 2 else "F3"
            direct_phase = {direct_axis: (p0, p1)}
        proc_params = dict(params or {})
        extract = _as_bool(proc_params.get("extract", True))
        ext_lo = str(proc_params.get("ext_lo", "11.0"))
        ext_hi = str(proc_params.get("ext_hi", "6.0"))
        processed, process_logs, spectrum = self._process(
            runtime,
            experiment,
            plan,
            work,
            in_file=in_file,
            direct_phase=direct_phase,
            extract=extract,
            ext_lo=ext_lo,
            ext_hi=ext_hi,
        )
        logs += process_logs
        if not processed:
            return {"success": False, "message": "NMRPipe 处理失败", "logs": logs}
        return {
            "success": True,
            "message": "NMRPipe 处理成功",
            "spectrum_path": str(spectrum),
            "logs": logs,
        }

    def convert_to_fid(
        self, experiment: Experiment, data_dir: Path | str
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
        }

    def reconstruct_nus(
        self, experiment: Experiment, params: dict[str, Any] | None = None
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
            in_file = fid_file.name

        direct_p0, direct_p1 = 0.0, 0.0
        override = params.get("direct_phase_override")
        if override is not None:
            direct_p0, direct_p1 = float(override[0]), float(override[1])
            logs.append(f"直接维相位覆盖: p0={direct_p0:g} p1={direct_p1:g}")
        elif bool(params.get("direct_phase_search", True)):
            fid_for_phase = (
                work / "seg_001" / f"{experiment.dataset_id}.fid"
                if experiment.segments
                else work / f"{experiment.dataset_id}.fid"
            )
            direct_p0, direct_p1 = self._search_direct_phase(
                work, fid_for_phase, logs
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
        nthread = int(params.get("nthread", 2))
        # 安全护栏（2026-08-11 sampleM 事故）：大网格 SMILE 满核曾致宿主断电，
        # 间接网格 >5000 点时线程数上限 2
        grid_points = int(td[1]) * (int(td[2]) if len(td) > 2 else 1)
        if grid_points > 5000 and nthread > 2:
            logs.append(f"大网格 {grid_points}：SMILE 线程数限制为 2（原 {nthread}）")
            nthread = 2
        ext_lo = str(params.get("ext_lo", "11.0"))
        ext_hi = str(params.get("ext_hi", "6.0"))
        extract = _as_bool(params.get("extract", True))
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
            direct_phase=(direct_p0, direct_p1),
            extract=extract,
        )
        nus_com = work / f"{experiment.dataset_id}_nus.com"
        nus_com.write_text(script, encoding="utf-8", newline="\n")
        logs.append(
            f"SMILE 重构（{nuslist_count} 采样点，{fraction * 100:.1f}%，"
            f"1H {ext_lo}-{ext_hi} ppm，nSigma={nsigma:g} thresh={thresh:g}）"
        )
        timeout = float(params.get("timeout_s", 3600))
        # 注意：不在 tcsh -c 包装内叠加 nice（实测会让 tcsh 脚本结束后挂起空转）
        run_result = runtime.run(
            ["csh", nus_com.name],
            cwd=str(work),
            timeout=timeout,
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
        logs.append(f"终谱 → {spectrum}")
        return {
            "success": True,
            "message": "SMILE 重构成功",
            "spectrum_path": str(spectrum),
            "logs": logs,
        }

    # ------------------------------------------------------------------ 转换

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
        source = raw_dir / "test.fid"
        if not source.is_file():
            return False
        shutil.move(str(source), dest_work / f"{experiment.dataset_id}.fid")
        logs.append(f"{experiment.dataset_id}.fid 已就位（{raw_dir.name}）")
        # SMILE 只需 nuslist；ser_full/mask.fid 可再生，转换后清理省磁盘
        for stale in ("ser_full", "mask.fid"):
            stale_path = raw_dir / stale
            if stale_path.is_file():
                stale_path.unlink()
        return True

    def _search_direct_phase(
        self,
        work: Path,
        fid_file: Path,
        logs: list[str],
        min_gain: float = 0.02,
    ) -> tuple[float, float]:
        """直接维统计相位搜索（内存内 FT + 全迹统计），结果缓存到 work/phase.json。"""
        phase_file = work / "phase.json"
        if phase_file.is_file():
            data = json.loads(phase_file.read_text(encoding="utf-8"))
            logs.append(f"直接维相位（缓存）: p0={data['p0']:g} p1={data['p1']:g}")
            return float(data["p0"]), float(data["p1"])
        try:
            import nmrglue as ng

            _dic, fid = ng.pipe.read(str(fid_file))
            direct_points = fid.shape[-1]
            zf_size = 1
            while zf_size < 2 * direct_points:
                zf_size *= 2
            traces = direct_ft_traces(
                fid, zf_size=zf_size, sp_off=0.45, sp_end=0.95, sp_pow=1
            )
            p0, p1, score, gain = search_phase(traces)
            phase_file.write_text(
                json.dumps({"p0": p0, "p1": p1, "score": score, "gain": gain}, indent=2),
                encoding="utf-8",
            )
            if gain < min_gain:
                logs.append(
                    f"直接维相位信息弱（gain={gain:.3f} < {min_gain:g}），保持 p1=0"
                )
                return 0.0, 0.0
            logs.append(
                f"直接维相位搜索: p1={p1:g} (score={score:.3f}, gain={gain:.3f})"
            )
            return p0, p1
        except Exception as exc:  # noqa: BLE001
            logs.append(f"直接维相位搜索失败（回退 p0=p1=0）: {exc}")
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
        extract: bool = True,
        ext_lo: str = "11.0",
        ext_hi: str = "6.0",
    ) -> tuple[bool, list[str], Path]:
        """生成并执行 NMRPipe 处理管道（输出 ft2/ft3）。"""
        logs: list[str] = []
        ext = "ft3" if experiment.ndim >= 3 else "ft2"
        in_file = in_file or f"{experiment.dataset_id}.fid"
        out_file = f"{experiment.dataset_id}.{ext}"
        script = generate_process_script(
            experiment,
            plan,
            in_file=in_file,
            out_file=out_file,
            direct_phase=direct_phase,
            extract=extract,
            ext_lo=ext_lo,
            ext_hi=ext_hi,
        )
        process_com = work / f"{experiment.dataset_id}_process.com"
        process_com.write_text(script, encoding="utf-8", newline="\n")
        run_result = runtime.run(
            ["csh", process_com.name], cwd=str(work), timeout=7200
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
