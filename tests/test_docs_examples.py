"""Guard that the documented examples actually run (2026-09-20).

Background (user report): the README Python API example printed
``result.summary["delta_std_ppm"]``, but ``StudyResult.summary`` reports the execution
outcome only (status/counts) and never returns that field -- copying it raised ``KeyError``.
The old doc tests only checked that sections and names were present, never that examples ran.

This file makes "the documented examples really run" a regression fact:

1. the README Python API block is **executed as written** (only the study root and
   dataset path are redirected to a temporary directory, and a deterministic stub
   backend is injected -- the example omits ``backend=`` because real use drives NMRPipe);
2. every ``*.summary["field"]`` in the docs must be a field that really exists (scan + run);
3. the two "Example workflow" commands in the README run as written;
4. the scripts under ``docs/external-api/examples/`` run with their documented arguments
   (the measurement one needs no NMRPipe; the study ones get the stub backend).
"""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from scipy.ndimage import gaussian_filter

from nmrforge_api import uncertainty_summary

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
EXAMPLES = ROOT / "docs" / "external-api" / "examples"
FENCE = re.compile(r"```python\n(.*?)```", re.S)
SUMMARY_ACCESS = re.compile(r"\.summary\[\s*[\"']([A-Za-z_][A-Za-z0-9_]*)[\"']\s*\]")
BARE_SUMMARY = re.compile(r"(?<![.\w])summary\[\s*[\"']([A-Za-z_][A-Za-z0-9_]*)[\"']\s*\]")

# synthetic spectrum geometry: data axis 0 = indirect (15N, 64 points), axis 1 = direct
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
            "FDF1LABEL": "N15",
            "FDF2LABEL": "H1",
            "FDF1SW": str(N15_SW),
            "FDF1OBS": str(N15_OBS),
            "FDF1CAR": str(N15_CAR),
            "FDF1ORIG": "0",
            "FDF2SW": str(H1_SW),
            "FDF2OBS": str(H1_OBS),
            "FDF2CAR": str(H1_CAR),
            "FDF2ORIG": "0",
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    pipe.write(str(path), dic, arr.astype(np.float32), overwrite=True)
    return path


class _DocBackend:
    """Deterministic stub backend: writes ``fid.com`` and an nmrglue-readable ``.ft2``."""

    def __init__(self) -> None:
        self.work_dir = ""

    def _work(self) -> Path:
        path = Path(self.work_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def convert_to_fid(self, experiment, data_dir, progress=None, **_kwargs) -> dict:
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


def _readme_section(title: str) -> str:
    text = README.read_text(encoding="utf-8")
    head = f"## {title}"
    assert head in text, f"README has no section {head!r}"
    body = text.split(head, 1)[1]
    return body.split("\n## ", 1)[0]


def _readme_python_api_block() -> str:
    blocks = _fenced_python_blocks(_readme_section("Python API"))
    assert blocks, "the README Python API section has no ```python block"
    return blocks[0]


#: internal process records (historical proposals and task sheets), not user docs
INTERNAL_DOC_DIRS = frozenset({"proposals", "tasks", "manager", "reviews"})


def _documented_summary_keys() -> list[tuple[str, str, bool]]:
    """``[(file, field)]``: where the docs read ``.summary["field"]``."""
    found: list[tuple[str, str, bool]] = []
    targets = [README]
    for candidate in sorted((ROOT / "docs").rglob("*.md")):
        if candidate.relative_to(ROOT / "docs").parts[0] in INTERNAL_DOC_DIRS:
            continue
        targets.append(candidate)
    for path in targets:
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = path.relative_to(ROOT).as_posix()
        statistical = ("uncertainty_summary" in text
                       or "position_uncertainty" in text)
        for match in SUMMARY_ACCESS.finditer(text):
            found.append((rel, match.group(1), statistical))
        if statistical:
            for match in BARE_SUMMARY.finditer(text):
                found.append((rel, match.group(1), True))
    return found


def _run_documented_python_example(
    code: str, root: Path, dataset: Path
) -> tuple[Any, set[str]]:
    """Run a documented block (stub backend + temporary paths) -> (result, keys read)."""
    import nmrforge_api
    import nmrforge_api.study as study_module

    backend = _DocBackend()
    real = study_module.run_parameter_study

    def wrapper(doc_root, doc_dataset=None, **kwargs):
        kwargs.setdefault("backend", backend)
        kwargs.setdefault("params", {"phase_route": "none"})
        return real(root, dataset, **kwargs)

    used: set[str] = set()
    for line in code.splitlines():
        used.update(SUMMARY_ACCESS.findall(line))

    namespace: dict[str, Any] = {}
    nmrforge_api.run_parameter_study = wrapper  # examples import from the package
    try:
        exec(compile(code, str(README), "exec"), namespace)  # noqa: S102 - doc example
    finally:
        nmrforge_api.run_parameter_study = real
    return namespace.get("result"), used


def test_readme_python_api_example_runs(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """The README Python API example runs as written and its summary fields exist."""
    code = _readme_python_api_block()
    result, used = _run_documented_python_example(
        code, tmp_path / "readme_study", bruker_dir / "hsqc_2d"
    )
    assert result is not None, "the README example produced no result"
    assert used, "the README example reads no .summary[...] at all; it may be stale"
    missing = sorted(used - set(result.summary))
    assert not missing, (
        f"the README example reads fields StudyResult.summary does not have: {missing};"
        f"actual fields: {sorted(result.summary)}"
    )


def test_documented_summary_keys_come_from_the_real_api(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """Every ``*.summary["field"]`` in the docs must be a field that really exists."""
    result, _used = _run_documented_python_example(
        _readme_python_api_block(), tmp_path / "keys_study", bruker_dir / "hsqc_2d"
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
        f"the docs read summary fields that do not exist: {unknown};"
        f"StudyResult.summary has {sorted(study_keys)}, uncertainty_summary has "
        f"{sorted(uncertainty_keys)}"
    )


def _run_example(script: str, tail: str) -> subprocess.CompletedProcess:
    """按 README 原文跑一个示例脚本,输出固定按 UTF-8 解码。

    ``text=True`` 会用本机区域编码(Windows 上常是 GBK)解码子进程输出,而子进程按
    ``PYTHONIOENCODING`` 写 UTF-8 —— 父进程设了 ``PYTHONIOENCODING=utf-8`` 时就会在读取线程里
    抛``UnicodeDecodeError``(2026-09-21 复核时看到的告警)。两个方向都钉成 UTF-8。
    """
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(
        [sys.executable, str(ROOT / script), *tail.split()],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=300,
        check=False,
    )


def test_readme_example_workflow_commands_run(tmp_path: Path) -> None:
    """The two "Example workflow" commands in the README run as written."""
    section = _readme_section("Example workflow")
    commands = re.findall(r"^python (\S+)([^\n]*)", section, re.M)
    assert len(commands) == 2, f"the README example command count changed: {commands}"
    out = tmp_path / "example_data" / "hsqc_2d"
    script, tail = commands[0]
    done = _run_example(script, tail.replace("./example_data/hsqc_2d", str(out)))
    assert done.returncode == 0, (
        f"{script} failed: {done.stdout[-800:]} {done.stderr[-800:]}"
    )
    assert (out / "acqus").is_file(), "the synthetic dataset wrote no acqus"
    script, tail = commands[1]
    done = _run_example(script, tail.replace("./example_data/hsqc_2d", str(out)))
    assert done.returncode == 0, (
        f"{script} failed: {done.stdout[-800:]} {done.stderr[-800:]}"
    )


def test_measure_only_example_runs(tmp_path: Path) -> None:
    """``examples/measure_only.py`` runs with its documented args (no NMRPipe)."""
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
    assert module.main(["--spectrum", str(spectrum), "--peaks", str(peaks),
                        "--out", str(out)]) == 0
    for method in ("parabolic", "gaussian"):
        assert (out / f"peak_table_{method}.csv").is_file()


def test_study_example_scripts_run(tmp_path: Path, bruker_dir: Path) -> None:
    """``run_study.py`` / ``step_by_step.py`` run with their documented args."""
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
    assert run_study.main([
        "--study", str(tmp_path / "s1"),
        "--a", str(bruker_dir / "hsqc_2d"),
        "--combos", str(combos),
    ]) == 0

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
    assert step.main([
        "--study", str(tmp_path / "s2"),
        "--dataset", str(bruker_dir / "hsqc_2d"),
        "--combos", str(combos),
    ]) == 0


def test_example_scripts_parse_their_documented_flags() -> None:
    """All three example scripts print ``--help`` (their documented flags parse)."""
    for name in ("run_study", "step_by_step", "measure_only"):
        module = _load_example(name)
        with pytest.raises(SystemExit) as excinfo:
            module.main(["--help"])
        assert excinfo.value.code == 0, name


def _load_example(name: str):
    """Load ``docs/external-api/examples/<name>.py`` by path (not a package module)."""
    path = EXAMPLES / f"{name}.py"
    assert path.is_file(), path
    spec = importlib.util.spec_from_file_location(f"docs_example_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module
