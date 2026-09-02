"""process/_intermediate 中间产物子目录 + 内存盘接管测试(0.2.199-补29ez)。"""

from __future__ import annotations

import os
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


def _big_memory_cfg(tmp_path: Path) -> dict:
    return {
        "processing": {
            "intermediate_memory": "auto",
            "memory_disk_path": str(tmp_path),
        }
    }


def test_estimate_intermediate_peak(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_plan(monkeypatch, {"F1": 512, "F2": 4096})
    assert memory_disk.estimate_intermediate_peak(_experiment(nus=True)) == 512 * 4096 * 8 * 3
    assert memory_disk.estimate_intermediate_peak(_experiment(nus=False)) == 512 * 4096 * 8 * 2


def test_select_memory_dir_policy_and_conditions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_off = {
        "processing": {
            "intermediate_memory": "off",
            "memory_disk_path": str(tmp_path),
        }
    }
    assert memory_disk.select_memory_dir(_experiment(), cfg_off) is None

    _patch_plan(monkeypatch, {"F1": 1024, "F2": 8192})  # 峰值 128MB(×2)
    monkeypatch.setattr(
        memory_disk, "system_available_bytes", lambda: 1024 * 1024 * 1024
    )
    mem = memory_disk.select_memory_dir(_experiment(), _big_memory_cfg(tmp_path))
    assert mem is not None and mem.is_dir()
    assert mem.parent == tmp_path

    # 系统内存不足 → 回退
    monkeypatch.setattr(memory_disk, "system_available_bytes", lambda: 1)
    assert memory_disk.select_memory_dir(_experiment(), _big_memory_cfg(tmp_path)) is None

    # 内存盘剩余不足 → 回退
    monkeypatch.setattr(
        memory_disk, "system_available_bytes", lambda: 1024 * 1024 * 1024
    )
    monkeypatch.setattr(
        memory_disk.shutil, "disk_usage", lambda p: SimpleNamespace(free=1)
    )
    assert memory_disk.select_memory_dir(_experiment(), _big_memory_cfg(tmp_path)) is None


def test_prepare_teardown_disk_mode(tmp_path: Path) -> None:
    """策略 off:work/_intermediate 为真实目录,teardown 整目录删除。"""
    work = tmp_path / "process"
    cfg_off = {"processing": {"intermediate_memory": "off"}}
    root, mem = memory_disk.prepare_intermediate(work, _experiment(), cfg_off)
    assert mem is None
    assert root == work / memory_disk.INTERMEDIATE_SUBDIR
    assert root.is_dir() and not root.is_symlink()
    (root / "preview.ft2").write_bytes(b"x")
    memory_disk.teardown_intermediate(work, mem)
    assert not root.exists()


def test_prepare_teardown_memory_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """内存充足:work/_intermediate 符号链接到内存目录,teardown 双双移除。

    Windows 无普通用户符号链接权限时自动回退真实目录(仍安全)。
    """
    work = tmp_path / "process"
    work.mkdir(parents=True, exist_ok=True)
    _patch_plan(monkeypatch, {"F1": 1024, "F2": 8192})
    monkeypatch.setattr(
        memory_disk, "system_available_bytes", lambda: 1024 * 1024 * 1024
    )
    root, mem = memory_disk.prepare_intermediate(
        work, _experiment(), _big_memory_cfg(tmp_path)
    )
    if os.name == "nt" and not root.is_symlink():
        # Windows 无符号链接权限 → 回退真实目录(内存目录已回收)
        assert mem is None
        assert root.is_dir()
        memory_disk.teardown_intermediate(work, None)
        assert not root.exists()
        return
    assert root.is_symlink()
    assert mem is not None and mem.is_dir()
    assert root.resolve() == mem
    (mem / "preview.ft2").write_bytes(b"x")
    memory_disk.teardown_intermediate(work, mem)
    assert not root.exists()
    assert not mem.exists()


def test_generate_spectrum_intermediate_subdir_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """磁盘模式:work_dir 保持 process 目录,中间产物在 _intermediate,结束清理。"""
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
    calls: dict[str, object] = {}

    def fake_impl(manager, exp_id, data_id, backend, *, work, params=None, progress=None):
        calls["work"] = work
        assert backend.work_dir == str(work)  # 原逻辑:work_dir 仍是 process 目录
        inter = work / memory_disk.INTERMEDIATE_SUBDIR
        assert inter.is_dir()
        (inter / "preview.ft2").write_bytes(b"x")
        return "ok"

    monkeypatch.setattr(stepwise, "_generate_spectrum_impl", fake_impl)
    backend = SimpleNamespace(work_dir="")
    stepwise.generate_spectrum(
        manager, entry.id, data.id, backend, params={"phase_route": "none"}
    )
    expected = manager.data_dir(entry.id, data.id, "process")
    assert calls["work"] == expected
    assert not (expected / memory_disk.INTERMEDIATE_SUBDIR).exists()


@pytest.mark.skipif(os.name == "nt", reason="符号链接需 POSIX(内存盘为 Linux 特性)")
def test_generate_spectrum_intermediate_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """内存模式:work/_intermediate 指向内存目录,结束整树删除。"""
    import workflow.stepwise as stepwise

    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment()
    data = manager.import_data(entry.id, "/sampleD")
    work = manager.data_dir(entry.id, data.id, "process")
    work.mkdir(parents=True, exist_ok=True)
    mem = tmp_path / "mem"
    mem.mkdir()

    monkeypatch.setattr(
        stepwise,
        "_read_experiment",
        lambda mgr, e, d: SimpleNamespace(
            dataset_id=d, dimensions=[], sampling=SimpleNamespace(mode="uniform")
        ),
    )

    def fake_prepare(work_dir, experiment, config=None):
        root = work_dir / memory_disk.INTERMEDIATE_SUBDIR
        root.symlink_to(mem, target_is_directory=True)
        return root, mem

    monkeypatch.setattr(stepwise.memory_disk, "prepare_intermediate", fake_prepare)

    def fake_impl(manager, exp_id, data_id, backend, *, work, params=None, progress=None):
        assert (work / memory_disk.INTERMEDIATE_SUBDIR).is_symlink()
        (mem / "joint.ft3").write_bytes(b"x")
        return "ok"

    monkeypatch.setattr(stepwise, "_generate_spectrum_impl", fake_impl)
    backend = SimpleNamespace(work_dir="")
    stepwise.generate_spectrum(
        manager, entry.id, data.id, backend, params={"phase_route": "none"}
    )
    assert not (work / memory_disk.INTERMEDIATE_SUBDIR).exists()
    assert not mem.exists()
