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
    window_points_by_axis,
)
from nmrforge_api.reference import ReferenceSpectrum, load_reference
from nmrforge_api.session import StudySession, now_iso
from workflow.pick_peaks import read_spectrum_axes
from workflow.stepwise import read_experiment

DEFAULT_MAX_RUNS = 256

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


def is_phase_axis(key: str) -> bool:
    """是否相位轴(phase./phase_delta. 前缀)。"""
    return str(key).startswith(PHASE_PREFIXES)


def parse_phase_axis(key: str) -> tuple[str, str, str]:
    """``phase.<轴>.p0`` / ``phase_delta.<轴>.p1`` → (kind, axis, comp)。

    kind:``phase``(绝对值)| ``phase_delta``(相对参考的偏差);comp:``p0``/``p1``。
    """
    kind, _, rest = str(key).partition(".")
    if kind not in ("phase", "phase_delta") or not rest:
        raise SweepError(f"相位轴写法不对: {key}(应为 phase.<轴>.p0 或 phase_delta.<轴>.p1)")
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
    """扫描计划:参数轴 → 组合网格 + 参考基底参数。"""

    axes: dict[str, list[Any]] = field(default_factory=dict)
    combos: list[dict[str, Any]] = field(default_factory=list)
    base_params: dict[str, Any] = field(default_factory=dict)
    grid_sha256: str = ""
    reference_script_sha256: str = ""
    reference_spectrum_sha256: str = ""
    max_runs: int = DEFAULT_MAX_RUNS
    design: str = "full"      # "full"(接口展开全因子) | "explicit"(外部给组合表)
    n_full: int = 0           # 全因子规模(对照用;显式设计等于组合数)
    diagnostics: dict[str, Any] = field(default_factory=dict)
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
            "design": self.design,
            "n_full": int(self.n_full),
            "diagnostics": self.diagnostics,
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
            design=str(data.get("design", "full")),
            n_full=int(data.get("n_full", 0) or 0),
            diagnostics=dict(data.get("diagnostics") or {}),
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
    phase: dict[str, list[float]] = field(default_factory=dict)
    # 本组合的峰位测量窗口换算记录(逐轴 points/ppm/点距/来源)
    window: dict[str, Any] = field(default_factory=dict)
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
            "phase": {str(k): [float(v[0]), float(v[1])] for k, v in self.phase.items()},
            "window": {str(k): dict(v) for k, v in self.window.items()},
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
            phase={
                str(k): [float(v[0]), float(v[1])]
                for k, v in (data.get("phase") or {}).items()
                if isinstance(v, (list, tuple)) and len(v) >= 2
            },
            window={
                str(k): dict(v)
                for k, v in (data.get("window") or {}).items()
                if isinstance(v, dict)
            },
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


def _axis_root(key: str) -> str:
    """网格键的首段(用于判定是否属后端读取的参数)。"""
    return str(key).split(".", 1)[0]


def validate_axes(
    axes: Mapping[str, Sequence[Any]], *, sampling: str = "uniform"
) -> list[str]:
    """检查网格键:锁定的键报错;确定性/未知键返回提示(不阻断)。

    - 锁定键(`phases`/`direct_phase`/`phase_route`/`sampling.auto_phase`):
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
    即可直接扫描。
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
    """由参数轴 + 参考谱生成扫描计划(校验键、检查组合数上限)。

    两种入口(必须且只能给一个):

    - ``axes``:各轴候选值 → 接口做全因子展开(便捷路径);
    - ``combos``:**外部给定的组合表**(正交表/部分因子/D-optimal/LHS/手挑都行),
      接口原样按表序执行,**不做任何设计决策**。

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
        axes=inferred,
        combos=resolved,
        base_params=base,
        grid_sha256=_grid_sha256(resolved),
        reference_script_sha256=reference.script_sha256,
        reference_spectrum_sha256=reference.spectrum_sha256,
        max_runs=int(max_runs),
        design=design,
        n_full=n_full,
        diagnostics=design_diagnostics(resolved, axes=inferred),
        phase_locked=phase_locked,
        notes=list(notes or []) + axis_notes,
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
    window_pts: int | None = None,
    window_ppm: float | None = None,
    sign: str = "abs",
    refine: str = "parabolic",
    roi_f1_ppm: float | None = None,
    roi_f2_ppm: float | None = None,
    resume: bool = True,
    stop_on_error: bool = False,
    progress: Callable[[str], None] | None = None,
    on_run: Callable[[SweepRun], None] | None = None,
) -> list[SweepRun]:
    """执行扫描计划,返回逐组合运行记录(成功/失败都在列表里)。

    峰位测量窗口按**物理宽度**定义(默认 1.5×该轴核素线宽折算 ppm,
    见 ``core.peaks.axis_units``),逐组合按该候选谱的实际点数换算:
    零填零 k 倍只改点距、不改变窗口覆盖的 ppm 宽度,峰位差里因此不混入
    「窗口口径随处理参数漂移」的成分(用户方案 A)。``window_ppm`` 显式
    给物理半径;``window_pts`` 强制点数(不推荐,跨分辨率不可比)。每个
    组合的换算结果(逐轴点数/ppm/点距)写进 ``run.json`` 的 ``window``。
    ``refine``(2026-09-13):``parabolic``(默认,既有 3 点抛物线)或
    ``gaussian``(2D 高斯拟合,仅 2D;ROI 半径 ``roi_f1_ppm``/``roi_f2_ppm``,
    缺省读 config ``peaks.localization``;失败逐峰回退抛物线并留原因)。
    两种方法对同一批峰独立运行,可直接比较峰位差。
    """
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

    base_override = ref.direct_phase_override()
    reference_phase = {
        axis: [float(pair[0]), float(pair[1])]
        for axis, pair in (base_override or {}).items()
    }
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
        param_part, phase_part = split_combo(combo)
        params = merge_overrides(plan.base_params, param_part)
        effective_phase = apply_phase_axes(reference_phase, phase_part)
        override = (
            {
                axis: (values[0], values[1])
                for axis, values in effective_phase.items()
            }
            if effective_phase
            else None
        )
        run = SweepRun(
            run_id=run_id,
            index=index,
            combo={str(k): v for k, v in combo.items()},
            params=params,
            run_dir=str(run_dir),
            phase_locked=override is not None,
            phase={axis: list(values) for axis, values in effective_phase.items()},
        )
        logs: list[str] = []

        def _log(message: str, _logs: list[str] = logs) -> None:
            _logs.append(str(message))
            _emit(f"[{run_id}] {message}")

        started = time.perf_counter()
        _emit(f"[{run_id}] 开始 {combo}")
        try:
            if is_nus:
                nus_params = normalize_nus_params(params)
                if override:
                    direct_axis = f"F{experiment.ndim}"
                    pair = effective_phase.get(direct_axis)
                    if pair is not None:
                        nus_params["direct_phase"] = [
                            float(pair[0]),
                            float(pair[1]),
                        ]
                if effective_phase:
                    nus_params["phases"] = {
                        axis: [values[0], values[1]]
                        for axis, values in effective_phase.items()
                    }
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
            spectrum_axes = read_spectrum_axes(target_spectrum)
            run.window = {
                str(axis): dict(spec)
                for axis, spec in window_points_by_axis(
                    spectrum_axes,
                    window_pts=window_pts,
                    window_ppm=window_ppm,
                ).items()
            }
            run.measurements = measure_peak_positions(
                target_spectrum,
                peak_rows,
                axes=spectrum_axes,
                window_pts=window_pts,
                window_ppm=window_ppm,
                sign=sign,
                refine=refine,
                roi_f1_ppm=roi_f1_ppm,
                roi_f2_ppm=roi_f2_ppm,
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
    "apply_phase_axes",
    "combos_from_rows",
    "design_diagnostics",
    "expand_grid",
    "infer_axes",
    "is_phase_axis",
    "load_combo_table",
    "load_plan",
    "load_runs",
    "merge_overrides",
    "normalize_nus_params",
    "parse_phase_axis",
    "plan_sweep",
    "run_sweep",
    "split_combo",
    "validate_axes",
    "write_combo_table",
]
