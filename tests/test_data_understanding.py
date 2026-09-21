"""Phase 1 data understanding: reading/sampling detection/acquisition mode/classification/axis
mapping."""

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
    """0.2.199-patch29fb: Representative of the 2D 1H-15N fallback HSQC family of unknown PULPROG
    (confidence <0.6 still to be confirmed), no longer purely Generic."""
    exp = read_dataset(bruker_dir / "unknown_2d")
    assert exp.experiment_type.name == "HSQC"
    assert exp.experiment_type.confidence < 0.6
    assert "safe family representative HSQC" in exp.experiment_type.evidence[-1]


def test_ft_neg_for_3d_first_indirect(bruker_dir: Path) -> None:
    """3D first indirect dimension (acqu2s/F2)States system requires -neg;E-A/second indirect/2D
    not added. Based on: bruk2pipe ACQUISITION MODES official table (-neg corresponds to
    States-N/ States-TPPI-N spectrum reverse) + Bruker 3D ser to make F2 dimension mirror; real
    verification sampleC(15N FnMODE=5 manual FT -alt -neg) and sampleB(FnMODE=6 E-A None)."""
    from core.experiment.acquisition_mode_detector import ft_neg_for

    exp3 = read_dataset(bruker_dir / "hnca_3d")  # F2(acqu2s)=5
    assert ft_neg_for(exp3, 5, "F2") is True   # States-TPPI → -alt -neg
    assert ft_neg_for(exp3, 4, "F2") is True   # States → -neg(Match None -alt).
    assert ft_neg_for(exp3, 6, "F2") is False  # Echo-Antiecho → None.
    assert ft_neg_for(exp3, 5, "F1") is False  # The second indirect dimension is not added.
    assert ft_neg_for(exp3, 5, "F3") is False  # Direct dimension not added.
    exp2 = read_dataset(bruker_dir / "hsqc_2d")
    assert ft_neg_for(exp2, 5, "F1") is False  # 2D Indirect dimension does not add.


def test_direct_dimension_sw_prefers_sw_h(bruker_dir: Path) -> None:
    """When acqus contains both SW(ppm) and SW_h(Hz), it is SW_h."""
    exp = read_dataset(bruker_dir / "hsqc_2d")
    direct = exp.direct_dimension
    assert direct is not None
    assert direct.sw == 10000.0


def test_carrier_ppm_fallback_without_o1p(tmp_path: Path, bruker_dir: Path) -> None:
    """BF1 Fallback O1/SFO1 when carrier ppm is missing (compatible with old data; fixture None
    BF1)."""
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
    """SampleK measured parameter (15N):O1/BF1 = 117.000, consistent with TopSpin display."""
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
        assert dim.sf == pytest.approx(121.666934964)  # sf Still SFO1.
        assert dim.o1p == pytest.approx(117.000, abs=1e-3)  # O1/BF1
        assert abs(dim.o1p - 116.986) > 0.01  # No longer O1/SFO1.


def test_o1p_prefers_explicit_o1p(tmp_path: Path, bruker_dir: Path) -> None:
    """Explicit O1P takes precedence: do not recompute even if BF1 exists."""
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
    assert direct.o1p == pytest.approx(4.703)  # O1P Original value, not recalculated due to BF1.


def test_o1p_fallback_bf1_for_1h(tmp_path: Path, bruker_dir: Path) -> None:
    """1H dimension: BF1 falls back to O1/BF1 when present (≈O1P 4.703, sampleI 4.700 same
    family)."""
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
    # The difference from the old O1/SFO1 value ≈ O1P²/1e6, prove that the BF1 branch is taken.
    assert abs(direct.o1p - 2821.062748 / 599.8937495) > 1e-5

def _experiment_with_nuclei(
    ndim: int, nuclei: list[str], pulprog: str
):
    """Construct an Experiment (for classifier testing) with the specified kernel combination and
    PULPROG."""
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
    """Solid NMR NNH: Pure nnh pulse procedure + nuclear combination 1H/15N/15N unique match.
    0.2.199-patch21: hncocannh/hncannh is solution HNN gradient pulse procedure, mapping HNN
    (ranked before nnh); solid NNH use case uses non-conflicting "cphNnh" pulse procedure name
    instead."""
    from core.experiment.experiment_classifier import classify

    exp = _experiment_with_nuclei(3, ["1H", "15N", "15N"], "cphNnh")
    result = classify(exp)
    assert result.name == "NNH"
    assert result.confidence >= 0.9
    assert any("PULPROG contains 'nnh'" in e for e in result.evidence)


def test_classify_hnn_by_solution_pulprog() -> None:
    """Solution NMR HNN(0.2.199-patch21):hncocannhgpwg3d + 1H/15N/15N -> HNN."""
    from core.experiment.experiment_classifier import classify

    exp = _experiment_with_nuclei(3, ["1H", "15N", "15N"], "hncocannhgpwg3d")
    result = classify(exp)
    assert result.name == "HNN"
    assert result.confidence >= 0.9


def test_classify_hncoca_only_with_13c() -> None:
    """The data that actually contains 13C (core combination 1H/15N/13C) is judged as HN(CO)CA
    according to PULPROG."""
    from core.experiment.experiment_classifier import classify

    exp = _experiment_with_nuclei(3, ["1H", "15N", "13C"], "hncocannhgpwg3d")
    result = classify(exp)
    assert result.name == "HN(CO)CA"
    assert result.confidence >= 0.8


def test_classify_ssnmr_nca_nco_by_pulprog() -> None:
    """2D 15N/13C:NCA/NCO distinguished by PULPROG."""
    from core.experiment.experiment_classifier import classify

    nca = classify(_experiment_with_nuclei(2, ["13C", "15N"], "SPECIFIC-CP nca"))
    assert nca.name == "NCA"
    nco = classify(_experiment_with_nuclei(2, ["13C", "15N"], "nco"))
    assert nco.name == "NCO"


def test_classify_ssnmr_3d_correlation_by_pulprog() -> None:
    """3D 15N/13C/13C:NCACX/NCOCX/NCACB/NCOCACB distinguished by PULPROG."""
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
    """3D 13C/15N/13C:CANCO/CAN(CO)CA/CBCANCO Press PULPROG to distinguish."""
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
    """2D 13C/13C:DARR/PDSD/RFDR/CORD/INADEQUATE/HCC distinguished by PULPROG."""
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
    """HETCOR: Same as PULPROG Distinguished by core combination 1H-13C / 1H-15N."""
    from core.experiment.experiment_classifier import classify

    hc = classify(_experiment_with_nuclei(2, ["13C", "1H"], "FSLGhetcor"))
    assert hc.name == "HETCOR"
    hn = classify(_experiment_with_nuclei(2, ["15N", "1H"], "FSLGhetcor"))
    assert hn.name == "HNHETCOR"


def test_classify_ssnmr_tedor_pain_nn() -> None:
    """2D distance constraints: TEDOR/PAIN-CP(15N/13C), NN(15N/15N)."""
    from core.experiment.experiment_classifier import classify

    tedor = classify(_experiment_with_nuclei(2, ["13C", "15N"], "tedor"))
    assert tedor.name == "TEDOR"
    pain = classify(_experiment_with_nuclei(2, ["13C", "15N"], "paincp"))
    assert pain.name == "PAIN-CP"
    nn = classify(_experiment_with_nuclei(2, ["15N", "15N"], "nn"))
    assert nn.name == "NN"




def test_classify_user_title_miss_and_hit() -> None:
    """0.2.199-patch29fd:pdata/title user type -- used when there is a miss; when the hits are
    inconsistent, the core compatibility will be based on the user title and a prompt will be
    given; if the core is not compatible, the title will be ignored."""
    from core.experiment.experiment_classifier import classify

    # Missed (customized PULPROG) -> title hit CBCANH.
    miss = classify(
        _experiment_with_nuclei(3, ["1H", "15N", "13C"], "custom123"),
        user_title="CBCANH",
    )
    assert miss.name == "CBCANH"
    assert miss.confidence == 0.75

    # Category hit HNCACB(0.9), title CBCANH Core compatible -> subject to user title.
    hit = classify(
        _experiment_with_nuclei(3, ["1H", "15N", "13C"], "hncacb"),
        user_title="CBCANH",
    )
    assert hit.name == "CBCANH"
    assert "user title wins" in hit.evidence[-1]
    assert "please check" in hit.evidence[-1]

    # Consistent.
    same = classify(
        _experiment_with_nuclei(3, ["1H", "15N", "13C"], "hncacb"),
        user_title="HNCACB",
    )
    assert same.name == "HNCACB"
    assert "agrees with the classification" in same.evidence[-1]

    # Title Core incompatible (write HNCACB on 2D 1H-15N) -> ignore the title and keep the
    # classification.
    incompat = classify(
        _experiment_with_nuclei(2, ["1H", "15N"], "hsqc"),
        user_title="HNCACB",
    )
    assert incompat.name == "HSQC"
    assert "ignoring the title" in incompat.evidence[-1]


def test_pdata_title_reads_latest_procno(tmp_path: Path) -> None:
    """0.2.199-patch29fd: Read the title of the largest procno of pdata."""
    from core.data.bruker_reader import _pdata_title

    root = tmp_path / "dataset"
    (root / "pdata" / "1").mkdir(parents=True)
    (root / "pdata" / "2").mkdir(parents=True)
    (root / "pdata" / "1" / "title").write_text("old name", encoding="utf-8")
    (root / "pdata" / "2" / "title").write_text("CBCANH", encoding="utf-8")
    assert _pdata_title(root) == "CBCANH"
    assert _pdata_title(tmp_path / "no_such") == ""


def test_classify_family_fallback_safe() -> None:
    """0.2.199-patch29fb: Homogenous combination family PULPROG falls back to the safe family
    representative when unknown, and is no longer purely Generic."""
    from core.experiment.experiment_classifier import classify

    n15c13 = classify(_experiment_with_nuclei(2, ["13C", "15N"], "unknown_pulprog"))
    assert n15c13.name == "NCA"
    assert 0.4 < n15c13.confidence < 0.6  # Waiting for user confirmation.
    assert "safe family representative NCA" in n15c13.evidence[-1]

    cc = classify(_experiment_with_nuclei(2, ["13C", "13C"], "unknown_pulprog"))
    assert cc.name == "DARR"

    hh = classify(_experiment_with_nuclei(2, ["1H", "1H"], "unknown_pulprog"))
    assert hh.name == "CHHC"

    cch3d = classify(
        _experiment_with_nuclei(3, ["1H", "13C", "13C"], "unknown_pulprog")
    )
    assert cch3d.name == "CCH"


def test_classify_family_fallback_mixed_sign_stays_generic() -> None:
    """0.2.199-patch29fb: 3D 13C/15N/13C The family is mixed uniform/mixed, the kernel combination
    cannot guess the symbol semantics, it must be Generic and list the candidates (the family
    representative cannot be selected by mistake)."""
    from core.experiment.experiment_classifier import classify

    result = classify(
        _experiment_with_nuclei(3, ["13C", "15N", "13C"], "unknown_pulprog")
    )
    assert result.name == "generic_3d"
    assert result.confidence < 0.6
    joined = " ".join(result.evidence)
    assert "NCACX" in joined and "NCACB" in joined and "CANCO" in joined


def test_classify_ssnmr_chhc_nhhc_and_ccc() -> None:
    """2D 1H/1H:CHHC/NHHC is distinguished by PULPROG; 3D 13C/13C/13C:CCC is the only hit."""
    from core.experiment.experiment_classifier import classify

    chhc = classify(_experiment_with_nuclei(2, ["1H", "1H"], "chhc"))
    assert chhc.name == "CHHC"
    nhhc = classify(_experiment_with_nuclei(2, ["1H", "1H"], "nhhc"))
    assert nhhc.name == "NHHC"
    ccc = classify(_experiment_with_nuclei(3, ["13C", "13C", "13C"], "ccc"))
    assert ccc.name == "CCC"
    assert ccc.confidence >= 0.9  # Nuclear combo only hit.


def test_classify_liquid_not_shadowed_by_solid() -> None:
    """Liquid keywords are not preempted by solid templates (same-core combinations are
    distinguished by PULPROG)."""
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
    """0.2.167: Supplement template according to PULPROG fine arrangement (same-core combinations
    are distinguished by keywords and do not fall into Generic)."""
    from core.experiment.experiment_classifier import classify

    cases = [
        # 1H/15N/1H family.
        (3, ["1H", "15N", "1H"], "tocsyhsqc", "TOCSY-HSQC-15N"),
        (3, ["1H", "15N", "1H"], "noesyhsqc", "NOESY-HSQC-15N"),
        (3, ["1H", "15N", "1H"], "hbhaconh", "HBHA(CO)NH"),
        (3, ["1H", "15N", "1H"], "hcaconh", "H(CA)NH"),
        (3, ["1H", "15N", "1H"], "hnha", "HNHA"),
        # 1H/13C/1H family.
        (3, ["1H", "13C", "1H"], "hcchco", "HCCH-COSY"),
        (3, ["1H", "13C", "1H"], "hcch", "HCCH-TOCSY"),
        (3, ["1H", "13C", "1H"], "noesyhsqc", "NOESY-HSQC-13C"),
        # 1H/13C/13C family.
        (3, ["1H", "13C", "13C"], "hcaco", "HCACO"),
        (3, ["1H", "13C", "13C"], "cchtocsy", "CCH-TOCSY"),
        (3, ["1H", "13C", "13C"], "cch", "CCH"),
        (3, ["1H", "13C", "13C"], "ccconh", "C(CCO)NH"),
        # 1H/15N/13C Supplement.
        (3, ["1H", "15N", "13C"], "hncaco", "HN(CA)CO"),
        (3, ["1H", "15N", "13C"], "hcconh", "H(CCO)NH"),
        # 2D edit/Other cores(1H detection: direct dimension is 1H, 13C/15N/31P/19F is in indirect
        # dimension).
        (2, ["1H", "13C"], "hsqctocsy", "HSQC-TOCSY-13C"),
        (2, ["1H", "15N"], "hsqctocsy", "HSQC-TOCSY-15N"),
        (2, ["1H", "31P"], "hmqc31", "HMQC-31P"),
        (2, ["1H", "31P"], "hmbc31", "HMBC-31P"),
        (2, ["1H", "19F"], "hsqc19", "HSQC-19F"),
        # Solid supplement.
        (2, ["13C", "15N"], "redor", "REDOR"),
    ]
    for ndim, nuclei, pulprog, expected in cases:
        result = classify(_experiment_with_nuclei(ndim, nuclei, pulprog))
        assert result.name == expected, (pulprog, result.name)

def test_classify_same_nucleus_position_sensitive() -> None:
    """0.2.168: The same core is distinguished by dimension position (x/y/z index) -- HETCOR(13C
    direct dimension) and HSQC-13C(13C indirect dimension), HNHETCOR(15N direct dimension) and
    HSQC(15N indirect dimension) no longer collide with cores due to the same disorder count,
    and are directly unique candidate hits."""
    from core.experiment.experiment_classifier import classify

    # 13C in direct dimension (F2) -> core sequence (13C, 1H), the only candidate HETCOR.
    r = classify(_experiment_with_nuclei(2, ["13C", "1H"], "hsqc"))
    assert r.name == "HETCOR"
    assert any("matches exactly one template" in e for e in r.evidence)
    # 15N in direct dimension (F2) -> core sequence (15N, 1H), the only candidate HNHETCOR.
    r = classify(_experiment_with_nuclei(2, ["15N", "1H"], "hsqc"))
    assert r.name == "HNHETCOR"
    # 13C in indirect dimension (F1) -> core sequence (1H, 13C), multiple candidates still rely on
    # PULPROG.
    r = classify(_experiment_with_nuclei(2, ["1H", "13C"], "hsqctocsy"))
    assert r.name == "HSQC-TOCSY-13C"

def test_is_data_directory(tmp_path: Path) -> None:
    """NO QUERY SPECIFIED. EXAMPLE REQUEST: GET?Q=HELLO&LANGPAIR=EN|IT."""
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
    """NO QUERY SPECIFIED. EXAMPLE REQUEST: GET?Q=HELLO&LANGPAIR=EN|IT."""
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
    """NO QUERY SPECIFIED. EXAMPLE REQUEST: GET?Q=HELLO&LANGPAIR=EN|IT."""
    from core.data.bruker_reader import read_dataset_container

    no_data = tmp_path / "no_data"
    no_data.mkdir()
    (no_data / "notes").mkdir()
    (no_data / "notes" / "readme.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="neither a Bruker dataset"):
        read_dataset_container(no_data)

    one = tmp_path / "one_data"
    one.mkdir()
    seg = one / "segA"
    seg.mkdir()
    (seg / "acqus").write_text("x", encoding="utf-8")
    (one / "notes").mkdir()
    (one / "notes" / "readme.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="only 1 subdirectory holds data files"):
        read_dataset_container(one)


def test_read_dataset_container_not_segmented_experiment(
    tmp_path: Path, bruker_dir: Path
) -> None:
    """0.2.198: When the sub-directory parameters are inconsistent (independent data sets), it is
    clearly informed that this is not a segmented experiment."""
    import shutil

    from core.data.bruker_reader import read_dataset_container

    container = tmp_path / "container"
    container.mkdir()
    a = container / "a"
    b = container / "b"
    shutil.copytree(bruker_dir / "nus_2d", a)
    shutil.copytree(bruker_dir / "nus_2d", b)
    # Change b's TD to make the two parameters inconsistent.
    acqu2s = b / "acqu2s"
    text = acqu2s.read_text(encoding="utf-8")
    acqu2s.write_text(text.replace("##$TD= 256", "##$TD= 128"), encoding="utf-8")
    with pytest.raises(ValueError, match="not a segmented experiment"):
        read_dataset_container(container)

def test_classify_kinetics_by_pulprog() -> None:
    """29hm: kinetics PULPROG identified as Kinetics."""
    from core.experiment.experiment_classifier import classify
    exp = _experiment_with_nuclei(2, ["1H", "13C"], "kinetics-2d")
    result = classify(exp)
    assert result.name == "Kinetics"
    assert result.confidence >= 0.9
    assert any("kinetics / variable-delay series detected" in e for e in result.evidence)


def test_classify_kinetics_by_vdlist() -> None:
    """29hm: acqus.VDLIST -> Kinetics."""
    from core.experiment.experiment_classifier import classify
    exp = _experiment_with_nuclei(2, ["1H", "13C"], "hsqc")
    exp.acquisition_parameters["acqus"]["VDLIST"] = "vdlist"
    result = classify(exp)
    assert result.name == "Kinetics"

def test_kinetics_import_blocked_before_project_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """IMPORT-007 A: Reject import after identification, and do not create data entries, run or raw
    copies."""
    from core.project import ProjectManager
    from workflow.import_workflow import KineticsUnsupportedError, import_data

    source = tmp_path / "kinetics"
    source.mkdir()
    (source / "acqus").write_text("##TITLE= kinetics", encoding="utf-8")
    kinetic = _experiment_with_nuclei(2, ["1H", "13C"], "hsqc")
    kinetic.acquisition_parameters["acqus"]["VDLIST"] = "vdlist"
    monkeypatch.setattr("workflow.import_workflow.read_dataset", lambda _path: kinetic)

    manager = ProjectManager.create_project(tmp_path / "project", "demo")
    entry = manager.create_experiment("Kinetics candidate")
    with pytest.raises(KineticsUnsupportedError, match="does not support importing"):
        import_data(manager, entry.id, source, copy=True)

    assert entry.data == []
    assert manager.project is not None
    assert manager.project.workflow_runs == []
    assert not (manager.root / entry.id).exists()


def test_non_kinetics_passes_import_policy_guard() -> None:
    """Ordinary experiments are not accidentally damaged by Kinetics strategies."""
    from workflow.import_workflow import _raise_if_kinetics

    normal = _experiment_with_nuclei(2, ["1H", "13C"], "hsqc")
    _raise_if_kinetics(normal)

def test_vdlist_placeholder_not_kinetics() -> None:
    """Patch29hq-Repair: acqus.VDLIST is a pure D occupancy (Bruker does not set a variable delay)
    and is not judged as dynamics."""
    from core.experiment.experiment_classifier import _is_kinetics, classify

    exp = _experiment_with_nuclei(2, ["1H", "13C"], "hncacbgp3d.x")
    exp.acquisition_parameters["acqus"]["VDLIST"] = "DDDDDDDDDDDDDDD"
    assert _is_kinetics(exp) is False
    assert classify(exp).name != "Kinetics"
