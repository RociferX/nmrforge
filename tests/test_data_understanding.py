"""Phase 1 数据理解：读取 / 采样检测 / 采集模式 / 分类 / 轴映射。"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.data.bruker_reader import read_dataset
from core.data.internal_data_model import SamplingMode
from core.experiment.acquisition_mode_detector import detect_modes, ft_alt_for
from core.experiment.dimension_mapper import map_dimensions


def test_read_hsqc_2d_uniform(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "hsqc_2d")
    assert exp.ndim == 2
    assert exp.sampling.mode is SamplingMode.UNIFORM
    assert exp.experiment_type.name == "HSQC"
    assert exp.experiment_type.confidence >= 0.9
    direct = exp.direct_dimension
    assert direct is not None
    assert direct.nucleus == "1H"
    assert direct.td == 2048
    f1 = next(d for d in exp.dimensions if d.logical_axis == "F1")
    assert f1.nucleus == "15N"
    assert f1.td == 256


def test_read_nus_2d(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "nus_2d")
    assert exp.sampling.mode is SamplingMode.NUS
    assert exp.sampling.sampling_fraction == 0.25
    assert exp.sampling.nus_list
    assert exp.sampling.confidence >= 0.9


def test_read_hnca_3d(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "hnca_3d")
    assert exp.ndim == 3
    assert exp.experiment_type.name == "HNCA"
    nuclei = {d.logical_axis: d.nucleus for d in exp.dimensions}
    assert nuclei == {"F1": "13C", "F2": "15N", "F3": "1H"}


def test_read_unknown_2d_generic(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "unknown_2d")
    assert exp.experiment_type.name == "generic_2d"
    assert exp.experiment_type.confidence < 0.6


def test_detect_modes_and_ft_alt(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "hnca_3d")
    modes = detect_modes(exp)
    assert modes == {"F1": "Echo-Antiecho", "F2": "States-TPPI", "F3": "States"}
    assert ft_alt_for(5) is True
    assert ft_alt_for(4) is False


def test_map_dimensions_2d(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "hsqc_2d")
    mappings = map_dimensions(exp)
    by_axis = {m.processing_axis: m for m in mappings}
    assert by_axis["F2"].acquisition_axis == 1
    assert by_axis["F1"].acquisition_axis == 2
    assert by_axis["F2"].display_axis == "X"
    assert by_axis["F1"].display_axis == "Y"


def test_map_dimensions_3d(bruker_dir: Path) -> None:
    exp = read_dataset(bruker_dir / "hnca_3d")
    mappings = map_dimensions(exp)
    by_axis = {m.processing_axis: m for m in mappings}
    assert by_axis["F1"].display_axis == "X"  # 13C
    assert by_axis["F2"].display_axis == "Y"  # 15N
    assert by_axis["F3"].display_axis == "Z"  # 1H


def test_direct_dimension_sw_prefers_sw_h(bruker_dir: Path) -> None:
    """acqus 同时含 SW(ppm) 与 SW_h(Hz) 时取 SW_h。"""
    exp = read_dataset(bruker_dir / "hsqc_2d")
    direct = exp.direct_dimension
    assert direct is not None
    assert direct.sw == 10000.0


def test_carrier_ppm_fallback_without_o1p(tmp_path: Path, bruker_dir: Path) -> None:
    """真实数据无 O1P 时载波 ppm = O1 / SFO1。"""
    import shutil

    dst = tmp_path / "no_o1p"
    shutil.copytree(bruker_dir / "hsqc_2d", dst)
    for name in ("acqus", "acqu2s"):
        path = dst / name
        lines = [
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if not line.startswith("##$O1P")
        ]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    exp = read_dataset(dst)
    direct = exp.direct_dimension
    assert direct is not None
    assert direct.o1p == pytest.approx(2821.062748 / 599.8937495)

def _experiment_with_nuclei(
    ndim: int, nuclei: list[str], pulprog: str
):
    """构造指定核组合与 PULPROG 的 Experiment(分类器测试用)。"""
    from pathlib import Path

    from core.data.internal_data_model import (
        AxisRole,
        Dimension,
        Experiment,
        Sampling,
    )

    axes = ["F2", "F1"] if ndim == 2 else ["F3", "F2", "F1"]
    dims = []
    for i, axis in enumerate(axes):
        dims.append(
            Dimension(
                logical_axis=axis,
                nucleus=nuclei[i],
                role=AxisRole.DIRECT if i == 0 else AxisRole.INDIRECT,
            )
        )
    exp = Experiment(
        dataset_id="x",
        source_path=Path("x"),
        ndim=ndim,
        dimensions=dims,
        sampling=Sampling(),
        acquisition_parameters={"acqus": {"PULPROG": pulprog}},
    )
    return exp


def test_classify_nnh_by_nuclei() -> None:
    """固体核磁 NNH:核组合 1H/15N/15N 唯一匹配,不再被 PULPROG 误配 HN(CO)CA。"""
    from core.experiment.experiment_classifier import classify

    exp = _experiment_with_nuclei(3, ["1H", "15N", "15N"], "hncocannhgpwg3d")
    result = classify(exp)
    assert result.name == "NNH"
    assert result.confidence >= 0.9
    assert any("核组合唯一匹配" in e for e in result.evidence)


def test_classify_hncoca_only_with_13c() -> None:
    """真正含 13C 的数据(核组合 1H/15N/13C)才按 PULPROG 判定为 HN(CO)CA。"""
    from core.experiment.experiment_classifier import classify

    exp = _experiment_with_nuclei(3, ["1H", "15N", "13C"], "hncocannhgpwg3d")
    result = classify(exp)
    assert result.name == "HN(CO)CA"
    assert result.confidence >= 0.8


def test_classify_ssnmr_nca_nco_by_pulprog() -> None:
    """2D 15N/13C:NCA/NCO 按 PULPROG 区分。"""
    from core.experiment.experiment_classifier import classify

    nca = classify(_experiment_with_nuclei(2, ["13C", "15N"], "SPECIFIC-CP nca"))
    assert nca.name == "NCA"
    nco = classify(_experiment_with_nuclei(2, ["13C", "15N"], "nco"))
    assert nco.name == "NCO"


def test_classify_ssnmr_3d_correlation_by_pulprog() -> None:
    """3D 15N/13C/13C:NCACX/NCOCX/NCACB/NCOCACB 按 PULPROG 区分。"""
    from core.experiment.experiment_classifier import classify

    cases = {
        "ncacx": "NCACX",
        "ncocx": "NCOCX",
        "ncacb": "NCACB",
        "ncocacb": "NCOCACB",
        "ncocb": "NCOCACB",
    }
    for pulprog, expected in cases.items():
        result = classify(_experiment_with_nuclei(3, ["13C", "15N", "13C"], pulprog))
        assert result.name == expected, pulprog
        assert result.confidence >= 0.8, pulprog


def test_classify_ssnmr_canco_family() -> None:
    """3D 13C/15N/13C:CANCO/CAN(CO)CA/CBCANCO 按 PULPROG 区分。"""
    from core.experiment.experiment_classifier import classify

    cases = {
        "canco": "CANCO",
        "cancoCA": "CAN(CO)CA",
        "cbcanco": "CBCANCO",
    }
    for pulprog, expected in cases.items():
        result = classify(_experiment_with_nuclei(3, ["13C", "15N", "13C"], pulprog))
        assert result.name == expected, pulprog


def test_classify_ssnmr_cc_correlation_family() -> None:
    """2D 13C/13C:DARR/PDSD/RFDR/CORD/INADEQUATE/HCC 按 PULPROG 区分。"""
    from core.experiment.experiment_classifier import classify

    cases = {
        "darr": "DARR",
        "pdsd": "PDSD",
        "rfdr": "RFDR",
        "cord": "CORD",
        "inadequate": "INADEQUATE",
        "cshi.hCC_sd": "HCC",
    }
    for pulprog, expected in cases.items():
        result = classify(_experiment_with_nuclei(2, ["13C", "13C"], pulprog))
        assert result.name == expected, pulprog


def test_classify_ssnmr_hetcor_by_nuclei() -> None:
    """HETCOR:同一 PULPROG 按核组合区分 1H-13C / 1H-15N。"""
    from core.experiment.experiment_classifier import classify

    hc = classify(_experiment_with_nuclei(2, ["13C", "1H"], "FSLGhetcor"))
    assert hc.name == "HETCOR"
    hn = classify(_experiment_with_nuclei(2, ["15N", "1H"], "FSLGhetcor"))
    assert hn.name == "HNHETCOR"


def test_classify_ssnmr_tedor_pain_nn() -> None:
    """2D 距离约束:TEDOR/PAIN-CP(15N/13C)、NN(15N/15N)。"""
    from core.experiment.experiment_classifier import classify

    tedor = classify(_experiment_with_nuclei(2, ["13C", "15N"], "tedor"))
    assert tedor.name == "TEDOR"
    pain = classify(_experiment_with_nuclei(2, ["13C", "15N"], "paincp"))
    assert pain.name == "PAIN-CP"
    nn = classify(_experiment_with_nuclei(2, ["15N", "15N"], "nn"))
    assert nn.name == "NN"


def test_classify_ssnmr_chhc_nhhc_and_ccc() -> None:
    """2D 1H/1H:CHHC/NHHC 按 PULPROG 区分;3D 13C/13C/13C:CCC 唯一命中。"""
    from core.experiment.experiment_classifier import classify

    chhc = classify(_experiment_with_nuclei(2, ["1H", "1H"], "chhc"))
    assert chhc.name == "CHHC"
    nhhc = classify(_experiment_with_nuclei(2, ["1H", "1H"], "nhhc"))
    assert nhhc.name == "NHHC"
    ccc = classify(_experiment_with_nuclei(3, ["13C", "13C", "13C"], "ccc"))
    assert ccc.name == "CCC"
    assert ccc.confidence >= 0.9  # 核组合唯一命中


def test_classify_liquid_not_shadowed_by_solid() -> None:
    """液体关键词不被固体模板抢占(同核组合靠 PULPROG 区分)。"""
    from core.experiment.experiment_classifier import classify

    cases = [
        (3, ["1H", "15N", "13C"], "hncacb", "HNCACB"),
        (3, ["1H", "15N", "13C"], "hnca", "HNCA"),
        (3, ["1H", "15N", "13C"], "hnco", "HNCO"),
        (3, ["1H", "15N", "13C"], "cbcanh", "CBCANH"),
        (3, ["1H", "15N", "13C"], "cbcaconh", "CBCA(CO)NH"),
        (2, ["1H", "1H"], "noesyph", "NOESY"),
    ]
    for ndim, nuclei, pulprog, expected in cases:
        result = classify(_experiment_with_nuclei(ndim, nuclei, pulprog))
        assert result.name == expected, (pulprog, result)

