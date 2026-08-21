"""G2B-008 进度日志测试:CshRuntime on_line 逐行转发 + stepwise progress 转发。"""

from __future__ import annotations

from pathlib import Path


def test_csh_runtime_on_line_forwards_lines(
    tmp_path: Path, monkeypatch
) -> None:
    """CshRuntime.run(on_line) 逐行转发 stdout(阶段日志实时可见)。"""
    from backend import runtime as rt

    class _FakePopen:
        def __init__(self, argv, **kwargs):
            self.stdout = iter(["line1\n", "line2\n"])
            self.stderr = None
            self.pid = 424242
            self._returncode = 0

        def wait(self, timeout=None):
            return self._returncode

    monkeypatch.setattr(rt, "shutil_which_csh", lambda: "/bin/csh")
    monkeypatch.setattr(rt.subprocess, "Popen", _FakePopen)
    collected: list[str] = []
    result = rt.CshRuntime().run(["echo", "x"], on_line=collected.append)
    assert result.returncode == 0
    assert collected == ["line1", "line2"]
    assert result.stdout == "line1\nline2\n"


class _ProgressBackend:
    """记录 progress 调用的 fake 后端。"""

    def __init__(self, work_dir: Path) -> None:
        self.work_dir = str(work_dir)
        self.progress_calls: list[str] = []

    def _touch(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")

    def convert_to_fid(self, experiment, data_dir, progress=None) -> dict:
        if progress:
            progress("开始转换 fid")
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
        if progress:
            progress("处理中")
        spectrum = Path(self.work_dir) / f"{experiment.dataset_id}.ft2"
        self._touch(spectrum)
        return {
            "success": True,
            "spectrum_path": str(spectrum),
            "message": "ok",
            "logs": [],
        }

    def reconstruct_nus(self, experiment, params, progress=None) -> dict:
        if progress:
            progress("开始 SMILE 重构")
        spectrum = Path(self.work_dir) / f"{experiment.dataset_id}.ft2"
        self._touch(spectrum)
        return {
            "success": True,
            "spectrum_path": str(spectrum),
            "message": "ok",
            "logs": [],
        }


def test_stepwise_generate_fid_forwards_progress(
    tmp_path: Path, bruker_dir: Path
) -> None:
    from core.project import ProjectManager
    from workflow.stepwise import generate_fid

    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment()
    data = manager.import_data(entry.id, str(bruker_dir / "hsqc_2d"))
    backend = _ProgressBackend(tmp_path / "work")
    messages: list[str] = []
    generate_fid(manager, entry.id, data.id, backend, progress=messages.append)
    assert "开始转换 fid" in messages


def test_stepwise_generate_spectrum_forwards_progress(
    tmp_path: Path, bruker_dir: Path
) -> None:
    from core.project import ProjectManager
    from workflow.stepwise import generate_fid, generate_spectrum

    manager = ProjectManager.create_project(tmp_path / "proj", "demo")
    entry = manager.create_experiment()
    data = manager.import_data(entry.id, str(bruker_dir / "hsqc_2d"))
    backend = _ProgressBackend(tmp_path / "work")
    messages: list[str] = []
    generate_fid(manager, entry.id, data.id, backend)
    generate_spectrum(
        manager,
        entry.id,
        data.id,
        backend,
        params={"phase_route": "none"},
        progress=messages.append,
    )
    assert "处理中" in messages
