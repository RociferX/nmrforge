"""1D 相位与 FID 转换修复回归测试(0.2.199-补29gk)。

- acqus TD=0 时回退到 acqu 权威 TD(修复 fid.com xN=0 转换卡死);
- dominant_absorption_ratio:1D 新旧相位择优用的主峰吸收比。
"""

import numpy as np

from core.data.bruker_reader import _build_dimensions
from core.experiment.bruker_parser import parse_dataset_params
from core.optimization.phase_search import (
    dominant_absorption_ratio,
    orient_dominant_positive,
)


def test_dimensions_acqus_td_zero_falls_back_to_acqu(tmp_path: object) -> None:
    """acqus TD=0 但 acqu TD 正常时,直接维 TD 应取 acqu(TDP43 13 号数据)。"""
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
    """acqus TD=0 且无 acqu 时,TD 保持 0(由转换侧守卫中止,不卡死)。"""
    import pytest  # noqa: F401

    # 无 acqu 回退源:直接构造最小字典
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
    """主峰为负(向下)时,p0 应翻转 180 让峰向上(正吸收)。"""
    n = 64
    spec = np.zeros(n, dtype=complex)
    spec[32] = -1.0 + 0.0j  # 向下/负峰
    assert orient_dominant_positive(spec, 0.0, 0.0) == 180.0
    spec[32] = 1.0 + 0.0j  # 向上/正峰
    assert orient_dominant_positive(spec, 0.0, 0.0) == 0.0
    # p0=180 时,负峰(原始 -1)旋转后已为正(+1,向上),无需再翻
    spec[32] = -1.0 + 0.0j
    assert orient_dominant_positive(spec, 180.0, 0.0) == 180.0


def test_dominant_absorption_ratio() -> None:
    """主峰吸收比:纯实峰 -> 1(吸收),旋转 90° -> 0(色散)。"""
    n = 64
    spec = np.zeros(n, dtype=complex)
    spec[32] = 1.0 + 0.0j
    assert dominant_absorption_ratio(spec, 0.0, 0.0) > 0.99
    assert dominant_absorption_ratio(spec, 90.0, 0.0) < 0.1
