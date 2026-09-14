"""NUS 采样表读取（nuslist / ser 联合判断）。"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np

from core.data.bruker_dtype import UnknownBrukerDtype, sample_dtype


def read_nuslist(path: Path) -> list[tuple[int, ...]]:
    """读取 nuslist 采样点（每行若干整数索引，跳过注释/空行）。"""
    points: list[tuple[int, ...]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            points.append(tuple(int(tok) for tok in line.split()))
        except ValueError:
            continue
    return points


#: FnMODE → 超复数分量数(States/TPPI/States-TPPI/Echo-Antiecho 为 2,QF 为 1)
_SUPERCOMPLEX_FNMODES = (0, 1, 2, 4, 5, 6)


def _int_or_none(value: object) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def indirect_grid_2d(experiment: Any) -> tuple[int, int, int]:
    """2D → (间接维复点网格, 超复数分量, 直接维点数);非 2D 给 0。

    与 ``backend.script_generator.effective_td`` 的 2D 规则一致(间接维
    复点 = TD // 超复数分量),但**不依赖 sampling.mode** —— 满采样降级后
    mode 已是 uniform,仍要按复点网格判断(2026-09-14 用户:满采样走 uniform)。
    """
    if int(getattr(experiment, "ndim", 0)) != 2:
        return 0, 0, 0
    dimensions = list(getattr(experiment, "dimensions", []) or [])
    if len(dimensions) < 2:
        return 0, 0, 0
    indirect = next(
        (
            dim
            for dim in dimensions
            if str(getattr(getattr(dim, "role", ""), "value", "")).startswith(
                "indirect"
            )
        ),
        None,
    )
    direct = next(
        (
            dim
            for dim in dimensions
            if str(getattr(getattr(dim, "role", ""), "value", "")).startswith("direct")
        ),
        None,
    )
    if indirect is None or direct is None:
        return 0, 0, 0
    fnmode = _int_or_none(getattr(indirect, "acquisition_mode", ""))
    mult = 2 if fnmode in _SUPERCOMPLEX_FNMODES else 1
    grid = int(getattr(indirect, "td", 0) or 0) // mult
    return max(grid, 0), mult, max(int(getattr(direct, "td", 0) or 0), 0)



def scan_dense_2d(
    raw_dir: Path,
    *,
    acqus: Mapping[str, Any] | None = None,
    grid_complex: int,
    mult: int,
    direct_points: int,
    fid_file: Path | None = None,
) -> dict[str, Any]:
    """扫描 2D「全格 + 零填充」密集模型 → 真实采样复点(前后端共用)。

    返回 ``{kind, points, rows, declared_rows, source, dtype, detail}``:

    - ``full``   全格行都在且**无零行** → 实际是满采样(即便标注 NUS);
    - ``partial``全格行都在、部分行为零 → 零模式里恢复出的采样子集;
    - ``sparse`` 行数 < 声明全格 → 稀疏文件,采样位置不可知;
    - ``mismatch``行数 != 声明全格 → 元数据与文件不一致;
    - ``all_zero`` / ``unknown_dtype`` / ``bad_layout`` / ``missing``。

    只读数据、不做处理决策:是否按 uniform 处理由 ``sampling_detector`` 与
    backend 依据本结果决定(2026-09-14 用户:满采样应走 uniform)。
    """
    raw = Path(raw_dir)
    declared_rows = int(mult) * int(grid_complex)
    result: dict[str, Any] = {
        "kind": "missing",
        "points": [],
        "rows": 0,
        "declared_rows": declared_rows,
        "source": "",
        "dtype": "",
        "detail": "",
    }
    if grid_complex <= 0 or mult <= 0 or direct_points <= 0:
        result["kind"] = "bad_layout"
        result["detail"] = "间接维网格/超复数分量/直接维点数非法,无法判断密集模型"
        return result
    try:
        dt = sample_dtype(acqus)
    except UnknownBrukerDtype as exc:
        result["kind"] = "unknown_dtype"
        result["detail"] = str(exc)
        return result
    result["dtype"] = str(dt.str[1:])
    x_n = int(direct_points)
    per_row = int(dt.itemsize) * x_n
    src = raw / "ser"
    if src.is_file():
        result["source"] = "ser"
        size = int(src.stat().st_size)
        if per_row <= 0 or size % per_row:
            result["kind"] = "bad_layout"
            result["detail"] = (
                f"ser 大小 {size} 不是「直接维 {x_n} × {int(dt.itemsize)} 字节」"
                f"({dt.str[1:]})的整数倍,无法判断密集模型"
            )
            return result
        rows = size // per_row
        result["rows"] = int(rows)
        if rows < declared_rows:
            result["kind"] = "sparse"
            return result
        if rows > declared_rows:
            result["kind"] = "mismatch"
            return result
        table = np.fromfile(src, dtype=dt).reshape(rows, x_n)
    elif fid_file is not None and Path(fid_file).is_file():
        result["source"] = "fid"
        import nmrglue as ng

        _dic, data = ng.pipe.read(str(fid_file))
        table = np.asarray(data)
        if table.ndim < 2:
            result["kind"] = "bad_layout"
            result["detail"] = "已转换 fid 不是二维,无法判断密集模型"
            return result
        rows = int(table.shape[0])
        result["rows"] = rows
        if rows != declared_rows:
            result["kind"] = "mismatch"
            return result
    else:
        return result

    energies = np.abs(table).sum(axis=1)
    points: list[int] = []
    for k in range(int(grid_complex)):
        lo, hi = int(mult) * k, int(mult) * k + int(mult)
        if lo >= rows:
            break
        if any(float(energies[i]) > 0.0 for i in range(lo, min(hi, rows))):
            points.append(k)
    result["points"] = points
    if not points:
        result["kind"] = "all_zero"
        return result
    result["kind"] = "full" if len(points) == int(grid_complex) else "partial"
    return result
