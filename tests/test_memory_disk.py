"""Process/_intermediate Intermediate product subdirectory + memory disk takeover test
(0.2.199-patch29ez)."""

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
    """Patch29fk: The direct dimension is estimated according to the default EXT window (10.5-6.5)
    and the number of points (fixture without sw/sf, according to the default 8ppm spectral
    width is halved), no longer according to the full direct dimension SI virtual height."""
    _patch_plan(monkeypatch, {"F1": 512, "F2": 4096})
    assert memory_disk.estimate_intermediate_peak(_experiment(nus=True)) == 256 * 4096 * 8 * 2
    assert memory_disk.estimate_intermediate_peak(_experiment(nus=False)) == 256 * 4096 * 8 * 2


def test_estimate_intermediate_peak_ext_window_params(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Patch29fk: user final_ext (applied by default) and ext override change direct dimension
    estimation; disable application and return to default."""
    _patch_plan(monkeypatch, {"F1": 512, "F2": 4096})
    exp = _experiment(nus=True)
    # Narrow window 8.5-7.5(1ppm/8ppm -> 1/8):512 -> 64.
    assert memory_disk.estimate_intermediate_peak(
        exp, {"final_ext_lo": "8.5", "final_ext_hi": "7.5"}
    ) == 64 * 4096 * 8 * 2
    # Turn off "Apply this range to optimisation process": intermediates are still estimated
    # according to the default wide window (conservative).
    assert memory_disk.estimate_intermediate_peak(
        exp,
        {
            "final_ext_lo": "8.5",
            "final_ext_hi": "7.5",
            "apply_ext_to_opt": "0",
        },
    ) == 256 * 4096 * 8 * 2
    # Explicit ext_lo/ext_hi takes precedence.
    assert memory_disk.estimate_intermediate_peak(
        exp, {"ext_lo": "8.5", "ext_hi": "7.5"}
    ) == 64 * 4096 * 8 * 2


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

    _patch_plan(monkeypatch, {"F1": 1024, "F2": 8192})  # Peak 128MB(x 2).
    monkeypatch.setattr(
        memory_disk, "system_available_bytes", lambda: 1024 * 1024 * 1024
    )
    mem = memory_disk.select_memory_dir(_experiment(), _big_memory_cfg(tmp_path))
    assert mem is not None and mem.is_dir()
    assert mem.parent == tmp_path

    # Insufficient system memory -> rollback.
    monkeypatch.setattr(memory_disk, "system_available_bytes", lambda: 1)
    assert memory_disk.select_memory_dir(_experiment(), _big_memory_cfg(tmp_path)) is None

    # Insufficient memory disk remaining -> rollback.
    monkeypatch.setattr(
        memory_disk, "system_available_bytes", lambda: 1024 * 1024 * 1024
    )
    monkeypatch.setattr(
        memory_disk.shutil, "disk_usage", lambda p: SimpleNamespace(free=1)
    )
    assert memory_disk.select_memory_dir(_experiment(), _big_memory_cfg(tmp_path)) is None


def test_selection_reason_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch29fk-fix: selection_reason gives the reason for the rollback; it is empty when the
    conditions are met."""
    cfg_off = {"processing": {"intermediate_memory": "off"}}
    assert "off" in memory_disk.selection_reason(_experiment(), cfg_off)
    _patch_plan(monkeypatch, {"F1": 1024, "F2": 8192})
    monkeypatch.setattr(
        memory_disk, "system_available_bytes", lambda: 1024 * 1024 * 1024
    )
    monkeypatch.setattr(
        memory_disk.shutil,
        "disk_usage",
        lambda p: SimpleNamespace(free=1024 * 1024 * 1024),
    )
    assert memory_disk.selection_reason(
        _experiment(), _big_memory_cfg(tmp_path)
    ) == ""


def test_prepare_teardown_disk_mode(tmp_path: Path) -> None:
    """Strategy off: work/_intermediate is the real directory, teardown deletes the entire
    directory."""
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
    """Sufficient memory: work/_intermediate symbolic link to the memory directory and teardown are
    both removed. Windows will automatically fall back to the real directory when there is no
    ordinary user symbolic link permission (still safe)."""
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
        # Windows unsymbolic link permissions -> roll back to real directory (memory directory has
        # been recycled).
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
    """Disk mode: work_dir Keep process directory, intermediate products in _intermediate, end
    cleanup."""
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
        assert backend.work_dir == str(work)  # Original logic: work_dir is still process directory.
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


@pytest.mark.skipif(os.name == "nt", reason=
    "Symbolic links require POSIX (ramdisk is a Linux feature)")
def test_generate_spectrum_intermediate_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Memory mode: work/_intermediate points to the memory directory and ends the entire tree
    deletion."""
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

    def fake_prepare(work_dir, experiment, config=None, params=None):
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
