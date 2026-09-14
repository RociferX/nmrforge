"""采样方式检测（uniform / NUS / uncertain）。

综合 nuslist、PULPROG、采样点数量、ser/ser_full、采集参数、FnMODE、实际数据长度；
metadata 矛盾时返回 uncertain（安全模式，不强行处理，框架 §6）。
"""

from __future__ import annotations

from core.data.internal_data_model import (
    Experiment,
    Sampling,
    SamplingMode,
)
from core.data.nus_reader import (
    indirect_grid_2d,
    read_nuslist,
    scan_dense_2d,
)


def full_sampling_evidence(
    experiment: Experiment,
    *,
    nus_list: list[tuple[int, ...]],
    has_nuslist: bool,
) -> str | None:
    """标注为 NUS 时判断「实际满采样」→ 证据字符串;否则 None。

    - ``nuslist`` 行数 >= 间接维复点网格 → 采样表覆盖全格 = 满采样;
    - 无 ``nuslist``(2D)且 ``ser``/fid 是「全格+零填充」密集模型、无零行
      → 满采样(数据里所有复点都采了)。

    只做判定,不改数据;调用方据此把 mode 降级为 uniform(用户 2026-09-14:
    满采样应走 uniform)。
    """
    grid, mult, direct_points = indirect_grid_2d(experiment)
    if grid <= 0:
        return None
    if has_nuslist:
        if len(nus_list) >= grid:
            return (
                f"实际满采样:nuslist 覆盖全部 {grid} 个间接维复点"
                "(标注 NUS,实际是满采样)→ 按 uniform 处理"
            )
        return None
    if int(experiment.ndim) != 2:
        return None
    scan = scan_dense_2d(
        experiment.source_path,
        acqus=(experiment.acquisition_parameters or {}).get("acqus", {}),
        grid_complex=grid,
        mult=mult,
        direct_points=direct_points,
    )
    if scan["kind"] == "full":
        return (
            f"实际满采样:{scan['source']} 全格 {scan['rows']} 行且无零行"
            f"(标注 NUS,实际是满采样;网格 {grid} 复点)→ 按 uniform 处理"
        )
    return None




def _int_param(block: dict, key: str, default: int) -> int:
    value = block.get(key, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def detect(experiment: Experiment) -> Sampling:
    """返回采样方式（含 evidence 与 confidence）。"""
    params = experiment.acquisition_parameters
    acqus = params.get("acqus", {})
    evidence: list[str] = []

    nuslist_path = experiment.source_path / "nuslist"
    has_nuslist = nuslist_path.is_file()
    if has_nuslist:
        evidence.append("dataset contains nuslist")

    nus_amount = _int_param(acqus, "NusAMOUNT", 100)
    blocks = [acqus] + [
        params[name] for name in ("acqu2s", "acqu3s") if name in params
    ]
    nus_markers = [
        f"{name}:NusT2={_int_param(block, 'NusT2', 0)}"
        f":NusTD={_int_param(block, 'NusTD', 0)}"
        f":NusJSP={_int_param(block, 'NusJSP', 0)}"
        for block, name in zip(blocks, ["acqus", "acqu2s", "acqu3s"])
        if _int_param(block, "NusT2", 0) > 0
        or _int_param(block, "NusTD", 0) > 0
        or _int_param(block, "NusJSP", 0) > 0
    ]
    nus_params = bool(nus_markers) and nus_amount < 100
    if nus_params:
        evidence.append(" ".join(nus_markers) + f" NusAMOUNT={nus_amount}")

    if has_nuslist and nus_params and nus_amount >= 100:
        evidence.append("nuslist 与 NusAMOUNT>=100 矛盾，进入安全模式")
        return Sampling(mode=SamplingMode.UNCERTAIN, confidence=0.5, evidence=evidence)

    if has_nuslist or nus_params:
        fraction = max(nus_amount, 1) / 100.0
        nus_list = read_nuslist(nuslist_path) if has_nuslist else []
        # 2026-09-14(用户):标注 NUS 但实际满采样 → 按 uniform 处理(证据留档)
        full_evidence = full_sampling_evidence(
            experiment, nus_list=nus_list, has_nuslist=has_nuslist
        )
        if full_evidence is not None:
            evidence.append(full_evidence)
            return Sampling(
                mode=SamplingMode.UNIFORM,
                sampling_fraction=1.0,
                schedule_type="full_sampling",
                confidence=0.9,
                evidence=evidence,
            )
        return Sampling(
            mode=SamplingMode.NUS,
            nus_list=nus_list,
            sampling_fraction=fraction,
            schedule_type="nuslist" if has_nuslist else "params",
            confidence=0.98 if has_nuslist else 0.9,
            evidence=evidence,
        )

    return Sampling(
        mode=SamplingMode.UNIFORM,
        sampling_fraction=1.0,
        confidence=0.95,
        evidence=evidence + ["无 NUS 标记（无 nuslist 且 Nus* 参数未启用）"],
    )
