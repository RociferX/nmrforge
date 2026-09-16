"""NUS 采样表读取（nuslist / ser 联合判断）。"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np

from core.data.bruker_dtype import UnknownBrukerDtype, sample_dtype


def read_nuslist(path: Path) -> list[tuple[int, ...]]:
    """读取 nuslist 采样点（每行若干整数索引，跳过注释/空行）。

    Parameters
    ----------
    path : Path
        ``nuslist`` 文件路径。

    Returns
    -------
    list[tuple[int, ...]]
        逐行采样坐标(每行一个或多个整数;空行跳过)。

    Raises
    ------
    - 不抛异常:文件不存在或不可读时返回空列表(调用方据此走安全分支)。

    Side effects
    ------------
    只读文件。

    Examples
    --------
        points = read_nuslist(raw_dir / "nuslist")
    """
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

    Parameters
    ----------
    experiment : Any
        已读取的 2D 数据集(读取 ``acqus``/``acqu2s`` 的 TD 与 FnMODE)。

    Returns
    -------
    tuple[int, int, int]
        ``(grid, mult, direct_points)``:间接维复点网格数、每复点行数(1 或 2)、直接维点数。

    Raises
    ------
    - 不抛异常:参数缺失时按已知默认回退,由调用方结合 evidence 判断。

    Side effects
    ------------
    纯计算(只读实验对象)。

    Examples
    --------
        grid, mult, direct = indirect_grid_2d(experiment)
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

    Parameters
    ----------
    raw_dir : Path
        含 ``ser`` 的 Bruker 原始目录。
    acqus : Mapping[str, Any], optional
        已解析的 ``acqus``(缺省重新读取,避免重复解析可显式传入)。
    grid_complex : int
        间接维复点网格数(由 :func:`indirect_grid_2d` 给出)。
    mult : int
        每复点行数(1 或 2)。
    direct_points : int
        直接维点数。
    fid_file : Path, optional
        直接指定要扫描的文件(缺省 ``ser`` → ``ser_full``)。

    Returns
    -------
    dict[str, Any]
        ``kind``(``full`` 满采样 / ``dense`` 密集子集 / ``sparse`` / ``mismatch``)、
        ``rows``、``points``(非零复点坐标)、``source`` 与 ``reason``。

    Raises
    ------
    - 不抛异常:行数/类型不符时以 ``kind`` + ``reason`` 表达,由调用方决定回退。

    Side effects
    ------------
    只读:只按需扫描文件头与零模式,不改写原始数据。

    Examples
    --------
        scan = scan_dense_2d(raw_dir, grid_complex=128, mult=2, direct_points=2048)
        if scan["kind"] == "full":
            ...  # 实际满采样
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
