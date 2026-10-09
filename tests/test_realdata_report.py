from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "vm_realdata_report.py"
SPEC = importlib.util.spec_from_file_location("vm_realdata_report", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
report = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = report
SPEC.loader.exec_module(report)


def test_run_once_passes_requested_processing(monkeypatch, tmp_path):
    calls = {}

    class Manager:
        def create_experiment(self, **kwargs):
            return types.SimpleNamespace(id="exp")

        def save(self):
            pass

    class ProjectManager:
        @staticmethod
        def create_project(path, name):
            return Manager()

    class Backend:
        pass

    def generate_spectrum(manager, exp_id, data_id, backend, *, params=None):
        calls["params"] = params
        return str(tmp_path / "not-created.ft2")

    replacements = {
        "backend.nmrpipe_backend": {"NMRPipeBackend": Backend},
        "core.project": {"ProjectManager": ProjectManager},
        "workflow.import_workflow": {
            "import_data": lambda *args: types.SimpleNamespace(data_id="data")
        },
        "workflow.stepwise": {
            "generate_fid": lambda *args: None,
            "generate_spectrum": generate_spectrum,
        },
    }
    for module_name, attrs in replacements.items():
        module = types.ModuleType(module_name)
        for name, value in attrs.items():
            setattr(module, name, value)
        monkeypatch.setitem(sys.modules, module_name, module)

    result = report.run_once(
        tmp_path / "dataset",
        tmp_path / "output",
        0,
        ext_lo=4.2,
        ext_hi=0.5,
        final_ext_lo=4.2,
        final_ext_hi=0.5,
    )
    assert calls["params"] == {
        "ext_lo": 4.2,
        "ext_hi": 0.5,
        "final_ext_lo": 4.2,
        "final_ext_hi": 0.5,
        "apply_ext_to_opt": True,
    }
    assert result["requested_processing"] == calls["params"]


def test_run_once_refuses_existing_project_without_deleting(monkeypatch, tmp_path):
    root = tmp_path / "existing-root"
    project_dir = root / "run_00"
    project_dir.mkdir(parents=True)
    sentinel = project_dir / "keep.txt"
    sentinel.write_text("preserve", encoding="utf-8")

    with pytest.raises(FileExistsError, match="new output root"):
        report.run_once(tmp_path / "dataset", root, 0)

    assert sentinel.read_text(encoding="utf-8") == "preserve"


@pytest.mark.parametrize(
    "extra_args",
    [
        ["--ext-lo", "1.0"],
        ["--ext-lo", "nan", "--ext-hi", "0.5"],
        ["--ext-lo", "1.0", "--ext-hi", "1.0"],
    ],
)
def test_cli_rejects_invalid_window_pair(monkeypatch, tmp_path, extra_args):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    with pytest.raises(SystemExit) as exc:
        report.main(["--dataset", str(dataset), *extra_args])
    assert exc.value.code == 2


def test_cli_rejects_existing_output_root(monkeypatch, tmp_path):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    root = tmp_path / "existing"
    root.mkdir()
    monkeypatch.setattr(report, "_fingerprint", lambda path: {})
    monkeypatch.setattr(report, "_classify", lambda path: {})

    with pytest.raises(SystemExit) as exc:
        report.main(["--dataset", str(dataset), "--root", str(root)])
    assert exc.value.code == 2
