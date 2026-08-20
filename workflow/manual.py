"""人工处理路径后端(用户反馈:查看/修改/运行脚本)。

与自动处理对应,集合了此前命令行人工处理的流程:
- 生成 FID:自动阶段先产出 fid.com(backend.convert_to_fid)→ 人工查看内容
  (manual_fid_com)→ 修改 → 运行(csh fid.com,run_manual_fid_com)→ 登记 fid;
- 生成谱图:脚本编辑(manual_scripts 渲染,process/ 已有脚本优先展示
  → 修改)→ 运行(process.com / nus*.com,run_manual_spectrum)→
  终谱归位 spectra/ 并登记。

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
    work = _work_dir(manager, exp_id, data_id)
    work.mkdir(parents=True, exist_ok=True)
    fid_com = work / "fid.com"
    if not fid_com.is_file():
        legacy = raw_dir / "fid.com"
        if legacy.is_file():
            return legacy.read_text(encoding="utf-8", errors="replace")
        experiment = read_dataset(raw_dir)
        if hasattr(backend, "work_dir"):
            backend.work_dir = str(work)
        resp = backend.convert_to_fid(experiment, raw_dir)
        if not resp.get("success"):
            raise ManualRunError(
                f"自动生成 fid.com 失败: {resp.get('message')}"
            )
        fid_com = work / "fid.com"
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
    fid_com = work / "fid.com"
    fid_com.write_text(content, encoding="utf-8", newline="\n")

    runtime = CshRuntime()
    result = runtime.run(["csh", str(fid_com)], cwd=str(raw_dir), timeout=timeout)
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
    """谱图步骤脚本(process.com / nus*.com,供脚本编辑器展示)。

    优先返回 process/ 目录下的已有脚本(自动处理运行过或上次人工保存的
    版本,与自动生成的保持一致);没有时才重新渲染默认脚本。只返回谱图
    脚本——fid 由「生成 FID」步骤产出。
    """
    data_entry = manager.data(exp_id, data_id)
    raw_dir = _resolve_raw_dir(manager, data_entry)
    work = _work_dir(manager, exp_id, data_id)
    existing: dict[str, str] = {}
    if work.is_dir():
        for path in sorted(work.glob("*.com")):
            if path.name == "fid.com":
                continue
            existing[path.name] = path.read_text(
                encoding="utf-8", errors="replace"
            )
    if existing:
        return existing
    if params is None:
        params = {}
    nus = dict(params.get("nus") or {})
    if not nus.get("nuslist_count"):
        nuslist_path = raw_dir / "nuslist"
        if nuslist_path.is_file():
            try:
                nus_rows = [
                    row
                    for row in nuslist_path.read_text(
                        encoding="utf-8", errors="replace"
                    ).splitlines()
                    if row.strip() and not row.lstrip().startswith("#")
                ]
                if nus_rows:
                    nus["nuslist_count"] = len(nus_rows)
                    params = {**params, "nus": {**nus}}
            except OSError:
                pass
    try:
        experiment = read_dataset(raw_dir)
        rendered = render_scripts(experiment, params)
    except NotImplementedError as exc:
        raise ManualRunError(f"无法渲染处理脚本(采集模式不支持): {exc}") from exc
    script_key = (
        "nus.com"
        if experiment.sampling.mode is SamplingMode.NUS
        else "process.com"
    )
    return {script_key: rendered[script_key]}


def run_manual_spectrum(
    manager: ProjectManager,
    exp_id: str,
    data_id: str,
    scripts: dict[str, str],
    *,
    work_dir: Path | str | None = None,
    timeout: float = 7200.0,
) -> str:
    """运行谱图脚本(process.com/nus*.com 在 process/,消费已转换 fid),
    终谱归位 spectra/ 并登记;不执行 fid.com(生成 FID 是独立步骤)。
    """
    data_entry = manager.data(exp_id, data_id)
    raw_dir = _resolve_raw_dir(manager, data_entry)
    work = Path(work_dir) if work_dir else _work_dir(manager, exp_id, data_id)
    work.mkdir(parents=True, exist_ok=True)
    experiment = read_dataset(raw_dir)
    workflow_ref = (
        "manual_nus"
        if experiment.sampling.mode is SamplingMode.NUS
        else "manual_process"
    )
    if not scripts:
        raise ManualRunError("缺少处理脚本(编辑器内容为空)")
    script_key = next(iter(scripts))
    runtime = CshRuntime()
    try:
        return _run_manual_spectrum_impl(
            manager,
            exp_id,
            data_id,
            data_entry,
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
    data_entry: Any,
    scripts: dict[str, str],
    raw_dir: Path,
    work: Path,
    experiment: Experiment,
    runtime: CshRuntime,
    script_key: str,
    workflow_ref: str,
    timeout: float,
) -> str:
    """run_manual_spectrum 的实际执行(成功路径;失败抛 ManualRunError)。

    谱图步骤只消费已转换 fid(「生成 FID」独立步骤产出),不执行 fid.com。
    """
    fid_candidate = (
        Path(data_entry.fid_path)
        if data_entry.fid_path
        else work / f"{experiment.dataset_id}.fid"
    )
    if not fid_candidate.is_file():
        raise ManualRunError(
            f"缺少已转换 fid,请先生成 FID: {exp_id}/{data_id}"
        )

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
