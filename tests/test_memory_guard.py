"""SMILE memory estimation and guardrail tests (0.2.112, calibrated from VM measurements)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from backend.memory_guard import (
    MEM_SAFETY,
    direct_points_after_ext,
    estimate_smile_peak_mb,
    memory_guard,
)


@dataclass
class _Dim:
    logical_axis: str
    sw: float
    sf: float


@dataclass
class _Exp:
    dimensions: list
    ndim: int


def _exp_hncacb() -> _Exp:
    # 3D HNCACB direct dimension 1H: SW≈8196.7Hz, SF=600.13MHz → SW≈13.66ppm
    return _Exp(
        dimensions=[_Dim("F3", 8196.721, 600.133), _Dim("F2", 0, 0), _Dim("F1", 0, 0)],
        ndim=3,
    )


def test_direct_points_after_ext() -> None:
    exp = _exp_hncacb()
    # 2048 points, window 4ppm/13.66ppm ≈ 0.293 → ~600 points
    # (VM-measured baseline: 600 → 665MB)
    pts = direct_points_after_ext(exp, 2048, 10.5, 6.5)
    assert 550 <= pts <= 650, pts
    # Window 1ppm → ~150 points (VM measured 150→179MB)
    pts2 = direct_points_after_ext(exp, 2048, 8.0, 7.0)
    assert 130 <= pts2 <= 180, pts2
    # Full size when there is no EXT
    pts3 = direct_points_after_ext(exp, 2048, None, None)
    assert pts3 == 2048


def test_estimate_smile_peak_mb_calibrated() -> None:
    # 0.2.199-patch15: model = direct-dim points × indirect-dim iterative FT size
    # product × 16B × 1.06, matching the Memory Used that SMILE reports at startup.
    # HNCACB (NusTD product 4000, FT 256×256): 600 points → ~636MB (measured 665)
    m600 = estimate_smile_peak_mb(3, 600, [80, 50])
    assert 600 <= m600 <= 680, m600
    m2048 = estimate_smile_peak_mb(3, 2048, [80, 50])
    assert 2100 <= m2048 <= 2350, m2048
    # sampleK reference point: 168 points, NusTD(292,290) → matches SMILE's 2.8GB
    # self-report
    ref = estimate_smile_peak_mb(3, 168, [292, 290])
    assert 2700 <= ref <= 3000, ref
    # Conservative 2D lower bound
    assert estimate_smile_peak_mb(2, 640, 256) == 128.0


def test_dev_smile_memory_ceiling() -> None:
    """0.2.199-patch15: dev-environment SMILE test memory ceiling is 2.8GB (documented
    constraint).

    The dev VM (16GB, unstable 32GB host) has a verified safe peak of ≈2.8GB
    (zero-fill 1024, sampleK, SMILE self-reported Memory Used); ≥5.6GB (zero-fill
    2048, direct dimension doubled) triggers an unexpected host power-off. Test data
    and reruns must keep the estimated peak ≤ 2.8GB.
    """
    ref = estimate_smile_peak_mb(3, 168, [292, 290])
    assert 2700 <= ref <= 2800 * 1.03  # Matches SMILE self-report, not near crash magnitude
    # Test fixture nus_3d (NusTD 48/128) is far below the ceiling
    fixture = estimate_smile_peak_mb(3, 200, [48, 128])
    assert fixture < 2800
    # Crash magnitude (zero-fill 2048, direct dim doubled) must be far above the
    # ceiling (documented warning)
    crash = estimate_smile_peak_mb(3, 336, [292, 290])
    assert crash >= 2 * ref


def test_memory_guard_ok() -> None:
    res = memory_guard(3, 600, 4000, available_mb=4096)
    assert res["ok"] is True
    assert res["message"] == ""


def test_memory_guard_insufficient_message() -> None:
    # 512MB available, SMILE peak ~690MB → over the limit, needed=1GB
    res = memory_guard(3, 600, 4000, available_mb=512)
    assert res["ok"] is False
    assert res["needed_gb"] == 1
    assert "请至少提供 1 GB 内存" in res["message"]
    # Large grid / large direct dimension → needs more
    res2 = memory_guard(3, 2048, 4000, available_mb=1024)
    assert res2["ok"] is False
    assert res2["needed_gb"] >= 2


def test_memory_guard_respects_safety_factor() -> None:
    # Peak 690MB, available 812MB×0.85≈690 → critical
    res = memory_guard(3, 600, 4000, available_mb=812)
    assert res["ok"] is (690.0 <= 812 * MEM_SAFETY)


def test_memory_guard_markers_are_translated_and_match_both_producers(monkeypatch) -> None:
    """Guard markers must match the producer messages in **both languages**.

    The consumer is ``gui/pipeline_panel.py`` (``MEMORY_GUARD_MARKERS``); the producers
    are ``backend/memory_guard.py`` (SMILE peak over limit, takes the real message from
    the API) and ``backend/nmrpipe_backend.py`` (intermediate spectra/ramdisk, message
    lives in the source). The 2026-09-21 runtime-isation of the messages briefly changed
    the check to match a single English phrase only: the English build matched, the
    Chinese one never did, and the guard silently failed -- this test pins down that
    correspondence.
    """
    import ast

    from ui_support import i18n

    root = Path(__file__).resolve().parents[1]
    # The markers are taken from the consumer source (the tr() literals inside
    # `_looks_like_memory_guard`) so the test does not carry its own copy. The second
    # producer (backend/nmrpipe_backend.py) also keeps its message in the source; take
    # it out by marker prefix.
    guard_tree = ast.parse((root / "gui" / "pipeline_panel.py").read_text(encoding="utf-8"))
    markers: tuple[str, ...] = ()
    for node in ast.walk(guard_tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_looks_like_memory_guard":
            markers = tuple(
                child.args[0].value
                for child in ast.walk(node)
                if isinstance(child, ast.Call)
                and isinstance(child.func, ast.Name)
                and child.func.id == "tr"
                and child.args
                and isinstance(child.args[0], ast.Constant)
            )
    assert markers, "gui/pipeline_panel.py 里找不到护栏判定片段"
    source = (root / "backend" / "nmrpipe_backend.py").read_text(encoding="utf-8")
    producer_keys: dict[str, str] = {}
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        for marker in markers:
            if marker not in producer_keys and node.value.startswith(marker):
                producer_keys[marker] = node.value
    assert producer_keys, "backend/nmrpipe_backend.py 里找不到护栏文案"

    monkeypatch.setattr(i18n, "_language", None)
    try:
        for language in ("en", "zh"):
            i18n.set_language(language)
            message = memory_guard(3, 2048, 4000, available_mb=256)["message"]
            assert message, language
            assert any(i18n.tr(marker) in message for marker in markers), (
                f"{language}: 护栏片段匹配不到 backend/memory_guard.py 的文案"
            )
            for marker, key in producer_keys.items():
                assert i18n.tr(marker) in i18n.tr(key), (language, marker)
    finally:
        i18n.set_language(None)
