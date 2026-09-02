"""中间谱内存盘自适应放置测试(0.2.199-补29ey/补29ey-修)。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from backend import memory_disk
from core.project import ProjectManager


def _experiment(
    ndim: int = 2,
    nus: bool = False,
    f1: int = 512,
    f2: int = 4096,
):
    dims = [
        SimpleNamespace(logical_axis="F1", ft_size=f1, td=f1 // 2),
        SimpleNamespace(logical_axis="F2", ft_size=f2, td=f2 // 2),
    ]
    if ndim == 3:
        dims.append(SimpleNamespace(logical_axis="F3", ft_size=128, td=64))
    return SimpleNamespace(
        dimensions=dims,
        sampling=SimpleNamespace(mode="nus" if nus else "uniform"),
    )


def _patch_plan(monkeypatch: pytest.MonkeyPatch, sizes: dict[str, int]) -> None:
    def fake_plan(experiment, *args, **kwargs):
        axes = [d.logical_axis for d in experiment.dimensions]
        return {axis: {"mode": "auto", "size": sizes[axis]} for axis in axes}

    def fake_td(experiment):
        return tuple(d.td for d in experiment.dimensions)

    monkeypatch.setattr("backend.script_generator.zero_fill_plan", fake_plan)
    monkeypatch.setattr("backend.script_generator.effective_td", fake_td)


def _make_persistent(directory: Path) -> None:
    (directory / "fid").mkdir(parents=True, exist_ok=True)
    (directory / "fid" / "d_001.fid").write_bytes(b"fid")
    (directory / "fid.com").write_text("fid script", encoding="utf-8")
    (directory / "nuslist").write_text("1 1", encoding="utf-8")
    (directory / "phase.json").write_text("{}", encoding="utf-8")


def test_estimate_intermediate_peak(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_plan(monkeypatch, {"F1": 512, "F2": 4096})
    assert memory_disk.estimate_intermediate_peak(_experiment(nus=True)) == 512 * 4096 * 8 * 3
    assert memory_disk.estimate_intermediate_peak(_experiment(nus=False)) == 512 * 4096 * 8 * 2


def test_persistent_usage_and_roundtrip(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    _make_persistent(src)
    (src / "preview.ft2").write_bytes(b"x" * 1000)  # 非持久中间谱不计入
    usage = memory_disk.persistent_usage(src)
    assert usage == len(b"fid") + len(b"fid script") + len(b"1 1") + 2

    dst = tmp_path / "dst"
    dst.mkdir()
    memory_disk.seed_persistent(src, dst)
    assert (dst / "fid" / "d_001.fid").read_bytes() == b"fid"
    assert (dst / "fid.com").read_text(encoding="utf-8") == "fid script"
    assert (dst / "nuslist").read_text(encoding="utf-8") == "1 1"
    # 运行后同步回磁盘:内存中更新的 phase.json 覆盖磁盘
    (dst / "phase.json").write_text("{\"v\": 2}", encoding="utf-8")
    memory_disk.sync_persistent(dst, src)
    assert (src / "phase.json").read_text(encoding="utf-8") == "{\"v\": 2}"
    assert (src / "fid" / "d_001.fid").read_bytes() == b"fid"


def test_memory_work_dir_policy_and_conditions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    persistent = tmp_path / "persist"
    persistent.mkdir()
    cfg_off = {
        "processing": {
            "intermediate_memory": "off",
            "memory_disk_path": str(tmp_path),
        }
    }
    assert memory_disk.memory_work_dir(persistent, _experiment(), cfg_off) is None

    cfg = {
        "processing": {
            "intermediate_memory": "auto",
            "memory_disk_path": str(tmp_path),
        }
    }
    _patch_plan(monkeypatch, {"F1": 1024, "F2": 8192})  # 峰值 128MB(×2)
    monkeypatch.setattr(
        memory_disk, "system_available_bytes", lambda: 1024 * 1024 * 1024
    )
    root = memory_disk.memory_work_dir(persistent, _experiment(), cfg)
    assert root is not None
    assert root.parent == tmp_path
    assert root.name.startswith("nmrforge-")

    # 系统内存不足 → 回退磁盘
    monkeypatch.setattr(memory_disk, "system_available_bytes", lambda: 1)
    assert memory_disk.memory_work_dir(persistent, _experiment(), cfg) is None

    # 内存盘剩余不足 → 回退磁盘
    monkeypatch.setattr(
        memory_disk, "system_available_bytes", lambda: 1024 * 1024 * 1024
    )
    monkeypatch.setattr(
        memory_disk.shutil, "disk_usage", lambda p: SimpleNamespace(free=1)
    )
    assert memory_disk.memory_work_dir(persistent, _experiment(), cfg) is None


def test_generate_spectrum_memory_disk_used_and_cleaned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """生成谱图:内存余量充足时工作目录用内存盘,结束后整目录删除。"""
    import workflow.stepwise as stepwise

    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment()
    data = manager.import_data(entry.id, "/sampleD")
    persistent = manager.data_dir(entry.id, data.id, "process")
    persistent.mkdir(parents=True, exist_ok=True)
    _make_persistent(persistent)
    mem = tmp_path / "memwork"

    monkeypatch.setattr(
        stepwise,
        "_read_experiment",
        lambda mgr, e, d: SimpleNamespace(
            dataset_id=d, dimensions=[], sampling=SimpleNamespace(mode="uniform")
        ),
    )
    monkeypatch.setattr(
        stepwise.memory_disk,
        "memory_work_dir",
        lambda persistent_dir, experiment, config=None: mem,
    )
    calls: dict[str, object] = {}

    def fake_impl(manager, exp_id, data_id, backend, *, work, params=None, progress=None):
        calls["work"] = work
        assert backend.work_dir == str(mem)
        # 预置的持久产物在内存工作目录里
        assert (mem / "fid" / "d_001.fid").read_bytes() == b"fid"
        # 模拟运行更新 phase.json
        (mem / "phase.json").write_text("{\"v\": 2}", encoding="utf-8")
        return "ok"

    monkeypatch.setattr(stepwise, "_generate_spectrum_impl", fake_impl)
    backend = SimpleNamespace(work_dir="")
    result = stepwise.generate_spectrum(
        manager, entry.id, data.id, backend, params={"phase_route": "none"}
    )
    assert result == "ok"
    assert calls["work"] == mem
    assert not mem.exists()  # 内存盘工作目录已删除
    # 持久产物已同步回磁盘(phase.json 更新, fid 保留)
    assert (persistent / "phase.json").read_text(encoding="utf-8") == "{\"v\": 2}"
    assert (persistent / "fid" / "d_001.fid").read_bytes() == b"fid"


def test_generate_spectrum_fallback_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """内存盘不可用(或未启用)时工作目录保持数据 process 目录。"""
    import workflow.stepwise as stepwise

    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment()
    data = manager.import_data(entry.id, "/sampleD")

    monkeypatch.setattr(
        stepwise,
        "_read_experiment",
        lambda mgr, e, d: SimpleNamespace(
            dataset_id=d, dimensions=[], sampling=SimpleNamespace(mode="uniform")
        ),
    )
    monkeypatch.setattr(
        stepwise.memory_disk,
        "memory_work_dir",
        lambda persistent_dir, experiment, config=None: None,
    )
    calls: dict[str, object] = {}

    def fake_impl(manager, exp_id, data_id, backend, *, work, params=None, progress=None):
        calls["work"] = work
        return "ok"

    monkeypatch.setattr(stepwise, "_generate_spectrum_impl", fake_impl)
    backend = SimpleNamespace(work_dir="")
    stepwise.generate_spectrum(
        manager, entry.id, data.id, backend, params={"phase_route": "none"}
    )
    expected = manager.data_dir(entry.id, data.id, "process")
    assert calls["work"] == expected
