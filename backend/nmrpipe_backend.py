"""NMRPipe 后端：bruker -AUTO 转换 + NMRPipe 处理管道 + NUS SMILE 重构（Linux/csh）。

NMRPipe 语义只存在于本层（backend/）与生成的脚本；上层通过 ProcessingBackend 协议调用。
查找路径：csh 环境 ``source ~/.cshrc; which nmrPipe`` 优先（用户要求），可显式指定 bin 目录。

重要设计（真实数据验证）：NUS 时 bruker -AUTO 原生识别正确——按 NusTD 取间接维点数、
生成 nusExpand/ser_full/mask.fid 的 NUS 工作流，输出单文件 test.fid。
因此**不做切片追加**，SMILE 直接从单文件走直接维处理（Phase 3）。
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
from core.planning.processing_plan import ProcessingPlan


@dataclass
class NMRPipeBackend:
    """NMRPipe 实现（Linux：bruker -AUTO + fid.com + NMRPipe 管道 + SMILE）。"""

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
        """均匀采样：转换 + NMRPipe 处理管道（NUS 请用 reconstruct_nus）。"""
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
        converted, convert_logs = self._convert(runtime, experiment, raw, work)
        logs += convert_logs
        if not converted:
            return {"success": False, "message": "Bruker→NMRPipe 转换失败", "logs": logs}
        processed, process_logs, spectrum = self._process(runtime, experiment, plan, work)
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
        """NUS 数据：bruker 原生转换（test.fid/mask.fid）+ SMILE 重构输出终谱。"""
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
            in_file=fid_file.name,
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
        run_result = runtime.run(
            ["nice", "-n", "10", "csh", nus_com.name],
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

    def _convert(
        self,
        runtime: CshRuntime,
        experiment: Experiment,
        raw: Path,
        work: Path,
    ) -> tuple[bool, list[str]]:
        """bruker -AUTO → patch_fid_com → 执行 → 移动 fid。

        NUS 时信任 bruker 原生识别（nusExpand/mask/单文件 test.fid），
        不做切片追加；bruker 失败时仅均匀采样走 bruk2pipe 回退。
        """
        logs: list[str] = []
        fid_com = raw / "fid.com"
        is_nus = experiment.sampling.mode is SamplingMode.NUS
        bruker_ok = False
        bruker = find_tool("bruker", self._bin_dir())
        if bruker is not None:
            result = runtime.run(["bruker", "-AUTO"], cwd=str(raw), timeout=120)
            logs.append(f"bruker -AUTO: rc={result.returncode}")
            if result.returncode == 0 and fid_com.is_file():
                text = fid_com.read_text(encoding="utf-8", errors="replace")
                patched, corrections = patch_fid_com(text, experiment)
                for correction in corrections:
                    logs.append(f"参数修正: {correction}")
                # LF 行尾必须：CRLF 会让 csh 的 \ 续行失效
                fid_com.write_text(patched, encoding="utf-8", newline="\n")
                run_result = runtime.run(["csh", "fid.com"], cwd=str(raw), timeout=900)
                logs.append(f"fid.com: rc={run_result.returncode}")
                bruker_ok = run_result.returncode == 0
        if not bruker_ok:
            if is_nus:
                return False, logs + ["NUS 转换需要 bruker 原生识别（不做 bruk2pipe 回退）"]
            logs.append("回退：使用内置 bruk2pipe 参数转换")
            script = generate_convert_script(experiment)
            convert_script = work / f"{experiment.dataset_id}_convert.com"
            convert_script.write_text(script, encoding="utf-8", newline="\n")
            run_result = runtime.run(
                ["csh", convert_script.name], cwd=str(raw), timeout=600
            )
            logs.append(f"convert.com: rc={run_result.returncode}")
            if run_result.returncode != 0:
                return False, logs
        source = raw / "test.fid"
        if not source.is_file():
            return False, logs + ["未找到 test.fid"]
        shutil.move(str(source), work / f"{experiment.dataset_id}.fid")
        logs.append(f"{experiment.dataset_id}.fid 已就位")
        if is_nus:
            mask = raw / "mask.fid"
            if mask.is_file():
                shutil.copy2(mask, work / "mask.fid")
                logs.append("mask.fid 已复制到工作目录")
        return True, logs

    def _process(
        self,
        runtime: CshRuntime,
        experiment: Experiment,
        plan: ProcessingPlan,
        work: Path,
    ) -> tuple[bool, list[str], Path]:
        """生成并执行 NMRPipe 处理管道（输出 ft2/ft3）。"""
        logs: list[str] = []
        ext = "ft3" if experiment.ndim >= 3 else "ft2"
        in_file = f"{experiment.dataset_id}.fid"
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
