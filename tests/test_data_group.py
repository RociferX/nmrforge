"""数据组(schema 1.4)测试:模型序列化、ProjectManager 组方法、run_batch 组解析与参考数据参数复用。"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.project import DataGroupEntry, ProjectError, ProjectManager
from workflow.batch import BatchError, run_batch


def _manager_with_data(
    tmp_path: Path, n: int = 3, source: str | None = None
) -> tuple[ProjectManager, str, list[str]]:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment(title="batch")
    data_ids = [
        manager.import_data(entry.id, source or f"/fake/{i}").id
        for i in range(n)
    ]
    manager.save()
    return manager, entry.id, data_ids


def test_group_model_roundtrip(tmp_path: Path) -> None:
    """DataGroupEntry 序列化往返 + ExperimentEntry.groups 落盘(schema 1.4)。"""
    manager, exp_id, data_ids = _manager_with_data(tmp_path)
    group = manager.create_data_group(
        exp_id, title="HSQC 组", data_ids=data_ids[:2]
    )
    assert group.id == "G1"
    assert group.title == "HSQC 组"
    manager.save()

    reopened = ProjectManager.open_project(tmp_path / "proj")
    assert reopened.project.schema_version == "1.4"
    entry = reopened.project.experiment(exp_id)
    assert entry is not None
    assert len(entry.groups) == 1
    restored = entry.groups[0]
    assert restored.id == "G1"
    assert restored.title == "HSQC 组"
    assert restored.data_ids == data_ids[:2]
    assert DataGroupEntry.from_dict(restored.to_dict()) == restored


def test_create_group_validates_members(tmp_path: Path) -> None:
    """create_data_group 校验成员必须属于该实验;编号含历史不复用。"""
    manager, exp_id, data_ids = _manager_with_data(tmp_path)
    with pytest.raises(Exception):
        manager.create_data_group(exp_id, data_ids=["d_999"])
    group = manager.create_data_group(exp_id, data_ids=data_ids[:1])
    assert group.id == "G1"
    manager.delete_data_group(exp_id, "G1")
    # 删除组后编号不复用 → G2(审计历史含 G1)
    group2 = manager.create_data_group(exp_id)
    assert group2.id == "G2"


def test_group_membership_operations(tmp_path: Path) -> None:
    """add/remove/group_of_data/delete 组操作。"""
    manager, exp_id, data_ids = _manager_with_data(tmp_path)
    group = manager.create_data_group(exp_id, data_ids=data_ids[:1])
    # add
    manager.add_to_group(exp_id, group.id, data_ids[1])
    assert manager.group_data_ids(exp_id, group.id) == data_ids[:2]
    assert manager.group_of_data(exp_id, data_ids[1]).id == group.id
    # add 幂等
    manager.add_to_group(exp_id, group.id, data_ids[1])
    assert len(manager.group_data_ids(exp_id, group.id)) == 2
    # remove
    manager.remove_from_group(exp_id, group.id, data_ids[1])
    assert manager.group_data_ids(exp_id, group.id) == data_ids[:1]
    assert manager.group_of_data(exp_id, data_ids[1]) is None
    # 非法成员
    with pytest.raises(Exception):
        manager.add_to_group(exp_id, group.id, "d_999")
    # delete 组(数据保留)
    manager.delete_data_group(exp_id, group.id)
    assert manager.group(exp_id, group.id) is None
    assert len(manager.data_groups(exp_id)) == 0
    assert len(manager.project.experiment(exp_id).data) == 3


def test_delete_data_keeps_group_membership_for_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-补29ex:删除数据保留组引用(软删除),恢复后无损回组。"""
    import shutil

    manager, exp_id, data_ids = _manager_with_data(tmp_path)
    manager.create_data_group(exp_id, data_ids=data_ids)
    trash = tmp_path / "trash"
    trash.mkdir()

    def fake(path, fallback_dir, rel=None):
        dest = trash / (rel or Path(path).name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(dest))
        return dest

    monkeypatch.setattr("core.project.manager.send_to_trash", fake)
    manager.delete_data(exp_id, data_ids[0])
    manager.save()
    group = manager.group(exp_id, "G1")
    assert group is not None
    assert group.data_ids == data_ids  # 组引用保留,待恢复
    with pytest.raises(ProjectError):
        manager.data(exp_id, data_ids[0])
    # 恢复后再次可访问
    (manager.data_base(exp_id, data_ids[0]) / "raw").mkdir(
        parents=True
    )
    manager.recover_trashed()
    assert manager.data(exp_id, data_ids[0]).id == data_ids[0]


class _FakeBackend:
    """记录调用的假后端;spectrum 记录 params 供参考数据复用断言。"""

    def __init__(self, work_dir: Path) -> None:
        self.work_dir = str(work_dir)
        self.calls: list[tuple[str, str]] = []
        self.spectrum_params: dict[str, dict] = {}

    def _touch(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")

    def convert_to_fid(self, experiment, data_dir, progress=None) -> dict:
        self.calls.append(("convert_to_fid", experiment.dataset_id))
        fid_path = Path(self.work_dir) / f"{experiment.dataset_id}.fid"
        self._touch(fid_path)
        return {
            "success": True,
            "fid_path": str(fid_path),
            "message": "ok",
            "logs": [],
        }

    def process(
        self,
        experiment,
        plan,
        direct_phase_override=None,
        params=None,
        progress=None,
    ) -> dict:
        self.calls.append(("process", experiment.dataset_id))
        self.spectrum_params[experiment.dataset_id] = dict(params or {})
        spectrum = Path(self.work_dir) / f"{experiment.dataset_id}.ft2"
        self._touch(spectrum)
        return {
            "success": True,
            "spectrum_path": str(spectrum),
            "message": "ok",
            "logs": [],
        }

    def reconstruct_nus(self, experiment, params, progress=None) -> dict:
        self.calls.append(("reconstruct_nus", experiment.dataset_id))
        self.spectrum_params[experiment.dataset_id] = dict(params or {})
        spectrum = Path(self.work_dir) / f"{experiment.dataset_id}.ft2"
        self._touch(spectrum)
        return {
            "success": True,
            "spectrum_path": str(spectrum),
            "message": "ok",
            "logs": [],
        }


def test_run_batch_resolves_project_group(tmp_path: Path) -> None:
    """run_batch 按 project.json 数据组(G1)解析成员。"""
    manager, exp_id, data_ids = _manager_with_data(tmp_path)
    manager.create_data_group(exp_id, data_ids=data_ids[:2])
    backend = _FakeBackend(tmp_path / "work")
    result = run_batch(manager, exp_id, "G1", ["fid"], backend)
    assert result["batch_id"] == "G1"
    assert result["data_ids"] == data_ids[:2]
    assert set(result["results"]) == set(data_ids[:2])
    # 旧 pipeline_state B 前缀兼容解析仍可用
    import json

    path = manager.data_base(exp_id, data_ids[2]) / ".pipeline_state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": 1, "steps": {}, "batch": "B1"}),
        encoding="utf-8",
    )
    result_b = run_batch(manager, exp_id, "B1", ["fid"], backend)
    assert result_b["data_ids"] == [data_ids[2]]


def test_run_batch_empty_group_raises(tmp_path: Path) -> None:
    """空数据组目标抛 BatchError。"""
    manager, exp_id, _data_ids = _manager_with_data(tmp_path)
    manager.create_data_group(exp_id)
    backend = _FakeBackend(tmp_path / "work")
    with pytest.raises(BatchError):
        run_batch(manager, exp_id, "G1", ["fid"], backend)


def test_run_batch_reference_data_params(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """reference_data_id:spectrum 步骤复用参考数据最近成功运行的有效参数。"""
    manager, exp_id, data_ids = _manager_with_data(
        tmp_path, n=2, source=str(bruker_dir / "hsqc_2d")
    )
    manager.create_data_group(exp_id, data_ids=data_ids[:2])
    backend = _FakeBackend(tmp_path / "work")

    # 先让参考数据(data_ids[0])跑一次 spectrum,登记带 params 的 WorkflowRun
    from workflow.stepwise import generate_spectrum

    generate_spectrum(
        manager,
        exp_id,
        data_ids[0],
        backend,
        params={"phase_route": "none", "baseline": "poly"},
    )
    # 参考参数在 WorkflowRun.params(phase_route 被消费,保留 baseline)
    ref_run = next(
        r
        for r in manager.project.workflow_runs
        if (r.inputs or {}).get("data_id") == data_ids[0]
        and r.workflow_ref == "process"
    )
    assert ref_run.params.get("baseline") == "poly"

    result = run_batch(
        manager,
        exp_id,
        "G1",
        ["fid", "spectrum"],
        backend,
        reference_data_id=data_ids[0],
        params={"phase_route": "none"},
    )
    assert result["summary"] == {"total": 2, "success": 2, "failed": 0}
    # 组内第二个数据 spectrum 使用参考数据的 baseline 参数
    assert backend.spectrum_params[data_ids[1]].get("baseline") == "poly"


def test_run_batch_explicit_params_override_reference(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """显式 params 覆盖参考参数(参考参数为基底)。"""
    manager, exp_id, data_ids = _manager_with_data(
        tmp_path, n=2, source=str(bruker_dir / "hsqc_2d")
    )
    manager.create_data_group(exp_id, data_ids=data_ids[:2])
    backend = _FakeBackend(tmp_path / "work")

    from workflow.stepwise import generate_spectrum

    generate_spectrum(
        manager,
        exp_id,
        data_ids[0],
        backend,
        params={"phase_route": "none", "baseline": "poly"},
    )
    result = run_batch(
        manager,
        exp_id,
        "G1",
        ["fid", "spectrum"],
        backend,
        reference_data_id=data_ids[0],
        params={"phase_route": "none", "baseline": "manual"},
    )
    assert result["summary"]["failed"] == 0
    assert backend.spectrum_params[data_ids[1]].get("baseline") == "manual"
