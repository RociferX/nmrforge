"""1D phase and FID conversion repair regression test (0.2.199-patch29gk). - Fall back to acqu
authority TD when acqus TD=0 (fix fid.com xN=0 conversion stuck); - dominant_absorption_ratio:
Main peak absorption ratio for 1D new and old phases."""

import numpy as np

from core.data.bruker_reader import _build_dimensions
from core.experiment.bruker_parser import parse_dataset_params
from core.optimization.phase_search import (
    dominant_absorption_ratio,
    orient_dominant_positive,
)


def test_dimensions_acqus_td_zero_falls_back_to_acqu(tmp_path: object) -> None:
    """Acqus TD=0 but when acqu TD is normal, direct dimension TD should be acqu(TDP43 No. 13
    data)."""
    import pytest  # noqa: F401

    dst = tmp_path / "td_fallback"
    dst.mkdir()
    (dst / "acqus").write_text(
        "##$PULPROG= zg\n"
        "##$TD= 0\n"
        "##$NUC1= 1H\n"
        "##$PARMODE= 0\n"
        "##$SFO1= 600.1332\n"
        "##$O1= 3200\n"
        "##$SW_h= 11904.7619047619\n"
        "##END=\n",
        encoding="utf-8",
    )
    (dst / "acqu").write_text(
        "##$TD= 8\n"
        "##$NUC1= 1H\n"
        "##END=\n",
        encoding="utf-8",
    )
    params = parse_dataset_params(dst)
    dims = _build_dimensions(params, 1)
    assert dims[0].td == 8


def test_dimensions_acqus_td_zero_no_acqu_keeps_zero() -> None:
    """When acqus TD=0 and there is no acqu, TD remains 0 (stopped by the conversion side guard and
    not stuck)."""
    import pytest  # noqa: F401

    # No acqu fallback source: directly construct the minimum dictionary.
    params = {
        "acqus": {
            "TD": "0",
            "NUC1": "1H",
            "SFO1": "600.1332",
            "O1": "3200",
            "SW_h": "11904.7619",
        },
        "order": ["acqus"],
    }
    dims = _build_dimensions(params, 1)
    assert dims[0].td == 0


def test_orient_dominant_positive() -> None:
    """When the main peak is negative (downward), p0 should be flipped 180 to make the peak upward
    (positive absorption)."""
    n = 64
    spec = np.zeros(n, dtype=complex)
    spec[32] = -1.0 + 0.0j  # Down/negative peak.
    assert orient_dominant_positive(spec, 0.0, 0.0) == 180.0
    spec[32] = 1.0 + 0.0j  # Up/Zhengfeng.
    assert orient_dominant_positive(spec, 0.0, 0.0) == 0.0
    # When p0=180, the negative peak (original -1) is already positive (+1, upward) after rotation,
    # and there is no need to turn it again.
    spec[32] = -1.0 + 0.0j
    assert orient_dominant_positive(spec, 180.0, 0.0) == 180.0


def test_dominant_absorption_ratio() -> None:
    """Main peak absorption ratio: pure peak -> 1 (absorption), rotated 90° -> 0 (dispersion)."""
    n = 64
    spec = np.zeros(n, dtype=complex)
    spec[32] = 1.0 + 0.0j
    assert dominant_absorption_ratio(spec, 0.0, 0.0) > 0.99
    assert dominant_absorption_ratio(spec, 90.0, 0.0) < 0.1
