"""1D phase and FID conversion repair regression test (0.2.199-patch29gk).

- Fall back to the authoritative acqu TD when acqus TD=0 (fixes the fid.com
  xN=0 conversion hang);
- dominant_absorption_ratio: main-peak absorption ratio used to pick the better
  phase for 1D old/new data.
"""

import numpy as np

from core.data.bruker_reader import _build_dimensions
from core.experiment.bruker_parser import parse_dataset_params
from core.optimization.phase_search import (
    dominant_absorption_ratio,
    orient_dominant_positive,
)


def test_dimensions_acqus_td_zero_falls_back_to_acqu(tmp_path: object) -> None:
    """acqus TD=0 but acqu TD is normal: direct-dim TD should take acqu (TDP43 dataset 13)."""
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
    """acqus TD=0 and no acqu: TD stays 0 (the conversion-side guard aborts instead of hanging)."""
    import pytest  # noqa: F401

    # No acqu fallback source: build the minimal dict directly
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
    """Negative main peak (pointing down): p0 flips 180 to point the peak up (absorption)."""
    n = 64
    spec = np.zeros(n, dtype=complex)
    spec[32] = -1.0 + 0.0j  # Down / negative peak
    assert orient_dominant_positive(spec, 0.0, 0.0) == 180.0
    spec[32] = 1.0 + 0.0j  # Up / positive peak
    assert orient_dominant_positive(spec, 0.0, 0.0) == 0.0
    # At p0=180 the negative peak (original -1) rotates to positive (+1, up): no further flip
    spec[32] = -1.0 + 0.0j
    assert orient_dominant_positive(spec, 180.0, 0.0) == 180.0


def test_dominant_absorption_ratio() -> None:
    """Main peak absorption ratio: pure real peak -> 1, rotated 90° -> 0 (dispersion)."""
    n = 64
    spec = np.zeros(n, dtype=complex)
    spec[32] = 1.0 + 0.0j
    assert dominant_absorption_ratio(spec, 0.0, 0.0) > 0.99
    assert dominant_absorption_ratio(spec, 90.0, 0.0) < 0.1
