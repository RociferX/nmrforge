"""NMRPipe 脚本生成测试（确定性 + SOFTWARE_SUMMARY §6.2 参数要求）。"""

from __future__ import annotations

from pathlib import Path

from backend.script_generator import (
    effective_td,
    generate_convert_script,
    generate_process_script,
)
from core.data.bruker_reader import read_dataset
from core.planning.method_selector import select_method


def _convert(exp) -> str:
    return generate_convert_script(exp)


def test_convert_script_2d_required_params(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "hsqc_2d")
    script = _convert(exp)
    assert script.startswith("#!/bin/csh")
    assert "-xMODE DQD" in script
    assert "-yMODE Complex" in script
    assert "-ws 8" in script and "-noi2f" in script  # DSPFVS=21
    assert "-DMX" not in script
    assert "-aq2D 2" in script  # FnMODE 5
    assert "-xN 2048" in script
    assert "-yN 256" in script
    assert "-ndim 2" in script
    assert "\r" not in script  # LF 行尾


def test_convert_script_nus3d_td1_uses_nustd(bruker_dir: Path) -> None:
    """acqu3s TD=1 时 zN 必须用 NusTD（否则 fid.com 输出单文件而非切片）。"""
    exp = read_dataset(bruker_dir / "nus_3d")
    assert exp.ndim == 3
    assert exp.sampling.mode.value == "nus"
    td = effective_td(exp)
    assert td[2] == 128  # NusTD，不是 1
    assert td[1] == 48  # acqu2s NusTD
    script = _convert(exp)
    assert "-zN 128" in script
    assert __import__("re").search(r"-zN 1(?![0-9])", script) is None
    assert "-yN 48" in script
    assert "-ndim 3" in script
    assert "-yMODE Complex" in script  # F2=acqu2s FnMODE=5
    assert "-zMODE Complex" in script


def test_convert_script_echo_antiecho_mode(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "hsqc_2d")
    exp.acquisition_parameters["acqu2s"]["FnMODE"] = 4
    script = _convert(exp)
    assert "-yMODE Echo-AntiEcho" in script  # F1 FnMODE=4
    assert "-aq2D 3" in script  # FnMODE 4 → 3


def test_convert_script_3d_y_mode_from_acqu2s(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "hnca_3d")
    script = _convert(exp)
    assert "-yMODE Complex" in script  # F2=acqu2s FnMODE=5
    assert "-aq2D 2" in script  # FnMODE 5 → 2


def test_process_script_2d(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "hsqc_2d")
    plan = select_method(exp)
    script = generate_process_script(exp, plan, in_file="test.fid", out_file="out.ft2")
    assert "xyz2pipe -in test.fid -x" in script
    assert "| nmrPipe -fn SP" in script
    assert "| nmrPipe -fn ZF" in script
    assert "| nmrPipe -fn FT" in script
    assert "| nmrPipe -fn PS" in script
    assert script.count("| nmrPipe -fn TP") == 2  # 直接维后 + 间接维后(转置回来)
    assert "| pipe2xyz -out out.ft2 -x" in script
    assert "\r" not in script


def test_process_script_3d_two_tps(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "hnca_3d")
    plan = select_method(exp)
    script = generate_process_script(exp, plan, in_file="test.fid", out_file="out.ft3")
    assert script.count("| nmrPipe -fn TP") == 2
    assert "| pipe2xyz -out out.ft3 -x" in script


def test_scripts_deterministic(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "hsqc_2d")
    plan = select_method(exp)
    assert _convert(exp) == _convert(exp)
    assert generate_process_script(exp, plan, in_file="a.fid", out_file="a.ft2") == (
        generate_process_script(exp, plan, in_file="a.fid", out_file="a.ft2")
    )


def test_preview_script_2d_f2_keeps_complex(bruker_dir: Path) -> None:
    """F2 预览:F2 PS 不加 -di,间接维 F1 加 -di,零填零。"""
    exp = read_dataset(bruker_dir / "hsqc_2d")
    plan = select_method(exp)
    from backend.script_generator import generate_preview_script

    script = generate_preview_script(
        exp, plan, in_file="test.fid", out_file="p_F2.ft2", preview_axis="F2"
    )
    assert "| nmrPipe -fn PS -p0 0 -p1 0 \\" in script  # F2 不加 -di
    assert "| nmrPipe -fn PS -p0 0 -p1 0 -di \\" in script  # F1 加 -di
    assert "| nmrPipe -fn ZF" not in script  # 零填零(与旧相位候选同参)
    assert script.count("| nmrPipe -fn PS") == 2
    assert script.count("| nmrPipe -fn POLY") == 1  # 搜索轴 F2 跳过 POLY
    assert "| pipe2xyz -out p_F2.ft2 -x" in script


def test_preview_script_2d_f1_fixed_phases_only_other_axes(bruker_dir: Path) -> None:
    """F1 预览:preview_axis 自身保持 PS(0,0) 不加 -di;固定相位只作用于其它轴。"""
    exp = read_dataset(bruker_dir / "hsqc_2d")
    plan = select_method(exp)
    from backend.script_generator import generate_preview_script

    script = generate_preview_script(
        exp,
        plan,
        in_file="test.fid",
        out_file="p_F1.ft2",
        preview_axis="F1",
        fixed_phases={"F1": (10.0, -2.0), "F2": (5.0, 0.0)},
    )
    # 预览轴 F1 必须 PS(0,0) 无 -di;传入的 F1 固定相位不得应用
    assert "| nmrPipe -fn PS -p0 0 -p1 0 \\" in script
    assert "| nmrPipe -fn PS -p0 5 -p1 0 -di \\" in script  # F2 固定相位应用
    assert "p0 10" not in script
    assert script.count("| nmrPipe -fn POLY") == 1  # 搜索轴 F1 跳过 POLY


def test_preview_script_3d(bruker_dir: Path) -> None:
    """3D F2 预览:F3/F1 加 -di,F2 不加;零填零;2 个 TP。"""
    exp = read_dataset(bruker_dir / "hnca_3d")
    plan = select_method(exp)
    from backend.script_generator import generate_preview_script

    script = generate_preview_script(
        exp, plan, in_file="test.fid", out_file="p_F2.ft3", preview_axis="F2"
    )
    assert script.count("| nmrPipe -fn PS") == 3
    assert script.count("| nmrPipe -fn PS -p0 0 -p1 0 -di \\") == 2
    assert "| nmrPipe -fn PS -p0 0 -p1 0 \\" in script
    assert script.count("| nmrPipe -fn TP") == 2
    assert "| nmrPipe -fn ZF" not in script
    assert script.count("| nmrPipe -fn POLY") == 2  # 搜索轴 F2 跳过 POLY


def test_2d_nus_script(bruker_dir: Path) -> None:
    """两阶段:stage1 nmrPipe -in + SMILE(-sample None,-xT 复点网格);
    stage2 nmrPipe -in recon.ft1 + FT -alt + -out -ov。"""
    exp = read_dataset(bruker_dir / "nus_2d")
    from backend.script_generator import generate_2d_nus_script

    script = generate_2d_nus_script(
        exp, in_file="exp.fid", nuslist="nuslist", out_file="exp.ft2",
        nuslist_count=5, ext_lo="9.0", ext_hi="7.5", nsigma=7.0, thresh=0.85,
    )
    assert script.startswith("#!/bin/csh")
    assert "nmrPipe -in exp.fid \\" in script
    assert "| nusPipe -fn SMILE -nDim 2" in script
    assert "-sample None" in script
    assert "-sampleCount 5" in script
    assert "-x1 9.0ppm -xn 7.5ppm" in script
    assert "-xT 128" in script  # F1 复点网格 256//2
    assert "| nmrPipe -fn FT -alt \\" in script  # States 间接维
    assert "nmrPipe -in nus2d/recon.ft1 \\" in script
    assert "  -out exp.ft2 -ov" in script
    assert "| pipe2xyz -out exp.ft2" not in script
    assert "\r" not in script
def test_nus_finalize_script_2d(bruker_dir: Path) -> None:
    """重构平面定稿(2D):nmrPipe -in + FT -alt + POLY + -out -ov,逐维 PS 可配。"""
    exp = read_dataset(bruker_dir / "nus_2d")
    from backend.script_generator import generate_nus_finalize_script

    script = generate_nus_finalize_script(
        exp, planes="nus2d/recon.ft1", out_file="e.ft2"
    )
    assert "nmrPipe -in nus2d/recon.ft1 \\" in script
    assert "| nmrPipe -fn FT -alt \\" in script
    assert "| nmrPipe -fn PS -p0 0 -p1 0 -di" in script
    assert "| nmrPipe -fn POLY -auto" in script  # 与验证 s2.com 一致
    assert "  -out e.ft2 -ov" in script
    assert "| nmrPipe -fn SMILE" not in script  # 不重跑 SMILE
    phased = generate_nus_finalize_script(
        exp, planes="nus2d/recon.ft1", out_file="e.ft2",
        phases={"F1": (12.0, -3.0)},
    )
    assert "| nmrPipe -fn PS -p0 12 -p1 -3 -di" in phased
def test_nus_finalize_script_3d(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "nus_3d")
    from backend.script_generator import generate_nus_finalize_script

    phased = generate_nus_finalize_script(
        exp, planes="nus3d_rc/test%04d.ft1", out_file="e.ft3",
        phases={"F2": (12.0, -3.0), "F1": (5.0, 2.0)},
    )
    assert "xyz2pipe -in nus3d_rc/test%04d.ft1 -x" in phased
    assert "| nmrPipe -fn PS -p0 12 -p1 -3 -di" in phased
    assert "| nmrPipe -fn PS -p0 5 -p1 2 -di" in phased
    assert phased.count("| nmrPipe -fn TP") == 2


def test_2d_nus_script_extract_off(bruker_dir: Path) -> None:
    """extract=False 时不写 EXT 行;True 保持现状(默认 6-11 ppm)。"""
    exp = read_dataset(bruker_dir / "nus_2d")
    from backend.script_generator import generate_2d_nus_script

    on = generate_2d_nus_script(
        exp, in_file="e.fid", nuslist="nuslist", out_file="e.ft2"
    )
    assert "| nmrPipe -fn EXT" in on
    assert "-x1 10.5ppm -xn 6.5ppm" in on
    off = generate_2d_nus_script(
        exp, in_file="e.fid", nuslist="nuslist", out_file="e.ft2",
        extract=False,
    )
    assert "| nmrPipe -fn EXT" not in off


def test_3d_nus_script_extract_off(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "nus_3d")
    from backend.script_generator import generate_3d_nus_script

    off = generate_3d_nus_script(
        exp, in_file="e.fid", nuslist="nuslist", out_file="e.ft3",
        extract=False,
    )
    assert "| nmrPipe -fn EXT" not in off
    on = generate_3d_nus_script(
        exp, in_file="e.fid", nuslist="nuslist", out_file="e.ft3"
    )
    assert "| nmrPipe -fn EXT" in on


def test_3d_nus_script(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "nus_3d")
    from backend.script_generator import generate_3d_nus_script

    script = generate_3d_nus_script(
        exp, in_file="exp.fid", nuslist="nuslist", out_file="exp.ft3",
        nuslist_count=4, ext_lo="9.0", ext_hi="7.5",
    )
    assert "-fn SMILE -nDim 3" in script
    assert "-sample nuslist" in script
    assert "| pipe2xyz -out exp.ft3 -x" in script
    assert script.count("| nmrPipe -fn TP") == 2
    assert "\r" not in script
    assert generate_3d_nus_script(
        exp, in_file="exp.fid", nuslist="nuslist", out_file="exp.ft3"
    ) == generate_3d_nus_script(
        exp, in_file="exp.fid", nuslist="nuslist", out_file="exp.ft3"
    )


def test_3d_nus_script_smile_tuning(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "nus_3d")
    from backend.script_generator import generate_3d_nus_script

    script = generate_3d_nus_script(
        exp, in_file="exp.fid", nuslist="nuslist", out_file="exp.ft3",
        nsigma=5.0, thresh=0.99, smile_xq3=2.0, smile_scaling=True, smile_report=2,
    )
    assert "-nSigma 5" in script
    assert "-thresh 0.99" in script
    assert "-xQ3 2" in script
    assert "-scaling 1" in script
    assert "-report 2" in script


def test_select_smile_params_low_sampling() -> None:
    from backend.script_generator import select_smile_params

    assert select_smile_params(0.04) == (5.0, 0.95)
    assert select_smile_params(0.3) == (5.0, 0.95)
    assert select_smile_params(0.8) == (5.0, 0.95)


def test_3d_nus_script_default_smile_params(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "nus_3d")
    from backend.script_generator import generate_3d_nus_script

    script = generate_3d_nus_script(
        exp, in_file="exp.fid", nuslist="nuslist", out_file="exp.ft3"
    )
    assert "-xQ3 2" in script
    assert "-scaling 1" in script


def test_process_script_direct_phase(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "hsqc_2d")
    plan = select_method(exp)
    script = generate_process_script(
        exp, plan, in_file="a.fid", out_file="a.ft2",
        direct_phase={"F2": (12.0, -3.0)},
    )
    assert "| nmrPipe -fn PS -p0 12 -p1 -3 -di \\" in script


def test_3d_nus_script_direct_phase(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "nus_3d")
    from backend.script_generator import generate_3d_nus_script

    script = generate_3d_nus_script(
        exp, in_file="e.fid", nuslist="nuslist", out_file="e.ft3",
        direct_phase=(12.0, -3.0),
    )
    assert "| nmrPipe -fn PS -p0 12 -p1 -3 -di \\" in script



def test_process_script_ext_default_6_11(bruker_dir: Path) -> None:
    """处理脚本直接维 FT 后加 EXT,默认选区 6-11 ppm(PS 之后、TP 之前)。"""
    exp = read_dataset(bruker_dir / "hsqc_2d")
    plan = select_method(exp)
    script = generate_process_script(exp, plan, in_file="a.fid", out_file="a.ft2")
    lines = script.splitlines()
    ps_index = next(
        i for i, line in enumerate(lines) if "| nmrPipe -fn PS" in line
    )
    ext_index = next(
        i for i, line in enumerate(lines) if "| nmrPipe -fn EXT" in line
    )
    tp_index = next(
        i for i, line in enumerate(lines) if "| nmrPipe -fn TP" in line
    )
    assert ps_index < ext_index < tp_index
    assert "| nmrPipe -fn EXT -x1 10.5ppm -xn 6.5ppm -sw -round 2" in script


def test_process_script_extract_disabled_and_custom(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "hsqc_2d")
    plan = select_method(exp)
    off = generate_process_script(
        exp, plan, in_file="a.fid", out_file="a.ft2", extract=False
    )
    assert "| nmrPipe -fn EXT" not in off
    custom = generate_process_script(
        exp, plan, in_file="a.fid", out_file="a.ft2",
        ext_lo="9.0", ext_hi="7.5",
    )
    assert "| nmrPipe -fn EXT -x1 9.0ppm -xn 7.5ppm" in custom



def test_process_script_baseline_default_and_overrides(bruker_dir: Path) -> None:
    """默认每维 POLY -auto;baseline 配置可关闭/改 order。"""
    exp = read_dataset(bruker_dir / "hsqc_2d")
    plan = select_method(exp)
    default = generate_process_script(exp, plan, in_file="a.fid", out_file="a.ft2")
    assert default.count("| nmrPipe -fn POLY -auto") == 2  # F2 + F1
    off = generate_process_script(
        exp, plan, in_file="a.fid", out_file="a.ft2",
        baseline={"F2": {"enabled": False}},
    )
    assert off.count("| nmrPipe -fn POLY") == 1
    ordered = generate_process_script(
        exp, plan, in_file="a.fid", out_file="a.ft2",
        baseline={"F1": {"mode": "order", "order": 2}},
    )
    assert "| nmrPipe -fn POLY -ord 2" in ordered


def test_2d_nus_script_baseline_insert(bruker_dir: Path) -> None:
    """两阶段基线:stage1 F2 POLY 在 EXT 后,stage2 F1 POLY 在 PS 后。"""
    exp = read_dataset(bruker_dir / "nus_2d")
    from backend.script_generator import generate_2d_nus_script

    script = generate_2d_nus_script(
        exp, in_file="e.fid", nuslist="nuslist", out_file="e.ft2"
    )
    assert script.count("| nmrPipe -fn POLY -auto") == 2
    stage2 = script.split("# stage 2:")[1].splitlines()
    ps_i = next(i for i, line in enumerate(stage2) if "| nmrPipe -fn PS" in line)
    assert "| nmrPipe -fn POLY" in stage2[ps_i + 1]
    off = generate_2d_nus_script(
        exp, in_file="e.fid", nuslist="nuslist", out_file="e.ft2",
        baseline={"F1": {"enabled": False}},
    )
    assert off.count("| nmrPipe -fn POLY") == 1
def test_3d_nus_script_baseline_insert(bruker_dir: Path) -> None:
    """NUS 3D:直接维 EXT 后 + F2/F1 各 PS 后插入 POLY(共 3 行)。"""
    exp = read_dataset(bruker_dir / "nus_3d")
    from backend.script_generator import generate_3d_nus_script

    script = generate_3d_nus_script(
        exp, in_file="e.fid", nuslist="nuslist", out_file="e.ft3"
    )
    assert script.count("| nmrPipe -fn POLY -auto") == 3

def test_effective_td_2d_nus_complex_grid(bruker_dir: Path) -> None:
    """2D NUS:F1 用复点网格 TD//mult;acqu2s NusTD=TD 时不被采信。"""
    exp = read_dataset(bruker_dir / "nus_2d")
    exp.acquisition_parameters["acqu2s"]["NusTD"] = 256  # 部分数据 NusTD=TD
    from backend.script_generator import effective_td

    td = effective_td(exp)
    assert td[1] == 128  # 256 // 2 (States)
    exp3 = read_dataset(bruker_dir / "nus_3d")
    td3 = effective_td(exp3)
    assert td3[1] == 48 and td3[2] == 128  # 3D 保持 NusTD(已是复点数)

def test_process_script_window_and_zero_fill_overrides(
    bruker_dir: Path,
) -> None:
    """窗函数/填零覆盖:GM 窗替换 SP;F1 不填零时省略 ZF 行。"""
    exp = read_dataset(bruker_dir / "hsqc_2d")
    plan = select_method(exp)
    from backend.script_generator import generate_process_script

    script = generate_process_script(
        exp,
        plan,
        in_file="a.fid",
        out_file="a.ft2",
        window={"F2": {"type": "gaussian", "lb": 4.0, "gb": 0.2}},
        zero_fill={"F1": {"mode": "none"}},
    )
    assert "| nmrPipe -fn GM -lb 4 -gb 0.2 \\" in script
    assert script.count("| nmrPipe -fn SP") == 1  # F1 仍是默认 SP
    # F1 不填零:FT 后无 ZF 行(F2 仍保留默认 2×TD 填零)
    assert script.count("| nmrPipe -fn ZF") == 1
    off = generate_process_script(
        exp,
        plan,
        in_file="a.fid",
        out_file="a.ft2",
        zero_fill={"F2": {"mode": "none"}, "F1": {"mode": "none"}},
    )
    assert "| nmrPipe -fn ZF" not in off


def _pow2_ge(value: int) -> int:
    return 1 << max(0, int(value) - 1).bit_length()


def test_zero_fill_plan_direct_and_indirect(bruker_dir: Path) -> None:
    """直接维 SI=2×TD;间接维按目标数字分辨率动态且受 1/AQ 约束(0.2.39)。"""
    exp = read_dataset(bruker_dir / "hsqc_2d")
    from backend.script_generator import effective_td, zero_fill_plan

    td = effective_td(exp)
    plan = zero_fill_plan(exp)
    # 直接维:2×TD 的 2 的幂
    assert plan["F2"]["size"] == _pow2_ge(2 * td[0])
    # 间接维:不小于 TD,不超过 next_pow2(2×TD)(points_per_line 默认 2)
    assert td[1] <= plan["F1"]["size"] <= _pow2_ge(2 * td[1])
    # 线宽越窄填零越多(宽线 60 Hz 目标点距放宽,SI 减小)
    narrow = zero_fill_plan(exp, linewidth_hz={"F1": 5.0})
    wide = zero_fill_plan(exp, linewidth_hz={"F1": 60.0})
    assert narrow["F1"]["size"] >= wide["F1"]["size"]
    # 覆盖语义:0=auto;int k=间接维 k×TD;逐轴 none 关闭
    assert zero_fill_plan(exp, 0) == zero_fill_plan(exp)
    fixed = zero_fill_plan(exp, 2)
    assert fixed["F2"]["size"] == _pow2_ge(2 * td[0])
    assert fixed["F1"]["size"] == _pow2_ge(2 * td[1])
    off = zero_fill_plan(exp, {"F1": {"mode": "none"}})
    assert off["F1"]["mode"] == "none" and off["F1"]["size"] is None
    assert off["F2"]["size"] == plan["F2"]["size"]


def test_2d_nus_script_zero_fill_plan(bruker_dir: Path) -> None:
    """NUS 两阶段 ZF 使用填零计划:直接维 2×TD,间接维按重构网格动态。"""
    exp = read_dataset(bruker_dir / "nus_2d")
    from backend.script_generator import (
        effective_td,
        generate_2d_nus_script,
        zero_fill_plan,
    )

    plan = zero_fill_plan(exp)
    script = generate_2d_nus_script(
        exp, in_file="e.fid", nuslist="nuslist", out_file="e.ft2"
    )
    assert f"| nmrPipe -fn ZF -zf -size {plan['F2']['size']} \\" in script
    assert f"| nmrPipe -fn ZF -size {plan['F1']['size']} \\" in script
    # NUS 间接维以重构后的复点网格为 TD(与 SMILE 重构相互独立)
    td = effective_td(exp)
    assert plan["F1"]["size"] >= td[1]
    # 间接维关闭:stage2 不再输出 ZF 行
    off = generate_2d_nus_script(
        exp,
        in_file="e.fid",
        nuslist="nuslist",
        out_file="e.ft2",
        zero_fill={"F1": {"mode": "none"}},
    )
    assert "| nmrPipe -fn ZF -size" not in off
    assert "| nmrPipe -fn ZF -zf -size" in off  # 直接维保留


def test_finalize_script_zero_fill_plan(bruker_dir: Path) -> None:
    """finalize(相位优化复用)的间接维 ZF 使用计划 SI,可关闭。"""
    exp = read_dataset(bruker_dir / "nus_2d")
    from backend.script_generator import (
        generate_nus_finalize_script,
        zero_fill_plan,
    )

    plan = zero_fill_plan(exp)
    script = generate_nus_finalize_script(
        exp, planes="nus2d/recon.ft1", out_file="e.ft2"
    )
    assert f"| nmrPipe -fn ZF -size {plan['F1']['size']} \\" in script
    off = generate_nus_finalize_script(
        exp,
        planes="nus2d/recon.ft1",
        out_file="e.ft2",
        zero_fill={"F1": {"mode": "none"}},
    )
    assert "| nmrPipe -fn ZF" not in off

def test_3d_nus_script_phases_baked(bruker_dir: Path) -> None:
    """完整脚本终跑:直接维相位在 EXT 后(与 recon 平面内存旋转同归一化),
    间接维相位填入 step3 PS,无冗余 PS(0,0) 行。"""
    exp = read_dataset(bruker_dir / "nus_3d")
    from backend.script_generator import generate_3d_nus_script

    script = generate_3d_nus_script(
        exp, in_file="e.fid", nuslist="nuslist", out_file="e.ft3",
        direct_phase=(12.0, -3.0),
        phases={"F2": (10.0, -5.0), "F1": (20.0, 3.0)},
    )
    lines = script.splitlines()
    ext_i = next(i for i, line in enumerate(lines) if "| nmrPipe -fn EXT" in line)
    step1_ps = next(
        i for i, line in enumerate(lines)
        if "| nmrPipe -fn PS -p0 12 -p1 -3 -di" in line
    )
    assert step1_ps > ext_i  # 直接维相位在 EXT 后
    assert "| nmrPipe -fn PS -p0 10 -p1 -5 -di \\" in script
    assert "| nmrPipe -fn PS -p0 20 -p1 3 -di \\" in script
    assert script.count("| nmrPipe -fn PS") == 3  # step1 + F2 + F1,无冗余 0 行


def test_2d_nus_script_phases_baked(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "nus_2d")
    from backend.script_generator import generate_2d_nus_script

    script = generate_2d_nus_script(
        exp, in_file="e.fid", nuslist="nuslist", out_file="e.ft2",
        direct_phase=(12.0, -3.0),
        phases={"F1": (20.0, 3.0)},
    )
    lines = script.splitlines()
    ext_i = next(i for i, line in enumerate(lines) if "| nmrPipe -fn EXT" in line)
    step1_ps = next(
        i for i, line in enumerate(lines)
        if "| nmrPipe -fn PS -p0 12 -p1 -3 -di" in line
    )
    assert step1_ps > ext_i
    assert "| nmrPipe -fn PS -p0 20 -p1 3 -di \\" in script
    assert script.count("| nmrPipe -fn PS") == 2


def test_3d_nus_script_window(bruker_dir: Path) -> None:
    """NUS 窗函数:直接维(step1)与间接维(step3)分别可配,缺省不插窗。"""
    exp = read_dataset(bruker_dir / "nus_3d")
    from backend.script_generator import generate_3d_nus_script

    script = generate_3d_nus_script(
        exp, in_file="e.fid", nuslist="nuslist", out_file="e.ft3",
        window={
            "F3": {"type": "gaussian", "lb": 3.0, "gb": 0.2},
            "F2": {"type": "sine_bell_squared"},
            "F1": {"type": "sine_bell", "off": 0.3, "end": 0.9},
        },
    )
    assert "| nmrPipe -fn GM -lb 3 -gb 0.2 \\" in script
    assert "| nmrPipe -fn SP -off 0.45 -end 0.95 -pow 2 -c 0.5 \\" in script
    assert "| nmrPipe -fn SP -off 0.3 -end 0.9 -pow 1 -c 0.5 \\" in script
    lines = script.splitlines()
    f2_sp = next(i for i, line in enumerate(lines) if "pow 2 -c 0.5" in line)
    f2_zf = next(
        i for i, line in enumerate(lines)
        if line.startswith("| nmrPipe -fn ZF") and i > f2_sp
    )
    f2_ft = next(
        i for i, line in enumerate(lines)
        if "| nmrPipe -fn FT" in line and i > f2_zf
    )
    assert f2_sp < f2_zf < f2_ft  # 窗在 ZF/FT 前
    plain = generate_3d_nus_script(
        exp, in_file="e.fid", nuslist="nuslist", out_file="e.ft3"
    )
    assert "| nmrPipe -fn GM" not in plain
    assert plain.count("| nmrPipe -fn SP") == 1  # 仅 step1 直接维默认窗


def test_nus_finalize_script_3d_window(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "nus_3d")
    from backend.script_generator import generate_nus_finalize_script

    script = generate_nus_finalize_script(
        exp, planes="nus3d_rc/test%04d.ft1", out_file="e.ft3",
        phases={"F2": (10.0, -5.0), "F1": (20.0, 3.0)},
        window={
            "F2": {"type": "sine_bell"},
            "F1": {"type": "gaussian", "lb": 4.0},
        },
    )
    assert "| nmrPipe -fn SP -off 0.45 -end 0.95 -pow 1 -c 0.5 \\" in script
    assert "| nmrPipe -fn GM -lb 4 -gb 0.1 \\" in script
    lines = script.splitlines()
    sp_idx = next(i for i, line in enumerate(lines) if "pow 1 -c 0.5" in line)
    zf_idx = next(
        i for i, line in enumerate(lines)
        if "| nmrPipe -fn ZF" in line and i > sp_idx
    )
    ft_idx = next(
        i for i, line in enumerate(lines)
        if "| nmrPipe -fn FT" in line and i > zf_idx
    )
    assert sp_idx < zf_idx < ft_idx
    gm_idx = next(i for i, line in enumerate(lines) if "GM -lb 4" in line)
    zf2_idx = next(
        i for i, line in enumerate(lines)
        if "| nmrPipe -fn ZF" in line and i > gm_idx
    )
    ft2_idx = next(
        i for i, line in enumerate(lines)
        if "| nmrPipe -fn FT" in line and i > zf2_idx
    )
    assert gm_idx < zf2_idx < ft2_idx
