"""人工处理路径后端(用户反馈:查看/修改/运行脚本)。

与自动处理对应,集合了此前命令行人工处理的流程:
- 生成 FID:自动阶段先产出 fid.com(backend.convert_to_fid)→ 人工查看内容
  (manual_fid_com)→ 修改 → 运行(run_manual_fid_com,人工参数作为覆盖交给后端)→ 登记 fid;
- 生成谱图:脚本编辑(manual_scripts 渲染,process/ 已有脚本优先展示
  → 修改)→ 运行(process.com / nus*.com,run_manual_spectrum)→
  终谱归位 spectra/ 并登记。

运行复用 backend.runtime.CshRuntime;产物登记 set_data_fid /
set_data_spectrum + WorkflowRun(审计)。
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from backend.bruker_workflow import parse_fid_com
from backend.runtime import CshRuntime
from backend.script_generator import render_scripts
from core.data.internal_data_model import Experiment, SamplingMode
from core.project import ProjectManager
from workflow.stepwise import _read_experiment, _register_spectrum


class ManualRunError(Exception):
    """人工处理运行错误(脚本缺失/执行失败/产物缺失)。"""


def _resolve_raw_dir(manager: ProjectManager, data_entry: Any) -> Path:
    raw = Path(data_entry.raw_dir) if data_entry.raw_dir else Path(data_entry.source)
    if not raw.is_absolute():
        raw = manager.root / raw
    return raw


def _work_dir(manager: ProjectManager, exp_id: str, data_id: str) -> Path:
    return manager.data_dir(exp_id, data_id, "process")


def _slice_files(directory: Path, dataset_id: str) -> list[Path]:
    """切片 fid 候选:新命名 {dataset_id}*.fid 优先,兼容旧 test*.fid。"""
    if not directory.is_dir():
        return []
    new_style = sorted(directory.glob(f"{dataset_id}*.fid"))
    legacy = sorted(directory.glob("test*.fid"))
    seen = {p.name for p in new_style}
    return new_style + [p for p in legacy if p.name not in seen]


def _fid_ready(candidate: Path | None) -> bool:
    """单文件或切片目录(任一命名)存在即视为已转换。"""
    if candidate is None:
        return False
    if candidate.is_file():
        return True
    if candidate.is_dir() and list(candidate.glob("*.fid")):
        return True
    return False


def _run_quality_check(
    manager: ProjectManager,
    exp_id: str,
    data_entry: Any,
    work: Path,
    data_id: str,
) -> None:
    """人工「运行」谱图前的质量诊断(不重跑优化)。

    结果写入 process/manual_quality.log;诊断会顺带修复坏点(备份),
    与自动路径「生成谱图」开端的质量检测一致(0.2.163-补13)。0.2.193:
    由脚本编辑器打开时改为点「运行」后执行(run_manual_spectrum),打开
    编辑器不再卡顿。
    """
    fid_candidate = (
        Path(data_entry.fid_path)
        if data_entry.fid_path
        else work / f"{data_id}.fid"
    )
    if not _fid_ready(fid_candidate):
        return  # fid 未就位时诊断无意义,不阻断编辑
    try:
        from workflow.direct_diagnostics import run_direct_diagnostics

        experiment = _read_experiment(manager, exp_id, data_id)
        result = run_direct_diagnostics(work, experiment)
        lines = ["== 质量检测(人工谱图准备,复用自动优化终脚本) =="]
        lines += [f"{i + 1}. {report}" for i, report in enumerate(result.reports)]
        lines.append(f"指标: {result.metrics}")
        (work / "manual_quality.log").write_text(
            (chr(10).join(lines) + chr(10)), encoding="utf-8"
        )
    except Exception as exc:  # noqa: BLE001 - 质量检测失败不阻断编辑
        try:
            (work / "manual_quality.log").write_text(
                f"质量检测失败: {type(exc).__name__}: {exc}" + chr(10),
                encoding="utf-8",
            )
        except OSError:
            pass


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
    backend: Any | None = None,
) -> str:
    """获取已生成的 fid.com 内容(供人工查看/修改);不触发自动转换。

    0.2.199-补29dm(用户):生成 FID 的人工按钮只在自动处理成功后出现,这里
    直接读已生成的 fid.com——单数据集 process/fid.com;分段取参考段
    process/seg_001/fid.com 并加提示头(人工改参数后由 run_manual_fid_com
    作为覆盖交给后端统一执行)。fid.com 不存在时明确报错,不再自动转换
    (避免点击后隔一会才打开)。
    """
    data_entry = manager.data(exp_id, data_id)
    raw_dir = _resolve_raw_dir(manager, data_entry)
    work = _work_dir(manager, exp_id, data_id)
    segments = list(getattr(data_entry, "segments", None) or [])
    if segments:
        seg_fid = work / "seg_001" / "fid.com"
        if seg_fid.is_file():
            header = (
                "# 分段采集:此为参考段 fid.com,修改参数将应用到所有段\n"
                "# (数据转换/切片/合并由后端统一执行,勿改输出名)\n"
            )
            return header + seg_fid.read_text(
                encoding="utf-8", errors="replace"
            )
    fid_com = work / "fid.com"
    if fid_com.is_file():
        return fid_com.read_text(encoding="utf-8", errors="replace")
    legacy = raw_dir / "fid.com"
    if legacy.is_file():
        return legacy.read_text(encoding="utf-8", errors="replace")
    raise ManualRunError(
        "fid.com 不存在,请先自动生成 FID 后再打开人工编辑器"
    )


def run_manual_fid_com(
    manager: ProjectManager,
    exp_id: str,
    data_id: str,
    content: str,
    *,
    work_dir: Path | str | None = None,
    timeout: float = 900.0,
    backend: Any | None = None,
    progress: Callable[[str], None] | None = None,
) -> str:
    """运行人工 fid.com 并登记 fid。

    单数据集与分段一致(0.2.199-补2):人工改的参数经 parse_fid_com 提取
    为 overrides,由后端 convert_to_fid 统一执行 bruker 生成/参数修正/
    坏点清理/切片归位——人工只调参数,转换结构由后端保证,不再直接 csh
    运行用户脚本(结构性改动不保留,与分段语义一致)。单数据集产物归位
    process/(单文件或切片),分段产物落 process/merged/fid。
    """
    data_entry = manager.data(exp_id, data_id)
    raw_dir = _resolve_raw_dir(manager, data_entry)
    work = Path(work_dir) if work_dir else _work_dir(manager, exp_id, data_id)
    work.mkdir(parents=True, exist_ok=True)
    experiment = _read_experiment(manager, exp_id, data_id)
    segments = list(getattr(data_entry, "segments", None) or [])
    if backend is None:
        raise ManualRunError(
            "人工 fid.com 需要后端执行转换/合并,请从界面运行"
        )
    overrides = parse_fid_com(content)
    if hasattr(backend, "work_dir"):
        backend.work_dir = str(work)
    resp = backend.convert_to_fid(
        experiment, raw_dir, fid_com_overrides=overrides, progress=progress
    )
    if not resp.get("success"):
        logs = list(resp.get("logs", []))
        message = (
            f"fid.com 转换失败: {resp.get('message')}"
            + (" | " + " | ".join(logs) if logs else "")
        )
        run = manager.start_run(
            exp_id,
            workflow_ref="manual_fid",
            inputs={"data_id": data_id},
            params={"mode": "manual", "segments": len(segments)},
        )
        manager.finish_run(run.run_id, "failed", message=message)
        raise ManualRunError(message)
    if segments:
        fid_path = Path(str(resp.get("fid_path") or (work / "merged" / "fid")))
        run_params = {"fid_path": str(fid_path), "segments": len(segments)}
        message = "人工 FID 完成(分段合并)"
    else:
        fid_path = Path(
            str(resp.get("fid_path") or (work / f"{experiment.dataset_id}.fid"))
        )
        run_params = {"fid_path": str(fid_path)}
        message = "人工 FID 完成"
    manager.set_data_fid(exp_id, data_id, fid_path)
    _finish_run(manager, exp_id, data_id, "manual_fid", run_params, message)
    return str(fid_path)


def manual_scripts(
    manager: ProjectManager,
    exp_id: str,
    data_id: str,
    params: dict[str, Any] | None = None,
) -> dict[str, str]:
    """谱图步骤脚本(process.com / nus*.com,供脚本编辑器展示)。

    人工生成谱图不是完全人工,数据转换/合并/坏点清理与自动路径对齐
    (0.2.163-补13):fid 缺失时提示用户先执行「生成 FID」步骤
    (0.2.163-补14,不在谱图入口偷跑转换);打开编辑器只读已有脚本,
    不跑质量诊断——诊断改到点「运行」时执行(run_manual_spectrum,
    0.2.193),打开大数据脚本编辑器不再卡顿。优先返回 process/ 目录下
    的已有脚本;没有时才重新渲染默认脚本。只返回谱图脚本——fid 由
    「生成 FID」步骤产出。
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
        # 已有脚本直接给人(0.2.193:质量诊断改到「运行」时执行,打开
        # 编辑器不再重跑,避免大数据每次打开卡顿)
        return existing
    # fid 缺失:提示先执行「生成 FID」步骤(转换/合并由自动路径完成),
    # 人工途径只是给人调参,不在谱图入口偷跑转换(0.2.163-补14)
    experiment = _read_experiment(manager, exp_id, data_id)
    fid_candidate = (
        Path(data_entry.fid_path)
        if data_entry.fid_path
        else work / f"{experiment.dataset_id}.fid"
    )
    if not _fid_ready(fid_candidate) and not _slice_files(
        work / "fid", experiment.dataset_id
    ):
        raise ManualRunError("缺少已转换 fid,请先执行「生成 FID」步骤")
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
        experiment = _read_experiment(manager, exp_id, data_id)
        rendered = render_scripts(experiment, params)
    except NotImplementedError as exc:
        raise ManualRunError(f"无法渲染处理脚本(采集模式不支持): {exc}") from exc
    script_key = (
        "nus.com"
        if experiment.sampling.mode is SamplingMode.NUS
        else "process.com"
    )
    content = rendered[script_key]
    # 0.2.163-补9:3D uniform/NUS 的 fid 为切片目录(fid/test*.fid),
    # 渲染默认 in_file 是单文件 {dataset_id}.fid——检测到切片时改写为
    # 切片流,人工运行才不失败(与自动路径 backend 切片切换一致)
    slice_dir = work / "fid"
    slices = _slice_files(slice_dir, experiment.dataset_id)
    if slices:
        single = f"{experiment.dataset_id}.fid"
        new_style = any(
            p.name.startswith(experiment.dataset_id) for p in slices
        )
        sliced = (
            f"fid/{experiment.dataset_id}%03d.fid"
            if new_style
            else "fid/test%03d.fid"
        )
        # 只改 xyz2pipe/nmrPipe 的 -in 输入,不动 -out 输出
        import re

        content = re.sub(
            r"(-in )" + re.escape(single),
            r"\g<1>" + sliced,
            content,
        )
    return {script_key: content}


def run_manual_spectrum(
    manager: ProjectManager,
    exp_id: str,
    data_id: str,
    scripts: dict[str, str],
    *,
    work_dir: Path | str | None = None,
    timeout: float = 7200.0,
    progress: Callable[[str], None] | None = None,
) -> str:
    """运行谱图脚本(process.com/nus*.com 在 process/,消费已转换 fid),
    终谱归位 spectra/ 并登记;不执行 fid.com(生成 FID 是独立步骤)。
    """
    data_entry = manager.data(exp_id, data_id)
    raw_dir = _resolve_raw_dir(manager, data_entry)
    work = Path(work_dir) if work_dir else _work_dir(manager, exp_id, data_id)
    work.mkdir(parents=True, exist_ok=True)
    experiment = _read_experiment(manager, exp_id, data_id)
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
            progress,
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
    progress: Callable[[str], None] | None = None,
) -> str:
    """run_manual_spectrum 的实际执行(成功路径;失败抛 ManualRunError)。

    谱图步骤只消费已转换 fid(「生成 FID」独立步骤产出),不执行 fid.com。
    """
    fid_candidate = (
        Path(data_entry.fid_path)
        if data_entry.fid_path
        else work / f"{experiment.dataset_id}.fid"
    )
    if not _fid_ready(fid_candidate):
        raise ManualRunError(
            f"缺少已转换 fid,请先生成 FID: {exp_id}/{data_id}"
        )

    if experiment.sampling.mode is SamplingMode.NUS:
        nuslist_src = raw_dir / "nuslist"
        nuslist_dst = work / "nuslist"
        if nuslist_src.is_file() and not nuslist_dst.is_file():
            shutil.copy2(nuslist_src, nuslist_dst)

    # 0.2.193:人工「运行」时先跑一次质量诊断(坏点修复/报告),与自动
    # 路径生成谱图开端一致;打开脚本编辑器不再执行(打开变快)
    _run_quality_check(manager, exp_id, data_entry, work, data_id)

    script = scripts.get(script_key)
    if script is None:
        raise ManualRunError(f"缺少脚本: {script_key}")
    (work / script_key).write_text(script, encoding="utf-8", newline="\n")
    result = runtime.run(
        ["csh", script_key],
        cwd=str(work),
        timeout=timeout,
        on_line=progress,
    )
    out_ext = "ft3" if experiment.ndim >= 3 else "ft2"
    spectrum_src = work / f"{experiment.dataset_id}.{out_ext}"
    if result.returncode != 0 or not spectrum_src.is_file():
        raise ManualRunError(f"{script_key} 运行失败: {result.stderr}")

    spectrum_path = _register_spectrum(
        manager, exp_id, data_id, str(spectrum_src)
    )
    # 0.2.199-补29v:人工途径同样生成谱图质量报告(数据质量诊断 +
    # 终谱质量评分),写 {谱}.quality.json 供 GUI 报告页显示——与自动
    # 途径一致,避免人工谱图「无报告记录」。
    try:
        from workflow.optimization_report import (
            spectrum_quality_report_lines,
            write_quality_record,
        )
        from workflow.phase_routes import _sign_mode

        lines = ["== 谱图质量与数据质量报告 =="]
        lines += spectrum_quality_report_lines(
            spectrum_path, sign_mode=_sign_mode(experiment)
        )
        qlog = work / "manual_quality.log"
        if qlog.is_file():
            diag = [
                ln
                for ln in qlog.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines()
                if ln.strip()
            ]
            if diag:
                lines += ["◆ 数据质量诊断(人工途径)"] + diag
        lines.append("◆ 处理参数: 人工途径(mode=manual)")
        write_quality_record(
            spectrum_path, {"mode": "manual"}, "\n".join(lines)
        )
    except Exception as exc:  # noqa: BLE001 - 报告失败不影响谱图生成
        # 0.2.199-补29eh:评估失败不静默——写入 manual_quality.log 并提示
        _msg = (
            "谱图质量评估失败: "
            f"{type(exc).__name__}: {exc}"
        )
        try:
            with (work / "manual_quality.log").open("a", encoding="utf-8") as _fh:
                _fh.write(_msg + "\n")
        except OSError:
            pass
        if progress is not None:
            progress(_msg)
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
