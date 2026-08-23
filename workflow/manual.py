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
    """已运行自动优化后,人工谱图准备再跑一次质量诊断(不重跑优化)。

    结果写入 process/manual_quality.log;诊断会顺带修复坏点(备份),
    与自动路径「生成谱图」开端的质量检测一致(0.2.163-补13)。
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
    backend: Any,
) -> str:
    """获取当前 fid.com 内容(供人工查看/修改);未生成时先自动生成。

    分段采集:合并链路逐段生成 fid.com(seg_001 参数一致,作参考段),
    返回参考段内容并加提示头;人工改参数后由 run_manual_fid_com
    逐段应用并合并(0.2.163-补13)。
    """
    data_entry = manager.data(exp_id, data_id)
    raw_dir = _resolve_raw_dir(manager, data_entry)
    work = _work_dir(manager, exp_id, data_id)
    work.mkdir(parents=True, exist_ok=True)
    segments = list(getattr(data_entry, "segments", None) or [])
    fid_com = work / "fid.com"
    if not fid_com.is_file():
        legacy = raw_dir / "fid.com"
        if legacy.is_file():
            return legacy.read_text(encoding="utf-8", errors="replace")
        experiment = _read_experiment(manager, exp_id, data_id)
        if hasattr(backend, "work_dir"):
            backend.work_dir = str(work)
        resp = backend.convert_to_fid(experiment, raw_dir)
        if not resp.get("success"):
            raise ManualRunError(
                f"自动生成 fid.com 失败: {resp.get('message')}"
            )
        if segments:
            seg_fid = work / "seg_001" / "fid.com"
            if seg_fid.is_file():
                content = seg_fid.read_text(encoding="utf-8", errors="replace")
                header = (
                    "# 分段采集:此为参考段 fid.com,修改参数将应用到所有段\n"
                    "# (数据转换/切片/合并由后端统一执行,勿改输出名)\n"
                )
                return header + content
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
    backend: Any | None = None,
) -> str:
    """运行人工 fid.com 并登记 fid。

    单数据集:写入修改后的 fid.com 并直接运行(只运行 fid.com 一个脚本),
    产物归位 process/;输出名已由后端补丁统一为 {dataset_id}.fid,
    旧 test.fid 命名兼容。分段采集:人工只调参数(parse_fid_com 覆盖),
    数据转换/切片/合并/坏点清理由后端按自动路径执行,合并 fid 落
    process/merged/fid(0.2.163-补13)。
    """
    data_entry = manager.data(exp_id, data_id)
    raw_dir = _resolve_raw_dir(manager, data_entry)
    work = Path(work_dir) if work_dir else _work_dir(manager, exp_id, data_id)
    work.mkdir(parents=True, exist_ok=True)
    experiment = _read_experiment(manager, exp_id, data_id)
    segments = list(getattr(data_entry, "segments", None) or [])
    if segments:
        if backend is None:
            raise ManualRunError(
                "分段采集数据的人工 fid.com 需要后端执行转换/合并,请从界面运行"
            )
        overrides = parse_fid_com(content)
        if hasattr(backend, "work_dir"):
            backend.work_dir = str(work)
        resp = backend.convert_to_fid(
            experiment, raw_dir, fid_com_overrides=overrides
        )
        if not resp.get("success"):
            logs = list(resp.get("logs", []))
            message = (
                f"分段 fid.com 转换/合并失败: {resp.get('message')}"
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
        fid_path = Path(str(resp.get("fid_path") or (work / "merged" / "fid")))
        manager.set_data_fid(exp_id, data_id, fid_path)
        _finish_run(
            manager,
            exp_id,
            data_id,
            "manual_fid",
            {"fid_path": str(fid_path), "segments": len(segments)},
            "人工 FID 完成(分段合并)",
        )
        return str(fid_path)

    fid_com = work / "fid.com"
    fid_com.write_text(content, encoding="utf-8", newline="\n")

    runtime = CshRuntime()
    result = runtime.run(["csh", str(fid_com)], cwd=str(raw_dir), timeout=timeout)
    # 转换产物:单文件 {dataset_id}.fid(旧命名 test.fid)或切片式 fid/*.fid
    src = raw_dir / f"{experiment.dataset_id}.fid"
    if not src.is_file():
        src = raw_dir / "test.fid"
    src_slices = _slice_files(raw_dir / "fid", experiment.dataset_id)
    if result.returncode != 0 or (not src.is_file() and not src_slices):
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

    if src.is_file():
        fid_path = work / f"{experiment.dataset_id}.fid"
        shutil.move(str(src), str(fid_path))
    else:
        dest_slice = work / "fid"
        dest_slice.mkdir(parents=True, exist_ok=True)
        for sp in src_slices:
            shutil.move(str(sp), str(dest_slice / sp.name))
        fid_path = dest_slice
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

    人工生成谱图不是完全人工,数据转换/合并/坏点清理与自动路径对齐
    (0.2.163-补13):fid 缺失时提示用户先执行「生成 FID」步骤
    (0.2.163-补14,不在谱图入口偷跑转换);随后
    - 未运行过自动优化:渲染初始脚本(默认参数)交给人改;
    - 运行过自动优化(process/ 有终跑脚本 {data_id}_process.com /
      {data_id}_nus.com):再跑一次质量诊断,直接把终脚本给人改。
    优先返回 process/ 目录下的已有脚本;没有时才重新渲染默认脚本。
    只返回谱图脚本——fid 由「生成 FID」步骤产出。
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
        # 已运行过自动优化(终跑脚本存在)→ 质量诊断一次,终脚本直接给人
        if any(
            name in existing
            for name in (f"{data_id}_process.com", f"{data_id}_nus.com")
        ):
            _run_quality_check(manager, exp_id, data_entry, work, data_id)
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
    if not _fid_ready(fid_candidate):
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
