"""人工处理路径后端(用户反馈:查看/修改/运行脚本)。

与自动处理对应,集合了此前命令行人工处理的流程:
- 生成 FID:自动阶段先产出 fid.com(backend.convert_to_fid)→ 人工查看内容
  (manual_fid_com)→ 修改 → 运行(csh fid.com,run_manual_fid_com)→ 登记 fid;
- 生成谱图:表格参数(param_schema/render_scripts)或直接脚本编辑
  (manual_scripts 渲染 → 修改)→ 运行 process.com / nus*.com
  (run_manual_spectrum)→ 终谱归位 spectra/ 并登记。

运行复用 backend.runtime.CshRuntime;产物登记 set_data_fid /
set_data_spectrum + WorkflowRun(审计)。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from backend.runtime import CshRuntime
from backend.script_generator import render_scripts
from core.data.bruker_reader import read_dataset
from core.data.internal_data_model import Experiment, SamplingMode
from core.project import ProjectManager
from workflow.stepwise import _register_spectrum


class ManualRunError(Exception):
    """人工处理运行错误(脚本缺失/执行失败/产物缺失)。"""


def _resolve_raw_dir(manager: ProjectManager, data_entry: Any) -> Path:
    raw = Path(data_entry.raw_dir) if data_entry.raw_dir else Path(data_entry.source)
    if not raw.is_absolute():
        raw = manager.root / raw
    return raw


def _work_dir(manager: ProjectManager, exp_id: str, data_id: str) -> Path:
    return manager.data_dir(exp_id, data_id, "process")


def _finish_run(
    manager: ProjectManager,
    exp_id: str,
    data_id: str,
    workflow_ref: str,
    outputs: dict[str, str],
    message: str,
) -> str:
    run = manager.start_run(
        exp_id,
        workflow_ref=workflow_ref,
        inputs={"data_id": data_id},
        params={"mode": "manual"},
    )
    manager.finish_run(run.run_id, "success", outputs=outputs, message=message)
    return run.run_id


def manual_fid_com(
    manager: ProjectManager,
    exp_id: str,
    data_id: str,
    backend: Any,
) -> str:
    """获取当前 fid.com 内容(供人工查看/修改);未生成时先自动生成。"""
    data_entry = manager.data(exp_id, data_id)
    raw_dir = _resolve_raw_dir(manager, data_entry)
    fid_com = raw_dir / "fid.com"
    if not fid_com.is_file():
        experiment = read_dataset(raw_dir)
        resp = backend.convert_to_fid(experiment, raw_dir)
        if not resp.get("success"):
            raise ManualRunError(
                f"自动生成 fid.com 失败: {resp.get('message')}"
            )
    return fid_com.read_text(encoding="utf-8", errors="replace")


def run_manual_fid_com(
    manager: ProjectManager,
    exp_id: str,
    data_id: str,
    content: str,
    *,
    work_dir: Path | str | None = None,
    timeout: float = 900.0,
) -> str:
    """写入修改后的 fid.com 并运行,产物归位 process/ 并登记 fid。"""
    data_entry = manager.data(exp_id, data_id)
    raw_dir = _resolve_raw_dir(manager, data_entry)
    work = Path(work_dir) if work_dir else _work_dir(manager, exp_id, data_id)
    work.mkdir(parents=True, exist_ok=True)
    (raw_dir / "fid.com").write_text(content, encoding="utf-8", newline="\n")

    runtime = CshRuntime()
    result = runtime.run(["csh", "fid.com"], cwd=str(raw_dir), timeout=timeout)
    src = raw_dir / "test.fid"
    if result.returncode != 0 or not src.is_file():
        run = manager.start_run(
            exp_id,
            workflow_ref="manual_fid",
            inputs={"data_id": data_id},
            params={"mode": "manual"},
        )
        manager.finish_run(
            run.run_id, "failed", message=f"fid.com 运行失败: {result.stderr}"
        )
        raise ManualRunError(f"fid.com 运行失败: {result.stderr}")

    experiment = read_dataset(raw_dir)
    fid_path = work / f"{experiment.dataset_id}.fid"
    shutil.move(str(src), str(fid_path))
    manager.set_data_fid(exp_id, data_id, fid_path)
    _finish_run(
        manager,
        exp_id,
        data_id,
        "manual_fid",
        {"fid_path": str(fid_path)},
        "人工 FID 完成",
    )
    return str(fid_path)


def manual_scripts(
    manager: ProjectManager,
    exp_id: str,
    data_id: str,
    params: dict[str, Any] | None = None,
) -> dict[str, str]:
    """按参数渲染 fid.com/process.com/nus*.com(供表格或脚本编辑器展示)。"""
    data_entry = manager.data(exp_id, data_id)
    raw_dir = _resolve_raw_dir(manager, data_entry)
    experiment = read_dataset(raw_dir)
    return render_scripts(experiment, params)


def run_manual_spectrum(
    manager: ProjectManager,
    exp_id: str,
    data_id: str,
    scripts: dict[str, str],
    *,
    work_dir: Path | str | None = None,
    timeout: float = 7200.0,
) -> str:
    """运行人工脚本(fid.com 在 raw 目录;process.com/nus*.com 在 process/),
    终谱归位 spectra/ 并登记。
    """
    data_entry = manager.data(exp_id, data_id)
    raw_dir = _resolve_raw_dir(manager, data_entry)
    work = Path(work_dir) if work_dir else _work_dir(manager, exp_id, data_id)
    work.mkdir(parents=True, exist_ok=True)
    experiment = read_dataset(raw_dir)
    if experiment.sampling.mode is SamplingMode.NUS:
        script_key = "nus.com"
        workflow_ref = "manual_nus"
    else:
        script_key = "process.com"
        workflow_ref = "manual_process"
    runtime = CshRuntime()
    try:
        return _run_manual_spectrum_impl(
            manager,
            exp_id,
            data_id,
            scripts,
            raw_dir,
            work,
            experiment,
            runtime,
            script_key,
            workflow_ref,
            timeout,
        )
    except ManualRunError as exc:
        run = manager.start_run(
            exp_id,
            workflow_ref=workflow_ref,
            inputs={"data_id": data_id},
            params={"mode": "manual"},
        )
        manager.finish_run(run.run_id, "failed", message=str(exc))
        raise


def _run_manual_spectrum_impl(
    manager: ProjectManager,
    exp_id: str,
    data_id: str,
    scripts: dict[str, str],
    raw_dir: Path,
    work: Path,
    experiment: Experiment,
    runtime: CshRuntime,
    script_key: str,
    workflow_ref: str,
    timeout: float,
) -> str:
    """run_manual_spectrum 的实际执行(成功路径;失败抛 ManualRunError)。"""
    fid_com = scripts.get("fid.com")
    if fid_com is not None:
        (raw_dir / "fid.com").write_text(fid_com, encoding="utf-8", newline="\n")
        result = runtime.run(["csh", "fid.com"], cwd=str(raw_dir), timeout=timeout)
        src = raw_dir / "test.fid"
        if result.returncode != 0 or not src.is_file():
            raise ManualRunError(f"fid.com 运行失败: {result.stderr}")
        fid_path = work / f"{experiment.dataset_id}.fid"
        shutil.move(str(src), str(fid_path))
        manager.set_data_fid(exp_id, data_id, fid_path)

    if experiment.sampling.mode is SamplingMode.NUS:
        nuslist_src = raw_dir / "nuslist"
        nuslist_dst = work / "nuslist"
        if nuslist_src.is_file() and not nuslist_dst.is_file():
            shutil.copy2(nuslist_src, nuslist_dst)

    script = scripts.get(script_key)
    if script is None:
        raise ManualRunError(f"缺少脚本: {script_key}")
    (work / script_key).write_text(script, encoding="utf-8", newline="\n")
    result = runtime.run(["csh", script_key], cwd=str(work), timeout=timeout)
    out_ext = "ft3" if experiment.ndim >= 3 else "ft2"
    spectrum_src = work / f"{experiment.dataset_id}.{out_ext}"
    if result.returncode != 0 or not spectrum_src.is_file():
        raise ManualRunError(f"{script_key} 运行失败: {result.stderr}")

    spectrum_path = _register_spectrum(
        manager, exp_id, data_id, str(spectrum_src)
    )
    _finish_run(
        manager,
        exp_id,
        data_id,
        workflow_ref,
        {"spectrum_path": spectrum_path},
        "人工谱图完成",
    )
    return spectrum_path


__all__ = [
    "ManualRunError",
    "manual_fid_com",
    "manual_scripts",
    "run_manual_fid_com",
    "run_manual_spectrum",
]
