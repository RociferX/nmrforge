"""批量处理引擎测试:多数据批量执行、单数据失败继续、WorkflowRun 登记、stepwise 复用。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.project import ProjectManager
from workflow.batch import BatchError, run_batch


class _FakeBackend:
    """记录调用的假后端;convert_to_fid 可配置第 N 次失败。"""

    def __init__(self, work_dir: Path, *, fail_fid_on: int = -1) -> None:
        self.work_dir = str(work_dir)
        self.fail_fid_on = fail_fid_on
        self.calls: list[tuple[str, str]] = []  # (method, dataset_id)
        self.fid_calls = 0

    def _touch(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")

    def convert_to_fid(self, experiment, data_dir, progress=None) -> dict:
        self.fid_calls += 1
        self.calls.append(("convert_to_fid", experiment.dataset_id))
        if self.fid_calls == self.fail_fid_on:
            return {"success": False, "message": "转换失败", "logs": []}
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
        spectrum = Path(self.work_dir) / f"{experiment.dataset_id}.ft2"
        self._touch(spectrum)
        return {
            "success": True,
            "spectrum_path": str(spectrum),
            "message": "ok",
            "logs": [],
        }


def _manager_with_data(
    tmp_path: Path, source: Path, n: int = 2
) -> tuple[ProjectManager, str, list[str]]:
    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment(title="batch")
    data_ids = [
        manager.import_data(entry.id, str(source)).id for _ in range(n)
    ]
    manager.save()
    return manager, entry.id, data_ids


def _set_batch(manager, exp_id: str, data_ids: list[str], batch: str) -> None:
    """写 GUI 侧 .pipeline_state.json 的 batch 键(模拟 GUI 批量组标记)。"""
    for data_id in data_ids:
        path = manager.data_base(exp_id, data_id) / ".pipeline_state.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        state = {"version": 1, "steps": {}, "batch": batch}
        path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def _runs_for(manager, data_id: str, refs: set[str]) -> list:
    return [
        r
        for r in manager.project.workflow_runs
        if (r.inputs or {}).get("data_id") == data_id and r.workflow_ref in refs
    ]


def test_run_batch_multiple_data(tmp_path: Path, bruker_dir: Path) -> None:
    """多数据批量:逐数据依次执行 fid→spectrum,全部成功并登记 WorkflowRun。"""
    manager, exp_id, data_ids = _manager_with_data(
        tmp_path, bruker_dir / "hsqc_2d", n=2
    )
    backend = _FakeBackend(tmp_path / "work")
    events: list[str] = []
    result = run_batch(
        manager,
        exp_id,
        data_ids,
        ["fid", "spectrum"],
        backend,
        params={"phase_route": "none"},
        progress=events.append,
    )
    assert result["summary"] == {"total": 2, "success": 2, "failed": 0}
    assert result["failed"] == []
    # 进度回调顺序:每数据前输出 x/y,再逐步骤消息
    assert events[0].startswith(f"[1/2] 开始处理数据 {data_ids[0]}")
    assert events[1].startswith(f"{data_ids[0]}: 开始 fid")
    assert events[2].startswith(f"{data_ids[0]}: 开始 spectrum")
    assert events[-1] == f"{data_ids[1]}: 成功"
    # 逐数据执行:convert → process → convert → process
    assert [m for m, _ in backend.calls] == [
        "convert_to_fid",
        "process",
        "convert_to_fid",
        "process",
    ]
    for data_id in data_ids:
        per = result["results"][data_id]
        assert per["status"] == "success"
        fid = per["steps"]["fid"]
        spectrum = per["steps"]["spectrum"]
        assert fid.endswith(".fid")
        assert spectrum.endswith(".ft2")
        # 契约 §9.2:终谱落盘 data_dir(..., "spectra")
        assert Path(spectrum).parent == manager.data_dir(
            exp_id, data_id, "spectra"
        )
        refs = {
            r.workflow_ref
            for r in _runs_for(
                manager, data_id, {"convert_to_fid", "process"}
            )
        }
        assert refs == {"convert_to_fid", "process"}


def test_run_batch_single_failure_continues(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """单数据失败不中断整组:第二个数据转换失败,第一个仍完整成功。"""
    manager, exp_id, data_ids = _manager_with_data(
        tmp_path, bruker_dir / "hsqc_2d", n=2
    )
    backend = _FakeBackend(tmp_path / "work", fail_fid_on=2)
    result = run_batch(
        manager,
        exp_id,
        data_ids,
        ["fid", "spectrum"],
        backend,
        params={"phase_route": "none"},
    )
    assert result["summary"] == {"total": 2, "success": 1, "failed": 1}
    assert result["failed"] == [data_ids[1]]
    assert result["results"][data_ids[0]]["status"] == "success"
    failed = result["results"][data_ids[1]]
    assert failed["status"] == "failed"
    assert failed["failed_step"] == "fid"
    assert "转换失败" in failed["error"]
    # 第一个数据走完 fid+spectrum,第二个停在 fid
    assert [m for m, _ in backend.calls] == [
        "convert_to_fid",
        "process",
        "convert_to_fid",
    ]
    # 失败数据不登记 spectrum run,成功数据两步都登记
    assert _runs_for(manager, data_ids[1], {"process"}) == []
    assert (
        len(
            _runs_for(
                manager, data_ids[0], {"convert_to_fid", "process"}
            )
        )
        == 2
    )


def test_run_batch_resolves_batch_id(tmp_path: Path, bruker_dir: Path) -> None:
    """batch_id 解析:按 .pipeline_state.json 的 batch 键取组内数据。"""
    manager, exp_id, data_ids = _manager_with_data(
        tmp_path, bruker_dir / "hsqc_2d", n=3
    )
    _set_batch(manager, exp_id, data_ids[:2], "B1")
    _set_batch(manager, exp_id, [data_ids[2]], "B2")
    backend = _FakeBackend(tmp_path / "work")
    result = run_batch(manager, exp_id, "B1", ["fid"], backend)
    assert result["batch_id"] == "B1"
    assert result["data_ids"] == data_ids[:2]
    assert set(result["results"]) == set(data_ids[:2])


def test_run_batch_reuses_stepwise(
    tmp_path: Path, bruker_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """fid/spectrum 复用 workflow.stepwise(monkeypatch 验证调用透传)。"""
    import workflow.stepwise as stepwise_mod

    manager, exp_id, data_ids = _manager_with_data(
        tmp_path, bruker_dir / "hsqc_2d", n=1
    )
    calls: list[tuple] = []
    original_fid = stepwise_mod.generate_fid
    original_spectrum = stepwise_mod.generate_spectrum

    def fake_fid(manager_, exp_id_, data_id_, backend_, **kw):
        calls.append(("fid", data_id_))
        return original_fid(manager_, exp_id_, data_id_, backend_, **kw)

    def fake_spectrum(manager_, exp_id_, data_id_, backend_, **kw):
        calls.append(("spectrum", data_id_, kw.get("params")))
        return original_spectrum(manager_, exp_id_, data_id_, backend_, **kw)

    monkeypatch.setattr(stepwise_mod, "generate_fid", fake_fid)
    monkeypatch.setattr(stepwise_mod, "generate_spectrum", fake_spectrum)
    backend = _FakeBackend(tmp_path / "work")
    result = run_batch(
        manager,
        exp_id,
        data_ids,
        ["fid", "spectrum"],
        backend,
        params={"phase_route": "none", "extract": False},
    )
    assert calls == [
        ("fid", data_ids[0]),
        ("spectrum", data_ids[0], {"phase_route": "none", "extract": False}),
    ]
    assert result["summary"]["success"] == 1


def test_run_batch_import_step_idempotent(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """import 步骤幂等:不重复创建数据条目,返回已导入状态。"""
    manager, exp_id, data_ids = _manager_with_data(
        tmp_path, bruker_dir / "hsqc_2d", n=1
    )
    backend = _FakeBackend(tmp_path / "work")
    result = run_batch(manager, exp_id, data_ids, ["import", "fid"], backend)
    imp = result["results"][data_ids[0]]["steps"]["import"]
    assert imp["status"] == "already_imported"
    assert imp["data_id"] == data_ids[0]
    assert len(manager.project.experiment(exp_id).data) == 1


def _write_ft2(path: Path) -> None:
    """写一个最小可读 2D ft2(单峰,供 pick_peaks 检测)。"""
    import numpy as np
    from nmrglue.fileio import pipe

    data = np.zeros((32, 64), dtype=np.float32)
    data[16, 30] = 500.0
    dic = {k: "0" for k in pipe.fdata_dic}
    dic["FDMAGIC"] = 9.2330230000000007e14
    dic["FDDIMCOUNT"] = 2
    dic["FDSIZE"] = 64
    dic["FDSPECNUM"] = 32
    dic["FDQUADFLAG"] = 1
    dic["FDF1QUADFLAG"] = 1
    dic["FDF2QUADFLAG"] = 1
    for prefix in ("FDF1", "FDF2"):
        dic[prefix + "SW"] = "6000.0"
        dic[prefix + "OBS"] = "600.0"
        dic[prefix + "CAR"] = "4.7"
        dic[prefix + "ORIG"] = "1000.0"
    pipe.write(str(path), dic, data, overwrite=True)


class _Ft2Backend(_FakeBackend):
    """process 产出可读 ft2(供 peaks 步骤真实检测)。"""

    def process(
        self,
        experiment,
        plan,
        direct_phase_override=None,
        params=None,
        progress=None,
    ) -> dict:
        self.calls.append(("process", experiment.dataset_id))
        spectrum = Path(self.work_dir) / f"{experiment.dataset_id}.ft2"
        _write_ft2(spectrum)
        return {
            "success": True,
            "spectrum_path": str(spectrum),
            "message": "ok",
            "logs": [],
        }


def test_run_batch_full_pipeline(tmp_path: Path, bruker_dir: Path) -> None:
    """GUI 全流程(分析已隐藏):import→fid→spectrum→peaks,逐数据成功。"""
    manager, exp_id, data_ids = _manager_with_data(
        tmp_path, bruker_dir / "hsqc_2d", n=1
    )
    backend = _Ft2Backend(tmp_path / "work")
    result = run_batch(
        manager,
        exp_id,
        data_ids,
        ["import", "fid", "spectrum", "peaks"],
        backend,
        params={"phase_route": "none"},
    )
    per = result["results"][data_ids[0]]
    assert per["status"] == "success"
    assert per["steps"]["import"]["status"] == "already_imported"
    assert per["steps"]["peaks"]["status"] == "success"
    assert per["steps"]["peaks"]["peak_count"] >= 1
    peak_path = Path(per["steps"]["peaks"]["peak_path"])
    assert peak_path.parent == manager.data_dir(exp_id, data_ids[0], "peaks")
    assert "analysis" not in per["steps"]
    assert any(r.workflow_ref == "pick_peaks" for r in manager.project.workflow_runs)


def test_run_batch_unknown_step_raises(
    tmp_path: Path, bruker_dir: Path
) -> None:
    manager, exp_id, data_ids = _manager_with_data(
        tmp_path, bruker_dir / "hsqc_2d", n=1
    )
    with pytest.raises(BatchError, match="不支持的批处理步骤"):
        run_batch(
            manager,
            exp_id,
            data_ids,
            ["smile"],
            _FakeBackend(tmp_path / "work"),
        )


def test_run_batch_empty_batch_raises(
    tmp_path: Path, bruker_dir: Path
) -> None:
    manager, exp_id, _data_ids = _manager_with_data(
        tmp_path, bruker_dir / "hsqc_2d", n=1
    )
    with pytest.raises(BatchError, match="没有数据"):
        run_batch(
            manager, exp_id, "B9", ["fid"], _FakeBackend(tmp_path / "work")
        )
