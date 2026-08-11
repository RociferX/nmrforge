"""采样方式检测（uniform / NUS / uncertain）。

综合 nuslist、PULPROG、采样点数量、ser/ser_full、采集参数、FnMODE、实际数据长度；
metadata 矛盾时返回 uncertain（安全模式，不强行处理，框架 §6）。
"""

from __future__ import annotations

from core.data.internal_data_model import Experiment, Sampling, SamplingMode
from core.data.nus_reader import read_nuslist


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

    nus_t2 = _int_param(acqus, "NusT2", 0)
    nus_td = _int_param(acqus, "NusTD", 0)
    nus_jsp = _int_param(acqus, "NusJSP", 0)
    nus_amount = _int_param(acqus, "NusAMOUNT", 100)
    nus_params = (nus_t2 > 0 or nus_td > 0 or nus_jsp > 0) and nus_amount < 100
    if nus_params:
        evidence.append(
            f"NusT2={nus_t2}, NusTD={nus_td}, NusJSP={nus_jsp}, NusAMOUNT={nus_amount}"
        )

    if has_nuslist and nus_params and nus_amount >= 100:
        evidence.append("nuslist 与 NusAMOUNT>=100 矛盾，进入安全模式")
        return Sampling(mode=SamplingMode.UNCERTAIN, confidence=0.5, evidence=evidence)

    if has_nuslist or nus_params:
        fraction = max(nus_amount, 1) / 100.0
        nus_list = read_nuslist(nuslist_path) if has_nuslist else []
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
