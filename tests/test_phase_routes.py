"""统一相位途径(unified_route)编排测试。"""

from __future__ import annotations

from pathlib import Path

import numpy as np

import workflow.phase_routes as routes
from core.data.bruker_reader import read_dataset


def _synthetic_preview(axis: int, p0: float) -> np.ndarray:
    """复型 Lorentzian:被评轴复型 + 已知相位,其它轴实型(与内存搜索测试同构)。"""
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
        path = str(self.work / (out_file or "final.ft2"))
        Path(path).write_bytes(b"x")
        return {"success": True, "spectrum_path": path, "logs": []}

    def reconstruct_nus(self, experiment, params, progress=None):
        self.reconstruct_params.append(dict(params or {}))
        # 模拟真实后端:每次调用写 {dataset_id}_nus.com(供初跑脚本保留断言)
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


def test_unified_route_uniform_order_and_phases(
    tmp_path: Path, monkeypatch, bruker_dir: Path
) -> None:
    """uniform:先 F1 后 F2(旧算法顺序);F2 预览携带已固定 F1 相位;终跑带全相位。"""
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
    # 0.2.163-补6:uniform 处理参数优化先跑 joint 复核谱
    # (F1 预览 + F2 预览 + joint + 终跑)
    assert len(backend.process_calls) == 4
    preview1, preview2, joint, final = backend.process_calls
    assert joint[2].get("preview_axis") is None  # joint 不是预览
    assert preview1[2].get("preview_axis") == "F1"
    assert preview1[3] == {}  # 首个轴无固定相位
    assert preview2[2].get("preview_axis") == "F2"
    assert set(preview2[3]) == {"F1"}  # F1 已固定后传给 F2 预览
    assert set(final[3]) == {"F1", "F2"}
    phases = result["phases"]
    assert abs((phases["F1"][0] - 25.0 + 180.0) % 360.0 - 180.0) <= 8.0, phases
    assert abs((phases["F2"][0] - 35.0 + 180.0) % 360.0 - 180.0) <= 8.0, phases
    assert result["backend_runs"] == 3
    assert "spectrum_path" in result


def test_disambiguate_180_mixed_uses_region_sign_convention() -> None:
    """±180° 化学位移分区消歧:预设 Cα 负/Cβ 正——相位 0 不翻,相位 180 翻回。"""
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
    for target in (50.0, 55.0, 60.0, 65.0):  # Cα 区(40-70),期望负
        i = int(np.argmin(np.abs(ppm - target)))
        arr[:, i] += -1.0
    for target in (18.0, 25.0, 30.0, 40.0):  # Cβ 区(15-45),期望正
        i = int(np.argmin(np.abs(ppm - target)))
        arr[:, i] += 1.0
    assert _disambiguate_180_mixed(arr, 1, (0.0, 0.0), exp, "F1") == (0.0, 0.0)
    flipped = _disambiguate_180_mixed(arr, 1, (180.0, 0.0), exp, "F1")
    assert abs((flipped[0] - 0.0 + 180.0) % 360.0 - 180.0) < 1e-6, flipped
    # 15N 轴无分区先验,不消歧
    assert _disambiguate_180_mixed(arr, 0, (90.0, 0.0), exp, "F2") == (90.0, 0.0)


def test_unified_route_nus_reconstruct_then_finalize(
    tmp_path: Path, monkeypatch, bruker_dir: Path
) -> None:
    """NUS:SMILE 首遍(关搜索)→ 直接维对称性调相 → 间接维 finalize 复型预览
    + 内存搜索 → 联合复核 → 处理参数优化 → 终跑为完整脚本(各维相位填入,
    不写 nus3d_rc_ph 旋转副本)。"""
    from types import SimpleNamespace

    experiment = read_dataset(bruker_dir / "nus_2d")
    backend = _FakeBackend(tmp_path / "nus_work")
    work = backend.work
    # 0.2.156:诊断检测到直流偏置 → 第一遍与终跑脚本均带 direct_poly_time
    monkeypatch.setattr(
        "workflow.direct_diagnostics.run_direct_diagnostics",
        lambda wk, exp: SimpleNamespace(
            reports=["直流偏置: 自动启用 POLY -time"],
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
        "core.optimization.phase_search.search_direct_phase_on_spectrum",
        lambda arr, axis=0, metric="symmetry", progress=None: (30.0, 0.0, 80.0),
    )

    def fake_read(path: str, unpack_axis: int | None = None):
        name = Path(path).name
        if "preview_F1" in name:
            return _synthetic_preview(0, -30.0)
        return _synthetic_preview(0, 0.0)

    monkeypatch.setattr(routes, "_read_complex_preview", fake_read)
    # 处理参数优化固定返回(基线/填零/窗),不依赖假后端写谱
    monkeypatch.setattr(
        routes,
        "_optimize_nus_processing",
        lambda exp, backend, work, fixed, base_params, progress=None: {
            "baseline": {"F2": {"enabled": False}},
            "zero_fill": {"F1": {"mode": "auto"}, "F2": {"mode": "auto"}},
            "window": None,
            "logs": ["处理参数优化(测试): 固定配置"],
        },
    )
    result = routes.unified_route(experiment, backend, work_dir=work)
    assert len(backend.reconstruct_params) == 2  # SMILE 首遍 + 终跑完整脚本
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
    # 末遍不写旋转平面副本:无 _apply_direct_phase 调用,无 planes 覆盖
    assert not backend.apply_direct_calls
    assert all(call["planes"] is None for call in backend.finalize_calls)
    assert "处理参数优化(测试): 固定配置" in result["logs"]
    assert result["backend_runs"] == 3  # SMILE 首遍 + F1 预览 + 终跑
    # 0.2.155/0.2.160:诊断检测到直流偏置时,终跑脚本携带
    # direct_poly_time(POLY -time);首遍脚本不加(避免带偏直接维相位搜索);
    # 日志含分步耗时与末尾汇总
    assert "direct_poly_time" in final_params
    assert backend.reconstruct_params[0].get("direct_poly_time") is None
    assert final_params.get("direct_poly_time") is True
    assert "== 质量与优化汇总 ==" in result["logs"]
    assert any("谱图质量" in line for line in result["logs"])
    assert any("相位搜索完成,耗时" in line for line in result["logs"])
    assert any("终跑完成,耗时" in line for line in result["logs"])
    # 0.2.156:初跑脚本保留为 {dataset_id}_before_optimize.com(内容=第一遍脚本)
    no_opt = work / f"{experiment.dataset_id}_before_optimize.com"
    assert no_opt.is_file()
    assert "# fake nus script #1" in no_opt.read_text(encoding="utf-8")
    assert "# fake nus script #2" not in no_opt.read_text(encoding="utf-8")
    assert any("初跑脚本保留" in line for line in result["logs"])



def test_optimize_nus_processing_baseline_and_window(
    tmp_path: Path, monkeypatch, bruker_dir: Path
) -> None:
    """处理参数优化:基线内存评分写回 + 间接维窗函数候选评分择优(不重跑 SMILE)。"""
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
        logs=["F1: 基线已优化"],
        optimized=["F1"],
        skipped=[],
    )
    monkeypatch.setattr(
        "workflow.baseline_optimize.optimize_baseline",
        lambda experiment, spectrum_path, **kw: fake_opt,
    )
    scores = iter([40.0, 55.0, 48.0, 50.0])  # 基底/正弦钟/正弦钟²/高斯
    monkeypatch.setattr(
        "core.qc.spectrum_quality.evaluate",
        lambda data, min_shape=None: type(
            "Q", (), {"score": type("S", (), {"overall": next(scores)})()}
        )(),
    )
    proc = routes._optimize_nus_processing(
        experiment,
        type("B", (), {"finalize_nus": staticmethod(fake_finalize)})(),
        work,
        fixed,
        None,
    )
    assert proc["baseline"]["F1"]["order"] == 2
    # 55 最优且 > 基底 40 + 0.5 → 选中正弦钟,填入间接维
    assert proc["window"] == {
        "F2": {"type": "sine_bell"},
        "F1": {"type": "sine_bell"},
    }
    assert "F1: 基线已优化" in proc["logs"]
    # 联合复核谱(1) + 间接维基线重渲(1) + 3 个窗候选
    assert len(calls) == 5
    assert calls[1]["baseline"]["F1"]["order"] == 2
    assert calls[2]["params"]["window"] == {
        "F2": {"type": "sine_bell"},
        "F1": {"type": "sine_bell"},
    }


def test_unified_route_nus_progress_stages(
    tmp_path: Path, monkeypatch, bruker_dir: Path
) -> None:
    """NUS:progress 覆盖 第一遍 SMILE/处理参数优化/完整脚本终跑 各阶段。"""
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
        "core.optimization.phase_search.search_direct_phase_on_spectrum",
        lambda arr, axis=0, metric="symmetry", progress=None: (30.0, 0.0, 80.0),
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
    assert "第一遍 SMILE 完成" in joined, messages
    assert "F1 复型预览中" in joined, messages
    assert "F1 复型预览完成" in joined, messages
    assert "联合复核完成,开始处理参数优化(基线/填零/窗函数)" in joined, messages
    assert "终跑(完整脚本,含各维最终相位)中" in joined, messages
    assert "终跑完成" in joined, messages
    assert all(call["progress"] is not None for call in backend.finalize_calls)
    assert result["backend_runs"] == 3



def test_unified_route_nus_final_ext_only_applies_to_final_run(
    tmp_path: Path, monkeypatch, bruker_dir: Path
) -> None:
    """0.2.162-补15:final_ext_lo/hi 只进终跑完整脚本,首遍重构保持原窗口。"""
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
        "core.optimization.phase_search.search_direct_phase_on_spectrum",
        lambda arr, axis=0, metric="symmetry", progress=None: (30.0, 0.0, 80.0),
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
        base_params={"final_ext_lo": "11.0", "final_ext_hi": "5.5"},
    )
    first_params = backend.reconstruct_params[0]
    final_params = backend.reconstruct_params[1]
    # 首遍:终跑范围不落入,也不带新 ext(保持原窗口/配置默认)
    assert "final_ext_lo" not in first_params
    assert "final_ext_hi" not in first_params
    assert first_params.get("ext_lo") is None
    # 终跑:范围已映射为 ext_lo/ext_hi,私有键不残留
    assert final_params["ext_lo"] == "11.0"
    assert final_params["ext_hi"] == "5.5"
    assert "final_ext_lo" not in final_params
    assert result["spectrum_path"]


def test_unified_route_uniform_optimization_passes_plan(
    tmp_path: Path, monkeypatch, bruker_dir: Path
) -> None:
    """0.2.163-补8:uniform 处理参数优化的 joint/候选 process 调用必须
    携带 ProcessingPlan(修复 plan=None 导致生成谱图失败)。"""
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
    # joint 与候选 process 调用(第 3、4、5 次)均带非 None plan
    for call in backend.process_calls[2:]:
        assert call[1] is not None
        assert isinstance(call[1], ProcessingPlan), type(call[1])


def test_unified_route_uniform_runs_processing_optimization(
    tmp_path: Path, monkeypatch, bruker_dir: Path
) -> None:
    """0.2.163-补6:uniform 处理参数优化——基线/直接维窗/填零+间接窗
    候选评分,终跑参数带优化结果;与 NUS 对称。"""
    from workflow.baseline_optimize import BaselineOptimizeResult
    from workflow.window_optimize import WindowOptimizeResult

    experiment = read_dataset(bruker_dir / "hsqc_2d")
    backend = _FakeBackend(tmp_path / "uni_opt_work")
    work = backend.work
    # 提供可评分的合成谱(基线/窗优化读谱文件)
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
    # 基线/窗优化降级为现有配置(假谱不可读),验证流程与终跑参数接线
    monkeypatch.setattr(
        "workflow.baseline_optimize.optimize_baseline",
        lambda experiment, path: BaselineOptimizeResult(
            baseline={"F1": {"enabled": True, "mode": "order", "order": 2},
                      "F2": {"enabled": False}},
            scores={},
            spectrum_path=str(path),
            logs=["测试基线"],
            optimized=["F1"],
        ),
    )
    monkeypatch.setattr(
        "workflow.window_optimize.optimize_direct_window_from_work",
        lambda work, experiment, current=None: WindowOptimizeResult(
            choice={"type": "sine_bell", "off": 0.45, "end": 0.95},
            changed=True,
            logs=["测试直接维窗"],
        ),
    )
    result = routes.unified_route(experiment, backend, work_dir=work)
    # 终跑调用(最后一次)带优化结果
    final_params = backend.process_calls[-1][2]
    assert final_params["baseline"]["F1"]["order"] == 2
    assert final_params["baseline"]["F2"]["enabled"] is False
    assert final_params["window"]["F2"]["type"] == "sine_bell"
    assert final_params["direct_poly_time"] is False
    assert result["baseline"]["F1"]["order"] == 2
    assert result["window"]["F2"]["type"] == "sine_bell"
    assert "diagnostics" in result


def test_unified_route_uniform_final_ext_only_applies_to_final_run(
    tmp_path: Path, monkeypatch, bruker_dir: Path
) -> None:
    """0.2.162-补15:uniform 首遍复型预览不改窗口,终跑完整脚本应用范围。"""
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
    result = routes.unified_route(
        experiment,
        backend,
        work_dir=work,
        base_params={"final_ext_lo": "11.0", "final_ext_hi": "5.5"},
    )
    # 0.2.163-补6:uniform 处理参数优化先跑 joint 复核谱
    assert len(backend.process_calls) == 4  # F1 预览 + F2 预览 + joint + 终跑
    preview1, preview2, _joint, final = backend.process_calls
    for preview in (preview1, preview2):
        assert preview[2].get("ext_lo") is None
        assert "final_ext_lo" not in preview[2]
    assert final[2]["ext_lo"] == "11.0"
    assert final[2]["ext_hi"] == "5.5"
    assert "final_ext_lo" not in final[2]
    assert result["spectrum_path"]


def test_renormalize_direct_p1_scales_with_window_width() -> None:
    """0.2.162-补16:p1 按终跑/首遍窗口宽度比例缩放,p0 不变;窗口相同不变。"""
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
    # 首遍未显式给窗口时回退配置默认(10.5-6.5=4.0),比例同样成立
    assert routes._renormalize_direct_p1((30.0, 20.0), {}, narrow) == (
        30.0,
        10.0,
    )


def test_unified_route_nus_final_ext_renormalizes_p1(
    tmp_path: Path, monkeypatch, bruker_dir: Path
) -> None:
    """0.2.162-补16:终跑窗口变窄时,直接维 p1 按窗口宽度重归一化并进报告。"""
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
        "core.optimization.phase_search.search_direct_phase_on_spectrum",
        lambda arr, axis=0, metric="symmetry", progress=None: (30.0, 15.0, 80.0),
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
        base_params={"final_ext_lo": "8.5", "final_ext_hi": "6.5"},
    )
    final_params = backend.reconstruct_params[1]
    # 首遍窗口 10.5-6.5=4.0,终跑 8.5-6.5=2.0 → p1 15°→7.5°;p0 不变
    assert final_params["direct_phase_override"] == [30.0, 7.5]
    assert result["direct_phase"] == (30.0, 7.5)
    assert any(
        "窗口重归一化" in line and "7.5" in line for line in result["logs"]
    )


def test_unified_route_uniform_progress_stages(
    tmp_path: Path, monkeypatch, bruker_dir: Path
) -> None:
    """uniform:progress 覆盖 F1/F2 复型预览与终跑。"""
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
    assert "F1 复型预览中" in joined, messages
    assert "F1 复型预览完成" in joined, messages
    assert "F2 复型预览中" in joined, messages
    assert "F2 复型预览完成" in joined, messages
    assert "终跑(完整重跑)中" in joined, messages
    assert "终跑完成" in joined, messages


def test_finalize_nus_progress_callback(tmp_path: Path, monkeypatch, bruker_dir: Path) -> None:
    """finalize_nus 直接调用:progress 覆盖 开始 finalize 与 finalize 完成。"""
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
    assert "开始 finalize(复型预览/终跑)" in messages, messages
    assert "finalize 完成" in messages, messages

def test_direct_phase_cache_roundtrip(tmp_path: Path) -> None:
    '''直接维相位缓存:保存→加载命中;shape/参数变化则失效。'''
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



def test_cleanup_unified_intermediates(tmp_path: Path) -> None:
    """清理后中间产物已删,保留项仍在。"""
    from workflow.phase_routes import _cleanup_unified_intermediates

    proc = tmp_path / "process"
    proc.mkdir()
    dataset_id = "d_001"

    # === 中间产物(应被删) ===
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
    ]
    for p in intermediates:
        p.write_text("x")

    # === 保留项(不应被删) ===
    kept = [
        proc / f"{dataset_id}_nus.com",
        proc / f"{dataset_id}_process.com",
        proc / f"{dataset_id}_finalize.com",
        proc / "phase.json",
        proc / "fid" / "test001.fid",
        proc / "nus3d_rc" / "test0001.ft1",
        proc / "nus2d" / "recon.ft1",
        proc / "spectra" / f"{dataset_id}.ft3",
        # 保留 prob 为 0 的 _c* / _j* 旧中间产物(0.2.77 清理范围,本次不碰)
        proc / f"{dataset_id}_c_0.ft3",
        proc / f"{dataset_id}_j_0.ft3",
    ]
    for p in kept:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")

    _cleanup_unified_intermediates(proc, dataset_id)

    # 验证中间产物已删
    for p in intermediates:
        assert not p.exists(), f"中间产物未删: {p}"

    # 验证保留项仍在
    for p in kept:
        assert p.exists(), f"保留项被误删: {p}"

    # 验证不存在的目录不报错
    _cleanup_unified_intermediates(tmp_path / "nonexistent", dataset_id)
