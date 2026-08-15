"""方案 B 相位门控测试(0.2.47):平坦回退/联合复核/可复现性/SMILE 复用。"""

from __future__ import annotations

import json
from pathlib import Path

from core.data.bruker_reader import read_dataset
from workflow.phase_optimize import (
    _joint_recheck,
    _reproducibility_check,
    optimize_phase_sequential,
)


def _write_ft2(path: Path, data=None) -> None:
    """写一个最小可读 2D ft2(单峰,供 phase_quality 评估)。"""
    import numpy as np
    from nmrglue.fileio import pipe

    if data is None:
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
    pipe.write(str(path), dic, np.asarray(data, dtype=np.float32), overwrite=True)


class _GatingBackend:
    """记录调用的假后端;process 按覆盖相位生成谱路径供评分。"""

    def __init__(self, work_dir: Path) -> None:
        self.work_dir = str(work_dir)
        self.calls: list[dict] = []
        self.reconstruct_calls = 0
        self.spectrum_template = None

    def _path_for(self, override: dict) -> Path:
        parts = []
        for axis in ("F2", "F1", "F3"):
            if axis in override:
                parts.append(f"{axis}{int(override[axis][1])}")
        return Path(self.work_dir) / ("out_" + "_".join(parts) + ".ft2")

    def process(
        self, experiment, plan, direct_phase_override=None, params=None,
        out_file=None, script_name=None,
    ) -> dict:
        self.calls.append(dict(direct_phase_override or {}))
        path = self._path_for(direct_phase_override or {})
        _write_ft2(path)
        return {
            "success": True,
            "spectrum_path": str(path),
            "message": "ok",
            "logs": [],
        }

    def reconstruct_nus(self, experiment, params) -> dict:
        self.reconstruct_calls += 1
        path = Path(self.work_dir) / "out_nus.ft2"
        _write_ft2(path)
        return {
            "success": True,
            "spectrum_path": str(path),
            "message": "ok",
            "logs": [],
        }

    def finalize_nus(
        self, experiment, phases=None, work_dir=None, baseline=None,
        out_file=None, script_name=None,
    ) -> dict:
        self.calls.append(dict(phases or {}))
        path = self._path_for(phases or {})
        _write_ft2(path)
        return {
            "success": True,
            "spectrum_path": str(path),
            "message": "ok",
            "logs": [],
        }


def _score_peak30(path: str) -> tuple[float, dict[str, float]]:
    """从路径解析各轴 p1,真值 30° 处得分最高(可加性,顺序搜索即联合最优)。"""
    name = Path(path).stem
    score = 100.0
    for axis in ("F2", "F1", "F3"):
        marker = f"{axis}"
        if marker in name:
            p1 = float(name.split(marker)[1].split("_")[0])
            score -= abs(p1 - 30.0)
    return score, {}


def test_flat_margin_falls_back_to_zero(tmp_path: Path, bruker_dir: Path) -> None:
    """评分面平坦(margin<0.05)时回退 (0,0),不再应用低置信相位。"""
    experiment = read_dataset(bruker_dir / "hsqc_2d")
    backend = _GatingBackend(tmp_path / "work")

    def _flat(path: str) -> tuple[float, dict[str, float]]:
        return 50.0, {}  # 所有候选同分 → margin=0 → 平坦

    result = optimize_phase_sequential(
        experiment,
        backend,
        p0_values=(0.0,),
        p1_values=(-5.0, 0.0, 5.0),
        score_fn=_flat,
        refine=False,
    )
    assert result.phases == {"F2": (0.0, 0.0), "F1": (0.0, 0.0)}
    assert "已回退" in " ".join(result.logs)


def test_clear_margin_keeps_best(tmp_path: Path, bruker_dir: Path) -> None:
    """评分面明确时正常采用最优(门控不误伤)。"""
    experiment = read_dataset(bruker_dir / "hsqc_2d")
    backend = _GatingBackend(tmp_path / "work")
    result = optimize_phase_sequential(
        experiment,
        backend,
        p0_values=(0.0,),
        p1_values=(-60.0, 0.0, 30.0, 60.0),
        score_fn=_score_peak30,
        refine=False,
    )
    assert result.phases["F2"][1] == 30.0
    assert result.phases["F1"][1] == 30.0


def test_joint_recheck_finds_better_combination(tmp_path: Path) -> None:
    """联合复核:顺序固定非联合最优时,联合网格找到更高分组合。"""
    experiment = read_dataset(
        tmp_path / "x"
    ) if False else None
    from core.data.internal_data_model import (
        AxisRole,
        Dimension,
        Experiment,
        Sampling,
        SamplingMode,
    )

    experiment = Experiment(
        dataset_id="e",
        source_path=Path("/fake/1"),
        ndim=2,
        dimensions=[
            Dimension(logical_axis="F1", nucleus="15N", td=128, role=AxisRole.INDIRECT),
            Dimension(logical_axis="F2", nucleus="1H", td=1024, role=AxisRole.DIRECT),
        ],
        sampling=Sampling(mode=SamplingMode.UNIFORM),
    )
    backend = _GatingBackend(tmp_path / "work")

    def _joint_score(path: str) -> tuple[float, dict[str, float]]:
        # 联合最优在 (F2=5, F1=5),与顺序固定 (0,0) 差 10 分
        name = Path(path).stem
        f2 = float(name.split("F2")[1].split("_")[0]) if "F2" in name else 0.0
        f1 = float(name.split("F1")[1].split("_")[0]) if "F1" in name else 0.0
        return 100.0 - abs(f2 - 5.0) - abs(f1 - 5.0), {}

    from workflow.phase_optimize import select_method

    fixed = {"F2": (0.0, 0.0), "F1": (0.0, 0.0)}
    runs = [0]
    (
        best_phases,
        best_score,
        best_path,
        joint_runs,
        fixed_score,
        zero_score,
        zero_path,
    ) = _joint_recheck(
        experiment,
        backend,
        select_method(experiment),
        False,
        fixed,
        ["F2", "F1"],
        5.0,
        tmp_path / "work",
        _joint_score,
        runs,
        {},
    )
    assert best_phases == {"F2": (0.0, 5.0), "F1": (0.0, 5.0)}
    assert best_score == 100.0
    assert best_path
    assert joint_runs == 10  # 3×3 p1 组合 + 全零组合
    assert runs[0] == 10
    assert fixed_score < best_score
    assert zero_path


def test_reproducibility_check_consistent(tmp_path: Path) -> None:
    """可复现性:同一谱 top-K 子采样两次最优 p1 一致。"""
    p0 = tmp_path / "cand_p10.ft2"
    _write_ft2(p0)
    consistent, p1a, p1b = _reproducibility_check(
        [((0.0, 10.0), str(p0))], "F1", k=200
    )
    assert consistent
    assert p1a == 10.0 and p1b == 10.0


def test_profile_symmetry_axis_absorption_vs_dispersion() -> None:
    """一维剖面镜像相关:吸收(偶函数)≈+1,色散(奇函数)≈-1。"""
    import numpy as np

    from core.qc import phase_quality

    n = 64
    x = np.arange(n) - n / 2.0
    absorption = 1.0 / (1.0 + (x / 4.0) ** 2)
    dispersion = x / (1.0 + (x / 4.0) ** 2) / 4.0
    spec_abs = np.zeros((16, n))
    spec_abs[8, :] = absorption
    spec_dis = np.zeros((16, n))
    spec_dis[8, :] = dispersion
    s_abs = phase_quality.profile_symmetry_axis(spec_abs, 1)
    s_dis = phase_quality.profile_symmetry_axis(spec_dis, 1)
    assert s_abs > 0.5, s_abs
    # 色散剖面以正峰顶为中心,窗口不对称使相关≈0(非正);吸收显著正相关
    assert s_dis < 0.0, s_dis


def test_smile_planes_reuse_when_params_match(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """SMILE 复用:平面存在且 .nus_params.json 为空参数 → 不再调 reconstruct。"""
    experiment = read_dataset(bruker_dir / "nus_2d")
    work = tmp_path / "work"
    backend = _GatingBackend(work)
    planes = work / "nus2d"
    planes.mkdir(parents=True, exist_ok=True)
    _write_ft2(planes / "recon.ft1")
    (work / ".nus_params.json").write_text(
        json.dumps({}), encoding="utf-8"
    )
    result = optimize_phase_sequential(
        experiment,
        backend,
        p0_values=(0.0,),
        p1_values=(-60.0, 0.0, 30.0, 60.0),
        score_fn=_score_peak30,
        refine=False,
        work_dir=work,
    )
    assert backend.reconstruct_calls == 0
    assert any("复用" in line for line in result.logs)
    assert result.backend_runs == 1 * 4  # 仅 F1 间接维候选(finalize)
