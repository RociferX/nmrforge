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

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.base import BackendCapabilities
from backend.bruker_workflow import patch_fid_com
from backend.nmrpipe_finder import find_nmrpipe_bin, find_tool
from backend.runtime import CshRuntime
from backend.script_generator import (
    effective_td,
    generate_2d_nus_script,
    generate_3d_nus_script,
    generate_convert_script,
    generate_process_script,
    select_smile_params,
)
from core.data.internal_data_model import Experiment, SamplingMode
from core.data.nus_reader import merge_nuslists
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

    def process(self, experiment: Experiment, plan: ProcessingPlan) -> dict[str, Any]:
        """均匀采样：转换（含多段合并）+ NMRPipe 处理管道（NUS 请用 reconstruct_nus）。"""
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

        if experiment.segments:
            converted, convert_logs = self._convert_segments(runtime, experiment, work, [])
            logs += convert_logs
            in_file = "merged/fid/test%03d.fid"
        else:
            converted, convert_logs = self._convert(runtime, experiment, raw, work)
            logs += convert_logs
            in_file = f"{experiment.dataset_id}.fid"
        if not converted:
            return {"success": False, "message": "Bruker→NMRPipe 转换失败", "logs": logs}
        processed, process_logs, spectrum = self._process(
            runtime, experiment, plan, work, in_file=in_file
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
            nuslist_count = self._write_merged_nuslist(work, experiment.segments, logs)
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
        nsigma, thresh = select_smile_params(fraction)
        nthread = int(params.get("nthread", 2))
        ext_lo = str(params.get("ext_lo", 10.5))
        ext_hi = str(params.get("ext_hi", 6.5))
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
        # SMILE 只需 nuslist，mask.fid 不复制（省磁盘）
        ser_full = raw_dir / "ser_full"
        if ser_full.is_file():
            ser_full.unlink()
        return True

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
        cmd += ["|", "pipe2xyz", "-out", pattern, "-z"]
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
        self, work: Path, segment_dirs: list[Path], logs: list[str]
    ) -> int:
        points = merge_nuslists([Path(d) / "nuslist" for d in segment_dirs])
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
    ) -> tuple[bool, list[str], Path]:
        """生成并执行 NMRPipe 处理管道（输出 ft2/ft3）。"""
        logs: list[str] = []
        ext = "ft3" if experiment.ndim >= 3 else "ft2"
        in_file = in_file or f"{experiment.dataset_id}.fid"
        out_file = f"{experiment.dataset_id}.{ext}"
        script = generate_process_script(
            experiment, plan, in_file=in_file, out_file=out_file
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
