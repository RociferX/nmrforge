"""2D NUS 兼容:转换跟随 bruker -AUTO 的输出形态 + 2D 留出残差留档。

用户裁定(2026-09-11):bruker -AUTO 对本就能识别的 2D NUS 直接给单文件
（`-out ./test.fid`，程序只把它改名成 `{dataset_id}.fid`）；切片流只是 3D
在直接维处理之后才出现的东西，因此**不做**「2D 强制单文件」这类脚本改写，
脚本一律以 -AUTO 给的为准。

2D 留出采样点残差靠 `script_generator.build_2d_direct_only_script` 留档
SMILE 输入（2D 单文件管道切不出切片）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.bruker_workflow import patch_fid_com
from core.data.bruker_reader import read_dataset

BRUKER = Path(__file__).resolve().parent / "fixtures" / "bruker"

_CONT = " \\"  # 行尾续行:空格 + 反斜杠


def _auto_2d_nus_fid_com(out_line: str) -> str:
    """bruker -AUTO 对 2D NUS 的脚本骨架(展开 + bruk2pipe + mask)。"""
    return (
        "nusExpand.tcl -mode bruker -sampleCount 32 -avg -off 0" + _CONT + "\n"
        " -in ./ser -out ./ser_full -sample ./nuslist\n"
        "\n"
        "bruk2pipe -in ./ser_full" + _CONT + "\n"
        "  -xN 2048 -yN 254 -xT 1024 -yT 127" + _CONT + "\n"
        f"  {out_line}\n"
        "\n"
        "nusExpand.tcl -mask -noexpand -mode pipe -sampleCount 32 -avg -off 0"
        + _CONT + "\n"
        " -in ./test.fid -out ./mask.fid -sample ./nuslist\n"
    )


def test_2d_nus_auto_single_file_out_renamed_to_dataset() -> None:
    """-AUTO 给的单文件(test.fid)→ 改名 {dataset_id}.fid(既有约定,不改形态)。"""
    exp = read_dataset(BRUKER / "nus_2d")
    assert exp.ndim == 2

    patched, warnings = patch_fid_com(
        _auto_2d_nus_fid_com("-out ./test.fid -ov"), exp
    )

    assert f"-out ./{exp.dataset_id}.fid -ov" in patched
    assert any("out:" in w and "test.fid" in w for w in warnings)


def test_2d_nus_auto_slice_out_left_untouched() -> None:
    """-AUTO 若给切片式输出,不做 2D 特有改写(一律以 -AUTO 为准)。"""
    exp = read_dataset(BRUKER / "nus_2d")

    patched, _warnings = patch_fid_com(
        _auto_2d_nus_fid_com("-out ./fid/test%03d.fid -ov"), exp
    )

    assert "-out ./fid/test%03d.fid -ov" in patched


def test_3d_nus_fid_com_keeps_slice_stream() -> None:
    """3D:切片式输出原样保留(切片只在 3D 直接维处理后出现)。"""
    exp = read_dataset(BRUKER / "nus_3d")
    assert exp.ndim == 3

    text = (
        "bruk2pipe -in ./ser_full" + _CONT + "\n"
        "  -xN 1024 -yN 166 -zN 4702 -xT 454 -yT 83 -zT 2351" + _CONT + "\n"
        "  -out ./fid/test%03d.fid -ov\n"
    )
    patched, _warnings = patch_fid_com(text, exp)

    assert "-out ./fid/test%03d.fid" in patched


def test_2d_uniform_fid_com_out_name_unchanged() -> None:
    """2D 均匀采样(非 NUS)输出名改写行为不回归。"""
    exp = read_dataset(BRUKER / "hsqc_2d")
    assert exp.ndim == 2

    text = "bruk2pipe -in ./ser" + _CONT + "\n  -out ./test.fid\n"
    patched, _warnings = patch_fid_com(text, exp)

    assert f"-out ./{exp.dataset_id}.fid" in patched


def test_build_2d_direct_only_script_trims_before_smile() -> None:
    """2D 直接维留档脚本:保留直接维处理、去掉 SMILE 及其后、末尾单文件输出。"""
    from backend.script_generator import (
        build_2d_direct_only_script,
        generate_2d_nus_script,
    )

    exp = read_dataset(BRUKER / "nus_2d")
    script = generate_2d_nus_script(
        exp, in_file="e.fid", nuslist="nuslist", out_file="e.ft2"
    )
    direct = build_2d_direct_only_script(script)

    assert direct.endswith("| pipe2xyz -out nus2d/direct.ft1 -x -ov\n")
    assert "-fn SMILE" not in direct
    assert "-out e.ft2" not in direct
    assert "nus2d/recon.ft1" not in direct
    assert "| nmrPipe -fn EXT" in direct  # 直接维处理阶段保留
    assert "| nmrPipe -fn POLY -auto" in direct  # SMILE 前最后一步保留


def test_build_2d_direct_only_script_rejects_unknown_shape() -> None:
    """切不出来时返回空串(调用方跳过留出残差,不静默错切)。"""
    from backend.script_generator import build_2d_direct_only_script

    assert build_2d_direct_only_script("#!/bin/csh\necho hi\n") == ""

def _make_2d_nus_dataset(
    root: Path,
    *,
    rows: int,
    keep: list[int],
    x_n: int = 2048,
    td_rows: int = 256,
    dtype_code: int = 0,
) -> Path:
    """造 2D NUS 数据集(无 nuslist):ser 有 rows 行,keep 里的复点非零。

    dtype_code:TopSpin DTYPE(0=int32 / 1=float64 / 2=float32),写入 acqus。
    """
    import numpy as np

    ds = root / f"ds_{rows}_{len(keep)}_{dtype_code}"
    ds.mkdir(parents=True, exist_ok=True)
    (ds / "acqus").write_text(
        f"##$TD= {x_n}\n##$FnMODE= 0\n##$NusAMOUNT= 25\n##$NusTD= 0\n"
        f"##$DTYPE= {dtype_code}\n",
        encoding="utf-8",
    )
    (ds / "acqu2s").write_text(
        f"##$TD= {td_rows}\n##$FnMODE= 5\n##$NusTD= {td_rows}\n##$NUC1= <15N>\n",
        encoding="utf-8",
    )
    # 未知 DTYPE 用途例:数据按 int32 写,判定应因未知 DTYPE 直接拒绝
    data = np.zeros((rows, x_n), dtype={0: "<i4", 1: "<f8", 2: "<f4"}.get(dtype_code, "<i4"))
    for k in keep:
        if 2 * k + 1 < rows:
            data[2 * k] = 7
            data[2 * k + 1] = -3
    data.tofile(ds / "ser")
    return ds


def _recover(ds: Path) -> tuple[list[int] | None, list[str]]:
    from backend.nmrpipe_backend import NMRPipeBackend

    exp = read_dataset(ds)
    # 满采样数据在读入阶段就已降级为 uniform(2026-09-14);恢复函数仍可直接调用
    assert exp.sampling.mode.value in ("nus", "uniform"), exp.sampling.mode
    logs: list[str] = []
    points = NMRPipeBackend(nmrpipe_bin="")._recover_dense_2d_nus(ds, exp, logs)
    return points, logs


def test_recover_dense_2d_nus_arbitrary_subset(tmp_path: Path) -> None:
    """密集模型:采样点从零模式恢复,支持任意子集(不是「前 N 个」前缀)。"""
    keep = [0, 5, 37, 64, 100, 127]
    ds = _make_2d_nus_dataset(tmp_path, rows=256, keep=keep)

    points, logs = _recover(ds)

    assert points == keep
    assert any("密集模型" in line for line in logs)
    assert any("6/128" in line for line in logs)


def test_recover_dense_2d_nus_prefix(tmp_path: Path) -> None:
    """前缀子集(常见造数据方式)同样恢复成真实点集。"""
    ds = _make_2d_nus_dataset(tmp_path, rows=256, keep=list(range(32)))

    points, _logs = _recover(ds)

    assert points == list(range(32))


def test_recover_dense_2d_nus_sparse_is_refused(tmp_path: Path) -> None:
    """真稀疏(行数 < 声明网格):采样位置不可知 → 返回 None(报缺 nuslist)。"""
    ds = _make_2d_nus_dataset(tmp_path, rows=64, keep=list(range(32)))

    points, logs = _recover(ds)

    assert points is None
    assert any("稀疏文件" in line for line in logs)


def test_recover_dense_2d_nus_metadata_mismatch_is_refused(tmp_path: Path) -> None:
    """行数 > 声明网格:元数据与文件不一致 → 返回 None。"""
    ds = _make_2d_nus_dataset(tmp_path, rows=512, keep=[0, 1, 2])

    points, logs = _recover(ds)

    assert points is None
    assert any("不一致" in line for line in logs)


def test_recover_dense_2d_nus_all_nonzero(tmp_path: Path) -> None:
    """全格无零行(NusAMOUNT 标注 NUS 但数据满采样)→ 读入即判 uniform。

    2026-09-14(用户「满采样应该走 uniform」):降级发生在读数据阶段;恢复函数
    作为防御性入口仍可直接调用,并留下「满采样」日志。
    """
    ds = _make_2d_nus_dataset(tmp_path, rows=256, keep=list(range(128)))
    exp = read_dataset(ds)
    assert exp.sampling.mode.value == "uniform", exp.sampling.mode
    assert exp.sampling.schedule_type == "full_sampling"
    assert exp.sampling.sampling_fraction == pytest.approx(1.0)
    assert any("满采样" in line for line in exp.sampling.evidence)

    points, logs = _recover(ds)

    assert points == list(range(128))
    assert any("满采样" in line for line in logs)


def test_full_nuslist_is_uniform(tmp_path: Path) -> None:
    """nuslist 覆盖全格 → 实际满采样 → uniform(不再走 SMILE)。"""
    ds = _make_2d_nus_dataset(tmp_path, rows=256, keep=list(range(128)))
    (ds / "nuslist").write_text(
        "\n".join(str(k) for k in range(128)) + "\n", encoding="utf-8"
    )
    exp = read_dataset(ds)
    assert exp.sampling.mode.value == "uniform"
    assert exp.sampling.schedule_type == "full_sampling"
    assert exp.sampling.sampling_fraction == pytest.approx(1.0)
    assert any("nuslist 覆盖全部 128" in line for line in exp.sampling.evidence)


def test_partial_nuslist_stays_nus(tmp_path: Path) -> None:
    """采样表只覆盖部分复点 → 仍是 NUS(SMILE 路径不变)。"""
    ds = _make_2d_nus_dataset(tmp_path, rows=256, keep=[0, 5, 37])
    (ds / "nuslist").write_text("0\n5\n37\n", encoding="utf-8")
    exp = read_dataset(ds)
    assert exp.sampling.mode.value == "nus"
    assert exp.sampling.schedule_type == "nuslist"
    assert not any("满采样" in line for line in exp.sampling.evidence)


@pytest.mark.parametrize(
    "coordinates",
    [list(range(127)) + [0], list(range(127)) + [999]],
    ids=["duplicate_missing", "out_of_range_missing"],
)
def test_malformed_full_length_nuslist_stays_nus(
    tmp_path: Path, coordinates: list[int]
) -> None:
    """行数达到网格但坐标重复/越界时不算覆盖全格，必须继续走 NUS。"""
    ds = _make_2d_nus_dataset(tmp_path, rows=256, keep=list(range(128)))
    (ds / "nuslist").write_text(
        "\n".join(str(value) for value in coordinates) + "\n", encoding="utf-8"
    )
    exp = read_dataset(ds)
    assert exp.sampling.mode.value == "nus"
    assert exp.sampling.schedule_type == "nuslist"
    assert not any("满采样" in line for line in exp.sampling.evidence)


def _finalize(raw: Path, work: Path, ndim: int) -> tuple[bool, list[str]]:
    from backend.nmrpipe_backend import NMRPipeBackend

    logs: list[str] = []
    ok = NMRPipeBackend(nmrpipe_bin="")._finalize_converted_fid(
        raw, work, "d_001", logs, ndim=ndim
    )
    return ok, logs


def test_finalize_2d_single_file_in_fid_dir(tmp_path: Path) -> None:
    """2D:bruker 把输出写进 fid/(名字带 %03d)也只是单平面 → 按单文件处理。"""
    raw = tmp_path / "raw"
    (raw / "fid").mkdir(parents=True)
    (raw / "fid" / "test%03d.fid").write_bytes(b"x" * 1024)
    work = tmp_path / "work"
    work.mkdir()

    ok, logs = _finalize(raw, work, 2)

    assert ok is True
    assert (work / "d_001.fid").is_file()
    assert not (raw / "fid" / "test%03d.fid").exists()
    assert any("单平面输出" in line for line in logs)


def test_finalize_3d_keeps_slice_stream(tmp_path: Path) -> None:
    """3D:真切片流(多文件)仍按切片目录归位,不改行为。"""
    raw = tmp_path / "raw"
    (raw / "fid").mkdir(parents=True)
    for index in (1, 2):
        (raw / "fid" / f"test{index:03d}.fid").write_bytes(b"x")
    work = tmp_path / "work"
    work.mkdir()

    ok, logs = _finalize(raw, work, 3)

    assert ok is True
    assert (work / "fid").is_dir()
    assert not (work / "d_001.fid").exists()
    assert any("切片式 fid" in line for line in logs)


def test_finalize_2d_multi_slice_falls_back_to_stream(tmp_path: Path) -> None:
    """2D 但 fid/ 里多于一个文件:保守回退到原切片流处理(不误吞)。"""
    raw = tmp_path / "raw"
    (raw / "fid").mkdir(parents=True)
    for index in (1, 2):
        (raw / "fid" / f"test{index:03d}.fid").write_bytes(b"x")
    work = tmp_path / "work"
    work.mkdir()

    ok, logs = _finalize(raw, work, 2)

    assert ok is True
    assert (work / "fid").is_dir()
    assert any("切片式 fid" in line for line in logs)

def test_recover_dense_2d_nus_float64(tmp_path: Path) -> None:
    """DTYPE=1(float64):按 8 字节/采样值算行数并读对(以前写死 int32 会误判行数)。"""
    keep = [0, 3, 40, 127]
    ds = _make_2d_nus_dataset(tmp_path, rows=256, keep=keep, dtype_code=1)

    points, logs = _recover(ds)

    assert points == keep
    assert any("f8" in line for line in logs)


def test_recover_dense_2d_nus_float32(tmp_path: Path) -> None:
    """DTYPE=2(float32):同样按元素字节数判定。"""
    keep = [1, 9, 64]
    ds = _make_2d_nus_dataset(tmp_path, rows=256, keep=keep, dtype_code=2)

    points, logs = _recover(ds)

    assert points == keep
    assert any("f4" in line for line in logs)


def test_recover_dense_2d_nus_unknown_dtype_refused(tmp_path: Path) -> None:
    """DTYPE 未知(9):不猜,判定失败 → 报缺采样表。"""
    ds = _make_2d_nus_dataset(tmp_path, rows=256, keep=[0], dtype_code=9)

    points, logs = _recover(ds)

    assert points is None
    assert any("DTYPE" in line for line in logs)

def test_smile_scan_runs_chosen_mode_once(tmp_path: Path, monkeypatch) -> None:
    """按排序口径选运行方式:净真峰→全采样跑;一致性→留出跑;每候选只跑一次。

    用户 2026-09-11:「应该按照需要的排序方法去选择全跑还是留出一部分,而不是跑两次」。
    """
    from backend import nmrpipe_backend as nb
    from backend.runtime import CompletedProcess

    exp = read_dataset(BRUKER / "nus_2d")

    def _install(monkeypatch, ran: list[str]) -> None:
        def _fake_reconstruct(self, experiment, params=None, **kwargs):
            params = dict(params or {})
            sample = str(params.get("nuslist_file") or "nuslist")
            return {
                "success": True,
                "message": "fake",
                "logs": [],
                "script": (
                    "#!/bin/csh\nmkdir -p nus2d\n"
                    "nmrPipe -in e.fid | nmrPipe -fn SMILE -nDim 2 \\\n"
                    f"  -sample {sample} -sampleCount 3 \\\n"
                    "| pipe2xyz -out nus2d/recon.ft1 -x -ov \\\n"
                    "  -out cand.ft2 -ov\n"
                ),
                "script_path": "",
                "work_dir": "",
            }

        class _FakeCsh:
            def run(self, argv, *, cwd=None, timeout=3600, on_line=None):
                script = (Path(cwd) / argv[-1]).read_text(encoding="utf-8")
                ran.append(script)
                out = None
                for line in script.splitlines():
                    if "-out " in line:
                        out = line.split("-out ", 1)[1].split()[0]
                if out:
                    (Path(cwd) / out).write_bytes(b"x")
                return CompletedProcess("", "", "", 0)

        monkeypatch.setattr(nb.NMRPipeBackend, "reconstruct_nus", _fake_reconstruct)
        monkeypatch.setattr(nb, "CshRuntime", lambda: _FakeCsh())

    # 净真峰口径(holdout_ratio=0)→ 全采样跑一次
    ran_full: list[str] = []
    _install(monkeypatch, ran_full)
    scan_full = nb.NMRPipeBackend(nmrpipe_bin="").smile_scan(
        exp, {}, [{"nsigma": 3.0, "thresh": 0.9}],
        work_dir=tmp_path / "scan_full", holdout_ratio=0.0,
    )
    smile_full = [s for s in ran_full if "-fn SMILE" in s]
    assert len(smile_full) == 1  # 每候选一次
    assert "nuslist_train" not in smile_full[0]

    # 一致性口径(holdout_ratio>0)→ 留出跑一次
    ran_ho: list[str] = []
    _install(monkeypatch, ran_ho)
    scan_ho = nb.NMRPipeBackend(nmrpipe_bin="").smile_scan(
        exp, {}, [{"nsigma": 3.0, "thresh": 0.9}],
        work_dir=tmp_path / "scan_ho", holdout_ratio=0.5,
    )
    smile_ho = [s for s in ran_ho if "-fn SMILE" in s]
    assert len(smile_ho) == 1  # 每候选一次
    assert "nuslist_train" in smile_ho[0]

    # 两种口径下,上榜脚本都是全采样(重跑用)
    for scan in (scan_full, scan_ho):
        assert "nuslist_train" not in scan["candidates"][0]["script"]
