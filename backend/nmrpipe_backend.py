"""NMRPipe 后端：bruker -AUTO 转换 + fid.com 修补 + NMRPipe 处理管道（Linux/csh）。

NMRPipe 语义只存在于本层（backend/）与生成的脚本；上层通过 ProcessingBackend 协议调用。
查找路径：csh 环境 ``source ~/.cshrc; which nmrPipe`` 优先（用户要求），可显式指定 bin 目录。
3D NUS 规避：acqu3s TD=1 时按 NusTD 修补 fid.com，并强制把输出拆成 fid 切片。
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
from backend.script_generator import generate_convert_script, generate_process_script
from core.data.internal_data_model import Experiment, SamplingMode
from core.planning.processing_plan import ProcessingPlan

_SLICE_SPLIT = (
    "\n# NMRForge: split 3D NUS fid into plane slices\n"
    "mkdir -p fid\n"
    "xyz2pipe -in ./test.fid -x | pipe2xyz -out ./fid/test%03d.fid -z\n"
)


@dataclass
class NMRPipeBackend:
    """NMRPipe 实现（Linux：bruker -AUTO + fid.com + NMRPipe 管道）。"""

    nmrpipe_bin: str = ""
    work_dir: str = ""
    capabilities: BackendCapabilities = field(
        default_factory=lambda: BackendCapabilities(provider="nmrpipe")
    )

    def _bin_dir(self) -> Path | None:
        return find_nmrpipe_bin(self.nmrpipe_bin)

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
        """执行转换（bruker -AUTO / 回退 bruk2pipe）+ NMRPipe 处理管道。"""
        bin_dir = self._bin_dir()
        if bin_dir is None:
            return {
                "success": False,
                "message": "未找到 nmrPipe（csh: which nmrPipe）",
                "logs": [],
            }
        runtime = CshRuntime()
        raw = Path(experiment.source_path)
        work = (
            Path(self.work_dir)
            if self.work_dir
            else raw.parent / f"{experiment.dataset_id}.nmrpipe"
        )
        work.mkdir(parents=True, exist_ok=True)
        logs: list[str] = []

        converted, convert_logs = self._convert(runtime, experiment, raw, work)
        logs += convert_logs
        if not converted:
            return {"success": False, "message": "Bruker→NMRPipe 转换失败", "logs": logs}
        if experiment.sampling.mode is SamplingMode.NUS:
            return {
                "success": False,
                "message": "NUS 数据转换完成，SMILE 重构待 Phase 3 接入",
                "logs": logs,
            }
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
        self, experiment: Experiment, params: dict[str, Any]
    ) -> dict[str, Any]:
        raise NotImplementedError("Phase 3: 实现 SMILE 重建")

    def _convert(
        self,
        runtime: CshRuntime,
        experiment: Experiment,
        raw: Path,
        work: Path,
    ) -> tuple[bool, list[str]]:
        """bruker -AUTO → patch_fid_com → 执行 → 移动 fid（单文件或切片）。"""
        logs: list[str] = []
        fid_com = raw / "fid.com"
        bruker_ok = False
        bruker = find_tool("bruker", self._bin_dir())
        if bruker is not None:
            result = runtime.run(["bruker", "-AUTO"], cwd=str(raw), timeout=120)
            logs.append(f"bruker -AUTO: rc={result.returncode}")
            if result.returncode == 0 and fid_com.is_file():
                text = fid_com.read_text(encoding="utf-8", errors="replace")
                patched, corrections = patch_fid_com(text, experiment)
                is_3d_nus = (
                    experiment.ndim >= 3
                    and experiment.sampling.mode is SamplingMode.NUS
                )
                if is_3d_nus and "test%" not in patched:
                    patched += _SLICE_SPLIT
                    corrections.append("fid.com 追加切片输出（fid/test%03d.fid）")
                for correction in corrections:
                    logs.append(f"参数修正: {correction}")
                # LF 行尾必须：CRLF 会让 csh 的 \ 续行失效
                fid_com.write_text(patched, encoding="utf-8", newline="\n")
                run_result = runtime.run(["csh", "fid.com"], cwd=str(raw), timeout=600)
                logs.append(f"fid.com: rc={run_result.returncode}")
                bruker_ok = run_result.returncode == 0
        if not bruker_ok:
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
        slices = raw / "fid"
        if slices.is_dir() and list(slices.glob("test*.fid")):
            dest = work / "fid"
            if dest.exists():
                shutil.rmtree(dest)
            shutil.move(str(slices), str(dest))
            logs.append("fid 切片 → work/fid/")
            return True, logs
        source = raw / "test.fid"
        if not source.is_file():
            return False, logs + ["未找到 test.fid / fid 切片"]
        shutil.move(str(source), work / f"{experiment.dataset_id}.fid")
        logs.append(f"{experiment.dataset_id}.fid 已就位")
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
        has_slices = (work / "fid").is_dir()
        in_file = "fid/test%03d.fid" if has_slices else f"{experiment.dataset_id}.fid"
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
