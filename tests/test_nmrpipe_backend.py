"""NMRPipe 后端行为测试（Windows 上无 NMRPipe，验证优雅降级）。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from backend.factory import create_backend
from backend.nmrpipe_backend import NMRPipeBackend
from backend.nmrpipe_finder import find_nmrpipe_bin
from core.data.bruker_reader import read_dataset
from core.planning.method_selector import select_method

_NO_NMRPIPE = find_nmrpipe_bin() is None


def test_factory_returns_nmrpipe_backend() -> None:
    backend = create_backend({"backend": {"provider": "nmrpipe"}})
    assert isinstance(backend, NMRPipeBackend)


@pytest.mark.skipif(not _NO_NMRPIPE, reason="本机已安装 NMRPipe，跳过缺失路径测试")
def test_health_check_missing_nmrpipe() -> None:
    backend = NMRPipeBackend(nmrpipe_bin="")
    health = backend.health_check()
    assert health["ok"] is False
    assert "nmrPipe" in health["message"]


@pytest.mark.skipif(not _NO_NMRPIPE, reason="本机已安装 NMRPipe，跳过缺失路径测试")
def test_process_missing_nmrpipe_graceful(bruker_dir: Path, tmp_path: Path) -> None:
    exp = read_dataset(bruker_dir / "hsqc_small")
    plan = select_method(exp)
    backend = NMRPipeBackend(nmrpipe_bin="")
    result = backend.process(exp, plan)
    assert result["success"] is False
    assert "未找到" in result["message"]


def test_process_bad_explicit_bin_graceful(bruker_dir: Path, tmp_path: Path) -> None:
    exp = read_dataset(bruker_dir / "hsqc_small")
    plan = select_method(exp)
    backend = NMRPipeBackend(nmrpipe_bin=str(tmp_path))
    result = backend.process(exp, plan)
    assert result["success"] is False


@pytest.mark.skipif(not _NO_NMRPIPE, reason="本机已安装 NMRPipe，跳过缺失路径测试")
def test_reconstruct_nus_missing_nmrpipe_graceful(bruker_dir: Path, tmp_path: Path) -> None:
    exp = read_dataset(bruker_dir / "nus_3d")
    backend = NMRPipeBackend(nmrpipe_bin="")
    result = backend.reconstruct_nus(exp, {})
    assert result["success"] is False


def test_process_accepts_extract_params(bruker_dir: Path) -> None:
    """process 接受 params(extract/ext_lo/ext_hi),无 NMRPipe 时优雅降级。"""
    exp = read_dataset(bruker_dir / "hsqc_small")
    plan = select_method(exp)
    backend = NMRPipeBackend(nmrpipe_bin="")
    result = backend.process(
        exp,
        plan,
        params={"extract": False, "ext_lo": "9.0", "ext_hi": "7.5"},
    )
    assert result["success"] is False


def test_reconstruct_nus_accepts_extract(bruker_dir: Path) -> None:
    """reconstruct_nus 接受 extract 参数,无 NMRPipe 时优雅降级。"""
    exp = read_dataset(bruker_dir / "nus_3d")
    backend = NMRPipeBackend(nmrpipe_bin="")
    result = backend.reconstruct_nus(exp, {"extract": False})
    assert result["success"] is False


def test_reconstruct_nus_rejects_uniform(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "hsqc_small")
    backend = NMRPipeBackend(nmrpipe_bin="")
    result = backend.reconstruct_nus(exp, {})
    assert result["success"] is False
    assert "非 NUS" in result["message"]


@pytest.mark.skipif(not _NO_NMRPIPE, reason="本机已安装 NMRPipe，跳过缺失路径测试")
def test_finalize_converted_fid_slice_form(tmp_path: Path) -> None:
    """0.2.80:bruker 切片式输出(fid/test%03d.fid)被接受并归位到 work/fid/。"""
    backend = NMRPipeBackend(nmrpipe_bin="")
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "fid").mkdir()
    for i in (1, 2, 3):
        (raw / "fid" / f"test{i:03d}.fid").write_bytes(b"x")
    dest = tmp_path / "work"
    dest.mkdir()
    logs: list[str] = []
    assert backend._finalize_converted_fid(raw, dest, "exp", logs)
    assert len(list((dest / "fid").glob("test*.fid"))) == 3
    assert not (dest / "exp.fid").exists()
    assert any("切片式 fid" in line for line in logs)


def test_finalize_converted_fid_single_file(tmp_path: Path) -> None:
    """单文件 test.fid 路径保持兼容(非切片式 bruker 输出)。"""
    backend = NMRPipeBackend(nmrpipe_bin="")
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "test.fid").write_bytes(b"x")
    dest = tmp_path / "work"
    dest.mkdir()
    logs: list[str] = []
    assert backend._finalize_converted_fid(raw, dest, "exp", logs)
    assert (dest / "exp.fid").is_file()


def _write_plane(
    out: Path, arr: np.ndarray, *, f1_size: int = 30, f3_size: int = 4
) -> None:
    """用 nmrglue 写一个 3D 重构平面(第一轴实/虚交错,与 nus3d_rc 一致)。

    nus3d_rc 平面为复型 (n_dir, n_f1) 实/虚交错存储,读回为 (2·n_dir,
    n_f1) 实型;头部用 FDSIZE=n_f1、FDSPECNUM=n_dir(nmrglue 2D 平面
    读取约定,已按真实平面头部核对)。
    """
    import nmrglue as ng

    dic = ng.pipe.create_empty_dic()
    dic.update(
        {
            "FDSIZE": float(arr.shape[1]),
            "FDSPECNUM": float(arr.shape[0]),
            "FDREALSIZE": float(2 * arr.shape[0]),
            "FDF1LABEL": "15N",
            "FDF1TDSIZE": float(f1_size),
            "FDF2LABEL": "1H",
            "FDF2TDSIZE": 1024.0,
            "FDF3LABEL": "13C",
            "FDF3TDSIZE": 75.0,
            "FDF3SIZE": float(f3_size),
            "FDFILECOUNT": float(f3_size),
            "FDDIMCOUNT": 3.0,
            "FDF2FTFLAG": 1.0,
            "FDF2QUADFLAG": 1.0,
        }
    )
    ng.pipe.write(str(out), dic, arr.astype(np.complex64), overwrite=True)


def _synthetic_3d_planes(
    work: Path, n_planes: int = 4, *, theta: float = 0.0
) -> Path:
    """合成 3D 复型平面:直接维 axis 0,干净信号峰,可带已知相位旋转。"""
    import nmrglue as ng

    rng = np.random.default_rng(7)
    n_dir, n_f1 = 64, 12
    plane_dir = work / "nus3d_rc"
    plane_dir.mkdir(parents=True, exist_ok=True)
    k = np.arange(n_dir, dtype=float)
    for p in range(n_planes):
        base = np.zeros((n_dir, n_f1), dtype=np.complex128)
        for j in range(0, n_f1, 4):
            center = 16 + p * 2
            for jj in range(j, j + 4):
                base[:, jj] = 400.0 / (1.0 + ((k - center) / 4.0) ** 2)
        if theta:
            ramp = np.exp(
                1j * np.deg2rad(theta + 12.0 * k / (n_dir - 1))
            )
            base = base * ramp[:, None]
        base = base + rng.normal(0, 0.05, size=base.shape)
        base = base + 1j * rng.normal(0, 0.05, size=base.shape)
        _write_plane(plane_dir / f"test{p + 1:04d}.ft1", base, f3_size=n_planes)
    dic, data = ng.pipe.read(str(plane_dir / "test0001.ft1"))
    assert np.asarray(data).dtype == np.float32, "平面应为实型交错存储"
    assert np.asarray(data).shape == (2 * n_dir, n_f1), "交错复型布局错误"
    return plane_dir


def test_display_phase_search_3d_unpacks_interleaved(
    tmp_path: Path,
) -> None:
    """0.2.98:3D 显示层相位搜索必须先把实型交错平面拆包为复型。

    主重构按 PS(0,0) 输出,干净峰近零相位时最小修正应返回 (0,0)
    (与 2D sampleA 100% 实测一致);拆包失败/交错数据直接评分会报错或
    返回无意义结果。
    """
    from backend.nmrpipe_backend import NMRPipeBackend

    backend = NMRPipeBackend(nmrpipe_bin="")
    plane_dir = _synthetic_3d_planes(tmp_path)
    logs: list[str] = []
    experiment = type("Exp", (), {"ndim": 3})()
    est = backend._display_phase_search(experiment, tmp_path, logs)
    assert est is not None, f"logs={logs}"
    p0, p1, score = est
    assert score >= 30.0
    assert abs(p0) <= 5.0, est
    assert abs(p1) <= 5.0, est
    assert plane_dir.is_dir()


def test_apply_direct_phase_3d_rotates_copy_not_source(
    tmp_path: Path,
) -> None:
    """0.2.98:3D 填相位写 nus3d_rc_ph/ 副本并 finalize,源平面保持不动。"""
    import nmrglue as ng

    from backend.nmrpipe_backend import NMRPipeBackend

    backend = NMRPipeBackend(nmrpipe_bin="")
    plane_dir = _synthetic_3d_planes(tmp_path)
    before = {
        path.name: np.asarray(ng.pipe.read(str(path))[1]).copy()
        for path in sorted(plane_dir.glob("test*.ft1"))
    }
    calls: list[tuple[Path | str | None, str | None]] = []

    def fake_finalize(experiment, *, work_dir=None, planes=None, **kwargs):
        calls.append((work_dir, planes))
        return {"success": True, "spectrum_path": str(tmp_path / "out.ft3")}

    backend.finalize_nus = fake_finalize  # type: ignore[method-assign]
    logs: list[str] = []
    experiment = type("Exp", (), {"ndim": 3})()
    ok = backend._apply_direct_phase(experiment, tmp_path, 33.0, 12.0, logs)
    assert ok
    assert calls and calls[0][0] == tmp_path
    assert calls[0][1] == "nus3d_rc_ph/test%04d.ft1"
    out_dir = tmp_path / "nus3d_rc_ph"
    assert out_dir.is_dir()
    assert len(list(out_dir.glob("test*.ft1"))) == len(
        list(plane_dir.glob("test*.ft1"))
    )
    # 源平面逐字节不动
    for path in sorted(plane_dir.glob("test*.ft1")):
        after = np.asarray(ng.pipe.read(str(path))[1])
        assert np.array_equal(after, before[path.name])



def test_finalize_converted_fid_missing(tmp_path: Path) -> None:
    """既无 test.fid 也无切片时失败(不静默)。"""
    backend = NMRPipeBackend(nmrpipe_bin="")
    raw = tmp_path / "raw"
    raw.mkdir()
    dest = tmp_path / "work"
    dest.mkdir()
    assert not backend._finalize_converted_fid(raw, dest, "exp", [])


def test_reconstruct_nus_segments_missing_nmrpipe(bruker_dir: Path, tmp_path: Path) -> None:
    import shutil

    from core.data.bruker_reader import read_segments

    dst_a = tmp_path / "seg_a"
    dst_b = tmp_path / "seg_b"
    shutil.copytree(bruker_dir / "nus_3d", dst_a)
    shutil.copytree(bruker_dir / "nus_3d", dst_b)
    exp = read_segments([dst_a, dst_b])
    backend = NMRPipeBackend(nmrpipe_bin="")
    result = backend.reconstruct_nus(exp, {})
    assert result["success"] is False

def test_write_merged_nuslist_detects_bad_points(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """坏点检测:越界点 + 跨段重复点从合并 nuslist 剔除并 ⚠ 提示。"""
    import shutil

    from backend.nmrpipe_backend import NMRPipeBackend
    from core.data.bruker_reader import read_segments

    seg1 = tmp_path / "s1"
    seg2 = tmp_path / "s2"
    shutil.copytree(bruker_dir / "nus_3d", seg1)
    shutil.copytree(bruker_dir / "nus_3d", seg2)
    nl1 = (seg1 / "nuslist").read_text(encoding="utf-8").splitlines()
    nl2 = (seg2 / "nuslist").read_text(encoding="utf-8").splitlines()
    first2 = nl2[0]
    nl1 = [first2] + nl1
    nl2 = nl2 + ["1000 1000"]
    (seg1 / "nuslist").write_text("\n".join(nl1) + "\n", encoding="utf-8")
    (seg2 / "nuslist").write_text("\n".join(nl2) + "\n", encoding="utf-8")
    exp = read_segments([seg1, seg2])
    backend = NMRPipeBackend()
    logs: list[str] = []
    count, bad = backend._write_merged_nuslist(tmp_path, [seg1, seg2], exp, logs)
    assert (1000, 1000) in bad
    assert tuple(int(v) for v in first2.split()) in bad
    joined = "\n".join(logs)
    assert "⚠ 检测到采样坏点" in joined
    assert "越界" in joined
    assert "重复" in joined
    written = (tmp_path / "nuslist").read_text(encoding="utf-8").splitlines()
    assert all(tuple(int(v) for v in line.split()) not in bad for line in written)
    assert count == len(written)

def test_ser_point_layout_derives_bytes(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """0.2.195:ser 布局按采样参数推导(直接维 TD 补齐 + 字长 + 冗余)。"""
    import shutil

    from backend.nmrpipe_backend import _ser_point_layout

    src = tmp_path / "nus"
    shutil.copytree(bruker_dir / "nus_2d", src)
    exp = read_dataset(src)
    # nus_2d 直接 TD=2048 → 补齐 2048 → 1024 复点 × 2 × 8 字节 = 16384
    layout = _ser_point_layout(exp, 4 * 16384, 4)
    assert layout == (16384, 16384, 1)
    # 冗余 4 个向量/点
    layout2 = _ser_point_layout(exp, 4 * 4 * 16384, 4)
    assert layout2 == (4 * 16384, 16384, 4)
    # 不整除/无法确定 → None
    assert _ser_point_layout(exp, 4 * 100, 4) is None


def test_zero_bad_point_fid_states_slices(
    tmp_path: Path, bruker_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.195:坏点清零按 States 布局落在切片 2*f1+1/2*f1+2 行 2*f2/2*f2+1,
    不再误用 test{f1}。"""
    import shutil

    from backend.nmrpipe_backend import NMRPipeBackend
    from core.data.bruker_reader import read_dataset

    src = tmp_path / "nus"
    shutil.copytree(bruker_dir / "nus_3d", src)
    exp = read_dataset(src)
    work = tmp_path / "work"
    slice_dir = work / "merged" / "fid"
    slice_dir.mkdir(parents=True)
    for z in (3, 7, 8):
        (slice_dir / f"test{z:03d}.fid").write_bytes(b"x")

    read_targets: list[str] = []
    write_targets: list[str] = []

    def fake_read(path):
        read_targets.append(Path(path).name)
        return {"FDSIZE": 454, "FDSPECNUM": 170}, np.ones(
            (170, 454), dtype=np.complex64
        )

    def fake_write(path, dic, arr, overwrite=False):
        write_targets.append(Path(path).name)

    monkeypatch.setattr("nmrglue.pipe.read", fake_read)
    monkeypatch.setattr("nmrglue.pipe.write", fake_write)

    backend = NMRPipeBackend()
    logs: list[str] = []
    backend._zero_bad_point_fid(
        work, [(5, 3)], logs, dataset_id=exp.dataset_id
    )
    joined = "\n".join(logs)
    # States:复点 (f2=5, f1=3) → 切片 7/8 行 10/11
    assert sorted(write_targets) == ["test007.fid", "test008.fid"]
    assert "test007.fid" in joined and "test008.fid" in joined
    assert "test003.fid" not in joined


def test_nus_grid_from_points_and_apply(tmp_path: Path) -> None:
    """0.2.197:坏点移除后按实际采样范围推导并缩小 NusTD。"""
    from backend.nmrpipe_backend import (
        _apply_nus_grid_after_clean,
        _nus_grid_from_points,
    )
    from core.data.internal_data_model import (
        AxisRole,
        Dimension,
        Experiment,
        Sampling,
        SamplingMode,
    )

    assert _nus_grid_from_points([]) is None
    assert _nus_grid_from_points([(10,), (63,)]) == [64]
    assert _nus_grid_from_points([(5, 3), (82, 25)]) == [83, 26]

    exp3 = Experiment(
        dataset_id="x",
        source_path=tmp_path,
        ndim=3,
        dimensions=[
            Dimension(logical_axis="F3", nucleus="1H", td=908, role=AxisRole.DIRECT),
            Dimension(logical_axis="F2", nucleus="15N", td=170, role=AxisRole.INDIRECT),
            Dimension(logical_axis="F1", nucleus="13C", td=52, role=AxisRole.INDIRECT),
        ],
        sampling=Sampling(mode=SamplingMode.NUS),
        acquisition_parameters={"acqu2s": {"NusTD": 170}, "acqu3s": {"NusTD": 52}},
    )
    logs = _apply_nus_grid_after_clean(exp3, [(5, 3), (82, 25)])
    assert exp3.acquisition_parameters["acqu2s"]["NusTD"] == 166
    # F1 实际范围 26 → 26×2=52,不变
    assert exp3.acquisition_parameters["acqu3s"]["NusTD"] == 52
    assert any("170→166" in line for line in logs)

    exp2 = Experiment(
        dataset_id="y",
        source_path=tmp_path,
        ndim=2,
        dimensions=[
            Dimension(logical_axis="F2", nucleus="1H", td=2048, role=AxisRole.DIRECT),
            Dimension(logical_axis="F1", nucleus="15N", td=64, role=AxisRole.INDIRECT),
        ],
        sampling=Sampling(mode=SamplingMode.NUS),
        acquisition_parameters={"acqu2s": {"NusTD": 64}},
    )
    logs2 = _apply_nus_grid_after_clean(exp2, [(10,), (20,)])
    assert exp2.acquisition_parameters["acqu2s"]["NusTD"] == 21
    assert any("64→21" in line for line in logs2)


def test_clean_work_nuslist_single_dataset(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """单 NUS 数据坏点清理:越界点剔除 + ⚠ 提示(所有 NUS 数据统一)。"""
    import shutil

    from backend.nmrpipe_backend import NMRPipeBackend
    from core.data.bruker_reader import read_dataset

    src = tmp_path / "nus"
    shutil.copytree(bruker_dir / "nus_2d", src)
    # 注入越界点(nus_2d F1 复点网格上限 td//2)
    lines = (src / "nuslist").read_text(encoding="utf-8").splitlines()
    (src / "nuslist").write_text("\n".join(lines) + "\n1000\n", encoding="utf-8")
    exp = read_dataset(src)
    work = tmp_path / "work"
    work.mkdir()
    shutil.copy2(src / "nuslist", work / "nuslist")
    backend = NMRPipeBackend()
    logs: list[str] = []
    count, bad = backend._clean_work_nuslist(work, exp, logs)
    assert (1000,) in bad
    assert count == len(lines)
    joined = "\n".join(logs)
    assert "⚠ 检测到采样坏点" in joined
    written = (work / "nuslist").read_text(encoding="utf-8").splitlines()
    assert len(written) == len(lines)


def test_clean_source_nus_single_removes_with_backup(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """0.2.124:坏点在源头 ser/nuslist 删除并备份,不再等生成 FID 清零。"""
    import shutil

    from backend.nmrpipe_backend import NMRPipeBackend

    raw = tmp_path / "raw"
    shutil.copytree(bruker_dir / "nus_2d", raw)
    lines = (raw / "nuslist").read_text(encoding="utf-8").splitlines()
    (raw / "nuslist").write_text("\n".join(lines) + "\n1000\n", encoding="utf-8")
    n = len(lines) + 1
    # 0.2.195:ser 字节随采样参数变化(直接维 TD 补齐 + 字长),按 nus_2d
    # fixture(直接 TD=2048,双精度字长 8)构造:2048//2×2×8 = 16384 字节/点
    row_bytes = 16384
    ser = b"".join(bytes([i % 256]) * row_bytes for i in range(n))
    (raw / "ser").write_bytes(ser)
    exp = read_dataset(raw)
    backend = NMRPipeBackend()
    logs: list[str] = []
    count, bad, removed = backend._clean_source_nus(exp, [raw], logs)
    assert removed is True
    assert count == len(lines)
    assert (1000,) in bad
    assert (raw / "ser.bak").is_file()
    assert (raw / "ser.bak").stat().st_size == len(ser)
    kept = (raw / "ser").read_bytes()
    assert len(kept) == len(ser) - row_bytes  # 坏点(末行)整块删除
    assert kept == ser[: len(lines) * row_bytes]
    written = (raw / "nuslist").read_text(encoding="utf-8").splitlines()
    assert all(tuple(int(v) for v in line.split()) != (1000,) for line in written)
    assert (raw / "nuslist.bak").is_file()
    joined = "\n".join(logs)
    assert "源头" in joined and "备份" in joined


def test_clean_source_nus_breaks_link_external_untouched(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """0.2.124:raw/ser 为硬链接时,源头删除不污染外部原件。"""
    import os
    import shutil

    from backend.nmrpipe_backend import NMRPipeBackend

    raw = tmp_path / "raw"
    shutil.copytree(bruker_dir / "nus_2d", raw)
    lines = (raw / "nuslist").read_text(encoding="utf-8").splitlines()
    (raw / "nuslist").write_text("\n".join(lines) + "\n1000\n", encoding="utf-8")
    n = len(lines) + 1
    row_bytes = 16384
    ser = b"".join(bytes([i % 256]) * row_bytes for i in range(n))
    external = tmp_path / "external_ser"
    external.write_bytes(ser)
    os.link(external, raw / "ser")
    exp = read_dataset(raw)
    backend = NMRPipeBackend()
    logs: list[str] = []
    _count, _bad, removed = backend._clean_source_nus(exp, [raw], logs)
    assert removed is True
    assert external.read_bytes() == ser  # 外部原件不变
    assert (raw / "ser").stat().st_size == len(ser) - row_bytes
    assert (raw / "ser.bak").stat().st_size == len(ser)


def test_clean_source_nus_segments_drops_bad_and_dups(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """0.2.124:多段源头清理——越界点与跨段重复点从各段 ser/nuslist 删除。"""
    import shutil

    from backend.nmrpipe_backend import NMRPipeBackend
    from core.data.bruker_reader import read_segments

    seg1 = tmp_path / "s1"
    seg2 = tmp_path / "s2"
    shutil.copytree(bruker_dir / "nus_2d", seg1)
    shutil.copytree(bruker_dir / "nus_2d", seg2)
    nl1 = (seg1 / "nuslist").read_text(encoding="utf-8").splitlines()
    dup_x = "7 3"  # 不在 fixture 中,只在 seg1/seg2 各出现一次 → 跨段重复
    (seg1 / "nuslist").write_text("\n".join(nl1) + "\n" + dup_x + "\n", encoding="utf-8")
    seg2_pts = [dup_x, "9 10", "11 12", "13 14", "15 16", "1000 1000"]
    (seg2 / "nuslist").write_text("\n".join(seg2_pts) + "\n", encoding="utf-8")
    n1, n2 = len(nl1) + 1, len(seg2_pts)
    row_bytes = 16384
    (seg1 / "ser").write_bytes(b"".join(bytes([i % 256]) * row_bytes for i in range(n1)))
    (seg2 / "ser").write_bytes(b"".join(bytes([j % 256]) * row_bytes for j in range(n2)))
    exp = read_segments([seg1, seg2])
    backend = NMRPipeBackend()
    logs: list[str] = []
    count, bad, removed = backend._clean_source_nus(exp, [seg1, seg2], logs)
    assert removed is True
    # seg1 删 1 行(跨段重复 dup_x);seg2 删 2 行(dup_x + 越界)
    assert (seg1 / "ser").stat().st_size == n1 * row_bytes - row_bytes
    assert (seg2 / "ser").stat().st_size == n2 * row_bytes - 2 * row_bytes
    assert (seg1 / "ser.bak").is_file() and (seg2 / "ser.bak").is_file()
    def _points(dir_path: Path) -> list[tuple[int, ...]]:
        return [
            tuple(int(v) for v in line.split())
            for line in (dir_path / "nuslist")
            .read_text(encoding="utf-8")
            .splitlines()
        ]

    merged = _points(seg1) + _points(seg2)
    assert len(merged) == count
    assert len(set(merged)) == len(merged)
    assert all(p != (1000, 1000) for p in merged)


class _FakeConvertRuntime:
    """模拟 bruker/fid.com:按请求产出单文件或切片式 fid。"""

    def __init__(self, slices: int = 0, single: bool = False) -> None:
        self.slices = slices
        self.single = single
        self.calls: list[tuple[list[str], str | None]] = []
        self.bruker_cwd: str | None = None
        self.acqu3s_td: int | None = None

    def run(self, argv, *, cwd=None, timeout=3600, on_line=None):
        from backend.runtime import CompletedProcess

        self.calls.append((list(argv), cwd))
        if argv[:2] == ["bruker", "-AUTO"]:
            from core.experiment.bruker_parser import parse_param_file

            base = Path(cwd)
            self.bruker_cwd = cwd
            if (base / "acqu3s").is_file():
                self.acqu3s_td = parse_param_file(base / "acqu3s")["TD"]
            (base / "fid.com").write_text(
                "bruk2pipe -in ./ser -out ./test.fid \\\n"
                " -xN 96 -yN 48 -zN 128\n",
                encoding="utf-8",
                newline="\n",
            )
            if self.slices:
                slice_dir = base / "fid"
                slice_dir.mkdir(exist_ok=True)
                for i in range(1, self.slices + 1):
                    (slice_dir / f"test{i:03d}.fid").write_bytes(b"x")
            elif self.single:
                (base / "test.fid").write_bytes(b"x")
            # fid.com 的 nusExpand.tcl -mask 输出采样掩码(1=采样点),测试清理
            mask_dir = base / "mask"
            mask_dir.mkdir(exist_ok=True)
            for i in range(1, 5):
                (mask_dir / f"test{i:03d}.fid").write_bytes(b"\x00")
        return CompletedProcess("", "", "", 0)


def _fake_bruker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "backend.nmrpipe_backend.find_tool",
        lambda name, nmrpipe_bin=None: (
            Path("/bin/bruker") if name == "bruker" else None
        ),
    )


def test_convert_dir_nus3d_stages_acqu3s_td_fix(
    tmp_path: Path, bruker_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """3D NUS(acqu3s TD=1):bruker 在 TD 修正暂存副本中运行,切片归位 work/fid。"""
    import shutil

    from backend.nmrpipe_backend import NMRPipeBackend
    from core.experiment.bruker_parser import parse_param_file

    raw = tmp_path / "raw"
    shutil.copytree(bruker_dir / "nus_3d", raw)
    work = tmp_path / "work"
    work.mkdir()
    exp = read_dataset(raw)
    fake = _FakeConvertRuntime(slices=128)
    backend = NMRPipeBackend(nmrpipe_bin="")
    _fake_bruker(monkeypatch)
    logs: list[str] = []
    assert backend._convert_dir(fake, exp, raw, work, True, logs)
    stage = Path(fake.bruker_cwd)
    assert stage != raw
    assert fake.acqu3s_td == 128  # 暂存副本 TD=NusTD
    assert parse_param_file(raw / "acqu3s")["TD"] == 1  # 原件未动
    # 0.2.198:修改前备份原始参数文件(.bak,内容为原件)
    assert (raw / "acqu3s.bak").is_file()
    assert parse_param_file(raw / "acqu3s.bak")["TD"] == 1
    assert (raw / "acqus.bak").is_file()
    assert (raw / "acqu2s.bak").is_file()
    assert any("原始参数已备份" in line for line in logs)
    assert len(list((work / "fid").glob("test*.fid"))) == 128
    assert not (work / f"{exp.dataset_id}.fid").exists()
    assert any("acqu3s TD" in line for line in logs)
    assert any("切片式" in line for line in logs)
    # 暂存已清理,raw 未产生 test.fid/fid.com/ser_full
    assert not stage.exists()
    assert not (raw / "test.fid").exists()
    assert not (raw / "fid.com").exists()
    assert not (raw / "ser_full").exists()
    assert not (raw / "mask").exists()  # fid.com 的 mask 输出留在暂存,随暂存清理


def test_convert_dir_nus2d_no_stage(
    tmp_path: Path, bruker_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """2D NUS 不受 acqu3s 修正影响:仍在 raw 中转换,单文件归位。"""
    import shutil

    from backend.nmrpipe_backend import NMRPipeBackend

    raw = tmp_path / "raw2d"
    shutil.copytree(bruker_dir / "nus_2d", raw)
    work = tmp_path / "work"
    work.mkdir()
    exp = read_dataset(raw)
    fake = _FakeConvertRuntime(single=True)
    backend = NMRPipeBackend(nmrpipe_bin="")
    _fake_bruker(monkeypatch)
    logs: list[str] = []
    assert backend._convert_dir(fake, exp, raw, work, True, logs)
    bruker_cwd = next(
        cwd for argv, cwd in fake.calls if argv[:2] == ["bruker", "-AUTO"]
    )
    assert Path(bruker_cwd) == raw
    assert (work / f"{exp.dataset_id}.fid").is_file()
    assert not (work / "fid").exists()
    assert not (raw / "mask").exists()  # 0.2.199-补13:raw 内 fid.com 输出的 mask/ 已清理


def test_converted_fid_path_slices_and_single(tmp_path: Path) -> None:
    """convert_to_fid 产物路径:切片式 → work/fid/,单文件 → work/<id>.fid。"""
    from backend.nmrpipe_backend import NMRPipeBackend

    work = tmp_path / "work"
    work.mkdir()
    assert NMRPipeBackend._converted_fid_path(work, "exp") == work / "exp.fid"
    slice_dir = work / "fid"
    slice_dir.mkdir()
    (slice_dir / "test001.fid").write_bytes(b"x")
    assert NMRPipeBackend._converted_fid_path(work, "exp") == slice_dir


def test_needs_acqu3s_td_fix_gates(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """修正用于 NUS 3D(含分段,acqu3s TD=1);均匀/2D 不触发。"""
    import shutil

    from backend.nmrpipe_backend import NMRPipeBackend
    from core.data.bruker_reader import read_segments

    backend = NMRPipeBackend(nmrpipe_bin="")
    raw3d = tmp_path / "raw3d"
    shutil.copytree(bruker_dir / "nus_3d", raw3d)
    assert backend._needs_acqu3s_td_fix(read_dataset(raw3d))
    # 多段:与普通 NUS 一致,同样触发 acqu3s TD 修正(切片流输出)
    seg_a = tmp_path / "seg_a"
    seg_b = tmp_path / "seg_b"
    shutil.copytree(bruker_dir / "nus_3d", seg_a)
    shutil.copytree(bruker_dir / "nus_3d", seg_b)
    assert backend._needs_acqu3s_td_fix(read_segments([seg_a, seg_b]))
    # 2D NUS 与均匀 3D 不触发
    raw2d = tmp_path / "raw2d"
    shutil.copytree(bruker_dir / "nus_2d", raw2d)
    assert not backend._needs_acqu3s_td_fix(read_dataset(raw2d))
    rawu = tmp_path / "rawu"
    shutil.copytree(bruker_dir / "hsqc_small", rawu)
    assert not backend._needs_acqu3s_td_fix(read_dataset(rawu))

def _write_3d_stream_ft3(path: Path) -> None:
    """写单流 3D 终谱头(FDSIZE=1H 直接维,尺寸与 sampleB.ft3 实测一致)。"""
    import nmrglue as ng

    dic = {k: "0" for k in ng.fileio.pipe.fdata_dic}
    dic["FDMAGIC"] = 9.2330230000000007e14
    dic["FDDIMCOUNT"] = 3
    dic["FDSIZE"] = 750.0
    dic["FDSPECNUM"] = 256.0
    dic["FDF3SIZE"] = 64.0
    dic["FDPIPEFLAG"] = 1.0
    dic["FDQUADFLAG"] = 1
    for pre, label, obs, car, orig, sw in (
        ("FDF1", "15N", 60.818, 117.986, 6115.307, 2189.142),
        ("FDF2", "1H", 600.133, 4.696, 3602.677, 3001.729),
        ("FDF3", "13C", 150.909, 38.996, 272.927, 11312.218),
    ):
        dic[pre + "LABEL"] = label
        dic[pre + "OBS"] = obs
        dic[pre + "CAR"] = car
        dic[pre + "ORIG"] = orig
        dic[pre + "SW"] = sw
        dic[pre + "QUADFLAG"] = 1
    data = np.zeros((64, 256, 750), dtype=np.float32)
    ng.pipe.write(str(path), dic, data, overwrite=True)


def _write_proj_ft2(path: Path, nrow: int, ncol: int) -> None:
    """写投影输出(头为 proj3D 实测的错误平面头 15N/1H,待重写)。"""
    import nmrglue as ng

    dic = {k: "0" for k in ng.fileio.pipe.fdata_dic}
    dic["FDMAGIC"] = 9.2330230000000007e14
    dic["FDDIMCOUNT"] = 2
    dic["FDSPECNUM"] = float(nrow)
    dic["FDSIZE"] = float(ncol)
    dic["FDQUADFLAG"] = 1
    dic["FDF1QUADFLAG"] = 1
    dic["FDF2QUADFLAG"] = 1
    for pre in ("FDF1", "FDF2"):
        dic[pre + "LABEL"] = "15N" if pre == "FDF1" else "1H"
        dic[pre + "OBS"] = 60.818 if pre == "FDF1" else 600.133
        dic[pre + "CAR"] = 117.986 if pre == "FDF1" else 4.696
        dic[pre + "ORIG"] = 6115.307 if pre == "FDF1" else 3602.677
        dic[pre + "SW"] = 2189.142 if pre == "FDF1" else 3001.729
    ng.pipe.write(
        str(path), dic, np.zeros((nrow, ncol), dtype=np.float32), overwrite=True
    )


def test_project_3d_mapping(tmp_path: Path, monkeypatch) -> None:
    """project_3d 直接喂 3D 谱给 proj3D.tcl:自动命名 *.dat,不重写头。"""
    from backend.nmrpipe_backend import NMRPipeBackend

    fake_tool = tmp_path / "tool"
    fake_tool.write_text("#!/bin/sh", encoding="utf-8")

    def fake_find(name, _bin=None):
        return fake_tool

    monkeypatch.setattr("backend.nmrpipe_finder.find_tool", fake_find)

    calls: list[list[str]] = []

    class FakeRun:
        def __init__(self, rc):
            self.returncode = rc

    def fake_run(argv, *, cwd=None, timeout=3600, on_line=None):
        calls.append(list(argv))
        # 0.2.133:proj3D.tcl 自动命名输出 {核A}.{核B}.dat(不预拆平面)
        out_dir = Path(argv[argv.index("-outDir") + 1])
        _write_proj_ft2(out_dir / "13C.15N.dat", 128, 256)
        _write_proj_ft2(out_dir / "1H.13C.dat", 256, 600)
        _write_proj_ft2(out_dir / "1H.15N.dat", 128, 600)
        return FakeRun(0)

    monkeypatch.setattr(
        "backend.nmrpipe_backend.CshRuntime.run", staticmethod(fake_run)
    )
    backend = NMRPipeBackend()
    src = tmp_path / "final.ft3"
    _write_3d_stream_ft3(src)
    out = tmp_path / "out"
    out.mkdir()
    result = backend.project_3d(src, out, prefix="proj", labels=["15N", "1H", "13C"])
    # 键 = 文件名两核;labels=固定轴核,nuclei=平面两核(文件名 X.Y 顺序)
    assert result["labels"] == {
        "13C-15N": "1H",
        "1H-13C": "15N",
        "1H-15N": "13C",
    }
    assert result["nuclei"] == {
        "13C-15N": ["13C", "15N"],
        "1H-13C": ["1H", "13C"],
        "1H-15N": ["1H", "15N"],
    }
    assert len(result["paths"]) == 3
    for key, p in result["paths"].items():
        assert Path(p).is_file()
    # 头未被重写:保持 proj3D 原样输出(槽位仍为 fake 写入的 15N/1H)
    import nmrglue as ng

    dic, _ = ng.pipe.read(result["paths"]["13C-15N"])
    assert str(dic["FDF1LABEL"]) == "15N"
    assert str(dic["FDF2LABEL"]) == "1H"
    # 只调用一次 proj3D.tcl 的等效命令(直接喂 3D 谱,自动命名,含 -sum)
    assert len(calls) == 1
    argv = calls[0]
    joined = " ".join(str(a) for a in argv)
    assert "-in" in argv and argv[argv.index("-in") + 1].endswith("final.ft3")
    assert "-outDir" in argv
    assert "-sum" in argv
    assert "-xyOutName" not in joined
    assert "-xzOutName" not in joined


def test_finalize_nus_window_param_passthrough(
    tmp_path: Path, monkeypatch, bruker_dir: Path
) -> None:
    """finalize_nus 把 params.window 透传给定稿脚本(间接维 FT 前)。"""
    from backend.nmrpipe_backend import NMRPipeBackend
    from backend.runtime import CompletedProcess

    experiment = read_dataset(bruker_dir / "nus_2d")
    work = tmp_path / "win_work"
    (work / "nus2d").mkdir(parents=True)
    (work / "nus2d" / "recon.ft1").write_bytes(b"x")

    class _FakeCsh:
        def run(self, argv, *, cwd=None, timeout=3600, on_line=None):
            (Path(cwd) / f"{experiment.dataset_id}.ft2").write_bytes(b"x")
            return CompletedProcess("", "", "", 0)

    monkeypatch.setattr("backend.nmrpipe_backend.CshRuntime", _FakeCsh)
    monkeypatch.setattr(
        "backend.nmrpipe_backend.find_nmrpipe_bin", lambda explicit="": Path("/bin")
    )
    backend = NMRPipeBackend(nmrpipe_bin="")
    resp = backend.finalize_nus(
        experiment,
        work_dir=work,
        params={"window": {"F1": {"type": "gaussian", "g1": 3.0}}},
    )
    assert resp["success"] is True, resp
    script = (work / f"{experiment.dataset_id}_finalize.com").read_text(
        encoding="utf-8"
    )
    assert "| nmrPipe -fn GM -g1 3 -g2 15 \\" in script
    lines = script.splitlines()
    gm = next(i for i, line in enumerate(lines) if "GM -g1 3" in line)
    zf = next(
        i for i, line in enumerate(lines)
        if "| nmrPipe -fn ZF" in line and i > gm
    )
    ft = next(
        i for i, line in enumerate(lines)
        if "| nmrPipe -fn FT" in line and i > zf
    )
    assert gm < zf < ft
