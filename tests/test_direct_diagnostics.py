"""直接维诊断门控测试:直流偏置→POLY -time、坏点→自动替换、渲染插入。

使用本地真实转换 fid(exp_001.fid)作字节布局模板;文件缺失时跳过。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from workflow.direct_diagnostics import (
    DirectDiagnosticsResult,
    _read_fid_raw,
    run_direct_diagnostics,
)

TEMPLATE = Path(
    r"某个开发机上的绝对路径"
)

pytestmark = pytest.mark.skipif(
    not TEMPLATE.is_file(), reason="需要本地真实转换 fid 模板"
)


@pytest.fixture
def exp_fixture(bruker_dir: Path):
    from core.data.bruker_reader import read_dataset

    return read_dataset(bruker_dir / "nus_2d")


def _synth_like(template: Path) -> np.ndarray:
    """与模板同尺寸的合成复型 fid(指数衰减+噪声,无直流/坏点)。"""
    got = _read_fid_raw(template)
    assert got is not None
    data, fdsize, specnum, _header = got
    rng = np.random.default_rng(7)
    t = np.arange(fdsize, dtype=float)
    sig = np.exp(-t / 150.0) * np.exp(2j * np.pi * 0.12 * t)
    arr = np.tile(sig, (specnum, 1))
    arr *= rng.uniform(0.5, 2.0, (specnum, 1))
    arr += rng.normal(0.0, 0.02, (specnum, fdsize))
    arr += 1j * rng.normal(0.0, 0.02, (specnum, fdsize))
    return arr.astype(np.complex64)


def _stage(
    tmp_path: Path,
    *,
    synthetic: bool = False,
    dc_amp: float = 0.0,
    spike: tuple[int, int, float] | None = None,
    nan_point: tuple[int, int] | None = None,
    zero_rows: list[int] | None = None,
    high_energy_row: int | None = None,
) -> Path:
    """从真实模板导出 fid 副本;synthetic=True 时数据区替换为合成数据。"""
    dst = tmp_path / "nus_2d.fid"
    dst.write_bytes(TEMPLATE.read_bytes())
    if synthetic:
        arr = _synth_like(TEMPLATE)
        if dc_amp:
            arr = (arr.real + dc_amp).astype(np.float32) + 1j * arr.imag
        got = _read_fid_raw(dst)
        assert got is not None
        data_, fdsize, specnum, header = got
        assert arr.shape == (specnum, fdsize)
        flat = np.frombuffer(
            dst.read_bytes(), dtype="<f4", count=specnum * fdsize * 2, offset=header
        ).astype(np.float32).reshape(specnum, fdsize * 2)
        flat[:, :fdsize] = arr.real.reshape(specnum, fdsize)
        flat[:, fdsize:] = arr.imag.reshape(specnum, fdsize)
        dst.write_bytes(dst.read_bytes()[:header] + flat.tobytes())
    if spike:
        row, col, mag = spike
        got = _read_fid_raw(dst)
        assert got is not None
        data, fdsize, specnum, header = got
        energies = np.sum(np.abs(data) ** 2, axis=1)
        target = row if row >= 0 else int(np.argmax(energies))
        val = data[target, col] * mag
        raw = bytearray(dst.read_bytes())
        re_off = header + target * fdsize * 8 + col * 4
        np.frombuffer(raw, dtype="<f4", offset=re_off, count=1)[0] = val.real
        np.frombuffer(raw, dtype="<f4", offset=re_off + fdsize * 4, count=1)[0] = val.imag
        dst.write_bytes(bytes(raw))
    if nan_point:
        row, col = nan_point
        got = _read_fid_raw(dst)
        assert got is not None
        data, fdsize, specnum, header = got
        target = row if row >= 0 else int(np.argmax(np.sum(np.abs(data) ** 2, axis=1)))
        raw = bytearray(dst.read_bytes())
        re_off = header + target * fdsize * 8 + col * 4
        np.frombuffer(raw, dtype="<f4", offset=re_off, count=1)[0] = np.nan
        np.frombuffer(raw, dtype="<f4", offset=re_off + fdsize * 4, count=1)[0] = np.nan
        dst.write_bytes(bytes(raw))
    if zero_rows:
        got = _read_fid_raw(dst)
        assert got is not None
        _data, fdsize, specnum, header = got
        raw = bytearray(dst.read_bytes())
        for row in zero_rows:
            re_off = header + row * fdsize * 8
            np.frombuffer(raw, dtype="<f4", offset=re_off, count=fdsize * 2)[:] = 0.0
        dst.write_bytes(bytes(raw))
    if high_energy_row is not None:
        got = _read_fid_raw(dst)
        assert got is not None
        _data, fdsize, specnum, header = got
        raw = bytearray(dst.read_bytes())
        re_off = header + high_energy_row * fdsize * 8
        block = np.frombuffer(raw, dtype="<f4", offset=re_off, count=fdsize * 2).copy()
        block *= 200.0
        np.frombuffer(raw, dtype="<f4", offset=re_off, count=fdsize * 2)[:] = block
        dst.write_bytes(bytes(raw))
    return dst


def test_parse_real_fid_layout(tmp_path: Path, exp_fixture) -> None:
    fid = _stage(tmp_path)
    got = _read_fid_raw(fid)
    assert got is not None
    data, fdsize, specnum, header = got
    assert fdsize == 1024
    assert specnum > 100
    assert header in (512, 1024, 2048)


def test_dc_offset_enables_poly_time(tmp_path: Path, exp_fixture) -> None:
    """显著直流偏置:自动启用 POLY -time 并报告(0.2.199-补29cw 阈值 0.25)。"""
    _stage(tmp_path, synthetic=True, dc_amp=1.0)
    res = run_direct_diagnostics(tmp_path, exp_fixture)
    assert isinstance(res, DirectDiagnosticsResult)
    assert res.apply_poly_time is True
    assert any("直流偏置" in r and "POLY -time" in r for r in res.reports)
    assert res.metrics["dc_ratio"] > 0.0


def test_dc_small_stays_off(tmp_path: Path, exp_fixture) -> None:
    """去直流后的数据:不启用 POLY -time。"""
    _stage(tmp_path, synthetic=True)
    res = run_direct_diagnostics(tmp_path, exp_fixture)
    assert res.apply_poly_time is False


def test_dc_small_offset_stays_off(tmp_path: Path, exp_fixture) -> None:
    """小幅 FID 均值(常规谱常见水平)不触发 POLY -time(0.2.199-补29cw)。"""
    _stage(tmp_path, synthetic=True, dc_amp=0.08)
    res = run_direct_diagnostics(tmp_path, exp_fixture)
    assert res.apply_poly_time is False


def test_dc_ratio_time_metric_unit() -> None:
    """时域直流指标:常数偏移 ≈ 偏移/峰值;干净衰减 FID 很小。"""
    from workflow.direct_diagnostics import _dc_ratio_time

    rng = np.random.default_rng(3)
    t = np.arange(1024, dtype=float)
    sig = np.exp(-t / 200.0) * np.exp(2j * np.pi * 0.1 * t)
    base = np.tile(sig, (20, 1)) * rng.uniform(0.5, 2.0, (20, 1))
    base += 0.02 * (rng.normal(size=(20, 1024)) + 1j * rng.normal(size=(20, 1024)))
    r_clean = _dc_ratio_time(base)
    r_dc = _dc_ratio_time(base + 1.0)
    assert r_clean < 0.20
    assert r_dc > 0.30


def test_badpoint_repaired_with_backup(tmp_path: Path, exp_fixture) -> None:
    """孤立尖峰:自动替换、写回磁盘、备份目录生成。"""
    fid = _stage(tmp_path, synthetic=True, spike=(-1, 128, 40.0))
    before = _read_fid_raw(fid)
    assert before is not None
    data_before, _, _, _ = before
    res = run_direct_diagnostics(tmp_path, exp_fixture)
    assert res.repaired_badpoints >= 1
    assert any("坏点" in r and "自动替换" in r for r in res.reports)
    backup = tmp_path / "fid_diag_bak"
    assert backup.is_dir()
    assert (backup / "nus_2d.fid").is_file()
    after = _read_fid_raw(fid)
    assert after is not None
    data_after, _, _, _ = after
    row = int(np.argmax(np.sum(np.abs(data_before) ** 2, axis=1)))
    orig_val = data_before[row, 128]
    expect = 0.5 * (data_after[row, 127] + data_after[row, 129])
    assert abs(data_after[row, 128] - expect) < 1.0
    # 备份仍保留尖峰
    orig = _read_fid_raw(backup / "nus_2d.fid")
    assert orig is not None
    assert np.isclose(np.asarray(orig[0])[row, 128], orig_val)


def test_nan_inf_reported_not_fixed(tmp_path: Path, exp_fixture) -> None:
    """0.2.196:NaN/Inf 值只报告不自动处理。"""
    fid = _stage(tmp_path, synthetic=True, nan_point=(-1, 64))
    got = _read_fid_raw(fid)
    assert got is not None  # NaN 不再导致布局解析失败
    res = run_direct_diagnostics(tmp_path, exp_fixture)
    assert any("NaN/Inf" in r and "未自动处理" in r for r in res.reports)
    assert res.metrics.get("nan_inf_count", 0) >= 1


def test_uniform_zero_trace_reported(tmp_path: Path, bruker_dir) -> None:
    """0.2.196:均匀采样全零迹线只报告不自动处理。"""
    from core.data.bruker_reader import read_dataset

    exp = read_dataset(bruker_dir / "hsqc_2d")
    fid = _stage(tmp_path, synthetic=True, zero_rows=[0])
    fid.rename(tmp_path / f"{exp.dataset_id}.fid")
    res = run_direct_diagnostics(tmp_path, exp)
    assert any("全零迹线" in r and "未自动处理" in r for r in res.reports)
    assert res.metrics.get("zero_traces", 0) >= 1


def test_high_energy_reported_not_fixed(tmp_path: Path, exp_fixture) -> None:
    """0.2.196:持续异常高能量迹线只报告不自动处理。"""
    _stage(tmp_path, synthetic=True, high_energy_row=1)
    res = run_direct_diagnostics(tmp_path, exp_fixture)
    assert any("能量异常偏高" in r and "未自动处理" in r for r in res.reports)
    assert res.metrics.get("high_energy_traces", 0) >= 1


def test_repair_false_leaves_data(tmp_path: Path, exp_fixture) -> None:
    _stage(tmp_path, synthetic=True, spike=(-1, 200, 40.0))
    res = run_direct_diagnostics(tmp_path, exp_fixture, repair=False)
    assert res.repaired_badpoints == 0
    assert not (tmp_path / "fid_diag_bak").exists()


def test_no_fid_skips_gracefully(tmp_path: Path, exp_fixture) -> None:
    res = run_direct_diagnostics(tmp_path, exp_fixture)
    assert res.reports
    assert "跳过" in res.reports[0]


def test_render_poly_time_inserted_before_sp(bruker_dir: Path) -> None:
    """direct_poly_time=True 时 step1 在 SP 前插入 POLY -time;默认不插。"""
    from backend.script_generator import (
        generate_2d_nus_script,
        generate_3d_nus_script,
        generate_process_script,
    )
    from core.data.bruker_reader import read_dataset
    from core.planning.method_selector import select_method

    exp2 = read_dataset(bruker_dir / "nus_2d")
    exp3 = read_dataset(bruker_dir / "nus_3d")
    b2 = dict(in_file="e.fid", nuslist="nuslist", out_file="e.ft2", nuslist_count=5)
    b3 = dict(in_file="e.fid", nuslist="nuslist", out_file="e.ft3", nuslist_count=4)
    assert "POLY -time" not in generate_2d_nus_script(exp2, **b2)
    assert "POLY -time" not in generate_3d_nus_script(exp3, **b3)
    s = generate_2d_nus_script(exp2, direct_poly_time=True, **b2)
    assert s.index("| nmrPipe -fn POLY -time") < s.index("| nmrPipe -fn SP")
    s = generate_3d_nus_script(exp3, direct_poly_time=True, **b3)
    assert s.index("| nmrPipe -fn POLY -time") < s.index("| nmrPipe -fn SP")
    # 0.2.165:uniform 2D/3D 终跑完整脚本同样在直接维 SP 前插入 POLY -time
    # (与 NUS step1 对齐);默认不插(首遍预览面,0.2.160 设计)
    u2 = dict(in_file="e.fid", out_file="e.ft2")
    u3 = dict(in_file="e.fid", out_file="e.ft3")
    assert "POLY -time" not in generate_process_script(
        exp2, select_method(exp2), **u2
    )
    assert "POLY -time" not in generate_process_script(
        exp3, select_method(exp3), **u3
    )
    s2 = generate_process_script(
        exp2, select_method(exp2), direct_poly_time=True, **u2
    )
    assert s2.index("| nmrPipe -fn POLY -time") < s2.index("| nmrPipe -fn SP")
    s3 = generate_process_script(
        exp3, select_method(exp3), direct_poly_time=True, **u3
    )
    assert s3.index("| nmrPipe -fn POLY -time") < s3.index("| nmrPipe -fn SP")
