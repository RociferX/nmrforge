"""采集模式检测：States / States-TPPI / Echo-Antiecho / QF / magnitude。

基于 FnMODE/FnTYPE/AQ_mod 判定，输出 per-dimension acquisition_mode，
供 FT 标志（-alt/-neg）、翻转与相位处理使用（框架 §6.1）。
"""

from __future__ import annotations

from core.data.internal_data_model import Experiment

# Bruker TopSpin FnMODE 官方枚举(nmrglue/acquNs 实现,与实验室 Acqua 一致):
#   0=未定义, 1=QF(magnitude), 2=QSEQ(magnitude), 3=TPPI(real),
#   4=States, 5=States-TPPI, 6=Echo-Antiecho
_FNMODE_TO_MODE = {
    0: "States",  # Bruker acqus 直接维默认 0(占位,无间接维意义;保持兼容输出)
    1: "Magnitude",  # QF
    2: "Magnitude",  # QSEQ
    3: "TPPI",
    4: "States",
    5: "States-TPPI",
    6: "Echo-Antiecho",
}

# bruk2pipe ACQUISITION MODES 官方表(见 nmrPipe/format 文档):
#   States/DQD/Complex -> FT 无标志; States-TPPI -> FT -alt;
#   States-N/Complex-N/States-TPPI-N -> 加 -neg; TPPI -> FT -real;
#   Sequential/QSEQ -> FT -alt(且 -real,等价 FT -bruk);
#   Echo-Antiecho -> 转换时 shuffling 完成,无需 FT 标志。
# 需 -alt 的间接维 FnMODE: States-TPPI(=5) 与 Sequential/QSEQ(=2,
# 以 -real 组合);TPPI(=3) 是 -real 而非 -alt。real/magnitude 类
# (1/2/3)的 NMRPipe real 处理路径未实现,入口显式报错(见
# unsupported_real_mode_error),因此实际落到脚本的 -alt 仅来自 5。
_FNMODE_FT_ALT = {2, 5}

# real/magnitude 间接维(QF/QSEQ/TPPI):uniform 处理已支持(-yMODE
# Real/TPPI/Sequential + FT -real/-bruk/MC,见 FT_KIND);SMILE 重构
# 仅支持 complex 编码(States/States-TPPI/E-A),NUS 入口对 real 类
# 显式拒绝(见 unsupported_real_mode_error)。
_REAL_FNMODE = {1, 2, 3}

# FnMODE -> NMRPipe 处理形态:
#   complex:    FT [-neg] [-alt](当前 Complex 系)
#   magnitude:  FT + MC(QF,aq2D Magnitude)
#   sequential: FT -bruk(= -alt -real,QSEQ/Sequential 直接检测)
#   tppi:       FT -real(phase-sensitive TPPI)
FT_KIND = {
    0: "complex",
    1: "magnitude",
    2: "sequential",
    3: "tppi",
    4: "complex",
    5: "complex",
    6: "complex",
}


def ft_kind_for(fnmode: int) -> str:
    """FnMODE 对应的 NMRPipe FT 处理形态(complex/magnitude/sequential/tppi)。"""
    return FT_KIND.get(fnmode, "complex")


def unsupported_real_mode_error(fnmode: int, *, logical_axis: str) -> str:
    """SMILE(NUS) 路径对 real 间接维的拒绝文案。

    uniform 处理已支持 real 模式;此处仅限 SMILE 重构:real/magnitude
    编码(TPPI/QSEQ/QF)无 quadrature 相位信息,Bruker NUS 采样器不会
    生成此类数据(实际 FnMODE 恒为 States-TPPI/Echo-Antiecho)。
    """
    name = _FNMODE_TO_MODE.get(fnmode, f"unknown({fnmode})")
    return (
        f"维度 {logical_axis} 采集模式 FnMODE={fnmode}({name}) 为 "
        "real/magnitude 类,NUS/SMILE 重构仅支持 complex 编码"
        "(States/States-TPPI/Echo-Antiecho);均匀采样路径已支持此模式。"
    )

# 3D 第一间接维(acqu2s/F2)需 -neg 的模式:States(=4)/States-TPPI(=5)。
# 依据: Bruker 3D ser 布局使 F2 维 States 系数据频率反向
# (实验室 sampleC 手工 FnMODE=5 → FT -alt -neg; nmrpipe 论坛 HNCA
# 15N 镜像同样以 FT -alt -neg 修复;sampleB FnMODE=6 E-A 无需)。
_FNMODE_FT_NEG_F2_3D = {4, 5}


def ft_alt_for(fnmode: int) -> bool:
    """FnMODE=5（States-TPPI）间接维 FT 需要 -alt。"""
    return fnmode in _FNMODE_FT_ALT


def ft_neg_for(experiment: Experiment, fnmode: int, logical_axis: str) -> bool:
    """3D 第一间接维（acqu2s/F2）FT 是否需 -neg。

    bruk2pipe 官方 ACQ MODE 表中 -neg 对应 States-N/Complex-N/
    States-TPPI-N（谱反向）;Bruker 3D 的 ser 排列使 F2 维
    States/States-TPPI 采集在 NMRPipe FT 时呈镜像,需 -neg 修正
    （等价 States-TPPI-N 处理）。E-A(6) 转换时已 shuffle、
    TPPI(3) 为 real 模式,均不加;第二间接维(F1)与 2D 不加。

    真实验证:sampleC 手工 FnMODE=5(F2=15N) → FT -alt -neg;
    sampleB FnMODE=6(E-A) → 无 -neg;同一 FnMODE=5 的 F1(13C) 只 -alt。
    """
    if experiment.ndim < 3 or logical_axis != "F2":
        return False
    return fnmode in _FNMODE_FT_NEG_F2_3D


def detect_modes(experiment: Experiment) -> dict[str, str]:
    """返回 {logical_axis: acquisition_mode}。"""
    params = experiment.acquisition_parameters
    if experiment.ndim >= 3:
        mapping = {"F3": "acqus", "F2": "acqu2s", "F1": "acqu3s"}
    else:
        mapping = {"F2": "acqus", "F1": "acqu2s"}
    modes: dict[str, str] = {}
    for logical, filename in mapping.items():
        block = params.get(filename)
        if not block:
            continue
        fnmode = block.get("FnMODE", 0)
        try:
            fnmode_int = int(fnmode)
        except (TypeError, ValueError):
            fnmode_int = 0
        modes[logical] = _FNMODE_TO_MODE.get(fnmode_int, f"unknown({fnmode_int})")
    return modes
