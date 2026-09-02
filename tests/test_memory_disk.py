"""中间谱内存盘自适应放置测试(0.2.199-补29ey)。"""

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


def test_estimate_intermediate_peak(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_plan(monkeypatch, {"F1": 512, "F2": 4096})
    assert memory_disk.estimate_intermediate_peak(_experiment(nus=True)) == 512 * 4096 * 8 * 3
    assert memory_disk.estimate_intermediate_peak(_experiment(nus=False)) == 512 * 4096 * 8 * 2


def test_select_work_root_policy_and_conditions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_off = {
        "processing": {
            "intermediate_memory": "off",
            "memory_disk_path": str(tmp_path),
        }
    }
    assert memory_disk.select_work_root(_experiment(), cfg_off) is None

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
    root = memory_disk.select_work_root(_experiment(), cfg)
    assert root is not None and root.is_dir()
    assert root.parent == tmp_path
    assert root.name.startswith("nmrforge-mem-")

    # 系统内存不足 → 回退磁盘
    monkeypatch.setattr(memory_disk, "system_available_bytes", lambda: 1)
    assert memory_disk.select_work_root(_experiment(), cfg) is None

    # 内存盘剩余不足 → 回退磁盘
    monkeypatch.setattr(
        memory_disk, "system_available_bytes", lambda: 1024 * 1024 * 1024
    )
    monkeypatch.setattr(
        memory_disk.shutil, "disk_usage", lambda p: SimpleNamespace(free=1)
    )
    assert memory_disk.select_work_root(_experiment(), cfg) is None


def test_generate_spectrum_memory_disk_used_and_cleaned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """生成谱图:内存余量充足时工作目录用内存盘,完成后整目录删除。"""
    import workflow.stepwise as stepwise

    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment()
    data = manager.import_data(entry.id, "/sampleD")
    mem = tmp_path / "memwork"
    mem.mkdir()

    monkeypatch.setattr(
        stepwise,
        "_read_experiment",
        lambda mgr, e, d: SimpleNamespace(
            dataset_id=d, dimensions=[], sampling=SimpleNamespace(mode="uniform")
        ),
    )
    monkeypatch.setattr(
        stepwise.memory_disk,
        "select_work_root",
        lambda experiment, config=None: mem,
    )
    calls: dict[str, object] = {}

    def fake_impl(manager, exp_id, data_id, backend, *, work, params=None, progress=None):
        calls["work"] = work
        assert backend.work_dir == str(mem)
        return "ok"

    monkeypatch.setattr(stepwise, "_generate_spectrum_impl", fake_impl)
    backend = SimpleNamespace(work_dir="")
    result = stepwise.generate_spectrum(
        manager, entry.id, data.id, backend, params={"phase_route": "none"}
    )
    assert result == "ok"
    assert calls["work"] == mem
    assert not mem.exists()  # 内存盘工作目录已删除


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
        "select_work_root",
        lambda experiment, config=None: None,
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
