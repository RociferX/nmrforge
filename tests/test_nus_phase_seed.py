"""2D pre-reconstruction bootstrap and its deliberately narrow boundaries."""

from __future__ import annotations

import numpy as np
import pytest

from core.optimization.nus_phase_seed import zero_increment_phase_seed


def _pair(indirect=65.0, direct=-42.0, mixed=False):
    x = np.arange(512, dtype=float)
    trace = sum(
        amplitude / (1 + 1j*(x-centre)/1.2)
        for amplitude, centre in ((100, 80), (-90 if mixed else 90, 240), (80, 410))
    ) * np.exp(1j*np.deg2rad(direct))
    return trace*np.cos(np.deg2rad(indirect)), trace*np.sin(np.deg2rad(indirect))


@pytest.mark.parametrize("indirect", [0, 65, 90, 179, 270])
def test_joint_seed_avoids_quadrature_error(indirect):
    r, i = _pair(indirect)
    result = zero_increment_phase_seed(r, i)
    assert result["accepted"]
    p0 = result["phases"]["F1"][0]
    assert abs((p0 + indirect + 90) % 180 - 90) < 1
    assert result["first_order_inferred"] is False
    assert result["phases"]["F1"][1] == result["direct_phase"][1] == 0
    phased = (r*np.cos(np.deg2rad(p0))-i*np.sin(np.deg2rad(p0)))
    phased *= np.exp(1j*np.deg2rad(result["direct_phase"][0]))
    assert np.min(phased.real[[80, 240, 410]]) > 70


def test_neg_uses_conjugated_indirect_phase():
    r, i = _pair()
    plain = zero_increment_phase_seed(r, i)
    neg = zero_increment_phase_seed(r, i, indirect_neg=True)
    assert neg["accepted"]
    assert neg["phases"]["F1"][0] == pytest.approx(-plain["phases"]["F1"][0] % 360)
    assert neg["direct_phase"] == plain["direct_phase"]


def test_mixed_seed_preserves_opposite_polarities():
    r, i = _pair(mixed=True)
    result = zero_increment_phase_seed(r, i, sign_mode="mixed")
    assert result["accepted"]
    p0 = result["phases"]["F1"][0]
    phased = (r*np.cos(np.deg2rad(p0))-i*np.sin(np.deg2rad(p0)))
    phased *= np.exp(1j*np.deg2rad(result["direct_phase"][0]))
    assert phased.real[80]*phased.real[240] < 0


@pytest.mark.parametrize("scale", [1e-180, 1e180])
def test_seed_scale_invariant(scale):
    r, i = _pair()
    result = zero_increment_phase_seed(r*scale, i*scale)
    expected = zero_increment_phase_seed(r, i)
    assert result["accepted"]
    assert result["direct_phase"] == pytest.approx(expected["direct_phase"])


def test_unrelated_quadrature_noise_is_not_a_seed():
    rng = np.random.default_rng(42)
    result = zero_increment_phase_seed(rng.normal(size=512), rng.normal(size=512))
    assert not result["accepted"]
    assert result["reason"] == "incoherent_zero_increment"


@pytest.mark.parametrize("bad,reason", [
    (np.zeros(512), "empty_zero_increment"),
    (np.full(512, np.nan), "nonfinite_quadrature"),
    (np.zeros(10), "invalid_quadrature_shape"),
])
def test_invalid_pair_is_explicitly_rejected(bad, reason):
    result = zero_increment_phase_seed(bad, bad)
    assert not result["accepted"]
    assert result["reason"] == reason


def test_seed_script_keeps_pair_unphased(bruker_dir):
    from backend.script_generator import generate_2d_nus_seed_script
    from core.data.bruker_reader import read_dataset

    exp = read_dataset(bruker_dir / "nus_2d")
    script = generate_2d_nus_seed_script(exp, in_file="merged/x.fid", out_file="seed.ft1",
                                         ext_lo="9.8", ext_hi="6.2")
    assert "nmrPipe -in merged/x.fid \\\n" in script
    assert "-fn FT" in script and "-x1 9.8ppm -xn 6.2ppm" in script
    for forbidden in ("-fn SMILE", "-fn PS", "-fn TP", "-fn POLY"):
        assert forbidden not in script
    with pytest.raises(ValueError, match="2D NUS"):
        generate_2d_nus_seed_script(read_dataset(bruker_dir / "nus_3d"),
                                   in_file="x.fid", out_file="seed.ft1")


@pytest.mark.parametrize("mode", ["missing_zero", "manual", "disabled", "unsupported"])
def test_backend_seed_skips_without_running_conversion_or_engine(tmp_path, bruker_dir, mode):
    from backend.nmrpipe_backend import NMRPipeBackend
    from core.data.bruker_reader import read_dataset

    exp = read_dataset(bruker_dir / "nus_2d")
    (tmp_path / "nuslist").write_text("1\n2\n")
    params = {}
    if mode == "manual":
        params["direct_phase_override"] = [20, 0]
    elif mode == "disabled":
        params["sampling"] = {"auto_phase": False}
    elif mode == "unsupported":
        exp.acquisition_parameters["acqu2s"]["FnMODE"] = 3

    class NeverRun:
        def run(self, *args, **kwargs):
            pytest.fail("A rejected seed must not run an engine or convert data")

    result = NMRPipeBackend()._phase_seed_2d(
        NeverRun(), exp, tmp_path, "existing.fid", params, [], None
    )
    assert not result["accepted"]
    assert result["backend_runs"] == 0
    assert (tmp_path / "phase_seed.json").is_file()
