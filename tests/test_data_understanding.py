"""Phase 1 数据理解：读取 / 采样检测 / 采集模式 / 分类 / 轴映射。"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.data.bruker_reader import read_dataset
from core.data.internal_data_model import SamplingMode


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


def test_read_unknown_2d_family_fallback(bruker_dir: Path) -> None:
    """0.2.199-补29fb:未知 PULPROG 的 2D 1H-15N 回退 HSQC 族代表(置信<0.6
    仍待确认),不再纯 Generic。"""
    exp = read_dataset(bruker_dir / "unknown_2d")
    assert exp.experiment_type.name == "HSQC"
    assert exp.experiment_type.confidence < 0.6
    assert "族代表 HSQC" in exp.experiment_type.evidence[-1]


def test_ft_neg_for_3d_first_indirect(bruker_dir: Path) -> None:
    """3D 第一间接维(acqu2s/F2)States 系需 -neg;E-A/第二间接/2D 不加。

    依据:bruk2pipe ACQUISITION MODES 官方表(-neg 对应 States-N/
    States-TPPI-N 谱反向)+ Bruker 3D ser 使 F2 维镜像;真实验证
    sampleC(15N FnMODE=5 手工 FT -alt -neg)与 sampleB(FnMODE=6 E-A 无)。
    """
    from core.experiment.acquisition_mode_detector import ft_neg_for

    exp3 = read_dataset(bruker_dir / "hnca_3d")  # F2(acqu2s)=5
    assert ft_neg_for(exp3, 5, "F2") is True   # States-TPPI → -alt -neg
    assert ft_neg_for(exp3, 4, "F2") is True   # States → -neg(配合无 -alt)
    assert ft_neg_for(exp3, 6, "F2") is False  # Echo-Antiecho → 无
    assert ft_neg_for(exp3, 5, "F1") is False  # 第二间接维不加
    assert ft_neg_for(exp3, 5, "F3") is False  # 直接维不加
    exp2 = read_dataset(bruker_dir / "hsqc_2d")
    assert ft_neg_for(exp2, 5, "F1") is False  # 2D 间接维不加


def test_direct_dimension_sw_prefers_sw_h(bruker_dir: Path) -> None:
    """acqus 同时含 SW(ppm) 与 SW_h(Hz) 时取 SW_h。"""
    exp = read_dataset(bruker_dir / "hsqc_2d")
    direct = exp.direct_dimension
    assert direct is not None
    assert direct.sw == 10000.0


def test_carrier_ppm_fallback_without_o1p(tmp_path: Path, bruker_dir: Path) -> None:
    """BF1 缺失时载波 ppm 回退 O1/SFO1(兼容旧数据;fixture 无 BF1)。"""
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


def test_o1p_uses_bf1_matching_topspin(tmp_path: Path) -> None:
    """sampleK 实测参数(15N):O1/BF1 = 117.000,与 TopSpin 显示一致。"""
    params = (
        "##$PULPROG= nuc\n"
        "##$TD= 1024\n"
        "##$SW_h= 10000.000000\n"
        "##$SFO1= 121.666934964\n"
        "##$BF1= 121.652701598\n"
        "##$O1= 14233.366\n"
        "##$NUC1= 15N\n"
        "##$PARMODE= 1\n"
        "##$FnMODE= 5\n"
        "##$END=\n"
    )
    dst = tmp_path / "o1p_bf1"
    dst.mkdir()
    (dst / "acqus").write_text(params, encoding="utf-8", newline="\n")
    (dst / "acqu2s").write_text(params, encoding="utf-8", newline="\n")
    exp = read_dataset(dst)
    assert len(exp.dimensions) == 2
    for dim in exp.dimensions:
        assert dim.sf == pytest.approx(121.666934964)  # sf 仍为 SFO1
        assert dim.o1p == pytest.approx(117.000, abs=1e-3)  # O1/BF1
        assert abs(dim.o1p - 116.986) > 0.01  # 不再是 O1/SFO1


def test_o1p_prefers_explicit_o1p(tmp_path: Path, bruker_dir: Path) -> None:
    """显式 O1P 优先:即使 BF1 存在也不重算。"""
    import shutil

    dst = tmp_path / "o1p_explicit"
    shutil.copytree(bruker_dir / "hsqc_2d", dst)
    acqus = dst / "acqus"
    lines = acqus.read_text(encoding="utf-8").splitlines()
    lines.append("##$BF1= 599.890928437")
    acqus.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    exp = read_dataset(dst)
    direct = exp.direct_dimension
    assert direct is not None
    assert direct.o1p == pytest.approx(4.703)  # O1P 原值,不因 BF1 重算


def test_o1p_fallback_bf1_for_1h(tmp_path: Path, bruker_dir: Path) -> None:
    """1H 维度:BF1 存在时回退 O1/BF1(≈O1P 4.703,sampleI 4.700 同族)。"""
    import shutil

    dst = tmp_path / "o1p_1h"
    shutil.copytree(bruker_dir / "hsqc_2d", dst)
    acqus = dst / "acqus"
    lines = [
        line
        for line in acqus.read_text(encoding="utf-8").splitlines()
        if not line.startswith("##$O1P")
    ]
    # SFO1 = BF1 + O1 → BF1 = 599.8937495 MHz − 2821.062748 Hz
    lines.append("##$BF1= 599.890928437252")
    acqus.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    exp = read_dataset(dst)
    direct = exp.direct_dimension
    assert direct is not None
    assert direct.o1p == pytest.approx(4.703, abs=1e-3)
    # 与旧 O1/SFO1 值的差 ≈ O1P²/1e6,证明走 BF1 分支
    assert abs(direct.o1p - 2821.062748 / 599.8937495) > 1e-5

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
    """固体核磁 NNH:纯 nnh 脉冲程序 + 核组合 1H/15N/15N 唯一匹配。

    0.2.199-补21:hncocannh/hncannh 是溶液 HNN 梯度脉冲程序,映射 HNN
    (排在 nnh 前);固体 NNH 用例改用不冲突的 "cphNnh" 脉冲程序名。
    """
    from core.experiment.experiment_classifier import classify

    exp = _experiment_with_nuclei(3, ["1H", "15N", "15N"], "cphNnh")
    result = classify(exp)
    assert result.name == "NNH"
    assert result.confidence >= 0.9
    assert any("PULPROG 含 'nnh'" in e for e in result.evidence)


def test_classify_hnn_by_solution_pulprog() -> None:
    """溶液核磁 HNN(0.2.199-补21):hncocannhgpwg3d + 1H/15N/15N → HNN。"""
    from core.experiment.experiment_classifier import classify

    exp = _experiment_with_nuclei(3, ["1H", "15N", "15N"], "hncocannhgpwg3d")
    result = classify(exp)
    assert result.name == "HNN"
    assert result.confidence >= 0.9


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




def test_classify_user_title_miss_and_hit() -> None:
    """0.2.199-补29fd:pdata/title 用户类型——未命中时采用;命中不一致时核兼容
    则以用户 title 为准并提示;核不兼容忽略标题。"""
    from core.experiment.experiment_classifier import classify

    # 未命中(自定义 PULPROG)→ title 命中 CBCANH
    miss = classify(
        _experiment_with_nuclei(3, ["1H", "15N", "13C"], "custom123"),
        user_title="CBCANH",
    )
    assert miss.name == "CBCANH"
    assert miss.confidence == 0.75

    # 分类命中 HNCACB(0.9),title CBCANH 核兼容 → 以用户 title 为准
    hit = classify(
        _experiment_with_nuclei(3, ["1H", "15N", "13C"], "hncacb"),
        user_title="CBCANH",
    )
    assert hit.name == "CBCANH"
    assert "不一致" in hit.evidence[-1] and "请核对" in hit.evidence[-1]

    # 一致
    same = classify(
        _experiment_with_nuclei(3, ["1H", "15N", "13C"], "hncacb"),
        user_title="HNCACB",
    )
    assert same.name == "HNCACB"
    assert "一致" in same.evidence[-1]

    # title 核不兼容(2D 1H-15N 上写 HNCACB)→ 忽略标题,保持分类
    incompat = classify(
        _experiment_with_nuclei(2, ["1H", "15N"], "hsqc"),
        user_title="HNCACB",
    )
    assert incompat.name == "HSQC"
    assert "忽略标题" in incompat.evidence[-1]


def test_pdata_title_reads_latest_procno(tmp_path: Path) -> None:
    """0.2.199-补29fd:读取 pdata 最大 procno 的 title。"""
    from core.data.bruker_reader import _pdata_title

    root = tmp_path / "dataset"
    (root / "pdata" / "1").mkdir(parents=True)
    (root / "pdata" / "2").mkdir(parents=True)
    (root / "pdata" / "1" / "title").write_text("old name", encoding="utf-8")
    (root / "pdata" / "2" / "title").write_text("CBCANH", encoding="utf-8")
    assert _pdata_title(root) == "CBCANH"
    assert _pdata_title(tmp_path / "no_such") == ""


def test_classify_family_fallback_safe() -> None:
    """0.2.199-补29fb:同核组合族 PULPROG 未知时回退安全族代表,不再纯 Generic。"""
    from core.experiment.experiment_classifier import classify

    n15c13 = classify(_experiment_with_nuclei(2, ["13C", "15N"], "unknown_pulprog"))
    assert n15c13.name == "NCA"
    assert 0.4 < n15c13.confidence < 0.6  # 待用户确认
    assert "族代表 NCA" in n15c13.evidence[-1]

    cc = classify(_experiment_with_nuclei(2, ["13C", "13C"], "unknown_pulprog"))
    assert cc.name == "DARR"

    hh = classify(_experiment_with_nuclei(2, ["1H", "1H"], "unknown_pulprog"))
    assert hh.name == "CHHC"

    cch3d = classify(
        _experiment_with_nuclei(3, ["1H", "13C", "13C"], "unknown_pulprog")
    )
    assert cch3d.name == "CCH"


def test_classify_family_fallback_mixed_sign_stays_generic() -> None:
    """0.2.199-补29fb:3D 13C/15N/13C 族混 uniform/mixed,核组合猜不出符号
    语义,必须 Generic 并列出候选(不能误选族代表)。"""
    from core.experiment.experiment_classifier import classify

    result = classify(
        _experiment_with_nuclei(3, ["13C", "15N", "13C"], "unknown_pulprog")
    )
    assert result.name == "generic_3d"
    assert result.confidence < 0.6
    joined = " ".join(result.evidence)
    assert "NCACX" in joined and "NCACB" in joined and "CANCO" in joined


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

def test_classify_liquid_edited_and_other_nuclei() -> None:
    """0.2.167:补充模板按 PULPROG 精排(同核组合靠关键词区分,不落 Generic)。"""
    from core.experiment.experiment_classifier import classify

    cases = [
        # 1H/15N/1H 族
        (3, ["1H", "15N", "1H"], "tocsyhsqc", "TOCSY-HSQC-15N"),
        (3, ["1H", "15N", "1H"], "noesyhsqc", "NOESY-HSQC-15N"),
        (3, ["1H", "15N", "1H"], "hbhaconh", "HBHA(CO)NH"),
        (3, ["1H", "15N", "1H"], "hcaconh", "H(CA)NH"),
        (3, ["1H", "15N", "1H"], "hnha", "HNHA"),
        # 1H/13C/1H 族
        (3, ["1H", "13C", "1H"], "hcchco", "HCCH-COSY"),
        (3, ["1H", "13C", "1H"], "hcch", "HCCH-TOCSY"),
        (3, ["1H", "13C", "1H"], "noesyhsqc", "NOESY-HSQC-13C"),
        # 1H/13C/13C 族
        (3, ["1H", "13C", "13C"], "hcaco", "HCACO"),
        (3, ["1H", "13C", "13C"], "cchtocsy", "CCH-TOCSY"),
        (3, ["1H", "13C", "13C"], "cch", "CCH"),
        (3, ["1H", "13C", "13C"], "ccconh", "C(CCO)NH"),
        # 1H/15N/13C 补充
        (3, ["1H", "15N", "13C"], "hncaco", "HN(CA)CO"),
        (3, ["1H", "15N", "13C"], "hcconh", "H(CCO)NH"),
        # 2D 编辑/其它核(1H 检测:直接维为 1H,13C/15N/31P/19F 在间接维)
        (2, ["1H", "13C"], "hsqctocsy", "HSQC-TOCSY-13C"),
        (2, ["1H", "15N"], "hsqctocsy", "HSQC-TOCSY-15N"),
        (2, ["1H", "31P"], "hmqc31", "HMQC-31P"),
        (2, ["1H", "31P"], "hmbc31", "HMBC-31P"),
        (2, ["1H", "19F"], "hsqc19", "HSQC-19F"),
        # 固体补充
        (2, ["13C", "15N"], "redor", "REDOR"),
    ]
    for ndim, nuclei, pulprog, expected in cases:
        result = classify(_experiment_with_nuclei(ndim, nuclei, pulprog))
        assert result.name == expected, (pulprog, result.name)

def test_classify_same_nucleus_position_sensitive() -> None:
    """0.2.168:同一核按维度位置(x/y/z 下标)区分——HETCOR(13C 直接维)
    与 HSQC-13C(13C 间接维)、HNHETCOR(15N 直接维)与 HSQC(15N 间接维)
    不再因无序计数相同而撞核,直接唯一候选命中。"""
    from core.experiment.experiment_classifier import classify

    # 13C 在直接维(F2)→ 核序列 (13C, 1H),唯一候选 HETCOR
    r = classify(_experiment_with_nuclei(2, ["13C", "1H"], "hsqc"))
    assert r.name == "HETCOR"
    assert any("核组合唯一匹配" in e for e in r.evidence)
    # 15N 在直接维(F2)→ 核序列 (15N, 1H),唯一候选 HNHETCOR
    r = classify(_experiment_with_nuclei(2, ["15N", "1H"], "hsqc"))
    assert r.name == "HNHETCOR"
    # 13C 在间接维(F1)→ 核序列 (1H, 13C),多候选仍靠 PULPROG
    r = classify(_experiment_with_nuclei(2, ["1H", "13C"], "hsqctocsy"))
    assert r.name == "HSQC-TOCSY-13C"

def test_is_data_directory(tmp_path: Path) -> None:
    '''含任一 Bruker 关键文件视为数据目录,全无则非数据(忽略用,Task F)。'''
    from core.data.bruker_reader import is_data_directory

    d = tmp_path / "segA"
    d.mkdir()
    (d / "acqus").write_text("x", encoding="utf-8")
    assert is_data_directory(d)
    assert is_data_directory(tmp_path / "segA")
    ser_only = tmp_path / "ser_only"
    ser_only.mkdir()
    (ser_only / "ser").write_text("x", encoding="utf-8")
    assert is_data_directory(ser_only)
    junk = tmp_path / "notes"
    junk.mkdir()
    (junk / "readme.txt").write_text("x", encoding="utf-8")
    assert not is_data_directory(junk)
    empty = tmp_path / "empty"
    empty.mkdir()
    assert not is_data_directory(empty)
    assert not is_data_directory(tmp_path / "missing")


def test_discover_segment_dirs_ignores_non_data(tmp_path: Path) -> None:
    '''容器发现只返回含 acqus 的子目录,非数据子目录被忽略(Task F)。'''
    from core.data.bruker_reader import discover_segment_dirs

    container = tmp_path / "container"
    container.mkdir()
    for name in ("segA", "segB"):
        d = container / name
        d.mkdir()
        (d / "acqus").write_text("x", encoding="utf-8")
    (container / "notes").mkdir()
    (container / "notes" / "readme.txt").write_text("x", encoding="utf-8")
    (container / "empty").mkdir()
    found = [p.name for p in discover_segment_dirs(container)]
    assert found == ["segA", "segB"]


def test_read_dataset_container_non_data_errors(tmp_path: Path) -> None:
    '''0 个或 1 个数据子目录时给出明确错误(非数据子目录已忽略),不报缺失 acqus。'''
    from core.data.bruker_reader import read_dataset_container

    no_data = tmp_path / "no_data"
    no_data.mkdir()
    (no_data / "notes").mkdir()
    (no_data / "notes" / "readme.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="未找到任何含数据文件"):
        read_dataset_container(no_data)

    one = tmp_path / "one_data"
    one.mkdir()
    seg = one / "segA"
    seg.mkdir()
    (seg / "acqus").write_text("x", encoding="utf-8")
    (one / "notes").mkdir()
    (one / "notes" / "readme.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="仅找到 1 个含数据文件的子目录"):
        read_dataset_container(one)


def test_read_dataset_container_not_segmented_experiment(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """0.2.198:子目录参数不一致(独立数据集)时明确告知不是分段实验。"""
    import shutil

    from core.data.bruker_reader import read_dataset_container

    container = tmp_path / "container"
    container.mkdir()
    a = container / "a"
    b = container / "b"
    shutil.copytree(bruker_dir / "nus_2d", a)
    shutil.copytree(bruker_dir / "nus_2d", b)
    # 改 b 的 TD 使两段参数不一致
    acqu2s = b / "acqu2s"
    text = acqu2s.read_text(encoding="utf-8")
    acqu2s.write_text(text.replace("##$TD= 256", "##$TD= 128"), encoding="utf-8")
    with pytest.raises(ValueError, match="不是分段实验"):
        read_dataset_container(container)
