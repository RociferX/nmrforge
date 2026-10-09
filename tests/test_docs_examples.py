"""Documentation example executability guard (2026-09-20).

Background (user report): the Python API example writes
``result.summary["delta_std_ppm"]``, while ``StudyResult.summary`` only aggregates
**execution results** (status / counts) and does not return that field -- copying the
example raises ``KeyError`` on lookup. The old documentation test only checked "is the
section name / parameter name present" and did not execute the examples, so this kind of
error was never caught.

This file turns "the documentation examples really run" into a regression fact:

1. the external API quickstart Python block is **executed verbatim** (only the study
   root and data directory are swapped for temporary paths, and a deterministic
   stand-in backend is injected -- the example itself does not write ``backend=``,
   because real usage is driven by the default backend running NMRPipe);
2. every ``*.summary["field"]`` appearing in the documentation must be a field that is
   **really returned** (static scan + real run);
3. the synthetic dataset and quickstart commands in one Getting started bash block are executed;
4. the example scripts under ``docs/external-api/examples/`` are really run with the
   documented parameters (the measurement-layer examples do not need NMRPipe; the study
   examples get a stand-in backend injected).
"""

from __future__ import annotations

import importlib.util
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from scipy.ndimage import gaussian_filter

from nmrforge_api import uncertainty_summary

ROOT = Path(__file__).resolve().parent.parent
QUICKSTART = ROOT / "docs" / "external-api" / "02-quickstart.md"
GETTING_STARTED = ROOT / "docs" / "getting-started.md"
EXAMPLES = ROOT / "docs" / "external-api" / "examples"
FENCE = re.compile(r"```python\n(.*?)```", re.S)
SUMMARY_ACCESS = re.compile(r"\.summary\[\s*[\"']([A-Za-z_][A-Za-z0-9_]*)[\"']\s*\]")
BARE_SUMMARY = re.compile(r"(?<![.\w])summary\[\s*[\"']([A-Za-z_][A-Za-z0-9_]*)[\"']\s*\]")

# Synthetic spectrum geometry: data axis 0 = indirect (15N, 64 points),
# axis 1 = direct (1H, 128 points)
N15_OBS, N15_SW, N15_CAR, N15_SIZE = 60.8, 2000.0, 118.0, 64
H1_OBS, H1_SW, H1_CAR, H1_SIZE = 600.0, 6000.0, 4.7, 128
PEAKS = ((30, 60), (45, 90), (18, 100))


def _write_ft2(path: Path) -> Path:
    """Write an nmrglue-readable 2D spectrum (for the examples; deterministic)."""
    from nmrglue.fileio import pipe

    shape = (N15_SIZE, H1_SIZE)
    yy, xx = np.mgrid[0 : shape[0], 0 : shape[1]]
    arr = np.zeros(shape, dtype=float)
    for index, (y, x) in enumerate(PEAKS, start=1):
        arr += (140.0 - 20.0 * (index - 1)) * np.exp(
            -(((yy - y) ** 2) / (2 * 1.2**2) + ((xx - x) ** 2) / (2 * 1.4**2))
        )
    rng = np.random.default_rng(20260920)
    arr = gaussian_filter(arr, sigma=0.5) + rng.normal(0.0, 0.4, shape)
    dic = {key: "0" for key in pipe.fdata_dic}
    dic.update(
        {
            "FDMAGIC": 9.2330230000000007e14,
            "FDDIMCOUNT": 2,
            "FDSIZE": shape[1],
            "FDSPECNUM": shape[0],
            "FDQUADFLAG": 1,
            "FDF1QUADFLAG": 1,
            "FDF2QUADFLAG": 1,
            "FDDIMORDER": [2, 1],
            "FDDIMORDER1": 2,
            "FDDIMORDER2": 1,
            "FDF1LABEL": "N15",
            "FDF2LABEL": "H1",
            "FDF1SW": str(N15_SW),
            "FDF1OBS": str(N15_OBS),
            "FDF1CAR": str(N15_CAR),
            "FDF1ORIG": str(N15_CAR * N15_OBS - N15_SW / 2 + N15_SW / N15_SIZE),
            "FDF2SW": str(H1_SW),
            "FDF2OBS": str(H1_OBS),
            "FDF2CAR": str(H1_CAR),
            "FDF2ORIG": str(H1_CAR * H1_OBS - H1_SW / 2 + H1_SW / H1_SIZE),
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    pipe.write(str(path), dic, arr.astype(np.float32), overwrite=True)
    return path


class _DocBackend:
    """Deterministic stand-in backend: produces ``fid.com`` and an nmrglue-readable ``.ft2``."""

    def __init__(self) -> None:
        self.work_dir = ""

    def _work(self) -> Path:
        path = Path(self.work_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def convert_to_fid(self, experiment, data_dir, progress=None, params=None, **_kwargs) -> dict:
        work = self._work()
        (work / "fid.com").write_text("#!/bin/csh\n", encoding="utf-8")
        fid = work / f"{experiment.dataset_id}.fid"
        fid.write_bytes(b"fid-bytes")
        return {
            "success": True,
            "fid_path": str(fid),
            "message": "ok",
            "logs": [],
            "effective_params": {},
        }

    def process(
        self,
        experiment,
        plan,
        *,
        params=None,
        direct_phase_override=None,
        script_name=None,
        out_file=None,
        progress=None,
        **_kwargs,
    ) -> dict:
        work = self._work()
        script = work / (script_name or f"{experiment.dataset_id}_process.com")
        script.write_text("#!/bin/csh\n", encoding="utf-8")
        target = work / (out_file or f"{experiment.dataset_id}.ft2")
        if out_file:
            target = work / "_intermediate" / out_file
        _write_ft2(target)
        return {
            "success": True,
            "message": "ok",
            "spectrum_path": str(target),
            "logs": ["stub process"],
            "effective_params": {},
        }


def _fenced_python_blocks(text: str) -> list[str]:
    return [block for block in FENCE.findall(text)]


def _quickstart_python_block() -> str:
    blocks = _fenced_python_blocks(QUICKSTART.read_text(encoding="utf-8"))
    assert blocks, "Quickstart guide has no ```python block"
    return blocks[0]


#: internal process records (historical proposals and task sheets), not user docs
INTERNAL_DOC_DIRS = frozenset({"proposals", "tasks", "manager", "reviews"})


def _documented_summary_keys() -> list[tuple[str, str, bool]]:
    """``[(file, field)]``: the places in the documentation that read ``.summary["field"]``."""
    found: list[tuple[str, str, bool]] = []
    targets = [QUICKSTART, ROOT / "README.md"]
    for candidate in sorted((ROOT / "docs").rglob("*.md")):
        if candidate.relative_to(ROOT / "docs").parts[0] in INTERNAL_DOC_DIRS:
            continue
        targets.append(candidate)
    for path in targets:
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = path.relative_to(ROOT).as_posix()
        statistical = "uncertainty_summary" in text or "position_uncertainty" in text
        for match in SUMMARY_ACCESS.finditer(text):
            found.append((rel, match.group(1), statistical))
        if statistical:
            for match in BARE_SUMMARY.finditer(text):
                found.append((rel, match.group(1), True))
    return found


def _run_documented_python_example(code: str, root: Path, dataset: Path) -> tuple[Any, set[str]]:
    """Execute a documentation code block (stand-in backend and temporary paths injected);
    returns (result, the summary fields the code block reads)."""
    import nmrforge_api
    import nmrforge_api.study as study_module

    backend = _DocBackend()
    real_parameter = study_module.run_parameter_study
    real_reference = study_module.run_reference_study
    real_combination = study_module.run_combination_study

    def parameter_wrapper(doc_root, doc_dataset=None, **kwargs):
        kwargs.setdefault("backend", backend)
        kwargs.setdefault("params", {"phase_route": "none"})
        kwargs.pop("datasets", None)
        kwargs.pop("dataset", None)
        return real_parameter(root, dataset, **kwargs)

    def reference_wrapper(doc_root, doc_dataset=None, **kwargs):
        kwargs.setdefault("backend", backend)
        kwargs.setdefault("params", {"phase_route": "none"})
        kwargs.pop("datasets", None)
        kwargs.pop("dataset", None)
        return real_reference(root, dataset, **kwargs)

    def combination_wrapper(doc_reference, **kwargs):
        kwargs.setdefault("backend", backend)
        return real_combination(str(root), **kwargs)

    used: set[str] = set()
    for line in code.splitlines():
        used.update(SUMMARY_ACCESS.findall(line))

    namespace: dict[str, Any] = {}
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(nmrforge_api, "run_parameter_study", parameter_wrapper)
        monkeypatch.setattr(nmrforge_api, "run_reference_study", reference_wrapper)
        monkeypatch.setattr(nmrforge_api, "run_combination_study", combination_wrapper)
        exec(compile(code, str(QUICKSTART), "exec"), namespace)  # noqa: S102 - documentation example
    return namespace.get("result"), used


def test_external_api_quickstart_example_runs(tmp_path: Path, bruker_dir: Path) -> None:
    """The API quickstart reference and combination examples run verbatim."""
    code = _quickstart_python_block()
    result, used = _run_documented_python_example(
        code, tmp_path / "readme_study", bruker_dir / "hsqc_2d"
    )
    assert result is not None, "Quickstart example did not produce result"
    assert used, "Quickstart example does not read any .summary[...] field"
    missing = sorted(used - set(result.summary))
    assert not missing, (
        f"Quickstart example reads fields missing from StudyResult.summary: {missing};"
        f"实际字段: {sorted(result.summary)}"
    )


def test_documented_summary_keys_come_from_the_real_api(tmp_path: Path, bruker_dir: Path) -> None:
    """Every ``*.summary["field"]`` appearing in the documentation must be a really
    returned field."""
    result, _used = _run_documented_python_example(
        _quickstart_python_block(), tmp_path / "keys_study", bruker_dir / "hsqc_2d"
    )
    study_keys = set(result.summary)
    uncertainty_keys = set(uncertainty_summary([]))
    unknown: list[tuple[str, str]] = []
    for rel, key, statistical in _documented_summary_keys():
        allowed = (study_keys | uncertainty_keys) if statistical else study_keys
        if key not in allowed and (rel, key) not in unknown:
            unknown.append((rel, key))
    unknown.sort()
    assert not unknown, (
        f"文档读取了不存在的 summary 字段: {unknown};"
        f"StudyResult.summary 有 {sorted(study_keys)},uncertainty_summary 有 "
        f"{sorted(uncertainty_keys)}"
    )


def _run_example(script: str, args: list[str]) -> subprocess.CompletedProcess:
    """Run a documented example script with output always decoded as
    UTF-8.

    ``text=True`` decodes the subprocess output with the machine locale encoding (often GBK
    on Windows), while the subprocess writes UTF-8 according to ``PYTHONIOENCODING`` -- with
    ``PYTHONIOENCODING=utf-8`` set in the parent this raises ``UnicodeDecodeError`` in the
    reader thread (a warning seen in the 2026-09-21 review). Both directions are pinned to
    UTF-8.
    """
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(
        [sys.executable, str(ROOT / script), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=300,
        check=False,
    )


def test_getting_started_synthetic_workflow_runs(tmp_path: Path) -> None:
    """Run the synthetic dataset and quickstart commands from one documented bash block."""
    blocks = re.findall(r"```bash\n(.*?)```", GETTING_STARTED.read_text(encoding="utf-8"), re.S)
    section = next((block for block in blocks if "make_synthetic_dataset.py" in block
                    and "quickstart.py" in block), None)
    assert section is not None, "Getting started has no bash block containing both example commands"
    commands = re.findall(r"^python (\S+)([^\n]*)", section, re.M)
    assert len(commands) == 2, f"Getting started example command count changed: {commands}"
    out = tmp_path / "example_data" / "hsqc_2d"
    script, tail = commands[0]
    args = shlex.split(tail)
    args[args.index("--out") + 1] = str(out)
    done = _run_example(script, args)
    assert done.returncode == 0, f"{script} 失败: {done.stdout[-800:]} {done.stderr[-800:]}"
    assert (out / "acqus").is_file(), "合成数据集没有写出 acqus"
    script, tail = commands[1]
    args = shlex.split(tail)
    args[-1] = str(out)
    done = _run_example(script, args)
    assert done.returncode == 0, f"{script} 失败: {done.stdout[-800:]} {done.stderr[-800:]}"


def test_measure_only_example_runs(tmp_path: Path) -> None:
    """``examples/measure_only.py`` really runs with the documented parameters (the
    measurement layer needs no NMRPipe)."""
    module = _load_example("measure_only")
    spectrum = _write_ft2(tmp_path / "candidate.ft2")
    peaks = tmp_path / "reference.list"
    peaks.write_text(
        "peak_id,H_ppm,N_ppm,height,linewidth,volume\n"
        "1,4.70,118.0,140.0,0.02,1.0\n"
        "2,4.55,111.1,120.0,0.02,1.0\n",
        encoding="utf-8",
    )
    out = tmp_path / "tables"
    assert module.main(["--spectrum", str(spectrum), "--peaks", str(peaks), "--out", str(out)]) == 0

    assert (out / "peak_table_parabolic.csv").is_file()
    assert not (out / "peak_table_gaussian.csv").exists()


def test_study_example_scripts_run(tmp_path: Path, bruker_dir: Path) -> None:
    """``run_study.py`` / ``step_by_step.py`` run with the documented parameters (stand-in
    backend injected)."""
    backend = _DocBackend()
    combos = EXAMPLES / "combos.csv"
    assert combos.is_file()

    run_study = _load_example("run_study")
    real_run = run_study.run_parameter_study

    def patched_run(root, dataset=None, **kwargs):
        kwargs.setdefault("backend", backend)
        kwargs.setdefault("params", {"phase_route": "none"})
        return real_run(root, dataset, **kwargs)

    run_study.run_parameter_study = patched_run
    assert (
        run_study.main(
            [
                "--study",
                str(tmp_path / "s1"),
                "--a",
                str(bruker_dir / "hsqc_2d"),
                "--combos",
                str(combos),
            ]
        )
        == 0
    )

    step = _load_example("step_by_step")
    real_open = step.open_study
    real_build = step.build_reference

    def patched_open(root, **kwargs):
        session = real_open(root, **kwargs)
        session.backend = backend
        return session

    def patched_build(session, *args, **kwargs):
        kwargs.setdefault("params", {"phase_route": "none"})
        return real_build(session, *args, **kwargs)

    step.open_study = patched_open
    step.build_reference = patched_build
    assert (
        step.main(
            [
                "--study",
                str(tmp_path / "s2"),
                "--dataset",
                str(bruker_dir / "hsqc_2d"),
                "--combos",
                str(combos),
            ]
        )
        == 0
    )


def test_example_scripts_parse_their_documented_flags() -> None:
    """``--help`` works for the three example scripts (the documented parameters are
    really accepted)."""
    for name in ("run_study", "step_by_step", "measure_only"):
        module = _load_example(name)
        with pytest.raises(SystemExit) as excinfo:
            module.main(["--help"])
        assert excinfo.value.code == 0, name


def _load_example(name: str):
    """Load ``docs/external-api/examples/<name>.py`` by path (they are not package modules)."""
    path = EXAMPLES / f"{name}.py"
    assert path.is_file(), path
    spec = importlib.util.spec_from_file_location(f"docs_example_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module
