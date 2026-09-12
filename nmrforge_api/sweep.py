"""参数扫描:同一份 fid、同一参考相位,只改被扫的处理参数,逐组合出谱。

研究设计要点(直接决定结论是否可用):

- **fid 只转一次**:``build_reference`` 已经把 fid 落在 ``study/work/``;
  扫描复用同一份 fid,参数变化是唯一变量;
- **相位锁定在参考值**:每组合传入 ``direct_phase_override``,否则每个组合
  会各自重跑相位搜索,峰位差里就混进相位差(不是处理参数的分辨率效应);
- **不替换活动谱**:候选谱写到 ``study/runs/<run_id>/``,项目的参考谱不动;
- **可断点续跑**:每个组合成功即写 ``run.json``;重跑时跳过已成功的组合;
- **单组合失败不中断**:状态记 ``failed`` + 原因,继续下一组合(与批量一致)。
"""

from __future__ import annotations

import copy
import hashlib
import inspect
import itertools
import json
import shutil
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.planning.method_selector import select_method
from core.project.manager import sha256_file
from core.version import software_version
from nmrforge_api.errors import SweepError
from nmrforge_api.peaks import (
    PeakMeasurement,
    measure_peak_positions,
    read_reference_peaks,
)
from nmrforge_api.reference import ReferenceSpectrum, load_reference
from nmrforge_api.session import StudySession, now_iso
from workflow.stepwise import read_experiment

DEFAULT_MAX_RUNS = 256


@dataclass
class SweepPlan:
    """扫描计划:参数轴 → 组合网格 + 参考基底参数。"""

    axes: dict[str, list[Any]] = field(default_factory=dict)
    combos: list[dict[str, Any]] = field(default_factory=list)
    base_params: dict[str, Any] = field(default_factory=dict)
    grid_sha256: str = ""
    reference_script_sha256: str = ""
    reference_spectrum_sha256: str = ""
    max_runs: int = DEFAULT_MAX_RUNS
    phase_locked: bool = True
    notes: list[str] = field(default_factory=list)

    @property
    def n_combos(self) -> int:
        return len(self.combos)

    def to_dict(self) -> dict[str, Any]:
        return {
            "axes": self.axes,
            "combos": self.combos,
            "base_params": self.base_params,
            "grid_sha256": self.grid_sha256,
            "reference_script_sha256": self.reference_script_sha256,
            "reference_spectrum_sha256": self.reference_spectrum_sha256,
            "max_runs": int(self.max_runs),
            "phase_locked": bool(self.phase_locked),
            "notes": list(self.notes),
            "n_combos": self.n_combos,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SweepPlan:
        return cls(
            axes=dict(data.get("axes") or {}),
            combos=[dict(c) for c in (data.get("combos") or [])],
            base_params=dict(data.get("base_params") or {}),
            grid_sha256=str(data.get("grid_sha256", "")),
            reference_script_sha256=str(data.get("reference_script_sha256", "")),
            reference_spectrum_sha256=str(data.get("reference_spectrum_sha256", "")),
            max_runs=int(data.get("max_runs", DEFAULT_MAX_RUNS) or DEFAULT_MAX_RUNS),
            phase_locked=bool(data.get("phase_locked", True)),
            notes=[str(n) for n in (data.get("notes") or [])],
        )


@dataclass
class SweepRun:
    """一个参数组合的运行记录。"""

    run_id: str
    index: int
    combo: dict[str, Any] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"
    message: str = ""
    run_dir: str = ""
    script_path: str = ""
    script_sha256: str = ""
    spectrum_path: str = ""
    spectrum_sha256: str = ""
    wall_time_s: float = 0.0
    phase_locked: bool = True
    logs_tail: list[str] = field(default_factory=list)
    measurements: list[PeakMeasurement] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "index": int(self.index),
            "combo": self.combo,
            "params": self.params,
            "status": self.status,
            "message": self.message,
            "run_dir": self.run_dir,
            "script_path": self.script_path,
            "script_sha256": self.script_sha256,
            "spectrum_path": self.spectrum_path,
            "spectrum_sha256": self.spectrum_sha256,
            "wall_time_s": float(self.wall_time_s),
            "phase_locked": bool(self.phase_locked),
            "logs_tail": list(self.logs_tail),
            "measurements": [m.to_dict() for m in self.measurements],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SweepRun:
        return cls(
            run_id=str(data.get("run_id", "")),
            index=int(data.get("index", 0) or 0),
            combo=dict(data.get("combo") or {}),
            params=dict(data.get("params") or {}),
            status=str(data.get("status", "pending")),
            message=str(data.get("message", "")),
            run_dir=str(data.get("run_dir", "")),
            script_path=str(data.get("script_path", "")),
            script_sha256=str(data.get("script_sha256", "")),
            spectrum_path=str(data.get("spectrum_path", "")),
            spectrum_sha256=str(data.get("spectrum_sha256", "")),
            wall_time_s=float(data.get("wall_time_s", 0.0) or 0.0),
            phase_locked=bool(data.get("phase_locked", True)),
            logs_tail=[str(x) for x in (data.get("logs_tail") or [])],
            measurements=[
                PeakMeasurement.from_dict(m)
                for m in (data.get("measurements") or [])
            ],
        )


def expand_grid(axes: Mapping[str, Sequence[Any]]) -> list[dict[str, Any]]:
    """展开参数轴为组合列表。

    键支持点号路径(如 ``"window.F1.off"``),值为候选取值序列:

    >>> expand_grid({"zero_fill": [1, 2], "window.F1.off": [0.35, 0.45]})
    [{'zero_fill': 1, 'window.F1.off': 0.35}, ...]
    """
    keys = [str(key) for key in axes.keys()]
    if not keys:
        return [{}]
    values: list[list[Any]] = []
    for key in keys:
        options = list(axes[key])
        if not options:
            raise SweepError(f"参数轴 {key} 没有候选值")
        values.append(options)
    return [
        dict(zip(keys, combination))
        for combination in itertools.product(*values)
    ]


def merge_overrides(
    base: Mapping[str, Any], overrides: Mapping[str, Any]
) -> dict[str, Any]:
    """把点号键覆盖合并进基底参数(不改原字典)。"""
    result = copy.deepcopy(dict(base))
    for dotted, value in overrides.items():
        parts = str(dotted).split(".")
        node = result
        for part in parts[:-1]:
            child = node.get(part)
            if not isinstance(child, dict):
                child = {}
                node[part] = child
            node = child
        node[parts[-1]] = value
    return result


def _grid_sha256(combos: Sequence[Mapping[str, Any]]) -> str:
    payload = json.dumps(
        [dict(c) for c in combos], sort_keys=True, ensure_ascii=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def plan_sweep(
    reference: ReferenceSpectrum,
    *,
    axes: Mapping[str, Sequence[Any]],
    max_runs: int = DEFAULT_MAX_RUNS,
    base_params: Mapping[str, Any] | None = None,
    notes: Iterable[str] | None = None,
) -> SweepPlan:
    """由参数轴 + 参考谱生成扫描计划(会检查组合数上限)。"""
    combos = expand_grid(axes)
    if len(combos) > int(max_runs):
        raise SweepError(
            f"参数组合 {len(combos)} 个超过上限 max_runs={max_runs};"
            "请减小网格或显式提高上限(长跑请分批)"
        )
    base = dict(base_params) if base_params is not None else dict(
        reference.sweep_params
    )
    phase_locked = reference.direct_phase_override() is not None
    if not phase_locked:
        sampling = dict(base.get("sampling") or {})
        if sampling.get("auto_phase") is not False:
            sampling["auto_phase"] = False
            base["sampling"] = sampling
    plan = SweepPlan(
        axes={str(k): list(v) for k, v in axes.items()},
        combos=combos,
        base_params=base,
        grid_sha256=_grid_sha256(combos),
        reference_script_sha256=reference.script_sha256,
        reference_spectrum_sha256=reference.spectrum_sha256,
        max_runs=int(max_runs),
        phase_locked=phase_locked,
        notes=list(notes or []),
    )
    if not phase_locked:
        plan.notes.append(
            "参考运行没有记录 direct_phase,扫描改为关闭自动相位搜索"
            "(sampling.auto_phase=False),相位取预设默认值"
        )
    return plan


def _supports_nus_candidates(backend: Any) -> tuple[bool, str]:
    """后端 ``reconstruct_nus`` 是否支持候选输出隔离(out_file/script_name)。"""
    method = getattr(backend, "reconstruct_nus", None)
    if method is None:
        return False, "后端没有 reconstruct_nus(),无法扫描 NUS 数据"
    try:
        parameters = inspect.signature(method).parameters
    except (TypeError, ValueError):
        return True, ""
    missing = [name for name in ("out_file", "script_name") if name not in parameters]
    if missing:
        return (
            False,
            f"后端 reconstruct_nus() 缺少参数 {missing},"
            "无法隔离候选输出(需升级 NMRForge 后端或换用自带该参数的后端)",
        )
    return True, ""


def _load_run(run_dir: Path) -> SweepRun | None:
    state_file = run_dir / "run.json"
    if not state_file.is_file():
        return None
    try:
        raw = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    run = SweepRun.from_dict(raw)
    if run.spectrum_path and not Path(run.spectrum_path).is_file():
        return None
    return run


def run_sweep(
    session: StudySession,
    plan: SweepPlan,
    *,
    reference: ReferenceSpectrum | None = None,
    peaks: Sequence[dict[str, Any]] | None = None,
    window_pts: int = 3,
    sign: str = "abs",
    refine: str = "parabolic",
    resume: bool = True,
    stop_on_error: bool = False,
    progress: Callable[[str], None] | None = None,
    on_run: Callable[[SweepRun], None] | None = None,
) -> list[SweepRun]:
    """执行扫描计划,返回逐组合运行记录(成功/失败都在列表里)。"""
    dataset = session.dataset
    if dataset is None:
        raise SweepError("研究里还没有数据集")
    ref = reference or load_reference(session)
    if ref is None:
        raise SweepError("还没有参考谱,先调用 build_reference()")
    if not ref.sweep_supported:
        if str(ref.sampling) == "nus":
            raise SweepError(
                f"当前只支持 2D NUS 参数扫描,检测到 {ref.ndim}D NUS"
                "(3D NUS 需要切片流与候选输出进一步改造,见 "
                "docs/external-api/09-limitations-and-roadmap.md)"
            )
        raise SweepError(f"当前不支持 {ref.ndim}D/{ref.sampling} 数据的扫描")
    backend = session.backend
    if not hasattr(backend, "process"):
        raise SweepError("后端不支持 process(),无法扫描")
    experiment = read_experiment(session.manager, dataset.exp_id, dataset.data_id)
    is_nus = str(experiment.sampling.mode) == "nus"
    if is_nus:
        if int(experiment.ndim) != 2:
            raise SweepError(
                f"当前只支持 2D NUS 参数扫描(检测到 {experiment.ndim}D NUS)"
            )
        ok, reason = _supports_nus_candidates(backend)
        if not ok:
            raise SweepError(reason)
    method_plan = select_method(experiment)
    peak_rows = list(peaks) if peaks is not None else (
        read_reference_peaks(ref.peak_table_path) if ref.peak_table_path else []
    )
    if not peak_rows:
        raise SweepError(
            "没有参考峰表:先用 pick_reference_peaks() 选峰,或把公开库峰表登记到 "
            "reference.peak_table_path"
        )

    override = ref.direct_phase_override()
    results: list[SweepRun] = []

    def _emit(message: str) -> None:
        if progress is not None:
            progress(message)

    session.runs_dir.mkdir(parents=True, exist_ok=True)
    for index, combo in enumerate(plan.combos, start=1):
        run_id = f"s{index:04d}"
        run_dir = session.runs_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        if resume:
            cached = _load_run(run_dir)
            if cached is not None and cached.status == "success":
                results.append(cached)
                _emit(f"[{run_id}] 已存在,跳过(断点续跑)")
                if on_run is not None:
                    on_run(cached)
                continue
        params = merge_overrides(plan.base_params, combo)
        run = SweepRun(
            run_id=run_id,
            index=index,
            combo={str(k): v for k, v in combo.items()},
            params=params,
            run_dir=str(run_dir),
            phase_locked=override is not None,
        )
        logs: list[str] = []

        def _log(message: str, _logs: list[str] = logs) -> None:
            _logs.append(str(message))
            _emit(f"[{run_id}] {message}")

        started = time.perf_counter()
        _emit(f"[{run_id}] 开始 {combo}")
        try:
            if is_nus:
                nus_params = dict(params)
                if override:
                    direct_axis = f"F{experiment.ndim}"
                    pair = override.get(direct_axis)
                    if pair is not None:
                        nus_params["direct_phase"] = [
                            float(pair[0]),
                            float(pair[1]),
                        ]
                response = backend.reconstruct_nus(
                    experiment,
                    nus_params,
                    progress=_log,
                    script_name=f"{run_id}.com",
                    out_file=f"{run_id}.ft2",
                )
            else:
                response = backend.process(
                    experiment,
                    method_plan,
                    params=params,
                    direct_phase_override=override,
                    script_name=f"{run_id}.com",
                    out_file=f"{run_id}.ft2",
                    progress=_log,
                )
        except Exception as exc:  # noqa: BLE001 - 单组合失败不中断整轮
            response = {
                "success": False,
                "message": f"{type(exc).__name__}: {exc}",
                "logs": logs,
            }
        run.wall_time_s = round(time.perf_counter() - started, 3)
        run.logs_tail = logs[-40:]
        if not response.get("success"):
            run.status = "failed"
            run.message = str(response.get("message", "处理失败"))
            _emit(f"[{run_id}] 失败: {run.message}")
            _write_run(run)
            results.append(run)
            if on_run is not None:
                on_run(run)
            if stop_on_error:
                break
            continue

        script_src = session.work_dir / f"{run_id}.com"
        spectrum_src = Path(str(response.get("spectrum_path", "")))
        if script_src.is_file():
            target = run_dir / "process.com"
            shutil.copy2(script_src, target)
            run.script_path = str(target)
            run.script_sha256 = sha256_file(target)
        if not spectrum_src.is_file():
            run.status = "failed"
            run.message = f"后端返回的谱不存在: {spectrum_src}"
            _write_run(run)
            results.append(run)
            if on_run is not None:
                on_run(run)
            if stop_on_error:
                break
            continue
        target_spectrum = run_dir / f"spectrum{spectrum_src.suffix}"
        shutil.copy2(spectrum_src, target_spectrum)
        run.spectrum_path = str(target_spectrum)
        run.spectrum_sha256 = sha256_file(target_spectrum)
        try:
            run.measurements = measure_peak_positions(
                target_spectrum,
                peak_rows,
                window_pts=window_pts,
                sign=sign,
                refine=refine,
            )
        except Exception as exc:  # noqa: BLE001 - 测量失败也算该组合失败
            run.status = "failed"
            run.message = f"峰位测量失败: {type(exc).__name__}: {exc}"
        else:
            run.status = "success"
            run.message = f"完成,{len(run.measurements)} 个峰位"
        finally:
            # 中间产物目录可能被内存盘接管:复制成功即清理,避免磁盘/内存膨胀
            try:
                if spectrum_src.is_file():
                    spectrum_src.unlink()
            except OSError:
                pass
        _write_run(run)
        results.append(run)
        _emit(f"[{run_id}] {run.status}: {run.message}")
        if on_run is not None:
            on_run(run)
    return results


def load_plan(session: StudySession) -> SweepPlan | None:
    """读取研究目录里的扫描计划(``records/sweep_plan.json``)。"""
    path = session.records_dir / "sweep_plan.json"
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return SweepPlan.from_dict(raw) if isinstance(raw, dict) else None


def load_runs(session: StudySession) -> list[SweepRun]:
    """读取已记录的扫描运行(按 run_id 排序;损坏记录跳过)。"""
    runs: list[SweepRun] = []
    if not session.runs_dir.is_dir():
        return runs
    for run_dir in sorted(p for p in session.runs_dir.iterdir() if p.is_dir()):
        run = _load_run(run_dir)
        if run is not None:
            runs.append(run)
    return runs


def _write_run(run: SweepRun) -> None:
    target = Path(run.run_dir) / "run.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = run.to_dict()
    payload["updated"] = now_iso()
    payload["software_version"] = software_version()
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


__all__ = [
    "DEFAULT_MAX_RUNS",
    "SweepPlan",
    "SweepRun",
    "expand_grid",
    "load_plan",
    "load_runs",
    "merge_overrides",
    "plan_sweep",
    "run_sweep",
]
