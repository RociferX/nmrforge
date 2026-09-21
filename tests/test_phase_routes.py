"""Unify phase approach (unified_route) to orchestrate tests."""

from __future__ import annotations

from pathlib import Path

import numpy as np

import workflow.phase_routes as routes
from core.data.bruker_reader import read_dataset


def _synthetic_preview(axis: int, p0: float) -> np.ndarray:
    """Complex Lorentzian: complex data of the evaluated axis + known phase, other axes real types
    (isomorphic to the memory search test)."""
    n0, n1 = 96, 80
    k0 = np.arange(n0, dtype=float)
    k1 = np.arange(n1, dtype=float)
    width = 1.5
    arr = np.zeros((n0, n1), dtype=np.complex128)
    for c0, c1, amp in ((n0 * 0.35, n1 * 0.45, 400.0), (n0 * 0.62, n1 * 0.58, 320.0)):
        z0 = 1.0 / (1.0 + 1j * (k0 - c0) / width)
        z1 = 1.0 / (1.0 + 1j * (k1 - c1) / width)
        r0 = 1.0 / (1.0 + ((k0 - c0) / width) ** 2)
        r1 = 1.0 / (1.0 + ((k1 - c1) / width) ** 2)
        if axis == 0:
            arr += amp * np.outer(z0, r1)
        else:
            arr += amp * np.outer(r0, z1)
    n = arr.shape[axis]
    k = np.arange(n, dtype=float)
    ramp = np.exp(1j * np.deg2rad(p0 + 0.0 * k / max(n - 1, 1)))
    shape = [1] * arr.ndim
    shape[axis] = n
    return arr * ramp.reshape(shape)


class _FakeBackend:
    def __init__(self, work: Path) -> None:
        self.work = Path(work)
        self.work.mkdir(parents=True, exist_ok=True)
        self.process_calls: list[tuple] = []
        self.reconstruct_params: list[dict] = []
        self.finalize_calls: list[dict] = []
        self.apply_direct_calls: list[tuple] = []

    def process(
        self,
        experiment,
        plan,
        params=None,
        direct_phase_override=None,
        out_file=None,
        script_name=None,
        progress=None,
    ):
        params = dict(params or {})
        self.process_calls.append((experiment, plan, params, dict(direct_phase_override or {})))
        if script_name:
            (self.work / script_name).write_text(
                f"# fake script {script_name}\n", encoding="utf-8"
            )
        path = str(self.work / (out_file or "final.ft2"))
        Path(path).write_bytes(b"x")
        return {"success": True, "spectrum_path": path, "logs": []}

    def reconstruct_nus(self, experiment, params, progress=None):
        self.reconstruct_params.append(dict(params or {}))
        # Simulate the real backend: write {dataset_id}_nus.com for each call (for initial script
        # running to retain assertions).
        script = self.work / f"{experiment.dataset_id}_nus.com"
        script.write_text(
            f"# fake nus script #{len(self.reconstruct_params)}\n",
            encoding="utf-8",
        )
        return {"success": True, "spectrum_path": str(self.work / "out.ft2"), "logs": []}

    def finalize_nus(
        self,
        experiment,
        phases=None,
        work_dir=None,
        planes=None,
        params=None,
        out_file=None,
        script_name=None,
        progress=None,
    ):
        self.finalize_calls.append(
            {
                "phases": dict(phases or {}),
                "planes": planes,
                "params": dict(params or {}),
                "progress": progress,
            }
        )
        path = str(self.work / (out_file or "final.ft2"))
        Path(path).write_bytes(b"x")
        return {"success": True, "spectrum_path": path, "logs": []}

    def _apply_direct_phase(self, experiment, work, p0, p1, logs, progress=None):
        self.apply_direct_calls.append((p0, p1))
        return True


def test_unified_route_uniform_dc_offset_poly_time_into_final(
    tmp_path: Path, monkeypatch, bruker_dir: Path
) -> None:
    """0.2.165: When uniform diagnosis detects DC offset, the final run parameter carries
    direct_poly_time (POLY -time), and the first pass replication preview does not add -- to
    align with the NUS path."""
    from types import SimpleNamespace

    experiment = read_dataset(bruker_dir / "hsqc_2d")
    backend = _FakeBackend(tmp_path / "uni_poly_work")
    work = backend.work
    monkeypatch.setattr(
        "workflow.direct_diagnostics.run_direct_diagnostics",
        lambda wk, exp: SimpleNamespace(
            reports=["DC Offset: Automatically enabled POLY -time"],
            metrics={},
            apply_poly_time=True,
            repaired_badpoints=0,
            backup_dir="",
        ),
    )

    def fake_read(path: str, unpack_axis: int | None = None):
        name = Path(path).name
        if "F1" in name:
            return _synthetic_preview(0, -25.0)
        if "F2" in name:
            return _synthetic_preview(1, -35.0)
        return _synthetic_preview(0, 0.0)

    monkeypatch.setattr(routes, "_read_complex_preview", fake_read)
    result = routes.unified_route(experiment, backend, work_dir=work)
    # 0.2.199-patch29dn/patch29dr: After direct dimension is determined, search indirect dimension
    # again for one round (F1 preview + F2 preview + F1 search + joint + final run = 5 times
    # process).
    preview1, preview2, preview_r2, _joint, final = backend.process_calls
    for preview in (preview1, preview2, preview_r2):
        assert preview[2].get("direct_poly_time") in (None, False)
    assert final[2]["direct_poly_time"] is True
    assert result["diagnostics"]["apply_poly_time"] is True
    assert any("POLY -time" in r for r in result["diagnostics"]["reports"])


def test_unified_route_uniform_magnitude_skips_phase_search(
    tmp_path: Path, monkeypatch, bruker_dir: Path
) -> None:
    """0.2.167:magnitude(QF) indirect dimension No phase node -- unified uniform only searches for
    the axis (F2) with PS, and does not generate F1 replica preview (previously, the QF axis was
    searched in vain for phase)."""
    experiment = read_dataset(bruker_dir / "hsqc_2d")
    experiment.acquisition_parameters["acqu2s"]["FnMODE"] = 1  # QF/magnitude
    backend = _FakeBackend(tmp_path / "uni_qf_work")
    work = backend.work

    def fake_read(path: str, unpack_axis: int | None = None):
        name = Path(path).name
        if "F2" in name:
            return _synthetic_preview(1, -35.0)
        return _synthetic_preview(0, 0.0)

    monkeypatch.setattr(routes, "_read_complex_preview", fake_read)
    result = routes.unified_route(experiment, backend, work_dir=work)
    # F2 preview + joint (processing parameter optimisation) + final run (without F1 preview).
    assert len(backend.process_calls) == 3
    assert all(
        call[2].get("preview_axis") != "F1" for call in backend.process_calls
    )
    assert set(result["phases"]) == {"F2"}
    assert result["spectrum_path"]


def test_unified_route_uniform_hmbc_skips_all_phase_search(
    tmp_path: Path, monkeypatch, bruker_dir: Path
) -> None:
    """0.2.167:HMBC(magnitude spectrum, template auto_phase=false) Skip phase search in all axes --
    Do not generate any complex preview, phase remains (0,0) (consistent with NUS branch)."""
    experiment = read_dataset(bruker_dir / "hsqc_2d")
    experiment.experiment_type.name = "HMBC"
    experiment.acquisition_parameters["acqu2s"]["FnMODE"] = 1  # QF
    backend = _FakeBackend(tmp_path / "uni_hmbc_work")
    work = backend.work
    result = routes.unified_route(experiment, backend, work_dir=work)
    # No preview:joint + final run.
    assert len(backend.process_calls) == 2
    assert all(
        call[2].get("preview_axis") is None for call in backend.process_calls
    )
    assert result["phases"]["F2"] == (0.0, 0.0)
    assert any(
        "magnitude spectra are not phase-optimised" in line
        for line in result["logs"]
    )
    assert result["spectrum_path"]


def test_unified_route_uniform_order_and_phases(
    tmp_path: Path, monkeypatch, bruker_dir: Path
) -> None:
    """Uniform: F1 first and then F2 (old algorithm order); F2 preview carries the fixed F1 phase;
    the final run carries all phases."""
    experiment = read_dataset(bruker_dir / "hsqc_2d")
    backend = _FakeBackend(tmp_path / "pv_work")
    work = backend.work

    def fake_read(path: str, unpack_axis: int | None = None):
        name = Path(path).name
        if "F1" in name:
            return _synthetic_preview(0, -25.0)
        if "F2" in name:
            return _synthetic_preview(1, -35.0)
        return _synthetic_preview(0, 0.0)

    monkeypatch.setattr(routes, "_read_complex_preview", fake_read)
    result = routes.unified_route(experiment, backend, work_dir=work)
    # 0.2.163-patch6: uniform processing parameter optimisation, run joint review spectrum first;
    # 0.2.199-patch29dn/patch29dr: direct dimension, re-search indirect dimension after confirmation
    # -> 5 times process (F1 preview + F2 preview + F1 re-search + joint + final run).
    assert len(backend.process_calls) == 5
    preview1, preview2, preview_r2, joint, final = backend.process_calls
    assert joint[2].get("preview_axis") is None  # joint Not preview.
    assert preview1[2].get("preview_axis") == "F1"
    assert preview1[3] == {}  # The first axis has no fixed phase.
    assert preview2[2].get("preview_axis") == "F2"
    assert set(preview2[3]) == {"F1"}  # F1 Fixed and passed to F2 preview.
    # Search again (direct dimension has been determined).
    assert preview_r2[2].get("preview_axis") == "F1"
    assert set(preview_r2[3]) == {"F2"}
    assert set(final[3]) == {"F1", "F2"}
    phases = result["phases"]
    assert abs((phases["F1"][0] - 25.0 + 180.0) % 360.0 - 180.0) <= 8.0, phases
    assert abs((phases["F2"][0] - 35.0 + 180.0) % 360.0 - 180.0) <= 8.0, phases
    # Combined review/handling parameter optimisation to score memory (without incrementing backend
    # run count): 3 preview + 1 final run.
    assert result["backend_runs"] == 4
    assert "spectrum_path" in result


def test_disambiguate_180_mixed_uses_region_sign_convention() -> None:
    """+/-180° chemical shift partition disambiguation: Default Cα burden/Cβ positive -- Phase 0
    does not turn over, phase 180 turns back."""
    from core.data.internal_data_model import (
        AxisRole,
        Dimension,
        Experiment,
        ExperimentType,
        Sampling,
        SamplingMode,
    )
    from workflow.phase_routes import _disambiguate_180_mixed

    n = 64
    dims = [
        Dimension(logical_axis="F3", nucleus="1H", sf=600.0, sw=8196.0,
                  o1p=4.7, role=AxisRole.DIRECT),
        Dimension(logical_axis="F2", nucleus="15N", sf=60.8, sw=2189.0,
                  o1p=118.0),
        Dimension(logical_axis="F1", nucleus="13C", sf=150.9, sw=11312.0,
                  o1p=39.0, td=n, ft_size=n),
    ]
    exp = Experiment(
        dataset_id="x",
        source_path=Path("x"),
        ndim=3,
        acquisition_order=["F3", "F2", "F1"],
        dimensions=dims,
        sampling=Sampling(mode=SamplingMode.NUS),
        experiment_type=ExperimentType(name="HNCACB", confidence=1.0),
    )
    k = np.arange(n)
    ppm = 39.0 + (n / 2.0 - k) * (11312.0 / (n * 150.9))
    arr = np.zeros((4, n), dtype=np.complex128)
    for target in (50.0, 55.0, 60.0, 65.0):  # Cα Zone (40-70), negative expectations.
        i = int(np.argmin(np.abs(ppm - target)))
        arr[:, i] += -1.0
    for target in (18.0, 25.0, 30.0, 40.0):  # Cβ District (15-45), expect positive.
        i = int(np.argmin(np.abs(ppm - target)))
        arr[:, i] += 1.0
    assert _disambiguate_180_mixed(arr, 1, (0.0, 0.0), exp, "F1") == (0.0, 0.0)
    flipped = _disambiguate_180_mixed(arr, 1, (180.0, 0.0), exp, "F1")
    assert abs((flipped[0] - 0.0 + 180.0) % 360.0 - 180.0) < 1e-6, flipped
    # 15N axis has no partition prior and no disambiguation.
    assert _disambiguate_180_mixed(arr, 0, (90.0, 0.0), exp, "F2") == (90.0, 0.0)


def test_unified_route_nus_reconstruct_then_finalize(
    tmp_path: Path, monkeypatch, bruker_dir: Path
) -> None:
    """NUS:SMILE First pass (off search) -> direct dimension symmetry phase modulation -> indirect
    dimension finalize replication preview + memory search -> joint review -> process parameter
    optimisation -> final run as a complete script (fill in each dimension phase, do not write
    nus3d_rc_ph rotation copy)."""
    from types import SimpleNamespace

    experiment = read_dataset(bruker_dir / "nus_2d")
    backend = _FakeBackend(tmp_path / "nus_work")
    work = backend.work
    # 0.2.156: Diagnosis detects DC offset -> Both the first pass and the final run script have
    # direct_poly_time.
    monkeypatch.setattr(
        "workflow.direct_diagnostics.run_direct_diagnostics",
        lambda wk, exp: SimpleNamespace(
            reports=["DC Offset: Automatically enabled POLY -time"],
            metrics={},
            apply_poly_time=True,
            repaired_badpoints=0,
            backup_dir="",
        ),
    )
    n_direct, n_t1 = 64, 32
    k0 = np.arange(n_direct, dtype=float)
    t1 = np.arange(n_t1, dtype=float)
    direct = 1.0 / (1.0 + 1j * (k0 - 22) / 1.5)
    direct = direct * np.exp(-1j * np.deg2rad(30.0))
    fid1 = np.exp(-t1 / 8.0) * np.cos(2.0 * np.pi * 8.0 * t1 / n_t1)
    planes = np.outer(direct, fid1)
    monkeypatch.setattr(routes, "_load_recon_planes", lambda exp, wk: planes)
    monkeypatch.setattr(
        "core.optimization.phase_consensus.search_direct_phase_real_ht",
        lambda arr, axis=-1, sign_mode="uniform", **kwargs: (30.0, 0.0, 80.0),
    )
    monkeypatch.setattr(
        routes, "_read_real_ft3",
        lambda path: np.zeros((32, 64), dtype=float),
    )

    def fake_read(path: str, unpack_axis: int | None = None):
        name = Path(path).name
        if "preview_F1" in name:
            return _synthetic_preview(0, -30.0)
        return _synthetic_preview(0, 0.0)

    monkeypatch.setattr(routes, "_read_complex_preview", fake_read)
    # Process parameter optimisation and fixed return (baseline / zero filling / window), do not
    # rely on fake backend to write music.
    monkeypatch.setattr(
        routes,
        "_optimize_nus_processing",
        lambda exp, backend, work, fixed, base_params, progress=None: {
            "baseline": {"F2": {"enabled": False}},
            "zero_fill": {"F1": {"mode": "auto"}, "F2": {"mode": "auto"}},
            "window": None,
            "logs": ["Handle parameter optimisation (test): fixed configuration"],
        },
    )
    result = routes.unified_route(experiment, backend, work_dir=work)
    # First run + final run of the complete script.
    assert len(backend.reconstruct_params) == 2
    assert backend.reconstruct_params[0].get("display_phase_search") is False
    final_params = backend.reconstruct_params[1]
    assert final_params.get("direct_phase_search") is False
    assert abs(final_params["direct_phase_override"][0] - 30.0) <= 8.0
    assert abs(final_params["direct_phase_override"][1]) <= 8.0
    assert abs((result["direct_phase"][0] - 30.0 + 180.0) % 360.0 - 180.0) <= 8.0
    assert abs((result["phases"]["F1"][0] - 30.0 + 180.0) % 360.0 - 180.0) <= 8.0
    assert final_params["phases"]["F1"] == list(result["phases"]["F1"])
    assert final_params["baseline"] == {"F2": {"enabled": False}}
    assert final_params["zero_fill"] == {
        "F1": {"mode": "auto"},
        "F2": {"mode": "auto"},
    }
    assert final_params["window"] is None
    # No copy of the rotation plane is written in the last pass: no _apply_direct_phase call, no
    # planes coverage.
    assert not backend.apply_direct_calls
    assert all(call["planes"] is None for call in backend.finalize_calls)
    assert "Handle parameter optimisation (test): fixed configuration" in result["logs"]
    # 0.2.199-patch29do: iterative -> SMILE + F1 preview + direct dimension preview + F1 re-search +
    # final run.
    assert result["backend_runs"] == 5
    # 0.2.199-patch29do (fix): NUS finalize preview will write the phase of the preview axis in
    # phases directly into PS (different from uniform preview, which will filter). The indirect
    # dimension re-search must exclude the preview axis itself, otherwise the residual (~0) will be
    # found in the old phase generation and the absolute phase will be overwritten and lost.
    f1_previews = [
        c
        for c in backend.finalize_calls
        if c["params"].get("preview_axis") == "F1"
    ]
    assert len(f1_previews) == 2  # First search + second search.
    assert "F1" not in f1_previews[-1]["phases"]
    # 0.2.155/0.2.160: When the diagnosis detects DC offset, the final run script carries
    # direct_poly_time(POLY -time); the first pass script is not added (to avoid biased direct
    # dimension phase search); the log contains step-by-step time consumption and end summary.
    assert "direct_poly_time" in final_params
    assert backend.reconstruct_params[0].get("direct_poly_time") is None
    assert final_params.get("direct_poly_time") is True
    assert "== spectrum quality and data quality report ==" in result["logs"]
    assert any("◆ Final spectrum image quality" in line for line in result["logs"])
    assert any("Phase search is completed, time-consuming" in line for line in result["logs"])
    assert any("The final run is completed and takes time" in line for line in result["logs"])
    # 0.2.156: The first run script is kept as {dataset_id}_before_optimize.com (content = first run
    # script).
    no_opt = work / f"{experiment.dataset_id}_before_optimize.com"
    assert no_opt.is_file()
    assert "# fake nus script #1" in no_opt.read_text(encoding="utf-8")
    assert "# fake nus script #2" not in no_opt.read_text(encoding="utf-8")
    assert any("first-run script kept as" in line for line in result["logs"])



def test_optimize_nus_processing_baseline_and_window(
    tmp_path: Path, monkeypatch, bruker_dir: Path
) -> None:
    """Processing parameter optimisation: baseline memory score write-back + indirect dimension
    window function candidate score selection (no rerun SMILE)."""
    import nmrglue as ng

    from workflow.baseline_optimize import BaselineOptimizeResult

    experiment = read_dataset(bruker_dir / "nus_3d")
    work = tmp_path / "proc_work"
    work.mkdir()
    calls: list[dict] = []

    def _dic_3d() -> dict:
        import nmrglue as ng

        dic = ng.pipe.create_empty_dic()
        dic.update(
            {
                "FDSIZE": 4.0,
                "FDSPECNUM": 4.0,
                "FDREALSIZE": 8.0,
                "FDF1TDSIZE": 4.0,
                "FDF2TDSIZE": 4.0,
                "FDF3TDSIZE": 4.0,
                "FDF3SIZE": 4.0,
                "FDFILECOUNT": 4.0,
                "FDDIMCOUNT": 3.0,
                "FDF2FTFLAG": 1.0,
                "FDF2QUADFLAG": 1.0,
            }
        )
        return dic

    def fake_finalize(
        experiment,
        *,
        phases=None,
        work_dir=None,
        baseline=None,
        params=None,
        out_file=None,
        script_name=None,
        progress=None,
    ):
        out = Path(work_dir) / (out_file or "out.ft3")
        ng.pipe.write(
            str(out),
            _dic_3d(),
            np.zeros((4, 4, 4), dtype=np.complex64),
            overwrite=True,
        )
        calls.append(
            {
                "phases": dict(phases or {}),
                "baseline": dict(baseline or {}),
                "params": dict(params or {}),
                "out_file": out_file,
            }
        )
        return {"success": True, "spectrum_path": str(out), "logs": []}

    fixed = {"F2": (10.0, -5.0), "F1": (20.0, 3.0)}
    fake_opt = BaselineOptimizeResult(
        baseline={
            "F3": {"enabled": True, "mode": "auto", "order": 0},
            "F2": {"enabled": True, "mode": "auto", "order": 0},
            "F1": {"enabled": True, "mode": "order", "order": 2},
        },
        scores={},
        spectrum_path="",
        logs=["F1: Baseline has been optimised"],
        optimized=["F1"],
        skipped=[],
    )
    monkeypatch.setattr(
        "workflow.baseline_optimize.optimize_baseline",
        lambda experiment, spectrum_path, **kw: fake_opt,
    )
    from workflow.window_optimize import MultiWindowOptimizeResult

    monkeypatch.setattr(
        "workflow.window_optimize.optimize_indirect_windows_from_recon",
        lambda work, experiment, current=None: MultiWindowOptimizeResult(
            choice={
                "F2": {"type": "sine_bell", "off": 0.45, "end": 0.95},
                "F1": {"type": "none"},
            },
            changed=True,
            logs=["indirect dimension window (test): F2 SP 0.45-0.95, F1 no window"],
        ),
    )
    proc = routes._optimize_nus_processing(
        experiment,
        type("B", (), {"finalize_nus": staticmethod(fake_finalize)})(),
        work,
        fixed,
        None,
    )
    assert proc["baseline"]["F1"]["order"] == 2
    # 0.2.190: indirect dimension window real optimisation (candidate including no window), no
    # longer hard-coded fixed no window.
    assert proc["window"] == {
        "F2": {"type": "sine_bell", "off": 0.45, "end": 0.95},
        "F1": {"type": "none"},
    }
    assert "F1: Baseline has been optimised" in " ".join(proc["logs"])
    assert "indirect dimension window (test)" in " ".join(proc["logs"])
    assert "Fixed windowless" not in " ".join(proc["logs"])
    # 0.2.199-patch29eb: Progress copy with specific product name 0.2.199-patch29fg: Remove 2.1
    # indirect dimension baseline re-rendering (dead rendering -- window function in recon/FID
    # memory score, zero filling no candidate), leaving only joint spectrum once finalize.
    assert len(calls) == 1  # joint Spectrum.
    assert calls[0]["baseline"] == {}


def test_unified_route_nus_progress_stages(
    tmp_path: Path, monkeypatch, bruker_dir: Path
) -> None:
    """NUS: progress covers the first pass SMILE/deal with parameter optimisation / complete script
    final run of each stage."""
    experiment = read_dataset(bruker_dir / "nus_2d")
    backend = _FakeBackend(tmp_path / "nus_prog_work")
    work = backend.work
    n_direct, n_t1 = 64, 32
    k0 = np.arange(n_direct, dtype=float)
    t1 = np.arange(n_t1, dtype=float)
    direct = 1.0 / (1.0 + 1j * (k0 - 22) / 1.5)
    fid1 = np.exp(-t1 / 8.0) * np.cos(2.0 * np.pi * 8.0 * t1 / n_t1)
    planes = np.outer(direct, fid1)
    monkeypatch.setattr(routes, "_load_recon_planes", lambda exp, wk: planes)
    monkeypatch.setattr(
        "core.optimization.phase_consensus.search_direct_phase_real_ht",
        lambda arr, axis=-1, sign_mode="uniform", **kwargs: (30.0, 0.0, 80.0),
    )
    monkeypatch.setattr(
        routes, "_read_real_ft3",
        lambda path: np.zeros((32, 64), dtype=float),
    )
    monkeypatch.setattr(
        routes, "_read_complex_preview",
        lambda path, unpack_axis=None: _synthetic_preview(0, -30.0),
    )
    monkeypatch.setattr(
        routes,
        "_optimize_nus_processing",
        lambda exp, backend, work, fixed, base_params, progress=None: {
            "baseline": {},
            "zero_fill": {"F1": {"mode": "auto"}, "F2": {"mode": "auto"}},
            "window": None,
            "logs": [],
        },
    )
    messages: list[str] = []
    result = routes.unified_route(
        experiment, backend, work_dir=work, progress=messages.append
    )
    joined = "\n".join(messages)
    assert any(
        "first pass SMILE reconstruction is completed" in line
        for line in result["logs"]
    ), result["logs"]
    assert "Replica preview in progress" in joined, messages
    assert "F1 Replica preview completed" in joined, messages
    assert (
        "The phase search is completed and processing of parameter optimisation (baseline/zero "
        "filling/window function) begins"
    ) in joined, messages
    assert (
        "In the final run (complete script, including the final phase of each dimension)"
    ) in joined, messages
    assert "Final run completed" in joined, messages
    assert all(call["progress"] is not None for call in backend.finalize_calls)
    # 0.2.199-patch29do:Iteration -> 5.
    assert result["backend_runs"] == 5



def test_unified_route_nus_final_ext_apply_to_opt(
    tmp_path: Path, monkeypatch, bruker_dir: Path
) -> None:
    """0.2.162-patch15/0.2.199-patch3:final_ext By default, "Apply this range to the optimisation
    process" is turned on, and the first pass of reconstruction is in the same window; when
    turned off, only the final run is applied to the range."""
    from types import SimpleNamespace

    experiment = read_dataset(bruker_dir / "nus_2d")
    backend = _FakeBackend(tmp_path / "nus_ext_work")
    work = backend.work
    monkeypatch.setattr(
        "workflow.direct_diagnostics.run_direct_diagnostics",
        lambda wk, exp: SimpleNamespace(
            reports=[],
            metrics={},
            apply_poly_time=False,
            repaired_badpoints=0,
            backup_dir="",
        ),
    )
    n_direct, n_t1 = 64, 32
    k0 = np.arange(n_direct, dtype=float)
    t1 = np.arange(n_t1, dtype=float)
    direct = 1.0 / (1.0 + 1j * (k0 - 22) / 1.5)
    fid1 = np.exp(-t1 / 8.0) * np.cos(2.0 * np.pi * 8.0 * t1 / n_t1)
    planes = np.outer(direct, fid1)
    monkeypatch.setattr(routes, "_load_recon_planes", lambda exp, wk: planes)
    monkeypatch.setattr(
        "core.optimization.phase_consensus.search_direct_phase_real_ht",
        lambda arr, axis=-1, sign_mode="uniform", **kwargs: (30.0, 0.0, 80.0),
    )
    monkeypatch.setattr(
        routes, "_read_real_ft3",
        lambda path: np.zeros((32, 64), dtype=float),
    )
    monkeypatch.setattr(
        routes,
        "_read_complex_preview",
        lambda path, unpack_axis=None: _synthetic_preview(0, 0.0),
    )
    monkeypatch.setattr(
        routes,
        "_optimize_nus_processing",
        lambda exp, backend, work, fixed, base_params, progress=None: {
            "baseline": {},
            "zero_fill": {},
            "window": None,
            "logs": [],
        },
    )
    # Turn off "Apply this range to the optimisation process": keep the default window for the first
    # pass and only use this range for the final run.
    result = routes.unified_route(
        experiment,
        backend,
        work_dir=work,
        base_params={
            "final_ext_lo": "11.0",
            "final_ext_hi": "5.5",
            "apply_ext_to_opt": "0",
        },
    )
    first_params = backend.reconstruct_params[0]
    final_params = backend.reconstruct_params[1]
    assert "final_ext_lo" not in first_params
    assert first_params.get("ext_lo") is None
    assert final_params["ext_lo"] == "11.0"
    assert final_params["ext_hi"] == "5.5"
    assert "final_ext_lo" not in final_params
    assert result["spectrum_path"]
    # Enabled by default: the range enters the first pass of reconstruction / phase search at the
    # same time (the reconstruction plane is the optimisation evaluation window).
    backend2 = _FakeBackend(tmp_path / "nus_ext_work_on")
    result2 = routes.unified_route(
        experiment,
        backend2,
        work_dir=backend2.work,
        base_params={"final_ext_lo": "11.0", "final_ext_hi": "5.5"},
    )
    first2 = backend2.reconstruct_params[0]
    final2 = backend2.reconstruct_params[1]
    assert first2.get("ext_lo") == "11.0"
    assert first2.get("ext_hi") == "5.5"
    assert "final_ext_lo" not in first2
    assert final2["ext_lo"] == "11.0"
    assert result2["spectrum_path"]


def test_unified_route_uniform_optimization_passes_plan(
    tmp_path: Path, monkeypatch, bruker_dir: Path
) -> None:
    """0.2.163-patch8: The joint/candidate process call of uniform processing parameter
    optimisation must carry ProcessingPlan (fix plan=None causing spectrum generation to fail)."""
    from core.planning.processing_plan import ProcessingPlan

    experiment = read_dataset(bruker_dir / "hsqc_2d")
    backend = _FakeBackend(tmp_path / "uni_plan_work")
    work = backend.work

    def fake_read(path: str, unpack_axis: int | None = None):
        name = Path(path).name
        if "F1" in name:
            return _synthetic_preview(0, -25.0)
        if "F2" in name:
            return _synthetic_preview(1, -35.0)
        return _synthetic_preview(0, 0.0)

    monkeypatch.setattr(routes, "_read_complex_preview", fake_read)
    routes.unified_route(experiment, backend, work_dir=work)
    # Joint and candidate process calls (3rd, 4th, 5th times) all have non-None plan.
    for call in backend.process_calls[2:]:
        assert call[1] is not None
        assert isinstance(call[1], ProcessingPlan), type(call[1])


def test_unified_route_uniform_runs_processing_optimization(
    tmp_path: Path, monkeypatch, bruker_dir: Path
) -> None:
    """0.2.163-patch6: uniform processing parameter optimisation -- baseline / direct dimension
    window / zero filling + indirect window candidate score, final running parameter with
    optimisation result; symmetrical with NUS."""
    from workflow.baseline_optimize import BaselineOptimizeResult
    from workflow.window_optimize import WindowOptimizeResult

    experiment = read_dataset(bruker_dir / "hsqc_2d")
    backend = _FakeBackend(tmp_path / "uni_opt_work")
    work = backend.work
    # Provides scoreable synthetic spectra (baseline/window optimisation reading file).
    for name in ("hsqc_2d_joint.ft2", "hsqc_2d_final.ft2"):
        path = work / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")

    def fake_read(path: str, unpack_axis: int | None = None):
        name = Path(path).name
        if "F1" in name:
            return _synthetic_preview(0, -25.0)
        if "F2" in name:
            return _synthetic_preview(1, -35.0)
        return _synthetic_preview(0, 0.0)

    monkeypatch.setattr(routes, "_read_complex_preview", fake_read)
    # Baseline/window optimisation is downgraded to the existing configuration (false spectrum is
    # unreadable), verification process and final run parameter wiring.
    monkeypatch.setattr(
        "workflow.baseline_optimize.optimize_baseline",
        lambda experiment, path, **kwargs: BaselineOptimizeResult(
            baseline={"F1": {"enabled": True, "mode": "order", "order": 2},
                      "F2": {"enabled": False}},
            scores={},
            spectrum_path=str(path),
            logs=["Test baseline"],
            optimized=["F1"],
        ),
    )
    monkeypatch.setattr(
        "workflow.window_optimize.optimize_direct_window_from_work",
        lambda work, experiment, current=None: WindowOptimizeResult(
            choice={"type": "sine_bell", "off": 0.45, "end": 0.95},
            changed=True,
            logs=["Test direct dimension window"],
        ),
    )
    from workflow.window_optimize import MultiWindowOptimizeResult

    monkeypatch.setattr(
        "workflow.window_optimize.optimize_indirect_windows_from_work",
        lambda work, experiment, current=None: MultiWindowOptimizeResult(
            choice={"F1": {"type": "none"}},
            changed=True,
            logs=["indirect dimension window (test): F1 no window"],
        ),
    )
    result = routes.unified_route(experiment, backend, work_dir=work)
    # Final run call (last) with optimisation result.
    final_params = backend.process_calls[-1][2]
    assert final_params["baseline"]["F1"]["order"] == 2
    assert final_params["baseline"]["F2"]["enabled"] is False
    assert final_params["window"]["F2"]["type"] == "sine_bell"
    assert final_params["window"]["F1"]["type"] == "none"
    assert final_params["direct_poly_time"] is False
    assert result["baseline"]["F1"]["order"] == 2
    assert result["window"]["F2"]["type"] == "sine_bell"
    assert "diagnostics" in result
    # 0.2.166: uniform summary and NUS symmetry -- baseline/window/zero filling/diagnosis into
    # "spectrum quality and data quality report" (0.2.169-fill readability: ◆ section +
    # comprehensive judgment + sub-item level).
    assert "== spectrum quality and data quality report ==" in result["logs"]
    assert any("◆ Final spectrum image quality" in line for line in result["logs"])
    assert any(" Baseline:" in line for line in result["logs"])
    assert any(" Window function:" in line for line in result["logs"])
    assert any(" zero filling:" in line for line in result["logs"])
    assert any("◆ Data quality diagnosis" in line for line in result["logs"])
    # 0.2.166: uniform script is retained for the first time (joint script copy, cleanup does not
    # clear it).
    no_opt = work / "hsqc_2d_before_optimize.com"
    assert no_opt.is_file()
    assert "# fake script hsqc_2d_joint.com" in no_opt.read_text(encoding="utf-8")
    assert any("first-run script kept as" in line for line in result["logs"])


def test_unified_route_uniform_final_ext_apply_to_opt(
    tmp_path: Path, monkeypatch, bruker_dir: Path
) -> None:
    """0.2.162-patch15/0.2.199-patch3: Uniform defaults to the same preview/optimisation/final run
    window when turned on, and only the final run application scope when turned off."""
    experiment = read_dataset(bruker_dir / "hsqc_2d")
    backend = _FakeBackend(tmp_path / "uni_ext_work")
    work = backend.work

    def fake_read(path: str, unpack_axis: int | None = None):
        name = Path(path).name
        if "F1" in name:
            return _synthetic_preview(0, -25.0)
        if "F2" in name:
            return _synthetic_preview(1, -35.0)
        return _synthetic_preview(0, 0.0)

    monkeypatch.setattr(routes, "_read_complex_preview", fake_read)
    # Turn off "Apply this range to the optimisation process": keep the default window for the first
    # pass preview and only use this range for the final run.
    result = routes.unified_route(
        experiment,
        backend,
        work_dir=work,
        base_params={
            "final_ext_lo": "11.0",
            "final_ext_hi": "5.5",
            "apply_ext_to_opt": "0",
        },
    )
    # 0.2.163-patch6: uniform processing parameter optimisation, run joint review spectrum first;
    # 0.2.199-patch29dn/patch29dr: Add F1 to re-search preview -> 5 times process.
    assert len(backend.process_calls) == 5
    preview1, preview2, preview_r2, _joint, final = backend.process_calls
    for preview in (preview1, preview2, preview_r2):
        assert preview[2].get("ext_lo") is None
        assert "final_ext_lo" not in preview[2]
    assert final[2]["ext_lo"] == "11.0"
    assert final[2]["ext_hi"] == "5.5"
    assert "final_ext_lo" not in final[2]
    assert result["spectrum_path"]
    # Enabled by default: replication preview / optimisation Evaluate/Run in the same window.
    backend2 = _FakeBackend(tmp_path / "uni_ext_work_on")
    result2 = routes.unified_route(
        experiment,
        backend2,
        work_dir=backend2.work,
        base_params={"final_ext_lo": "11.0", "final_ext_hi": "5.5"},
    )
    # 0.2.199-patch29dn/patch29dr: Added F1 re-search preview -> 5 times process.
    assert len(backend2.process_calls) == 5
    p1b, p2b, p_r2b, jointb, finalb = backend2.process_calls
    for call in (p1b, p2b, p_r2b, jointb, finalb):
        assert call[2].get("ext_lo") == "11.0"
        assert call[2].get("ext_hi") == "5.5"
    assert "final_ext_lo" not in p1b[2]
    assert result2["spectrum_path"]


def test_renormalize_direct_p1_scales_with_window_width() -> None:
    """0.2.162-patch16:p1 Press to end the run/First pass window width scaling, p0 remains
    unchanged; the window remains the same."""
    first = {"ext_lo": "10.5", "ext_hi": "6.5"}
    narrow = {"ext_lo": "8.5", "ext_hi": "6.5"}
    assert routes._renormalize_direct_p1((30.0, 20.0), first, narrow) == (
        30.0,
        10.0,
    )
    assert routes._renormalize_direct_p1((30.0, 20.0), first, first) == (
        30.0,
        20.0,
    )
    # When the first pass is not explicitly given to the window, the configuration falls back to the
    # default (10.5-6.5=4.0), and the ratio is also true.
    assert routes._renormalize_direct_p1((30.0, 20.0), {}, narrow) == (
        30.0,
        10.0,
    )


def test_unified_route_nus_final_ext_renormalizes_p1(
    tmp_path: Path, monkeypatch, bruker_dir: Path
) -> None:
    """0.2.162-patch16: When "Apply this range to the optimisation process" is turned off, the
    final run window becomes narrower, and the direct dimension p1 is renormalized according to
    the window width and reported; when turned on (default), the same window is not scaled in
    the first pass."""
    from types import SimpleNamespace

    experiment = read_dataset(bruker_dir / "nus_2d")
    backend = _FakeBackend(tmp_path / "nus_p1_work")
    work = backend.work
    monkeypatch.setattr(
        "workflow.direct_diagnostics.run_direct_diagnostics",
        lambda wk, exp: SimpleNamespace(
            reports=[],
            metrics={},
            apply_poly_time=False,
            repaired_badpoints=0,
            backup_dir="",
        ),
    )
    n_direct, n_t1 = 64, 32
    k0 = np.arange(n_direct, dtype=float)
    t1 = np.arange(n_t1, dtype=float)
    direct = 1.0 / (1.0 + 1j * (k0 - 22) / 1.5)
    fid1 = np.exp(-t1 / 8.0) * np.cos(2.0 * np.pi * 8.0 * t1 / n_t1)
    planes = np.outer(direct, fid1)
    monkeypatch.setattr(routes, "_load_recon_planes", lambda exp, wk: planes)
    monkeypatch.setattr(
        "core.optimization.phase_consensus.search_direct_phase_real_ht",
        lambda arr, axis=-1, sign_mode="uniform", **kwargs: (30.0, 15.0, 80.0),
    )
    monkeypatch.setattr(
        routes, "_read_real_ft3",
        lambda path: np.zeros((32, 64), dtype=float),
    )
    monkeypatch.setattr(
        routes,
        "_read_complex_preview",
        lambda path, unpack_axis=None: _synthetic_preview(0, 0.0),
    )
    monkeypatch.setattr(
        routes,
        "_optimize_nus_processing",
        lambda exp, backend, work, fixed, base_params, progress=None: {
            "baseline": {},
            "zero_fill": {},
            "window": None,
            "logs": [],
        },
    )
    result = routes.unified_route(
        experiment,
        backend,
        work_dir=work,
        base_params={
            "final_ext_lo": "8.5",
            "final_ext_hi": "6.5",
            "apply_ext_to_opt": "0",
        },
    )
    final_params = backend.reconstruct_params[1]
    # First pass window 10.5-6.5=4.0, final run 8.5-6.5=2.0 -> p1 15° -> 7.5°; p0 remains unchanged.
    assert final_params["direct_phase_override"] == [30.0, 7.5]
    assert result["direct_phase"] == (30.0, 7.5)
    assert any(
        "renormalized by final run window" in line and "7.5" in line
        for line in result["logs"]
    )
    # Enabled by default: the first pass of reconstruction is in the same window as the final run
    # (8.5-6.5), p1 is not renormalized.
    backend2 = _FakeBackend(tmp_path / "nus_p1_work_on")
    result2 = routes.unified_route(
        experiment,
        backend2,
        work_dir=backend2.work,
        base_params={"final_ext_lo": "8.5", "final_ext_hi": "6.5"},
    )
    final2 = backend2.reconstruct_params[1]
    assert final2["direct_phase_override"] == [30.0, 15.0]
    assert result2["direct_phase"] == (30.0, 15.0)
    assert not any(
        "window renormalization" in line for line in result2["logs"]
    )


def test_unified_route_uniform_progress_stages(
    tmp_path: Path, monkeypatch, bruker_dir: Path
) -> None:
    """Uniform:progress overrides F1/F2 to replicate preview and final runs."""
    experiment = read_dataset(bruker_dir / "hsqc_2d")
    backend = _FakeBackend(tmp_path / "uni_prog_work")
    work = backend.work

    def fake_read(path: str, unpack_axis: int | None = None):
        name = Path(path).name
        if "F1" in name:
            return _synthetic_preview(0, -25.0)
        return _synthetic_preview(1, -35.0)

    monkeypatch.setattr(routes, "_read_complex_preview", fake_read)
    messages: list[str] = []
    routes.unified_route(experiment, backend, work_dir=work, progress=messages.append)
    joined = "\n".join(messages)
    assert "Replica preview in progress" in joined, messages
    assert "F1 Replica preview completed" in joined, messages
    assert "F2 Replica preview in progress" in joined, messages
    assert "F2 Replica preview completed" in joined, messages
    assert "Final run (complete rerun) in progress" in joined, messages
    assert "Final run completed" in joined, messages


def test_finalize_nus_progress_callback(tmp_path: Path, monkeypatch, bruker_dir: Path) -> None:
    """Finalize_nus Call directly: progress overrides start finalize and finalize completion."""
    from backend.nmrpipe_backend import NMRPipeBackend
    from backend.runtime import CompletedProcess

    experiment = read_dataset(bruker_dir / "nus_2d")
    work = tmp_path / "fw_work"
    (work / "nus2d").mkdir(parents=True)
    (work / "nus2d" / "recon.ft1").write_bytes(b"x")

    class _FakeCsh:
        def run(self, argv, *, cwd=None, timeout=3600, on_line=None):
            (Path(cwd) / f"{experiment.dataset_id}.ft2").write_bytes(b"x")
            return CompletedProcess("", "", "", 0)

    monkeypatch.setattr("backend.nmrpipe_backend.CshRuntime", _FakeCsh)
    monkeypatch.setattr(
        "backend.nmrpipe_backend.find_nmrpipe_bin", lambda explicit="": Path("/bin")
    )
    messages: list[str] = []
    backend = NMRPipeBackend(nmrpipe_bin="")
    resp = backend.finalize_nus(experiment, work_dir=work, progress=messages.append)
    assert resp["success"] is True, resp
    # 0.2.199-patch29eb:finalize no longer outputs the generalization progress (the caller outputs
    # the specific "phase optimisation:...").
    assert messages == []

def test_split_final_ext_apply_to_opt_default_on() -> None:
    """0.2.199-patch3: "Apply this range to the optimisation process" is enabled by default,
    parameter transparent transmission analysis."""
    from workflow.phase_routes import _split_final_ext

    p, lo, hi, apply = _split_final_ext(
        {"final_ext_lo": "8.0", "final_ext_hi": "6.0"}
    )
    assert (lo, hi, apply) == ("8.0", "6.0", True)
    assert "apply_ext_to_opt" not in p
    assert "final_ext_lo" not in p
    p, lo, hi, apply = _split_final_ext(
        {"final_ext_lo": "8.0", "apply_ext_to_opt": "0"}
    )
    assert (lo, hi, apply) == ("8.0", None, False)
    p, lo, hi, apply = _split_final_ext({"apply_ext_to_opt": "off"})
    assert (lo, hi, apply) == (None, None, False)


def test_direct_phase_cache_roundtrip(tmp_path: Path) -> None:
    """NO QUERY SPECIFIED. EXAMPLE REQUEST: GET?Q=HELLO&LANGPAIR=EN|IT."""
    from core.data.internal_data_model import (
        AxisRole,
        Dimension,
        Experiment,
        Sampling,
    )
    from workflow.phase_routes import (
        _direct_phase_params_fp,
        _load_direct_phase_cache,
        _save_direct_phase_cache,
    )

    dims = [
        Dimension(logical_axis="F3", nucleus="1H", role=AxisRole.DIRECT),
        Dimension(logical_axis="F2", nucleus="15N", role=AxisRole.INDIRECT),
        Dimension(logical_axis="F1", nucleus="13C", role=AxisRole.INDIRECT),
    ]
    exp = Experiment(
        dataset_id="x",
        source_path=Path("x"),
        ndim=3,
        dimensions=dims,
        sampling=Sampling(),
        acquisition_parameters={},
        processing_state={},
        segments=[],
    )
    params = {"ext_lo": 10.5, "ext_hi": 6.5, "extract": True}
    shape = (20, 16, 12)
    fp = _direct_phase_params_fp(exp, params)
    assert fp == _direct_phase_params_fp(exp, dict(params))
    assert fp != _direct_phase_params_fp(exp, {"ext_lo": 9.0, "ext_hi": 7.0})

    _save_direct_phase_cache(
        tmp_path, exp, params, shape, 12.5, -3.0, 40.0, 22.3
    )
    data = _load_direct_phase_cache(tmp_path, exp, params, shape)
    assert data is not None
    assert float(data["p0"]) == 12.5
    assert float(data["p1"]) == -3.0
    assert float(data["duration_s"]) == 22.3
    assert _load_direct_phase_cache(tmp_path, exp, params, (21, 16, 12)) is None
    assert (
        _load_direct_phase_cache(
            tmp_path, exp, {"ext_lo": 9.0, "ext_hi": 7.0}, shape
        )
        is None
    )
    # 0.2.166: direct dimension window affects the reconstruction plane, cache fingerprint must
    # contain window.
    assert (
        _load_direct_phase_cache(
            tmp_path,
            exp,
            {
                "ext_lo": 10.5,
                "ext_hi": 6.5,
                "extract": True,
                "window": {"F3": {"type": "sine_bell"}},
            },
            shape,
        )
        is None
    )



def test_append_final_summary_readable_report(tmp_path: Path) -> None:
    """0.2.169 - Supplement: The end report is readable -- ◆ Sectionalization, comprehensive
    judgment, sub-item level, baseline uneven reasons, data quality diagnosis conclusion (Check
    out/Processed automatically)."""
    import nmrglue as ng
    import numpy as np

    from workflow.phase_routes import _append_final_summary

    path = tmp_path / "r.ft2"
    n1, n2 = 128, 64
    data = np.zeros((n2, n1), dtype=np.float32)
    data += (100.0 * np.linspace(0.0, 1.0, n1)).astype(np.float32)[None, :]
    data[30, 60] = 1000.0
    dic = {k: "0" for k in ng.fileio.pipe.fdata_dic}
    dic["FDMAGIC"] = 9.2330230000000007e14
    dic["FDDIMCOUNT"] = 2
    dic["FDSIZE"] = n1
    dic["FDSPECNUM"] = n2
    dic["FDQUADFLAG"] = 1
    dic["FDF1QUADFLAG"] = 1
    dic["FDF2QUADFLAG"] = 1
    dic["FDTRANSPOSED"] = 0
    for prefix, size in (("FDF1", n2), ("FDF2", n1)):
        dic[prefix + "T"] = size
        dic[prefix + "SW"] = 6000.0
        dic[prefix + "OBS"] = 600.0
        dic[prefix + "CAR"] = 4.7
        dic[prefix + "ORIG"] = 4.7 * 600.0
    ng.pipe.write(str(path), dic, data, overwrite=True)
    logs: list[str] = []
    _append_final_summary(
        logs,
        str(path),
        direct_axis="F2",
        phases={"F1": (0.0, 0.0), "F2": (0.0, 0.0)},
        baseline={"F1": {"enabled": False}, "F2": {"enabled": False}},
        zero_fill={"F1": {"mode": "auto"}, "F2": {"mode": "auto"}},
        window=None,
        diagnostics={
            "reports": [
                    "direct dimension There is a DC offset (DC peak is 1.20 times the strongest "
                    "signal), POLY -time automatic correction is enabled",
                "2 peak bad points were detected and have been automatically replaced",
            ],
            "apply_poly_time": True,
            "repaired_badpoints": 2,
        },
        optimization_logs=[
            "F1: The candidate is not better than the current configuration, keep off (score=72.3)",
            "Baseline optimisation summary: F1/F2 all remain off",
        ],
    )
    text = "\n".join(logs)
    assert "== spectrum quality and data quality report ==" in text
    assert "◆ Final spectrum image quality" in text
    assert "Comprehensive judgment:" in text
    assert "- baseline: Needs attention" in text or "- baseline: Poor" in text
    assert "Baseline indicators" in text
    assert "Reasons for uneven baseline:" in text
    assert "Baseline optimisation summary: F1/F2 all remain off" in text
    assert "stays off" in text
    assert "◆ Data quality diagnosis" in text
    assert "issue(s) detected" in text
    assert "◆ processing parameters and optimisation" in text


def test_cleanup_unified_intermediates(tmp_path: Path) -> None:
    """After cleaning, the intermediate products have been deleted, but the reserved items are
    still there."""
    from workflow.phase_routes import _cleanup_unified_intermediates

    proc = tmp_path / "process"
    proc.mkdir()
    dataset_id = "d_001"

    # === Intermediate products (should be deleted) ===.
    intermediates = [
        # preview
        proc / f"{dataset_id}_preview_F1.com",
        proc / f"{dataset_id}_preview_F1.ft2",
        proc / f"{dataset_id}_preview_F1.ft3",
        proc / f"{dataset_id}_preview_F1.fdf",
        proc / f"{dataset_id}_preview_F2.com",
        proc / f"{dataset_id}_preview_F2.ft2",
        proc / f"{dataset_id}_preview_F2.fdf",
        proc / f"{dataset_id}_preview_F3.com",
        proc / f"{dataset_id}_preview_F3.ft3",
        # joint
        proc / f"{dataset_id}_joint.ft3",
        proc / f"{dataset_id}_joint.fdf",
        proc / f"{dataset_id}_joint_finalize.com",
        # win
        proc / f"{dataset_id}_win1.ft3",
        proc / f"{dataset_id}_win1.fdf",
        proc / f"{dataset_id}_win1_finalize.com",
        proc / f"{dataset_id}_win2.ft3",
        proc / f"{dataset_id}_win2_finalize.com",
        proc / f"{dataset_id}_win3.ft3",
        proc / f"{dataset_id}_win3_finalize.com",
        # 0.2.199-patch29em: Clean up the old zero filling/window candidate legacy (winzf) together.
        proc / f"{dataset_id}_winzf_auto.ft2",
        proc / f"{dataset_id}_winzf_auto.com",
        proc / f"{dataset_id}_winzf_1×TD.ft2",
        proc / f"{dataset_id}_winzf_2×TD.ft2",
        # 0.2.199-patch29dy:NUS reconstruction middle directory is not left.
        proc / "nus3d_1" / "stage1.ft1",
        proc / "nus3d_rc" / "test0001.ft1",
        proc / "nus3d_rc_ph" / "test0001.ft1",
        proc / "nus2d" / "recon.ft1",
    ]
    for p in intermediates:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")

    # === Reserved (should not be deleted) ===.
    kept = [
        proc / f"{dataset_id}_nus.com",
        proc / f"{dataset_id}_process.com",
        proc / f"{dataset_id}_finalize.com",
        proc / "phase.json",
        proc / "fid" / "test001.fid",
        proc / "spectra" / f"{dataset_id}.ft3",
        # Keep the _c* / _j* old intermediate products with prob 0 (0.2.77 clean up the range, not
        # touched this time).
        proc / f"{dataset_id}_c_0.ft3",
        proc / f"{dataset_id}_j_0.ft3",
    ]
    for p in kept:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")

    # 0.2.199-patch29em: Backend default working directory fallback (.nmrpipe) deletes the whole.
    fallback = tmp_path / f"{dataset_id}.nmrpipe"
    fallback.mkdir()
    (fallback / f"{dataset_id}_preview_F1.ft2").write_text("x")
    (fallback / f"{dataset_id}_joint.ft2").write_text("x")
    (fallback / f"{dataset_id}_winzf_auto.ft2").write_text("x")

    _cleanup_unified_intermediates(proc, dataset_id)

    # Verify that the intermediate product has been deleted.
    for p in intermediates:
        assert not p.exists(), f"The intermediate product is not deleted: {p}"

    # Verify that the hold is still there.
    for p in kept:
        assert p.exists(), f"Reserved item deleted by mistake: {p}"

    # Verify that.nmrpipe fallback directory has been deleted.
    assert not fallback.exists(), f".nmrpipe fallback directory is not deleted: {fallback}"

    # Verify that directory does not exist without reporting an error.
    _cleanup_unified_intermediates(tmp_path / "nonexistent", dataset_id)


def test_load_preview_memory_error_hint(tmp_path, monkeypatch) -> None:
    # 29ec: MemoryError on preview load -> clear RuntimeError hint.
    from workflow.phase_routes import _load_preview_with_memory_guard

    fake = tmp_path / "preview.ft3"
    fake.write_bytes(b"x" * 1024)

    def boom(path, unpack_axis=None):
        raise MemoryError

    monkeypatch.setattr(
        "workflow.phase_routes._read_complex_preview", boom
    )
    import pytest

    with pytest.raises(RuntimeError) as ei:
        _load_preview_with_memory_guard(str(fake), axis="F1")
    msg = str(ei.value)
    assert "Out of memory" in msg  # oom prefix
    assert "F1" in msg
