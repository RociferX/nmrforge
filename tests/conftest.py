"""Shared fixtures and test category marking (Phase 12).

The **single source** of test categories is ``tests/categories.py``; here every test gets its
``unit`` / ``integration`` / ``regression`` mark from the file name, so ``pytest -m unit`` and
friends can select a subset.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import types
from pathlib import Path

import pytest

from core.data.internal_data_model import (
    AxisRole,
    Dimension,
    Experiment,
    Sampling,
    SamplingMode,
)
from ui_support.i18n import default_language


def _load_test_categories() -> types.ModuleType:
    """Load the category table (``tests/categories.py``) via importlib rather than import, so the
    test directory never enters ``sys.path``."""
    spec = importlib.util.spec_from_file_location(
        "nmrforge_test_categories", Path(__file__).with_name("categories.py")
    )
    if spec is None or spec.loader is None:  # pragma: no cover - the file always exists
        raise RuntimeError("找不到 tests/categories.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# The rendered interface language must not depend on the machine's locale. The shared suite
# asserts the Chinese message catalogue (it is the private trunk's language), and this tree ships
# that catalogue in full (``ui_support/locales/zh.json``), so tests pin ``zh`` as well: the runtime
# default of this tree stays ``en`` (``ui_support/locales/default.json``) and the English rendering
# is covered by ``tests/test_ui_i18n.py``, which switches languages explicitly.
# (2026-09-25 sync note: the previous per-tree default pin made the shared suite fail here on
# ~280 assertions that quote the Chinese catalogue; making those assertions catalogue-driven is a
# follow-up, this pin keeps the gate meaningful in the meantime.)
# The runtime default of this tree (``en``) still comes from ``ui_support/locales/default.json``;
# only the test language is pinned separately below.
TREE_DEFAULT_LANGUAGE = default_language()
os.environ["NMRFORGE_LANG"] = "zh"


TEST_CATEGORIES = _load_test_categories()
category_of = TEST_CATEGORIES.category_of


@pytest.fixture(scope="session")
def test_categories() -> types.ModuleType:
    """The category table (the Phase 12 single source), read by the completeness guard test."""
    return TEST_CATEGORIES


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Phase 12: mark every test with its category from ``tests/categories.py``.

    The marks are added centrally in conftest so 100+ test files do not each need a ``pytestmark``
    line (the category table is the single source, and ``tests/test_test_categories.py`` guards
    against anything being left unregistered).
    """
    for item in items:
        marker = category_of(Path(str(item.fspath)).name)
        item.add_marker(getattr(pytest.mark, marker))


@pytest.fixture
def hsqc_experiment() -> Experiment:
    """Build a minimal 2D HSQC-style Experiment (used by the skeleton stage)."""
    return Experiment(
        dataset_id="exp_001",
        source_path=Path("/fake/bruker/1"),
        ndim=2,
        dimensions=[
            Dimension(logical_axis="F1", nucleus="15N", td=128, role=AxisRole.INDIRECT),
            Dimension(logical_axis="F2", nucleus="1H", td=1024, role=AxisRole.DIRECT),
        ],
        sampling=Sampling(mode=SamplingMode.UNIFORM),
    )


FIXTURES_BRUKER = Path(__file__).parent / "fixtures" / "bruker"

NMRPIPE_FID_FDSIZE = 1024
NMRPIPE_FID_SPECNUM = 120


@pytest.fixture(scope="session")
def nmrpipe_fid_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Template for the real NMRPipe 2D single-file fid layout (machine independent).

    A real conversion product is "a 2048-byte parameter header + a real block / imaginary block per
    trace": the header carries ``FDDIMCOUNT=2``, ``FDSIZE`` = complex points in the direct
    dimension, ``FDSPECNUM`` = number of traces, ``FDQUADFLAG=0``. The key detail: nmrglue decodes
    the real data of ``(specnum, 2*fdsize)`` back into complex ``(specnum, fdsize)`` only when
    ``FDF2QUADFLAG=0``; keeping the ``create_empty_dic()`` default of 1 reads it as real, while the
    2D branch of ``workflow.direct_diagnostics._read_fid_raw`` requires complex -- which is exactly
    why the synthetic template was never accepted before (unrelated to the parser).

    The data is handed to ``ng.pipe.write`` as complex64: it expands it by real/imaginary block into
    "2048-byte header + float32", byte-for-byte the same layout as a real file (the size is kept
    small for test speed; the layout matches the real 862-trace file).
    """
    import nmrglue as ng
    import numpy as np

    path = tmp_path_factory.mktemp("nmrpipe_fid") / "template.fid"
    fdsize, specnum = NMRPIPE_FID_FDSIZE, NMRPIPE_FID_SPECNUM
    dic = ng.pipe.create_empty_dic()
    dic["FDDIMCOUNT"] = 2
    dic["FDSIZE"] = fdsize
    dic["FDSPECNUM"] = specnum
    dic["FDQUADFLAG"] = 0
    dic["FDF2QUADFLAG"] = 0
    rng = np.random.default_rng(20260916)
    t = np.arange(fdsize, dtype=float)
    signal = np.exp(-t / 150.0) * np.exp(2j * np.pi * 0.12 * t)
    data = np.tile(signal, (specnum, 1)) * rng.uniform(0.5, 2.0, (specnum, 1))
    data = data + rng.normal(0.0, 0.02, (specnum, fdsize))
    data = data + 1j * rng.normal(0.0, 0.02, (specnum, fdsize))
    ng.pipe.write(str(path), dic, data.astype(np.complex64), overwrite=True)

    # The template must really be accepted by the diagnostics parser, otherwise the fixture itself
    # is wrong (the earlier synthetic approach failed twice)
    from workflow.direct_diagnostics import _read_fid_raw

    parsed = _read_fid_raw(path)
    assert parsed is not None, "合成 fid 模板必须能被 _read_fid_raw 解析(真实布局)"
    _data, got_fdsize, got_specnum, header = parsed
    assert (got_fdsize, got_specnum) == (fdsize, specnum)
    assert header == 2048, header
    return path


@pytest.fixture
def bruker_dir(tmp_path: Path) -> Path:
    """Bruker test dataset fixture directory (each test gets its own copy).

    Linking the shared fixture directly would accumulate the file hard link count up to the NTFS
    limit (1024) and make os.link fail; the copy keeps links on a per-test inode
    (0.2.162-patch13)."""
    copy = tmp_path / "bruker"
    if not copy.exists():
        shutil.copytree(FIXTURES_BRUKER, copy)
    return copy


@pytest.fixture(autouse=True)
def _isolate_machine_settings(tmp_path: Path, monkeypatch) -> None:
    """Keep probe and GUI configuration writes inside each case's temporary directory."""
    from backend import config
    from core import app_paths
    from gui import settings

    original = app_paths.local_config_path

    def isolated(filename: str = "nmrforge.local.yaml", *, packaged=None) -> Path:
        frozen = app_paths.is_frozen() if packaged is None else bool(packaged)
        if frozen:
            return original(filename, packaged=packaged)
        return tmp_path / "machine-config" / filename

    monkeypatch.setattr(settings, "local_config_path", isolated)
    monkeypatch.setattr(config, "local_config_path", isolated)


@pytest.fixture(autouse=True)
def _clear_cancel_between_tests() -> None:
    """0.2.199-patch29hg: clear the backend cancel flag before each test, so the previous test
    (e.g. the GUI stop button) cannot leak _CANCEL into the next processing/phase-search test and
    cause a false "task cancelled" report."""
    from backend.runtime import clear_cancel

    clear_cancel()
    yield


_QAPP_STRONG_REF = None


@pytest.fixture(scope="session")
def qapp():
    """One Qt application per test process, retained through session cleanup."""
    global _QAPP_STRONG_REF

    from qtcompat.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    _QAPP_STRONG_REF = app
    yield app


def _qt_widgets_loaded() -> bool:
    """Whether this session really loaded Qt (checks sys.modules, triggers no new import)."""
    return any(name in sys.modules for name in ("PySide6.QtWidgets", "PyQt6.QtWidgets"))


@pytest.fixture(scope="session", autouse=True)
def _close_gui_windows_at_session_end() -> None:
    """Close every leftover top-level window and process events before the session ends.

    On the offscreen platform, leftover top-level windows (including the import / inter-group
    analysis drop-down Tool windows restored in 0.2.194) are destroyed in an unpredictable order
    when the interpreter exits, which intermittently triggers a Qt access violation (0xC0000005);
    an explicit teardown close removes that jitter.

    Only run the teardown when the session really used Qt: the CI release-readiness job runs plain
    text tests only and installs no Qt runtime library, so an unconditional import would raise
    ``ImportError: libEGL.so.1`` in teardown and turn the whole report red (measured 2026-09-17).
    """
    yield
    if not _qt_widgets_loaded():
        return
    from qtcompat.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return
    for widget in list(app.topLevelWidgets()):
        try:
            widget.close()
        except RuntimeError:  # pragma: no cover - already destroyed
            pass
    app.processEvents()
    from shiboken6 import getAllValidWrappers

    # Qt dispatches events while destroying parent/child objects. Retain their
    # Python wrappers until native destruction finishes, before Python finalizes.
    wrappers = getAllValidWrappers()
    app.shutdown()
    del wrappers
