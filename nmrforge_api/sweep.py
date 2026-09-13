"""参数组合批量执行:参考工作流为模板,每个 workflow 跑全部条件。

语义(2026-09-13 用户 API 规范):

- **workflow_id = W0001…**:用户参数组合表里每行一个 workflow,编号唯一;
- **以参考脚本为模板**:该条件的扫描基底 = 该条件参考运行的有效参数(相位锁定
  在参考值),组合表只覆盖它显式指定的键;
- **参数三层留档**:``parameters_requested``(用户原样给的行)/``parameters_used``
  (实际喂给后端的完整参数)/``parameters_resolved``(自动参数的**实际结果**:
  自动相位的 actual_p0/actual_p1、SMILE 自动分档的 nSigma/thresh、谱噪声 σ);
- **同一张谱两种定位**:每个条件各跑一次处理,再对同一张候选谱分别做
  parabolic 与 2D gaussian 定位,输出两张结构一致的峰表;
- **多条件(A/B)同参数**:同一个 workflow 对全部条件用同一份
  ``parameters_requested``;每个条件各有一份参考(相位/噪声来自该条件自身),
  但峰身份(``reference_peak_id``)全条件共享;
- **状态三值**:``success`` / ``success_with_warning`` / ``failed``;单条件失败
  不静默、不中断整轮(写入 ``failed`` + 原因后继续);
- **不替换活动谱**:候选谱只写 ``study/workflows/<workflow_id>/<条件>/``。

软件边界(规范 J):这里只产出**谱 + 峰表 + 处理记录**;不做 CSP、不做
robustness、不做统计推断与显著性判断——那些由下游独立分析程序从峰表计算。
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
from core.version import software_version, tool_versions
from nmrforge_api.errors import SweepError
from nmrforge_api.peak_tables import (
    gaussian_fallback_rows,
    peak_table_digest,
    peak_table_rows,
    write_peak_table,
)
from nmrforge_api.peaks import (
    PeakMeasurement,
    measure_peak_positions,
    read_reference_peaks,
    window_points_by_axis,
)
from nmrforge_api.reference import (
    GAUSSIAN_UNSUPPORTED_NDIM_REASON,
    ReferenceSpectrum,
    load_reference,
)
from nmrforge_api.session import DatasetRef, StudySession, now_iso
from workflow.pick_peaks import read_spectrum_axes
from workflow.stepwise import read_experiment

DEFAULT_MAX_RUNS = 256

#: workflow 状态(规范 D9)
STATUS_SUCCESS = "success"
STATUS_WARNING = "success_with_warning"
STATUS_FAILED = "failed"
SUCCESS_STATUSES: frozenset[str] = frozenset({STATUS_SUCCESS, STATUS_WARNING})

#: 警告码(规范 D9/G3:任何影响判读的情况都要显式落盘,不静默)
WARN_PEAK_NOT_DETECTED = "peak_not_detected"
WARN_PEAK_WINDOW_EDGE = "peak_window_edge"
WARN_PEAK_OUT_OF_RANGE = "peak_out_of_range"
WARN_GAUSSIAN_FALLBACK = "gaussian_fallback"
WARN_GAUSSIAN_BOUNDARY_HIT = "gaussian_boundary_hit"
WARN_GAUSSIAN_UNSUPPORTED = "gaussian_unsupported_ndim"
WARN_WINDOW_FALLBACK = "window_points_fallback"

# 相位轴(保留前缀):phase.<轴>.p0|p1 为绝对值,phase_delta.<轴>.p0|p1 为
# 相对参考相位的偏差(人工相位识别偏差 ±5° 之类)。
PHASE_PREFIXES: tuple[str, ...] = ("phase.", "phase_delta.")

# 后端实际读取的处理参数键(按路径首段判定);不在其中的键写进网格不会报错但
# 也不会生效——plan_sweep 会写进 notes 提醒。
_UNIFORM_KEYS: frozenset[str] = frozenset(
    {
        "extract",
        "ext_lo",
        "ext_hi",
        "window",
        "baseline",
        "zero_fill",
        "linewidth_hz",
        "points_per_line",
        "sampling",
        "direct_poly_time",
        "keep_direct_complex",
        "keep_complex_all",
        "segment_shift_hz",
    }
)
_NUS_KEYS: frozenset[str] = _UNIFORM_KEYS | frozenset(
    {
        "nsigma",
        "nSigma",
        "thresh",
        "nthread",
        "nThread",
        "smile_scaling",
        "smile_report",
        "nuslist_file",
        "nuslist_count",
        "fid_noise",
        "fid_noise_seed",
        "timeout_s",
        "direct_phase_search",
        "display_phase_search",
        "light_phase_search",
    }
)

# 确定性/策略参数:不是「人工调参」的自由度(改了会换峰集或只是口径),
# 进网格时只提示、不阻断。
_DETERMINISTIC_KEYS: frozenset[str] = frozenset(
    {
        "extract",
        "ext_lo",
        "ext_hi",
        "points_per_line",
        "nuslist_file",
        "nuslist_count",
        "timeout_s",
        "direct_poly_time",
        "keep_direct_complex",
        "keep_complex_all",
        "segment_shift_hz",
        "fid_noise",
        "fid_noise_seed",
    }
)

# 直接写会影响相位锁定语义(且可能被后端静默忽略)→ 报错并给出替代写法。
_LOCKED_AXIS_KEYS: frozenset[str] = frozenset(
    {"phases", "direct_phase", "phase_route", "sampling.auto_phase"}
)


def workflow_id_for(index: int) -> str:
    """组合序号 → workflow_id(规范 C4:W0001、W0002…)。"""
    return f"W{int(index):04d}"


def is_phase_axis(key: str) -> bool:
    """是否相位轴(phase./phase_delta. 前缀)。"""
    return str(key).startswith(PHASE_PREFIXES)


def parse_phase_axis(key: str) -> tuple[str, str, str]:
    """``phase.<轴>.p0`` / ``phase_delta.<轴>.p1`` → (kind, axis, comp)。

    kind:``phase``(绝对值)| ``phase_delta``(相对参考的偏差);comp:``p0``/``p1``。
    """
    kind, _, rest = str(key).partition(".")
    if kind not in ("phase", "phase_delta") or not rest:
        raise SweepError(
            f"相位轴写法不对: {key}(应为 phase.<轴>.p0 或 phase_delta.<轴>.p1)"
        )
    axis, _, comp = rest.partition(".")
    if axis not in ("F1", "F2", "F3") or comp not in ("p0", "p1"):
        raise SweepError(f"相位轴写法不对: {key}(轴取 F1/F2/F3,分量取 p0/p1)")
    return kind, axis, comp


def apply_phase_axes(
    base_phase: Mapping[str, Sequence[float]],
    phase_part: Mapping[str, float],
) -> dict[str, list[float]]:
    """在参考相位上施加相位轴 → 各轴 PS(p0, p1)。

    ``phase_delta.*`` 在参考值上加偏差(人工识别偏差);``phase.*`` 直接取绝对值。
    """
    effective = {
        str(axis): [float(values[0]), float(values[1])]
        for axis, values in (base_phase or {}).items()
    }
    for key, value in phase_part.items():
        kind, axis, comp = parse_phase_axis(key)
        current = list(effective.get(axis, [0.0, 0.0]))
        index = 0 if comp == "p0" else 1
        if kind == "phase":
            current[index] = float(value)
        else:
            current[index] = float(current[index]) + float(value)
        effective[axis] = current
    return effective


def split_combo(
    combo: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, float]]:
    """组合 → (处理参数覆盖, 相位轴覆盖)。"""
    params: dict[str, Any] = {}
    phases: dict[str, float] = {}
    for key, value in combo.items():
        if is_phase_axis(key):
            phases[str(key)] = float(value)
        else:
            params[str(key)] = value
    return params, phases


# NUS(SMILE)参数键别名:后端输入约定为小写,运行记录回写的是 nSigma 等
# 驼峰键;两者都接受,调用前统一成后端输入键。
_NUS_ALIASES: dict[str, str] = {"nSigma": "nsigma", "nThread": "nthread"}


def normalize_nus_params(params: Mapping[str, Any]) -> dict[str, Any]:
    """把 NUS 参数键统一成后端输入约定(驼峰别名 → 小写)。"""
    out = dict(params)
    for camel, lower in _NUS_ALIASES.items():
        if camel in out and lower not in out:
            out[lower] = out.pop(camel)
    return out


@dataclass
class SweepPlan:
    """workflow 计划:参数轴 → 组合表 + 参考基底参数。"""

    axes: dict[str, list[Any]] = field(default_factory=dict)
    combos: list[dict[str, Any]] = field(default_factory=list)
    base_params: dict[str, Any] = field(default_factory=dict)
    grid_sha256: str = ""
    reference_script_sha256: str = ""
    reference_spectrum_sha256: str = ""
    reference_peak_table_sha256: str = ""
    max_runs: int = DEFAULT_MAX_RUNS
    design: str = "full"      # "full"(接口展开全因子) | "explicit"(外部给组合表)
    n_full: int = 0           # 全因子规模(对照用;显式设计等于组合数)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    phase_locked: bool = True
    notes: list[str] = field(default_factory=list)

    @property
    def n_combos(self) -> int:
        return len(self.combos)

    @property
    def n_workflows(self) -> int:
        return len(self.combos)

    def workflow_ids(self) -> list[str]:
        return [workflow_id_for(index) for index in range(1, self.n_combos + 1)]

    def to_dict(self) -> dict[str, Any]:
        return {
            "axes": self.axes,
            "combos": self.combos,
            "base_params": self.base_params,
            "grid_sha256": self.grid_sha256,
            "reference_script_sha256": self.reference_script_sha256,
            "reference_spectrum_sha256": self.reference_spectrum_sha256,
            "reference_peak_table_sha256": self.reference_peak_table_sha256,
            "max_runs": int(self.max_runs),
            "design": self.design,
            "n_full": int(self.n_full),
            "diagnostics": self.diagnostics,
            "phase_locked": bool(self.phase_locked),
            "notes": list(self.notes),
            "n_combos": self.n_combos,
            "n_workflows": self.n_workflows,
            "workflow_ids": self.workflow_ids(),
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
            reference_peak_table_sha256=str(
                data.get("reference_peak_table_sha256", "")
            ),
            max_runs=int(data.get("max_runs", DEFAULT_MAX_RUNS) or DEFAULT_MAX_RUNS),
            design=str(data.get("design", "full")),
            n_full=int(data.get("n_full", 0) or 0),
            diagnostics=dict(data.get("diagnostics") or {}),
            phase_locked=bool(data.get("phase_locked", True)),
            notes=[str(n) for n in (data.get("notes") or [])],
        )


@dataclass
class SweepRun:
    """一个 (workflow, 条件) 的运行记录 = 一次处理 + 两张峰表 + 溯源。

    ``measurements`` 只在内存里(写盘的是两张峰表 CSV 与 run.json);
    加载历史记录时该字段为空,峰表以 CSV 为准。
    """

    workflow_id: str
    index: int
    condition: str = ""
    dataset: dict[str, Any] = field(default_factory=dict)
    parameters_requested: dict[str, Any] = field(default_factory=dict)
    parameters_used: dict[str, Any] = field(default_factory=dict)
    parameters_resolved: dict[str, Any] = field(default_factory=dict)
    phase: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"
    warnings: list[dict[str, Any]] = field(default_factory=list)
    message: str = ""
    run_dir: str = ""
    script_path: str = ""
    script_sha256: str = ""
    spectrum_path: str = ""
    spectrum_sha256: str = ""
    log_path: str = ""
    base_script: dict[str, Any] = field(default_factory=dict)
    peak_tables: dict[str, dict[str, Any]] = field(default_factory=dict)
    peak_localization: dict[str, Any] = field(default_factory=dict)
    window: dict[str, Any] = field(default_factory=dict)
    wall_time_s: float = 0.0
    phase_locked: bool = True
    logs_tail: list[str] = field(default_factory=list)
    versions: dict[str, str] = field(default_factory=dict)
    resume_fingerprint: str = ""
    measurements_by_method: dict[str, list[PeakMeasurement]] = field(
        default_factory=dict
    )

    @property
    def run_id(self) -> str:
        """旧名(兼容):workflow_id 即原来的 run_id。"""
        return self.workflow_id

    @property
    def combo(self) -> dict[str, Any]:
        """旧名(兼容):用户给的参数组合行。"""
        return self.parameters_requested

    @property
    def params(self) -> dict[str, Any]:
        """旧名(兼容):实际使用的完整参数。"""
        return self.parameters_used

    @property
    def measurements(self) -> list[PeakMeasurement]:
        """旧名(兼容):抛物线定位的测量(参考方法)。"""
        return list(self.measurements_by_method.get("parabolic") or [])

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "run_id": self.workflow_id,          # 旧字段,值相同
            "index": int(self.index),
            "condition": self.condition,
            "dataset": self.dataset,
            "parameters_requested": self.parameters_requested,
            "parameters_used": self.parameters_used,
            "parameters_resolved": self.parameters_resolved,
            "phase": self.phase,
            "status": self.status,
            "warnings": self.warnings,
            "message": self.message,
            "run_dir": self.run_dir,
            "script_path": self.script_path,
            "script_sha256": self.script_sha256,
            "spectrum_path": self.spectrum_path,
            "spectrum_sha256": self.spectrum_sha256,
            "log_path": self.log_path,
            "base_script": self.base_script,
            "peak_tables": self.peak_tables,
            "peak_localization": self.peak_localization,
            "window": self.window,
            "wall_time_s": float(self.wall_time_s),
            "phase_locked": bool(self.phase_locked),
            "logs_tail": list(self.logs_tail),
            "versions": self.versions,
            "resume_fingerprint": self.resume_fingerprint,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SweepRun:
        return cls(
            workflow_id=str(data.get("workflow_id") or data.get("run_id") or ""),
            index=int(data.get("index", 0) or 0),
            condition=str(data.get("condition", "")),
            dataset=dict(data.get("dataset") or {}),
            parameters_requested=dict(
                data.get("parameters_requested") or data.get("combo") or {}
            ),
            parameters_used=dict(
                data.get("parameters_used") or data.get("params") or {}
            ),
            parameters_resolved=dict(data.get("parameters_resolved") or {}),
            phase=dict(data.get("phase") or {}),
            status=str(data.get("status", "pending")),
            warnings=[
                dict(item)
                for item in (data.get("warnings") or [])
                if isinstance(item, dict)
            ],
            message=str(data.get("message", "")),
            run_dir=str(data.get("run_dir", "")),
            script_path=str(data.get("script_path", "")),
            script_sha256=str(data.get("script_sha256", "")),
            spectrum_path=str(data.get("spectrum_path", "")),
            spectrum_sha256=str(data.get("spectrum_sha256", "")),
            log_path=str(data.get("log_path", "")),
            base_script=dict(data.get("base_script") or {}),
            peak_tables={
                str(k): dict(v)
                for k, v in (data.get("peak_tables") or {}).items()
                if isinstance(v, dict)
            },
            peak_localization=dict(data.get("peak_localization") or {}),
            window={
                str(k): dict(v)
                for k, v in (data.get("window") or {}).items()
                if isinstance(v, dict)
            },
            wall_time_s=float(data.get("wall_time_s", 0.0) or 0.0),
            phase_locked=bool(data.get("phase_locked", True)),
            logs_tail=[str(x) for x in (data.get("logs_tail") or [])],
            versions={
                str(k): str(v) for k, v in (data.get("versions") or {}).items()
            },
            resume_fingerprint=str(data.get("resume_fingerprint", "")),
        )

    def peak_table_path(self, method: str) -> str:
        return str((self.peak_tables.get(str(method)) or {}).get("path", ""))


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


def _axis_root(key: str) -> str:
    """网格键的首段(用于判定是否属后端读取的参数)。"""
    return str(key).split(".", 1)[0]


def validate_axes(
    axes: Mapping[str, Sequence[Any]], *, sampling: str = "uniform"
) -> list[str]:
    """检查网格键:锁定的键报错;确定性/未知键返回提示(不阻断)。

    - 锁定键(``phases``/``direct_phase``/``phase_route``/``sampling.auto_phase``):
      会破坏相位锁定语义或被后端静默忽略 → 抛 `SweepError`,并提示改用
      `phase_delta.<轴>.p0|p1` / `phase.<轴>.p0|p1`;
    - 确定性/策略参数(提取窗口、目标点距、采样表、超时等):提示「一般不必进网格」;
    - 既不在后端读取清单、也不是相位轴的键:提示「可能不会生效」。
    """
    live = _NUS_KEYS if str(sampling) == "nus" else _UNIFORM_KEYS
    notes: list[str] = []
    for raw_key in axes:
        key = str(raw_key)
        if key in _LOCKED_AXIS_KEYS:
            if key in ("phases", "direct_phase"):
                raise SweepError(
                    f"网格里的 {key!r} 会破坏相位锁定:相位请用 "
                    "phase_delta.<轴>.p0|p1(相对参考的偏差,如 ±5°)或 "
                    "phase.<轴>.p0|p1(绝对值)"
                )
            raise SweepError(
                f"网格里的 {key!r} 不支持:该键由参考运行/接口决定,不是自由参数"
            )
        if is_phase_axis(key):
            parse_phase_axis(key)
            continue
        root = _axis_root(key)
        if key in _DETERMINISTIC_KEYS or root in _DETERMINISTIC_KEYS:
            notes.append(
                f"提示: {key} 属确定性/策略参数,一般不必进网格"
                "(改了会换峰集或只是分辨率口径)"
            )
        elif root not in live:
            notes.append(
                f"警告: {key} 不在后端读取的参数清单内,可能不会生效(请核对键名)"
            )
    return notes


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


def infer_axes(combos: Sequence[Mapping[str, Any]]) -> dict[str, list[Any]]:
    """从显式组合表推断各键出现过的取值(首次出现顺序;供校验/留档)。"""
    axes: dict[str, list[Any]] = {}
    for combo in combos:
        for key, value in combo.items():
            bucket = axes.setdefault(str(key), [])
            if value not in bucket:
                bucket.append(value)
    return axes


def _encode_column(rows: Sequence[Mapping[str, Any]], key: str) -> list[float]:
    """一列取值 → 数值(数值列直接取;分类列按首次出现顺序编码)。"""
    raw = [row.get(key) for row in rows]
    if all(
        isinstance(v, (int, float)) and not isinstance(v, bool) for v in raw
    ):
        return [float(v) for v in raw]
    order: list[Any] = []
    for value in raw:
        if value not in order:
            order.append(value)
    return [float(order.index(value)) for value in raw]


def _pairwise_correlation(
    rows: Sequence[Mapping[str, Any]], key_a: str, key_b: str
) -> float:
    """两列编码序号的相关系数(任一侧取值恒定 → 0)。"""
    values_a = _encode_column(rows, key_a)
    values_b = _encode_column(rows, key_b)
    n = len(values_a)
    if n < 2:
        return 0.0
    mean_a = sum(values_a) / n
    mean_b = sum(values_b) / n
    cov = sum((a - mean_a) * (b - mean_b) for a, b in zip(values_a, values_b))
    var_a = sum((a - mean_a) ** 2 for a in values_a)
    var_b = sum((b - mean_b) ** 2 for b in values_b)
    if var_a <= 0 or var_b <= 0:
        return 0.0
    return float(cov / (var_a**0.5 * var_b**0.5))


def design_diagnostics(
    combos: Sequence[Mapping[str, Any]],
    *,
    axes: Mapping[str, Sequence[Any]] | None = None,
) -> dict[str, Any]:
    """设计诊断(信息性,不做设计决策)。

    - ``level_counts``:每个键各取值的出现次数(检查水平均衡);
    - ``duplicated_rows``:完全重复的组合数(重复会浪费运行);
    - ``max_abs_correlation``:两两因子编码序号的 |相关系数| 最大值(0 = 主效应正交;
      分类水平的相关只是诊断量,不是统计检验);
    - ``missing_levels``:声明了 ``axes`` 时未出现的水平取值。

    正交表/部分因子/D-optimal/LHS 由外部工具生成后交给接口,这里只做核对。
    """
    rows = [dict(combo) for combo in combos]
    keys = list(infer_axes(rows).keys())
    level_counts: dict[str, dict[str, int]] = {}
    for key in keys:
        counts: dict[str, int] = {}
        for row in rows:
            token = json.dumps(row.get(key), sort_keys=True, ensure_ascii=False)
            counts[token] = counts.get(token, 0) + 1
        level_counts[key] = counts
    seen: dict[str, int] = {}
    for row in rows:
        token = json.dumps(row, sort_keys=True, ensure_ascii=False)
        seen[token] = seen.get(token, 0) + 1
    duplicated = sum(count - 1 for count in seen.values() if count > 1)
    max_corr = 0.0
    for index, key_a in enumerate(keys):
        for key_b in keys[index + 1:]:
            max_corr = max(
                max_corr, abs(_pairwise_correlation(rows, key_a, key_b))
            )
    diagnostics: dict[str, Any] = {
        "n_runs": len(rows),
        "factors": keys,
        "level_counts": level_counts,
        "duplicated_rows": duplicated,
        "max_abs_correlation": round(max_corr, 6),
    }
    if axes is not None:
        used = infer_axes(rows)
        missing: dict[str, list[Any]] = {}
        for key, values in axes.items():
            absent = [v for v in values if v not in used.get(str(key), [])]
            if absent:
                missing[str(key)] = absent
        diagnostics["missing_levels"] = missing
    return diagnostics


def combos_from_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    axes: Mapping[str, Sequence[Any]] | None = None,
) -> list[dict[str, Any]]:
    """行表(列名 = 轴键)→ 组合列表;``axes`` 给定时校验键与水平。

    外部工具产出的正交表/部分因子表只要整理成「一行一个组合」即可;顺序保留,
    组合按表序执行。
    """
    combos: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, Mapping):
            raise SweepError(f"组合表第 {index} 行不是键值表: {row!r}")
        combo = {str(key): value for key, value in row.items()}
        if not combo:
            raise SweepError(f"组合表第 {index} 行为空")
        if axes is not None:
            for key, value in combo.items():
                if key not in axes:
                    raise SweepError(f"组合表第 {index} 行的 {key!r} 未在 axes 中声明")
                levels = list(axes[key])
                if levels and value not in levels:
                    raise SweepError(
                        f"组合表第 {index} 行的 {key}={value!r} 不在声明水平 "
                        f"{levels!r} 内"
                    )
        combos.append(combo)
    if not combos:
        raise SweepError("组合表为空")
    return combos


def _parse_cell(text: str) -> Any:
    """CSV 单元格 → int/float/bool/字符串。"""
    value = str(text).strip()
    if value.lower() in ("true", "false"):
        return value.lower() == "true"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def load_combo_table(path: Path | str) -> list[dict[str, Any]]:
    """读组合表:CSV/TSV(首行表头 = 轴键)或 YAML/JSON(组合列表)。

    外部设计工具(pyDOE2、Taguchi 正交表、LHS、手写表)能导出成这两种形式之一
    即可直接执行。
    """
    target = Path(path)
    if not target.is_file():
        raise SweepError(f"组合表不存在: {target}")
    suffix = target.suffix.lower()
    if suffix in (".csv", ".tsv", ".txt"):
        import csv

        delimiter = "\t" if suffix == ".tsv" else ","
        text = target.read_text(encoding="utf-8-sig")
        rows = [
            {str(key): _parse_cell(value) for key, value in row.items() if key}
            for row in csv.DictReader(text.splitlines(), delimiter=delimiter)
        ]
        return combos_from_rows(rows)
    import yaml

    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("combos")
    if not isinstance(data, list):
        raise SweepError(f"组合表格式不对(应为列表或 combos: 列表): {target}")
    return combos_from_rows(data)


def write_combo_table(
    path: Path | str, combos: Sequence[Mapping[str, Any]]
) -> Path:
    """写组合表(CSV;列 = 所有出现过的键,首次出现顺序)。"""
    import csv

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    keys = list(infer_axes(combos).keys())
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        for combo in combos:
            writer.writerow({key: combo.get(key, "") for key in keys})
    return target


def _grid_sha256(combos: Sequence[Mapping[str, Any]]) -> str:
    payload = json.dumps(
        [dict(c) for c in combos], sort_keys=True, ensure_ascii=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def plan_sweep(
    reference: ReferenceSpectrum,
    *,
    axes: Mapping[str, Sequence[Any]] | None = None,
    combos: Sequence[Mapping[str, Any]] | None = None,
    max_runs: int = DEFAULT_MAX_RUNS,
    base_params: Mapping[str, Any] | None = None,
    notes: Iterable[str] | None = None,
) -> SweepPlan:
    """由参数轴 + 参考谱生成 workflow 计划(校验键、检查组合数上限)。

    两种入口(必须且只能给一个):

    - ``axes``:各轴候选值 → 接口做全因子展开(便捷路径);
    - ``combos``:**外部给定的组合表**(正交表/部分因子/D-optimal/LHS/手挑都行),
      接口原样按表序执行,**不做任何设计决策**(规范 H2)。

    进网格的应当是「人工处理时会动的参数」:窗函数与窗参数、基线(开关/程度)、
    填零倍数、相位识别偏差(`phase_delta.*`)、NUS 重构参数;确定性/策略参数
    (提取窗口、目标点距、采样表、超时等)会写进 `notes` 提示。
    """
    if (axes is None) == (combos is None):
        raise SweepError("必须且只能给一个:axes(全因子)或 combos(显式组合表)")
    if combos is not None:
        resolved = combos_from_rows(combos)
        design = "explicit"
    else:
        assert axes is not None
        resolved = expand_grid(axes)
        design = "full"
    n_full = len(resolved)
    inferred = infer_axes(resolved)
    axis_notes = validate_axes(inferred, sampling=reference.sampling)
    if len(resolved) > int(max_runs):
        raise SweepError(
            f"参数组合 {len(resolved)} 个超过上限 max_runs={max_runs};"
            "请减小网格/组合表或显式提高上限(长跑请分批)"
        )
    base = (
        dict(base_params)
        if base_params is not None
        else dict(reference.sweep_params)
    )
    phase_locked = reference.direct_phase_override() is not None
    if not phase_locked:
        sampling = dict(base.get("sampling") or {})
        if sampling.get("auto_phase") is not False:
            sampling["auto_phase"] = False
            base["sampling"] = sampling
    plan = SweepPlan(
        axes=inferred,
        combos=resolved,
        base_params=base,
        grid_sha256=_grid_sha256(resolved),
        reference_script_sha256=reference.script_sha256,
        reference_spectrum_sha256=reference.spectrum_sha256,
        reference_peak_table_sha256=reference.peak_table_sha256,
        max_runs=int(max_runs),
        design=design,
        n_full=n_full,
        diagnostics=design_diagnostics(resolved, axes=inferred),
        phase_locked=phase_locked,
        notes=list(notes or []) + axis_notes,
    )
    if not phase_locked:
        plan.notes.append(
            "参考运行没有记录 direct_phase,workflow 改为关闭自动相位搜索"
            "(sampling.auto_phase=False),相位取预设默认值"
        )
    return plan


def _supports_nus_candidates(backend: Any) -> tuple[bool, str]:
    """后端 ``reconstruct_nus`` 是否支持候选输出隔离(out_file/script_name)。"""
    method = getattr(backend, "reconstruct_nus", None)
    if method is None:
        return False, "后端没有 reconstruct_nus(),无法处理 NUS 数据"
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


def _phase_entries(
    effective_phase: Mapping[str, Sequence[float]],
    phase_part: Mapping[str, float],
    reference: ReferenceSpectrum,
) -> dict[str, Any]:
    """相位溯源(规范 G1):每轴 phase_mode + actual_p0/actual_p1。"""
    touched_axes = {parse_phase_axis(key)[1] for key in phase_part}
    overridden = {
        axis: sorted(
            {
                parse_phase_axis(key)[1]
                for key in phase_part
                if parse_phase_axis(key)[1] == axis
            }
        )
        for axis in touched_axes
    }
    entries: dict[str, Any] = {}
    for axis, values in effective_phase.items():
        kinds = {
            parse_phase_axis(key)[0]
            for key in phase_part
            if parse_phase_axis(key)[1] == axis
        }
        if kinds == {"phase"}:
            mode = "manual_absolute"
        elif "phase_delta" in kinds:
            mode = "manual_delta_from_reference"
        else:
            mode = "auto_reference_locked"
        entries[str(axis)] = {
            "phase_mode": mode,
            "actual_p0": float(values[0]),
            "actual_p1": float(values[1]),
            "source": (
                f"reference_run:{reference.run_id}"
                if reference.run_id
                else "reference_run"
            ),
            "overridden_components": overridden.get(str(axis), []),
        }
    return entries


def _smile_entries(
    params: Mapping[str, Any],
    requested: Mapping[str, Any],
    effective: Mapping[str, Any],
) -> dict[str, Any]:
    """SMILE 自动分档的实际结果(规范 G2):requested vs actual + 来源。"""
    entries: dict[str, Any] = {}
    for key, record_key in (("nsigma", "nSigma"), ("thresh", "thresh")):
        actual = effective.get(record_key, effective.get(key))
        if actual is None:
            actual = params.get(key, params.get(record_key))
        user_key = None
        for candidate in (key, record_key):
            if candidate in requested:
                user_key = candidate
                break
        if actual is None and user_key is None:
            continue
        entries[key] = {
            "requested": (
                requested[user_key] if user_key is not None else "auto(smile_tier)"
            ),
            "actual": actual,
            "source": "user" if user_key is not None else "auto(smile_tier)",
        }
    return entries


def _warnings_for(
    measurements: Sequence[PeakMeasurement],
    *,
    method: str,
    window: Mapping[str, Any],
    fallback_reason: str = "",
) -> list[dict[str, Any]]:
    """把逐峰 QC 汇总成 workflow 警告(规范 D9/G3,不静默)。"""
    warnings: list[dict[str, Any]] = []

    def _add(code: str, message: str, peaks: Sequence[str], **extra: Any) -> None:
        warnings.append(
            {
                "code": code,
                "message": message,
                "count": len(peaks),
                "peaks": list(peaks[:20]),
                **extra,
            }
        )

    missing = [
        m.reference_peak_id or f"R{m.peak_id:04d}"
        for m in measurements
        if not m.found
    ]
    if missing:
        _add(
            WARN_PEAK_NOT_DETECTED,
            f"{len(missing)} 个参考峰在该谱上未检测到(detected=false,记录保留)",
            missing,
            localization_method=str(method),
        )
    edges = [
        m.reference_peak_id or f"R{m.peak_id:04d}"
        for m in measurements
        if m.found and m.window_edge
    ]
    if edges:
        _add(
            WARN_PEAK_WINDOW_EDGE,
            f"{len(edges)} 个峰落在搜索窗口边界(真峰可能在窗外,可加大 window_ppm)",
            edges,
            localization_method=str(method),
        )
    outside = [
        m.reference_peak_id or f"R{m.peak_id:04d}"
        for m in measurements
        if m.out_of_range
    ]
    if outside:
        _add(
            WARN_PEAK_OUT_OF_RANGE,
            f"{len(outside)} 个参考峰位置落在谱范围外",
            outside,
            localization_method=str(method),
        )
    for spec in (window or {}).values():
        if not isinstance(spec, Mapping):
            continue
        if str(spec.get("source", "")).startswith("points(回退"):
            _add(
                WARN_WINDOW_FALLBACK,
                "窗口无法按物理宽度换算,已回退固定点数",
                [],
                axis=str(spec.get("nucleus", "")),
            )
            break
    if str(method) == "gaussian":
        if fallback_reason:
            _add(
                WARN_GAUSSIAN_UNSUPPORTED,
                f"高斯定位不适用({fallback_reason}),位置回退抛物线并留档",
                [m.reference_peak_id for m in measurements],
                localization_method="gaussian",
            )
        fallback = [
            m.reference_peak_id or f"R{m.peak_id:04d}"
            for m in measurements
            if (m.localization or {}).get("fallback")
        ]
        if fallback:
            reasons: dict[str, int] = {}
            for m in measurements:
                record = m.localization or {}
                if not record.get("fallback"):
                    continue
                reason = str(record.get("fallback_reason", "") or "")
                reasons[reason] = reasons.get(reason, 0) + 1
            _add(
                WARN_GAUSSIAN_FALLBACK,
                f"{len(fallback)} 个峰高斯拟合失败/回退抛物线(原因已落盘)",
                fallback,
                reasons=reasons,
            )
        boundary = [
            m.reference_peak_id or f"R{m.peak_id:04d}"
            for m in measurements
            if (m.localization or {}).get("boundary_hit")
        ]
        if boundary:
            _add(
                WARN_GAUSSIAN_BOUNDARY_HIT,
                f"{len(boundary)} 个峰的高斯中心/宽度撞到拟合边界",
                boundary,
            )
    return warnings


def _localization_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    detected = sum(1 for row in rows if row.get("detected"))
    fallback = sum(1 for row in rows if row.get("fallback"))
    reasons: dict[str, int] = {}
    for row in rows:
        if not row.get("fallback"):
            continue
        reason = str(row.get("fallback_reason", "") or "")
        reasons[reason] = reasons.get(reason, 0) + 1
    return {
        "n_peaks": int(total),
        "n_detected": int(detected),
        "n_missing": int(total - detected),
        "n_fallback": int(fallback),
        "fallback_reasons": reasons,
        "n_boundary_hit": sum(1 for row in rows if row.get("boundary_hit")),
    }


def _write_log(
    path: Path,
    *,
    header: Mapping[str, Any],
    logs: Sequence[str],
    warnings: Sequence[Mapping[str, Any]] = (),
) -> Path:
    """写完整运行日志(不是只有尾部;规范 D7)。"""
    lines = ["# NMRForge workflow run log", ""]
    for key, value in header.items():
        if isinstance(value, (dict, list)):
            rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
        else:
            rendered = str(value)
        lines.append(f"{key}: {rendered}")
    lines.append("")
    lines.append("--- processing log ---")
    lines.extend(str(line) for line in logs)
    if warnings:
        lines.append("")
        lines.append("--- warnings ---")
        for warning in warnings:
            lines.append(
                f"[{warning.get('code')}] {warning.get('message')} "
                f"(count={warning.get('count')})"
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _write_run(run: SweepRun) -> None:
    target = Path(run.run_dir) / "run.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = run.to_dict()
    payload["updated"] = now_iso()
    payload["software_version"] = software_version()
    payload["versions"] = dict(run.versions or tool_versions())
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


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


def _resume_fingerprint(
    *,
    combo: Mapping[str, Any],
    target: DatasetRef,
    reference: ReferenceSpectrum,
    peaks: Sequence[dict[str, Any]] | None,
    window_pts: int | None,
    window_ppm: float | None,
    sign: str,
    roi_f1_ppm: float | None,
    roi_f2_ppm: float | None,
) -> str:
    """计算会改变单条件运行结果的规范化输入指纹。"""
    param_part, phase_part = split_combo(combo)
    params = merge_overrides(dict(reference.sweep_params), param_part)
    effective_phase = apply_phase_axes(
        {
            axis: list(pair)
            for axis, pair in (reference.direct_phase_override() or {}).items()
        },
        phase_part,
    )
    peak_identity: Any = (
        [dict(row) for row in peaks]
        if peaks is not None
        else {
            "path": reference.peak_table_path,
            "sha256": reference.peak_table_sha256,
        }
    )
    payload = {
        "schema": "nmrforge_api.resume.v1",
        "dataset": target.to_dict(),
        "combo": dict(combo),
        "parameters_used": params,
        "phase": effective_phase,
        "reference": {
            "dataset_key": reference.dataset_key,
            "script_sha256": reference.script_sha256,
            "spectrum_sha256": reference.spectrum_sha256,
            "peak_table_sha256": reference.peak_table_sha256,
        },
        "peaks": peak_identity,
        "measurement": {
            "window_pts": window_pts,
            "window_ppm": window_ppm,
            "sign": sign,
            "roi_f1_ppm": roi_f1_ppm,
            "roi_f2_ppm": roi_f2_ppm,
        },
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()



def _condition_references(
    session: StudySession,
    datasets: Sequence[DatasetRef],
    reference: ReferenceSpectrum | None,
) -> dict[str, ReferenceSpectrum]:
    """每个条件 → 参考谱(显式传入的参考覆盖对应条件)。"""
    refs: dict[str, ReferenceSpectrum] = {}
    for ref in datasets:
        if reference is not None and reference.dataset_key == ref.key:
            refs[ref.key] = reference
            continue
        loaded = load_reference(session, ref)
        if loaded is None:
            raise SweepError(
                f"条件 {ref.condition or ref.key} 还没有参考谱:先调用 "
                "build_reference()/ensure_reference_peaks()"
            )
        refs[ref.key] = loaded
    return refs


def _workflow_record(runs: Sequence[SweepRun], plan: SweepPlan) -> dict[str, Any]:
    """一个 workflow 的汇总记录(workflow.json)。"""
    ordered = sorted(runs, key=lambda run: str(run.condition))
    statuses = {run.status for run in ordered}
    if STATUS_FAILED in statuses:
        status = STATUS_FAILED
    elif STATUS_WARNING in statuses:
        status = STATUS_WARNING
    else:
        status = STATUS_SUCCESS
    first = ordered[0] if ordered else None
    warnings = [warning for run in ordered for warning in run.warnings]
    return {
        "workflow_id": first.workflow_id if first else "",
        "index": int(first.index) if first else 0,
        "status": status,
        "message": "; ".join(
            f"{run.condition or run.dataset.get('key', '')}: {run.status}"
            for run in ordered
        ),
        "parameters_requested": dict(first.parameters_requested) if first else {},
        "conditions": [run.condition for run in ordered],
        "condition_records": [
            {
                "condition": run.condition,
                "dataset": run.dataset,
                "status": run.status,
                "message": run.message,
                "parameters_used": run.parameters_used,
                "parameters_resolved": run.parameters_resolved,
                "phase": run.phase,
                "warnings": run.warnings,
                "script_path": run.script_path,
                "script_sha256": run.script_sha256,
                "spectrum_path": run.spectrum_path,
                "spectrum_sha256": run.spectrum_sha256,
                "log_path": run.log_path,
                "peak_tables": run.peak_tables,
                "peak_localization": run.peak_localization,
                "window": run.window,
                "run_json": str(Path(run.run_dir) / "run.json"),
                "versions": run.versions,
                "wall_time_s": run.wall_time_s,
            }
            for run in ordered
        ],
        "warnings": warnings,
        "versions": dict(first.versions) if first else {},
        "base_script": dict(first.base_script) if first else {},
        "grid_sha256": plan.grid_sha256,
        "updated": now_iso(),
    }


def write_workflow_record(
    session: StudySession,
    workflow_id: str,
    plan: SweepPlan,
    runs: Sequence[SweepRun] | None = None,
) -> dict[str, Any]:
    """写 ``study/workflows/<workflow_id>/workflow.json`` + 组合级日志。"""
    workflow_dir = session.workflows_dir / workflow_id
    current_runs = [
        run
        for run in (runs if runs is not None else load_runs(session))
        if run.workflow_id == workflow_id
    ]
    record = _workflow_record(current_runs, plan)
    workflow_dir.mkdir(parents=True, exist_ok=True)
    (workflow_dir / "workflow.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    logs: list[str] = []
    for run in sorted(current_runs, key=lambda item: str(item.condition)):
        log_path = Path(run.log_path) if run.log_path else None
        if log_path is not None and log_path.is_file():
            logs.append(f"===== {run.condition or run.dataset.get('key', '')} =====")
            logs.append(log_path.read_text(encoding="utf-8").rstrip())
    _write_log(
        workflow_dir / "log.txt",
        header={
            "workflow_id": workflow_id,
            "status": record["status"],
            "parameters_requested": record["parameters_requested"],
            "conditions": record["conditions"],
        },
        logs=logs,
        warnings=record["warnings"],
    )
    return record


def run_sweep(
    session: StudySession,
    plan: SweepPlan,
    *,
    reference: ReferenceSpectrum | None = None,
    datasets: Sequence[DatasetRef] | None = None,
    peaks: Sequence[dict[str, Any]] | None = None,
    window_pts: int | None = None,
    window_ppm: float | None = None,
    sign: str = "abs",
    refine: str | None = None,
    roi_f1_ppm: float | None = None,
    roi_f2_ppm: float | None = None,
    resume: bool = True,
    stop_on_error: bool = False,
    progress: Callable[[str], None] | None = None,
    on_run: Callable[[SweepRun], None] | None = None,
) -> list[SweepRun]:
    """执行 workflow 计划:每个组合对**全部条件**跑一遍处理 + 两种定位。

    返回逐 (workflow, 条件) 的运行记录(成功/警告/失败都在列表里)。

    - ``datasets`` 缺省用会话里的全部条件(A/B);``reference`` 可显式传入该
      条件的参考(按 dataset_key 匹配);
    - ``peaks`` 可显式给参考峰表行(否则读各条件参考冻结的身份表,峰身份一致);
    - 峰位搜索窗口按**物理宽度**定义(默认 1.5×该轴核素线宽折算 ppm),逐组合按
      候选谱的实际点距换算点数,换算结果写进 ``run.json`` 的 ``window``;
    - ``refine`` 参数已废弃(两种定位方法现在**始终都要跑**),仅为兼容保留。
    """
    if refine not in (None, "parabolic", "gaussian", "none"):
        raise SweepError(f"未知 refine: {refine!r}")
    targets = list(datasets) if datasets is not None else list(session.datasets)
    if not targets:
        raise SweepError("研究里还没有数据集:先调用 add_dataset()")
    references = _condition_references(session, targets, reference)
    backend = session.backend
    if not hasattr(backend, "process"):
        raise SweepError("后端不支持 process(),无法执行 workflow")
    for target in targets:
        ref = references[target.key]
        if not ref.sweep_supported:
            if str(ref.sampling) == "nus":
                raise SweepError(
                    f"当前只支持 2D NUS 参数组合,检测到 {ref.ndim}D NUS"
                    "(3D NUS 需要切片流与候选输出进一步改造,见 "
                    "docs/external-api/09-limitations-and-roadmap.md)"
                )
            raise SweepError(f"当前不支持 {ref.ndim}D/{ref.sampling} 数据的参数组合")

    experiments = {
        target.key: read_experiment(session.manager, target.exp_id, target.data_id)
        for target in targets
    }
    results: list[SweepRun] = []

    def _emit(message: str) -> None:
        if progress is not None:
            progress(message)

    session.workflows_dir.mkdir(parents=True, exist_ok=True)
    for index, combo in enumerate(plan.combos, start=1):
        workflow_id = workflow_id_for(index)
        workflow_dir = session.workflows_dir / workflow_id
        workflow_dir.mkdir(parents=True, exist_ok=True)
        for target in targets:
            ref = references[target.key]
            experiment = experiments[target.key]
            run_dir = workflow_dir / target.token
            run_dir.mkdir(parents=True, exist_ok=True)
            fingerprint = _resume_fingerprint(
                combo=combo,
                target=target,
                reference=ref,
                peaks=peaks,
                window_pts=window_pts,
                window_ppm=window_ppm,
                sign=sign,
                roi_f1_ppm=roi_f1_ppm,
                roi_f2_ppm=roi_f2_ppm,
            )
            if resume:
                cached = _load_run(run_dir)
                if (
                    cached is not None
                    and cached.status in SUCCESS_STATUSES
                    and cached.resume_fingerprint == fingerprint
                ):
                    results.append(cached)
                    _emit(
                        f"[{workflow_id}/{target.condition}] 已存在,跳过(断点续跑)"
                    )
                    if on_run is not None:
                        on_run(cached)
                    continue
            run = _run_condition(
                session,
                plan=plan,
                combo=combo,
                workflow_id=workflow_id,
                index=index,
                target=target,
                reference=ref,
                experiment=experiment,
                run_dir=run_dir,
                peaks=peaks,
                window_pts=window_pts,
                window_ppm=window_ppm,
                sign=sign,
                roi_f1_ppm=roi_f1_ppm,
                roi_f2_ppm=roi_f2_ppm,
                resume_fingerprint=fingerprint,
                emit=_emit,
            )
            results.append(run)
            if on_run is not None:
                on_run(run)
            if stop_on_error and run.status == STATUS_FAILED:
                break
        write_workflow_record(session, workflow_id, plan, runs=results)
        if stop_on_error and any(
            run.workflow_id == workflow_id and run.status == STATUS_FAILED
            for run in results
        ):
            break
    return results


def _run_condition(
    session: StudySession,
    *,
    plan: SweepPlan,
    combo: Mapping[str, Any],
    workflow_id: str,
    index: int,
    target: DatasetRef,
    reference: ReferenceSpectrum,
    experiment: Any,
    run_dir: Path,
    peaks: Sequence[dict[str, Any]] | None,
    window_pts: int | None,
    window_ppm: float | None,
    sign: str,
    roi_f1_ppm: float | None,
    roi_f2_ppm: float | None,
    resume_fingerprint: str,
    emit: Callable[[str], None],
) -> SweepRun:
    """跑单个 (workflow, 条件):处理 → 两种定位 → 两张峰表 + 完整记录。"""
    logs: list[str] = []

    def _log(message: str) -> None:
        logs.append(str(message))
        emit(f"[{workflow_id}/{target.condition}] {message}")

    param_part, phase_part = split_combo(combo)
    params = merge_overrides(dict(reference.sweep_params), param_part)
    base_phase = reference.direct_phase_override() or {}
    effective_phase = apply_phase_axes(
        {axis: list(pair) for axis, pair in base_phase.items()}, phase_part
    )
    override = (
        {axis: (values[0], values[1]) for axis, values in effective_phase.items()}
        if effective_phase
        else None
    )
    dataset_label = f"{target.key}"
    run = SweepRun(
        workflow_id=workflow_id,
        index=int(index),
        condition=target.condition,
        dataset={
            "key": target.key,
            "exp_id": target.exp_id,
            "data_id": target.data_id,
            "title": target.title,
            "condition": target.condition,
            "ndim": int(target.ndim),
            "nuclei": list(target.nuclei),
            "sampling": target.sampling,
            "source": target.source,
        },
        parameters_requested={str(k): v for k, v in combo.items()},
        parameters_used=params,
        phase=_phase_entries(effective_phase, phase_part, reference),
        run_dir=str(run_dir),
        phase_locked=override is not None,
        versions=tool_versions(),
        resume_fingerprint=resume_fingerprint,
        base_script={
            "path": reference.script_path,
            "sha256": reference.script_sha256,
            "spectrum_path": reference.frozen_spectrum,
            "spectrum_sha256": reference.spectrum_sha256,
        },
    )
    is_nus = str(getattr(experiment.sampling, "mode", "")) == "nus"
    started = time.perf_counter()
    _log(f"开始 {workflow_id}: {dict(combo)}")
    try:
        if is_nus:
            ok, reason = _supports_nus_candidates(session.backend)
            if not ok:
                raise SweepError(reason)
            nus_params = normalize_nus_params(params)
            if override:
                pair = effective_phase.get(f"F{experiment.ndim}")
                if pair is not None:
                    nus_params["direct_phase"] = [float(pair[0]), float(pair[1])]
            if effective_phase:
                nus_params["phases"] = {
                    axis: [values[0], values[1]]
                    for axis, values in effective_phase.items()
                }
            response = session.backend.reconstruct_nus(
                experiment,
                nus_params,
                progress=_log,
                script_name=f"{workflow_id}_{target.token}.com",
                out_file=f"{workflow_id}_{target.token}.ft2",
            )
        else:
            method_plan = select_method(experiment)
            response = session.backend.process(
                experiment,
                method_plan,
                params=params,
                direct_phase_override=override,
                script_name=f"{workflow_id}_{target.token}.com",
                out_file=f"{workflow_id}_{target.token}.ft2",
                progress=_log,
            )
    except Exception as exc:  # noqa: BLE001 - 单条件失败不中断整轮
        response = {
            "success": False,
            "message": f"{type(exc).__name__}: {exc}",
            "logs": logs,
        }
    run.wall_time_s = round(time.perf_counter() - started, 3)
    logs.extend(str(line) for line in (response.get("logs") or []))
    run.logs_tail = logs[-40:]
    if not response.get("success"):
        run.status = STATUS_FAILED
        run.message = str(response.get("message", "处理失败"))
        run.log_path = str(
            _write_log(
                run_dir / "log.txt",
                header={
                    "workflow_id": workflow_id,
                    "condition": target.condition,
                    "dataset": dataset_label,
                    "status": run.status,
                    "parameters_requested": run.parameters_requested,
                    "parameters_used": run.parameters_used,
                    "message": run.message,
                },
                logs=logs,
            )
        )
        _write_run(run)
        _log(f"失败: {run.message}")
        return run

    script_src = session.work_dir / f"{workflow_id}_{target.token}.com"
    spectrum_src = Path(str(response.get("spectrum_path", "")))
    if script_src.is_file():
        target_script = run_dir / "process.com"
        shutil.copy2(script_src, target_script)
        run.script_path = str(target_script)
        run.script_sha256 = sha256_file(target_script)
    if not spectrum_src.is_file():
        run.status = STATUS_FAILED
        run.message = f"后端返回的谱不存在: {spectrum_src}"
        run.log_path = str(
            _write_log(
                run_dir / "log.txt",
                header={
                    "workflow_id": workflow_id,
                    "condition": target.condition,
                    "dataset": dataset_label,
                    "status": run.status,
                    "message": run.message,
                },
                logs=logs,
            )
        )
        _write_run(run)
        return run
    target_spectrum = run_dir / f"spectrum{spectrum_src.suffix}"
    shutil.copy2(spectrum_src, target_spectrum)
    run.spectrum_path = str(target_spectrum)
    run.spectrum_sha256 = sha256_file(target_spectrum)

    try:
        spectrum_axes = read_spectrum_axes(target_spectrum)
        run.window = {
            str(axis): dict(spec)
            for axis, spec in window_points_by_axis(
                spectrum_axes,
                window_pts=window_pts,
                window_ppm=window_ppm,
            ).items()
        }
        peak_rows = (
            [dict(row) for row in peaks]
            if peaks is not None
            else read_reference_peaks(reference.peak_table_path)
        )
        parabolic = measure_peak_positions(
            target_spectrum,
            peak_rows,
            axes=spectrum_axes,
            window_pts=window_pts,
            window_ppm=window_ppm,
            sign=sign,
            refine="parabolic",
        )
    except Exception as exc:  # noqa: BLE001 - 测量失败也算该条件失败
        run.status = STATUS_FAILED
        run.message = f"峰位测量失败: {type(exc).__name__}: {exc}"
        run.log_path = str(
            _write_log(
                run_dir / "log.txt",
                header={
                    "workflow_id": workflow_id,
                    "condition": target.condition,
                    "dataset": dataset_label,
                    "status": run.status,
                    "message": run.message,
                },
                logs=logs,
            )
        )
        _write_run(run)
        return run

    run.measurements_by_method["parabolic"] = list(parabolic)
    parabolic_rows = peak_table_rows(
        parabolic,
        workflow_id=workflow_id,
        condition=target.condition,
        dataset=dataset_label,
        method="parabolic",
    )
    warnings = _warnings_for(parabolic, method="parabolic", window=run.window)
    if int(getattr(experiment, "ndim", 2)) == 2:
        try:
            gaussian = measure_peak_positions(
                target_spectrum,
                peak_rows,
                axes=spectrum_axes,
                window_pts=window_pts,
                window_ppm=window_ppm,
                sign=sign,
                refine="gaussian",
                roi_f1_ppm=roi_f1_ppm,
                roi_f2_ppm=roi_f2_ppm,
            )
        except Exception as exc:  # noqa: BLE001 - 单条件测量失败不中断整轮
            run.status = STATUS_FAILED
            run.message = f"Gaussian 峰位测量失败: {type(exc).__name__}: {exc}"
            logs.append(run.message)
            run.logs_tail = logs[-40:]
            run.log_path = str(
                _write_log(
                    run_dir / "log.txt",
                    header={
                        "workflow_id": workflow_id,
                        "condition": target.condition,
                        "dataset": dataset_label,
                        "status": run.status,
                        "message": run.message,
                    },
                    logs=logs,
                )
            )
            _write_run(run)
            return run
        run.measurements_by_method["gaussian"] = list(gaussian)
        gaussian_rows = peak_table_rows(
            gaussian,
            workflow_id=workflow_id,
            condition=target.condition,
            dataset=dataset_label,
            method="gaussian",
        )
        warnings.extend(_warnings_for(gaussian, method="gaussian", window=run.window))
    else:
        gaussian_rows = gaussian_fallback_rows(
            parabolic,
            workflow_id=workflow_id,
            condition=target.condition,
            dataset=dataset_label,
            reason=GAUSSIAN_UNSUPPORTED_NDIM_REASON,
        )
        run.measurements_by_method["gaussian"] = list(parabolic)
        warnings.extend(
            _warnings_for(
                parabolic,
                method="gaussian",
                window=run.window,
                fallback_reason=GAUSSIAN_UNSUPPORTED_NDIM_REASON,
            )
        )
    run.peak_tables = {
        "parabolic": peak_table_digest(
            write_peak_table(run_dir / "peak_table_parabolic.csv", parabolic_rows)
        ),
        "gaussian": peak_table_digest(
            write_peak_table(run_dir / "peak_table_gaussian.csv", gaussian_rows)
        ),
    }
    run.peak_localization = {
        "parabolic": _localization_summary(parabolic_rows),
        "gaussian": _localization_summary(gaussian_rows),
    }
    run.parameters_resolved = {
        "phase": run.phase,
        "smile": (
            _smile_entries(params, combo, response.get("effective_params") or {})
            if is_nus
            else {}
        ),
        "spectrum_noise_sigma": {
            "value": (
                float(getattr(parabolic[0], "noise_sigma", 0.0)) if parabolic else 0.0
            ),
            "source": "core.qc.noise(robust MAD)",
            "used_for": "SNR",
        },
        "window": run.window,
        "effective_params_backend": response.get("effective_params") or {},
    }
    run.warnings = warnings
    if warnings:
        run.status = STATUS_WARNING
        run.message = (
            f"完成但有待注意项:{', '.join(str(w.get('code')) for w in warnings)}"
        )
    else:
        run.status = STATUS_SUCCESS
        run.message = (
            f"完成,{len(parabolic)} 个参考峰 × 2 种定位 "
            f"(detected {sum(1 for m in parabolic if m.found)})"
        )
    run.log_path = str(
        _write_log(
            run_dir / "log.txt",
            header={
                "workflow_id": workflow_id,
                "condition": target.condition,
                "dataset": dataset_label,
                "status": run.status,
                "parameters_requested": run.parameters_requested,
                "parameters_used": run.parameters_used,
                "parameters_resolved": run.parameters_resolved,
                "phase": run.phase,
                "base_script": run.base_script,
                "script_sha256": run.script_sha256,
                "spectrum_sha256": run.spectrum_sha256,
                "peak_tables": run.peak_tables,
                "versions": run.versions,
                "wall_time_s": run.wall_time_s,
            },
            logs=logs,
            warnings=warnings,
        )
    )
    try:
        spectrum_src.unlink()
    except OSError:
        pass
    _write_run(run)
    _log(f"{run.status}: {run.message}")
    return run


def load_plan(session: StudySession) -> SweepPlan | None:
    """读取研究目录里的 workflow 计划(``records/sweep_plan.json``)。"""
    path = session.records_dir / "sweep_plan.json"
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return SweepPlan.from_dict(raw) if isinstance(raw, dict) else None


def load_runs(session: StudySession) -> list[SweepRun]:
    """读取全部 (workflow, 条件) 运行记录(按 workflow_id、条件排序)。"""
    runs: list[SweepRun] = []
    root = session.workflows_dir
    if not root.is_dir():
        return runs
    plan = load_plan(session)
    active = set(plan.workflow_ids()) if plan is not None else None
    for workflow_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        if active is not None and workflow_dir.name not in active:
            continue
        for condition_dir in sorted(
            path for path in workflow_dir.iterdir() if path.is_dir()
        ):
            run = _load_run(condition_dir)
            if run is not None:
                runs.append(run)
    return runs


def load_workflows(session: StudySession) -> list[dict[str, Any]]:
    """读取组合级记录(``workflows/<id>/workflow.json``;损坏项跳过)。"""
    records: list[dict[str, Any]] = []
    root = session.workflows_dir
    if not root.is_dir():
        return records
    plan = load_plan(session)
    active = set(plan.workflow_ids()) if plan is not None else None
    for workflow_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        if active is not None and workflow_dir.name not in active:
            continue
        path = workflow_dir / "workflow.json"
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


def workflow_summary(runs: Sequence[SweepRun]) -> dict[str, Any]:
    """workflow 状态计数(success / success_with_warning / failed)。"""
    return {
        "n_runs": len(runs),
        "success": sum(1 for run in runs if run.status == STATUS_SUCCESS),
        "success_with_warning": sum(
            1 for run in runs if run.status == STATUS_WARNING
        ),
        "failed": sum(1 for run in runs if run.status == STATUS_FAILED),
    }


__all__ = [
    "DEFAULT_MAX_RUNS",
    "STATUS_FAILED",
    "STATUS_SUCCESS",
    "STATUS_WARNING",
    "SUCCESS_STATUSES",
    "SweepPlan",
    "SweepRun",
    "WARN_GAUSSIAN_BOUNDARY_HIT",
    "WARN_GAUSSIAN_FALLBACK",
    "WARN_GAUSSIAN_UNSUPPORTED",
    "WARN_PEAK_NOT_DETECTED",
    "WARN_PEAK_OUT_OF_RANGE",
    "WARN_PEAK_WINDOW_EDGE",
    "WARN_WINDOW_FALLBACK",
    "apply_phase_axes",
    "combos_from_rows",
    "design_diagnostics",
    "expand_grid",
    "infer_axes",
    "is_phase_axis",
    "load_combo_table",
    "load_plan",
    "load_runs",
    "load_workflows",
    "merge_overrides",
    "normalize_nus_params",
    "parse_phase_axis",
    "plan_sweep",
    "run_sweep",
    "split_combo",
    "validate_axes",
    "workflow_id_for",
    "workflow_summary",
    "write_combo_table",
    "write_workflow_record",
]
