"""Peak-file whole-spectrum alignment tests (0.2.199-补29fw)."""

from __future__ import annotations

from pathlib import Path

from workflow.peak_align import (
    MIN_ACCEPTABLE_RATIO,
    TOLERANCE_PPM,
    align_peak_files,
    common_nuclei,
    filter_by_reference,
    row_coords,
    shifted_rows,
)


def _hsqc_rows(
    h_ppm: list[float], n_ppm: list[float]
) -> list[dict]:
    return [
        {"label": "", "H_shift": h, "N_shift": n, "Intensity": 1.0}
        for h, n in zip(h_ppm, n_ppm)
    ]


def test_row_coords_2d_and_3d() -> None:
    row2 = {"H_shift": 8.1, "N_shift": 118.5}
    coords = row_coords(row2)
    assert coords == {"1H": 8.1, "15N": 118.5}
    row3 = {"F1_shift": 118.5, "F2_shift": 8.1, "F3_shift": 42.0}
    coords3 = row_coords(row3, nuclei=["15N", "1H", "13C"])
    assert coords3 == {"15N": 118.5, "1H": 8.1, "13C": 42.0}
    # 3D without nuclei -> no parseable coordinates
    assert row_coords(row3) == {}


def test_common_nuclei_2d_3d() -> None:
    cur = [row_coords({"F1_shift": 118.0, "F2_shift": 8.0, "F3_shift": 40.0},
                      ["15N", "1H", "13C"])]
    ref = [row_coords({"H_shift": 8.0, "N_shift": 118.0})]
    assert common_nuclei(cur, ref) == ["1H", "15N"]


def test_align_recovers_known_shift() -> None:
    """Whole shift recovery: ref = cur + shift (pure translation)."""
    h = [7.0, 7.5, 8.0, 8.2, 8.7, 9.0, 9.4, 10.0, 10.3]
    n = [110.0, 112.5, 115.0, 117.0, 119.0, 121.5, 124.0, 126.0, 128.0]
    cur = _hsqc_rows(h, n)
    dh, dn = 0.40, -1.20  # known whole-spectrum shift (> tolerance)
    ref = _hsqc_rows([x + dh for x in h], [y + dn for y in n])
    result = align_peak_files(cur, ref)
    assert result["status"] == "ok"
    assert result["ratio"] >= MIN_ACCEPTABLE_RATIO
    assert abs(result["shift"]["1H"] - dh) <= 0.05
    assert abs(result["shift"]["15N"] - dn) <= 0.25
    assert result["total_min"] == min(len(cur), len(ref)) == len(cur)


def test_align_majority_shift_with_outliers() -> None:
    """A few peaks with large real displacement must not drag the shift."""
    h = [7.0, 7.5, 8.0, 8.2, 8.7, 9.0, 9.4, 10.0]
    n = [110.0, 112.5, 115.0, 117.0, 119.0, 121.5, 124.0, 126.0]
    cur = _hsqc_rows(h, n)
    dh, dn = 0.30, -0.80  # majority whole shift
    ref_h = [x + dh for x in h]
    ref_n = [y + dn for y in n]
    # one outlier peak moves differently (large displacement)
    ref_h[0] += 0.8
    ref_n[0] -= 2.0
    ref = _hsqc_rows(ref_h, ref_n)
    result = align_peak_files(cur, ref)
    assert result["status"] == "ok"
    assert result["matched"] >= 6
    assert abs(result["shift"]["1H"] - dh) <= 0.05
    assert abs(result["shift"]["15N"] - dn) <= 0.25


def test_align_2d_cur_3d_ref_uses_common_nuclei() -> None:
    """2D current vs 3D reference: match on 1H/15N only."""
    h = [7.2, 8.0, 8.6, 9.1]
    n = [111.0, 116.0, 121.0, 126.0]
    cur = _hsqc_rows(h, n)
    ref = []
    for hi, ni, ci in zip(
        [7.2, 8.0, 8.6, 9.1],
        [111.0, 116.0, 121.0, 126.0],
        [30.0, 45.0, 52.0, 40.0],
    ):
        ref.append({"F1_shift": ni, "F2_shift": hi, "F3_shift": ci})
    result = align_peak_files(
        cur, ref, ref_nuclei=["15N", "1H", "13C"]
    )
    assert result["nuclei"] == ["1H", "15N"]
    assert result["status"] == "ok"
    assert result["ratio"] >= 0.9


def test_low_ratio_when_spectra_dissimilar() -> None:
    """Few matching peaks -> status low (check similarity)."""
    cur = _hsqc_rows([7.0, 8.0, 9.0], [110.0, 115.0, 120.0])
    # reference peaks far away from every current peak (larger than the
    # +-5 ppm search window after considering the range)
    ref = _hsqc_rows([10.0, 10.5, 11.0], [128.0, 130.0, 132.0])
    result = align_peak_files(cur, ref)
    assert result["status"] in ("low", "no_common")
    if result["status"] == "low":
        assert result["ratio"] < MIN_ACCEPTABLE_RATIO


def test_shifted_rows_and_filter() -> None:
    rows = _hsqc_rows([8.0, 8.5], [115.0, 118.0])
    shift = {"1H": 0.05, "15N": 0.2}
    shifted = shifted_rows(rows, shift)
    assert shifted[0]["H_shift"] == 8.05
    assert shifted[0]["N_shift"] == 115.2
    # reference contains only the second peak (after shift)
    ref = _hsqc_rows([8.55, 9.9], [118.2, 130.0])
    kept, stats = filter_by_reference(
        rows, ref, shift, tol_ppm={"1H": 0.1, "15N": 0.5}
    )
    assert len(kept) == 1
    assert kept[0]["H_shift"] == 8.5
    assert stats["removed"] == 1


def test_ratio_denominator_is_smaller_list() -> None:
    """Denominator = min(current count, reference count)."""
    cur = _hsqc_rows([7.0, 7.5, 8.0, 8.4, 8.8], [110.0, 113.0, 116.0,
                                                 119.0, 122.0])
    ref = _hsqc_rows([7.0, 7.5, 8.0], [110.0, 113.0, 116.0])
    result = align_peak_files(cur, ref)
    assert result["total_min"] == 3
    assert result["matched"] <= 3
    assert result["ratio"] == result["matched"] / 3.0


def test_alignment_figure_writes_png(tmp_path: Path) -> None:
    """Alignment check figure lands as a PNG under the given path."""
    from workflow.peak_align import (
        align_peak_files,
        alignment_figure,
        matched_pairs,
    )

    cur = _hsqc_rows([8.0, 8.5, 9.0], [118.0, 121.0, 124.0])
    ref = _hsqc_rows([8.03, 8.52], [118.1, 121.2])
    result = align_peak_files(cur, ref, ref_nuclei=["15N", "1H"])
    pairs, nuclei = matched_pairs(cur, ref, result["shift"])
    assert nuclei == ["1H", "15N"]
    assert len(pairs) == min(len(cur), len(ref))
    out = tmp_path / "d_001_aligned_d_002.png"
    written = alignment_figure(
        cur,
        ref,
        result["shift"],
        out,
        ref_nuclei=["15N", "1H"],
        cur_label="d_001",
        ref_label="d_002",
    )
    assert written == out
    assert out.is_file()
    assert out.stat().st_size > 1000

def test_constants() -> None:
    """0.2.199-补29fx:对齐容差按 Poky kr 默认(1H ±0.02,其它核 ±0.2)。"""
    assert MIN_ACCEPTABLE_RATIO == 0.60
    assert TOLERANCE_PPM["1H"] == 0.02
    for nucleus in ("2H", "15N", "13C", "19F", "31P", "23Na", "29Si"):
        assert TOLERANCE_PPM[nucleus] == 0.2


def test_matched_pairs_uses_original_row_indices() -> None:
    """0.2.199-补29fx:缺共同核行被矩阵跳过时,配对必须返回原行下标
    (否则检查图会把连线画到错误的峰上)。"""
    from workflow.peak_align import matched_pairs

    cur = [
        {"1H": 7.2},  # 缺 15N,不能参与共同核匹配 → 被矩阵跳过
        {"1H": 8.0, "15N": 118.0},
    ]
    ref = [{"1H": 8.0, "15N": 118.0}]
    pairs, nuclei = matched_pairs(cur, ref, {})
    assert nuclei == ["1H", "15N"]
    assert pairs == [(1, 0)]
