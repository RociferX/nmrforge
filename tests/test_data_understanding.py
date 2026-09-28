"""Phase 1 data understanding: reading / sampling detection / acquisition mode / classification /
axis mapping."""

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
    """0.2.199-patch29fb: an unknown PULPROG on 2D 1H-15N falls back to the HSQC family
    representative (confidence <0.6, still to be confirmed), no longer purely Generic."""
    exp = read_dataset(bruker_dir / "unknown_2d")
    assert exp.experiment_type.name == "HSQC"
    assert exp.experiment_type.confidence < 0.6
    assert "族代表 HSQC" in exp.experiment_type.evidence[-1]


def test_ft_neg_for_3d_first_indirect(bruker_dir: Path) -> None:
    """``-neg`` is decided only by the coherence-pathway criterion; when it is undecidable
    (no pulse program / E-A / direct dimension) it is never added.

    Second finalization 2026-09-24: the old empirical criterion "the first indirect dimension of
    a 3D (acqu2s/F2) States family is always negated" is void (review round 20: that is not a
    mechanism but treating "that particular batch happened to have inverted imaginary parts" as a
    rule). The only basis now is the qphase criterion of ``core.experiment.pulse_pathways``; the
    synthetic ``bruker_dir`` fixture has no ``pulseprogram`` ⇒ undecidable ⇒ no ``-neg`` on any
    dimension, and ``review_lines`` asks for manual review in three places: log, report and
    import warning.
    """
    from core.experiment.acquisition_mode_detector import ft_neg_for
    from core.experiment.pulse_pathways import review_lines

    exp3 = read_dataset(bruker_dir / "hnca_3d")  # F2(acqu2s)=5
    assert ft_neg_for(exp3, 5, "F2") is False  # no pulse program ⇒ unknown ⇒ not added
    assert ft_neg_for(exp3, 4, "F2") is False
    assert ft_neg_for(exp3, 6, "F2") is False  # Echo-Antiecho → none
    assert ft_neg_for(exp3, 5, "F1") is False
    assert ft_neg_for(exp3, 5, "F3") is False  # direct dimension not added
    exp2 = read_dataset(bruker_dir / "hsqc_2d")
    assert ft_neg_for(exp2, 5, "F1") is False
    # Undecidable dimensions must leave a review line (same text as the report/log)
    lines = review_lines(exp3)
    assert lines and all("MODE" in line for line in lines)


def test_direct_dimension_sw_prefers_sw_h(bruker_dir: Path) -> None:
    """With acqus SW (ppm) and SW_h (Hz) **in agreement**, take SW_h (within ≤1% = same value)."""
    exp = read_dataset(bruker_dir / "hsqc_2d")
    direct = exp.direct_dimension
    assert direct is not None
    assert direct.sw == 10000.0
    assert direct.sw_source == "sw_h"
    assert direct.sw_hz_raw == 10000.0
    assert direct.sw_ppm > 0.0
    assert direct.sw_note == ""


def _write_2d_params(tmp_path: Path, name: str, acqus: str, acqu2s: str) -> Path:
    """Build a 2D dataset directory from the given acqus/acqu2s text (as in the o1p tests)."""
    dst = tmp_path / name
    dst.mkdir()
    (dst / "acqus").write_text(acqus, encoding="utf-8", newline="\n")
    (dst / "acqu2s").write_text(acqu2s, encoding="utf-8", newline="\n")
    return dst


#: Direct dimension self-consistent (real machine 33/exp_002 values):
#: SW=13.9790082212254 ppm × SFO1 600.13282861 = SW_h
_DIRECT_OK = (
    "##$PULPROG= hsqcetgpsi\n"
    "##$TD= 2048\n"
    "##$SW= 13.9790082212254\n"
    "##$SW_h= 8389.26174496644\n"
    "##$SFO1= 600.13282861\n"
    "##$BF1= 600.13\n"
    "##$O1= 2828.61000005141\n"
    "##$NUC1= 1H\n"
    "##$PARMODE= 1\n"
    "##$FnMODE= 0\n"
    "##$END=\n"
)


def _indirect(SW: str, SW_h: str, SFO1: str, BF1: str, O1: str) -> str:
    return (
        "##$PULPROG= hsqcetgpsi\n"
        "##$TD= 256\n"
        f"##$SW= {SW}\n"
        f"##$SW_h= {SW_h}\n"
        f"##$SFO1= {SFO1}\n"
        f"##$BF1= {BF1}\n"
        f"##$O1= {O1}\n"
        "##$NUC1= 15N\n"
        "##$PARMODE= 1\n"
        "##$FnMODE= 6\n"
        "##$END=\n"
    )


def test_sweep_width_conflict_uses_the_ppm_convention(tmp_path: Path) -> None:
    """When SW_h contradicts SW(ppm)×SFO1 (>1%), follow the ppm convention and leave a note.

    Ground truth (2026-09-24, BMRB deposited data): acqu2s writes ``SW=30 ppm`` and
    ``SW_h=2000 Hz``, while ``SFO1=60.81782065611`` in the same file converts to 1824.5346 Hz;
    the 800 MHz dataset also has ``SW_h`` = 2000 Hz ⇒ it is a copied constant, not the width of
    this spectrum. The depositor's own conversion script (AGNuS ``Convert_HSQC.csh``) and
    nmrglue's indirect dimension both use ``ppm×SFO1``.
    """
    acqu2s = _indirect("30", "2000", "60.81782065611", "60.810645", "7175.65611")
    exp = read_dataset(_write_2d_params(tmp_path, "or8c_600", _DIRECT_OK, acqu2s))
    dims = {dim.logical_axis: dim for dim in exp.dimensions}
    direct, indirect = dims["F2"], dims["F1"]
    assert direct.sw_source == "sw_h"  # direct dimension self-consistent, unaffected
    assert indirect.sw == pytest.approx(1824.5346196833, rel=1e-9)
    assert indirect.sw_source == "ppm_x_sfo"
    assert indirect.sw_hz_raw == 2000.0
    assert indirect.sw_ppm == 30.0
    assert "2000" in indirect.sw_note and "1824.5" in indirect.sw_note


def test_sweep_width_conflict_follows_the_field(tmp_path: Path) -> None:
    """The same ``SW=30 ppm``: at 800 MHz it is 2432.76 Hz -- the constant 2000 Hz must be wrong."""
    acqu2s = _indirect("30", "2000", "81.092116740782", "81.082549", "9567.740782")
    exp = read_dataset(_write_2d_params(tmp_path, "or8c_800", _DIRECT_OK, acqu2s))
    dim = next(d for d in exp.dimensions if d.logical_axis == "F1")
    assert dim.sw == pytest.approx(2432.76350222346, rel=1e-9)
    assert dim.sw_source == "ppm_x_sfo"


def test_sweep_width_conflict_accepts_a_rounded_ppm(tmp_path: Path) -> None:
    """27 ppm @ 86.1501 MHz = 2326.05 Hz; the depositor's fid.com gives 2326.122 (27.0008 ppm),
    off by 0.003% -- a rounding difference must not be taken as another convention."""
    acqu2s = _indirect("27", "2000", "86.150135646315", "86.139885", "10250.646315")
    exp = read_dataset(_write_2d_params(tmp_path, "acp", _DIRECT_OK, acqu2s))
    dim = next(d for d in exp.dimensions if d.logical_axis == "F1")
    assert dim.sw == pytest.approx(2326.0536624505, rel=1e-9)
    assert abs(dim.sw - 2326.122) / 2326.122 < 3e-5


def test_sweep_width_without_sw_h_uses_the_ppm_convention(tmp_path: Path) -> None:
    """With only ``SW`` (ppm) it **must not** be used as Hz (the old code took SW as Hz).

    This is a unit bug: treating 13.979 ppm as 13.979 Hz makes the ppm axis off by a factor of
    600.
    """
    no_sw_h = _DIRECT_OK.replace("##$SW_h= 8389.26174496644\n", "")
    exp = read_dataset(_write_2d_params(tmp_path, "no_sw_h", no_sw_h, _indirect(
        "30", "1824.534", "60.81782065611", "60.810645", "7175.65611"
    )))
    direct = exp.direct_dimension
    assert direct is not None
    assert direct.sw == pytest.approx(8389.26174496644, rel=1e-6)
    assert direct.sw_source == "ppm_x_sfo"
    assert "SW_h" in direct.sw_note


def test_carrier_ppm_fallback_without_o1p(tmp_path: Path, bruker_dir: Path) -> None:
    """With BF1 missing the carrier ppm falls back to O1/SFO1 (old-data compatibility; the
    fixture has no BF1)."""
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
    """Measured parameters from sampleK (15N): O1/BF1 = 117.000, matching the TopSpin display."""
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
        assert dim.sf == pytest.approx(121.666934964)  # sf still SFO1
        assert dim.o1p == pytest.approx(117.000, abs=1e-3)  # O1/BF1
        assert abs(dim.o1p - 116.986) > 0.01  # no longer O1/SFO1


def test_o1p_prefers_explicit_o1p(tmp_path: Path, bruker_dir: Path) -> None:
    """Explicit O1P wins: no recomputation even when BF1 is present."""
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
    assert direct.o1p == pytest.approx(4.703)  # original O1P value, not recomputed from BF1


def test_o1p_fallback_bf1_for_1h(tmp_path: Path, bruker_dir: Path) -> None:
    """1H dimension: falls back to O1/BF1 when BF1 exists (≈O1P 4.703; sampleI 4.700 same family)."""
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
    # Difference from the old O1/SFO1 value ≈ O1P²/1e6, proving the BF1 branch is taken
    assert abs(direct.o1p - 2821.062748 / 599.8937495) > 1e-5

def _experiment_with_nuclei(
    ndim: int, nuclei: list[str], pulprog: str
):
    """Build an Experiment with the given nuclei and PULPROG (for classifier tests)."""
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
    """Solid-state NMR NNH: pure nnh pulse program + nuclei 1H/15N/15N unique match.

    0.2.199-patch21: hncocannh/hncannh are solution HNN gradient pulse programs mapped to HNN
    (ranked before nnh); the solid NNH case uses the non-conflicting "cphNnh" name instead.
    """
    from core.experiment.experiment_classifier import classify

    exp = _experiment_with_nuclei(3, ["1H", "15N", "15N"], "cphNnh")
    result = classify(exp)
    assert result.name == "NNH"
    assert result.confidence >= 0.9
    assert any("PULPROG 含 'nnh'" in e for e in result.evidence)


def test_classify_hnn_by_solution_pulprog() -> None:
    """Solution NMR HNN (0.2.199-patch21): hncocannhgpwg3d + 1H/15N/15N -> HNN."""
    from core.experiment.experiment_classifier import classify

    exp = _experiment_with_nuclei(3, ["1H", "15N", "15N"], "hncocannhgpwg3d")
    result = classify(exp)
    assert result.name == "HNN"
    assert result.confidence >= 0.9


def test_classify_hncoca_only_with_13c() -> None:
    """Only data that really contains 13C (nuclei 1H/15N/13C) is judged HN(CO)CA by PULPROG."""
    from core.experiment.experiment_classifier import classify

    exp = _experiment_with_nuclei(3, ["1H", "15N", "13C"], "hncocannhgpwg3d")
    result = classify(exp)
    assert result.name == "HN(CO)CA"
    assert result.confidence >= 0.8


def test_classify_ssnmr_nca_nco_by_pulprog() -> None:
    """2D 15N/13C: NCA/NCO distinguished by PULPROG."""
    from core.experiment.experiment_classifier import classify

    nca = classify(_experiment_with_nuclei(2, ["13C", "15N"], "SPECIFIC-CP nca"))
    assert nca.name == "NCA"
    nco = classify(_experiment_with_nuclei(2, ["13C", "15N"], "nco"))
    assert nco.name == "NCO"


def test_classify_ssnmr_3d_correlation_by_pulprog() -> None:
    """3D 15N/13C/13C: NCACX/NCOCX/NCACB/NCOCACB distinguished by PULPROG."""
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
    """3D 13C/15N/13C: CANCO/CAN(CO)CA/CBCANCO distinguished by PULPROG."""
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
    """2D 13C/13C: DARR/PDSD/RFDR/CORD/INADEQUATE/HCC distinguished by PULPROG."""
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
    """HETCOR: the same PULPROG distinguished by nuclei 1H-13C / 1H-15N."""
    from core.experiment.experiment_classifier import classify

    hc = classify(_experiment_with_nuclei(2, ["13C", "1H"], "FSLGhetcor"))
    assert hc.name == "HETCOR"
    hn = classify(_experiment_with_nuclei(2, ["15N", "1H"], "FSLGhetcor"))
    assert hn.name == "HNHETCOR"


def test_classify_ssnmr_tedor_pain_nn() -> None:
    """2D distance restraints: TEDOR/PAIN-CP (15N/13C), NN (15N/15N)."""
    from core.experiment.experiment_classifier import classify

    tedor = classify(_experiment_with_nuclei(2, ["13C", "15N"], "tedor"))
    assert tedor.name == "TEDOR"
    pain = classify(_experiment_with_nuclei(2, ["13C", "15N"], "paincp"))
    assert pain.name == "PAIN-CP"
    nn = classify(_experiment_with_nuclei(2, ["15N", "15N"], "nn"))
    assert nn.name == "NN"




def test_classify_user_title_miss_and_hit() -> None:
    """0.2.199-patch29fd: pdata/title user type -- adopted when there is no hit; when a hit is
    inconsistent but nucleus-compatible the user title wins with a warning; incompatible nuclei
    means the title is ignored."""
    from core.experiment.experiment_classifier import classify

    # No hit (custom PULPROG) -> title hits CBCANH
    miss = classify(
        _experiment_with_nuclei(3, ["1H", "15N", "13C"], "custom123"),
        user_title="CBCANH",
    )
    assert miss.name == "CBCANH"
    assert miss.confidence == 0.75

    # Classification hits HNCACB (0.9), title CBCANH is nucleus-compatible -> user title wins
    hit = classify(
        _experiment_with_nuclei(3, ["1H", "15N", "13C"], "hncacb"),
        user_title="CBCANH",
    )
    assert hit.name == "CBCANH"
    assert "不一致" in hit.evidence[-1] and "请核对" in hit.evidence[-1]

    # Consistent
    same = classify(
        _experiment_with_nuclei(3, ["1H", "15N", "13C"], "hncacb"),
        user_title="HNCACB",
    )
    assert same.name == "HNCACB"
    assert "一致" in same.evidence[-1]

    # title nuclei incompatible (HNCACB on a 2D 1H-15N) -> ignore the title, keep the class
    incompat = classify(
        _experiment_with_nuclei(2, ["1H", "15N"], "hsqc"),
        user_title="HNCACB",
    )
    assert incompat.name == "HSQC"
    assert "忽略标题" in incompat.evidence[-1]


def test_pdata_title_reads_latest_procno(tmp_path: Path) -> None:
    """0.2.199-patch29fd: read the title of the largest procno under pdata."""
    from core.data.bruker_reader import _pdata_title

    root = tmp_path / "dataset"
    (root / "pdata" / "1").mkdir(parents=True)
    (root / "pdata" / "2").mkdir(parents=True)
    (root / "pdata" / "1" / "title").write_text("old name", encoding="utf-8")
    (root / "pdata" / "2" / "title").write_text("CBCANH", encoding="utf-8")
    assert _pdata_title(root) == "CBCANH"
    assert _pdata_title(tmp_path / "no_such") == ""


def test_classify_family_fallback_safe() -> None:
    """0.2.199-patch29fb: with an unknown PULPROG the same-nuclei family falls back to a safe
    family representative, no longer purely Generic."""
    from core.experiment.experiment_classifier import classify

    n15c13 = classify(_experiment_with_nuclei(2, ["13C", "15N"], "unknown_pulprog"))
    assert n15c13.name == "NCA"
    assert 0.4 < n15c13.confidence < 0.6  # awaiting user confirmation
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
    """0.2.199-patch29fb: the 3D 13C/15N/13C family mixes uniform/mixed, so the nuclei cannot
    reveal the sign semantics: it must stay Generic and list the candidates (no wrong family
    representative pick)."""
    from core.experiment.experiment_classifier import classify

    result = classify(
        _experiment_with_nuclei(3, ["13C", "15N", "13C"], "unknown_pulprog")
    )
    assert result.name == "generic_3d"
    assert result.confidence < 0.6
    joined = " ".join(result.evidence)
    assert "NCACX" in joined and "NCACB" in joined and "CANCO" in joined


def test_classify_ssnmr_chhc_nhhc_and_ccc() -> None:
    """2D 1H/1H: CHHC/NHHC distinguished by PULPROG; 3D 13C/13C/13C: CCC unique hit."""
    from core.experiment.experiment_classifier import classify

    chhc = classify(_experiment_with_nuclei(2, ["1H", "1H"], "chhc"))
    assert chhc.name == "CHHC"
    nhhc = classify(_experiment_with_nuclei(2, ["1H", "1H"], "nhhc"))
    assert nhhc.name == "NHHC"
    ccc = classify(_experiment_with_nuclei(3, ["13C", "13C", "13C"], "ccc"))
    assert ccc.name == "CCC"
    assert ccc.confidence >= 0.9  # unique nuclei hit


def test_classify_liquid_not_shadowed_by_solid() -> None:
    """Liquid keywords are not shadowed by solid templates (same nuclei split by PULPROG)."""
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
    """0.2.167: extra templates are ordered precisely by PULPROG (the same nuclei are told apart
    by keywords and do not fall back to Generic)."""
    from core.experiment.experiment_classifier import classify

    cases = [
        # 1H/15N/1H family
        (3, ["1H", "15N", "1H"], "tocsyhsqc", "TOCSY-HSQC-15N"),
        (3, ["1H", "15N", "1H"], "noesyhsqc", "NOESY-HSQC-15N"),
        (3, ["1H", "15N", "1H"], "hbhaconh", "HBHA(CO)NH"),
        (3, ["1H", "15N", "1H"], "hcaconh", "H(CA)NH"),
        (3, ["1H", "15N", "1H"], "hnha", "HNHA"),
        # 1H/13C/1H family
        (3, ["1H", "13C", "1H"], "hcchco", "HCCH-COSY"),
        (3, ["1H", "13C", "1H"], "hcch", "HCCH-TOCSY"),
        (3, ["1H", "13C", "1H"], "noesyhsqc", "NOESY-HSQC-13C"),
        # 1H/13C/13C family
        (3, ["1H", "13C", "13C"], "hcaco", "HCACO"),
        (3, ["1H", "13C", "13C"], "cchtocsy", "CCH-TOCSY"),
        (3, ["1H", "13C", "13C"], "cch", "CCH"),
        (3, ["1H", "13C", "13C"], "ccconh", "C(CCO)NH"),
        # 1H/15N/13C extras
        (3, ["1H", "15N", "13C"], "hncaco", "HN(CA)CO"),
        (3, ["1H", "15N", "13C"], "hcconh", "H(CCO)NH"),
        # 2D edited/other nuclei (1H detected: direct dim is 1H, 13C/15N/31P/19F indirect)
        (2, ["1H", "13C"], "hsqctocsy", "HSQC-TOCSY-13C"),
        (2, ["1H", "15N"], "hsqctocsy", "HSQC-TOCSY-15N"),
        (2, ["1H", "31P"], "hmqc31", "HMQC-31P"),
        (2, ["1H", "31P"], "hmbc31", "HMBC-31P"),
        (2, ["1H", "19F"], "hsqc19", "HSQC-19F"),
        # Solid-state extras
        (2, ["13C", "15N"], "redor", "REDOR"),
    ]
    for ndim, nuclei, pulprog, expected in cases:
        result = classify(_experiment_with_nuclei(ndim, nuclei, pulprog))
        assert result.name == expected, (pulprog, result.name)

def test_classify_same_nucleus_position_sensitive() -> None:
    """0.2.168: the same nucleus is told apart by dimension position (x/y/z index) -- HETCOR (13C
    direct) and HSQC-13C (13C indirect), HNHETCOR (15N direct) and HSQC (15N indirect) no longer
    collide through equal unordered counts and hit a single unique candidate."""
    from core.experiment.experiment_classifier import classify

    # 13C in the direct dimension (F2) -> nucleus sequence (13C, 1H), unique candidate HETCOR
    r = classify(_experiment_with_nuclei(2, ["13C", "1H"], "hsqc"))
    assert r.name == "HETCOR"
    assert any("核组合唯一匹配" in e for e in r.evidence)
    # 15N in the direct dimension (F2) -> nucleus sequence (15N, 1H), unique candidate HNHETCOR
    r = classify(_experiment_with_nuclei(2, ["15N", "1H"], "hsqc"))
    assert r.name == "HNHETCOR"
    # 13C in the indirect dimension (F1) -> (1H, 13C), many candidates still rely on PULPROG
    r = classify(_experiment_with_nuclei(2, ["1H", "13C"], "hsqctocsy"))
    assert r.name == "HSQC-TOCSY-13C"

def test_is_data_directory(tmp_path: Path) -> None:
    '''A directory holding any key Bruker file counts as a data directory; none of them means
    not data (used for ignoring, Task F).'''
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
    '''Container discovery returns only subdirectories holding acqus; non-data subdirectories
    are ignored (Task F).'''
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
    '''With 0 or 1 data subdirectories report a clear error (non-data ones are already
    ignored) rather than a missing acqus.'''
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
    """0.2.198: when the subdirectory parameters differ (independent datasets), say clearly that
    this is not a segmented experiment."""
    import shutil

    from core.data.bruker_reader import read_dataset_container

    container = tmp_path / "container"
    container.mkdir()
    a = container / "a"
    b = container / "b"
    shutil.copytree(bruker_dir / "nus_2d", a)
    shutil.copytree(bruker_dir / "nus_2d", b)
    # Change b's TD so the two segments disagree
    acqu2s = b / "acqu2s"
    text = acqu2s.read_text(encoding="utf-8")
    acqu2s.write_text(text.replace("##$TD= 256", "##$TD= 128"), encoding="utf-8")
    with pytest.raises(ValueError, match="不是分段实验"):
        read_dataset_container(container)

def test_classify_kinetics_by_pulprog() -> None:
    """29hm: a kinetics PULPROG is identified as Kinetics."""
    from core.experiment.experiment_classifier import classify
    exp = _experiment_with_nuclei(2, ["1H", "13C"], "kinetics-2d")
    result = classify(exp)
    assert result.name == "Kinetics"
    assert result.confidence >= 0.9
    assert any("动力学" in e for e in result.evidence)


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
    """IMPORT-007 A: the import is rejected after identification, and no data entry, run or raw
    copy is created."""
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
    with pytest.raises(KineticsUnsupportedError, match="不支持导入"):
        import_data(manager, entry.id, source, copy=True)

    assert entry.data == []
    assert manager.project is not None
    assert manager.project.workflow_runs == []
    assert not (manager.root / entry.id).exists()


def test_non_kinetics_passes_import_policy_guard() -> None:
    """Ordinary experiments are not caught by the Kinetics policy."""
    from workflow.import_workflow import _raise_if_kinetics

    normal = _experiment_with_nuclei(2, ["1H", "13C"], "hsqc")
    _raise_if_kinetics(normal)

def test_vdlist_placeholder_not_kinetics() -> None:
    """patch29hq-fix: acqus.VDLIST holding only D placeholders (Bruker set no variable delay) is
    not judged kinetics."""
    from core.experiment.experiment_classifier import _is_kinetics, classify

    exp = _experiment_with_nuclei(2, ["1H", "13C"], "hncacbgp3d.x")
    exp.acquisition_parameters["acqus"]["VDLIST"] = "DDDDDDDDDDDDDDD"
    assert _is_kinetics(exp) is False
    assert classify(exp).name != "Kinetics"


def test_resolve_sweep_width_treats_non_finite_values_as_missing() -> None:
    """Review 2026-09-24: NaN/Inf are treated as missing -- `float("nan")` is truthy, so the old
    code wrote NaN as the adopted value into fid.com's `-ySW` (or produced a malformed note
    containing `nan%`)."""
    from core.data.bruker_reader import resolve_sweep_width

    hz, source, note = resolve_sweep_width(30.0, float("nan"), 60.8178)
    assert hz == pytest.approx(1824.5346, rel=1e-6)
    assert source == "ppm_x_sfo"
    assert "nan" not in note.lower()

    hz2, source2, _note2 = resolve_sweep_width(float("nan"), 2000.0, 60.8178)
    assert hz2 == 2000.0
    assert source2 == "sw_h"

    hz3, source3, _note3 = resolve_sweep_width(float("inf"), float("inf"), 60.8178)
    assert (hz3, source3) == (0.0, "missing")
