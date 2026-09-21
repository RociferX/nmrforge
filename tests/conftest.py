"""Shared fixtures and test classification marking (Phase 12). The **single source** of test
classification is ``tests/categories.py``; here, each use case is marked with ``unit`` /
``integration`` / ``regression`` according to the file name, so ``pytest -m unit`` and so on can
select subsets."""

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
    """Load the classification table (``tests/categories.py``); use importlib instead of import to
    avoid testing directory into sys.path."""
    spec = importlib.util.spec_from_file_location(
        "nmrforge_test_categories", Path(__file__).with_name("categories.py")
    )
    if spec is None or spec.loader is None:  # pragma: no cover - File must exist.
        raise RuntimeError("tests/categories.py not found")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# The rendered interface language must not depend on the machine's locale: pin the language
# this tree declares (``ui_support/locales/default.json``: private repository = zh, public
# English tree = en) unless the environment already asks for something else.
os.environ.setdefault("NMRFORGE_LANG", default_language())


TEST_CATEGORIES = _load_test_categories()
category_of = TEST_CATEGORIES.category_of


@pytest.fixture(scope="session")
def test_categories() -> types.ModuleType:
    """Classification table (Phase 12 single source), read by integrity guard tests."""
    return TEST_CATEGORIES


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Phase 12: Press ``tests/categories.py`` to mark each use case with a category. The marks are
    added uniformly in conftest to avoid writing one line for each of 100+ test files
    ``pytestmark`` (the classification table is from a single source, and
    ``tests/test_test_categories.py`` ensures that no registration is missed)."""
    for item in items:
        marker = category_of(Path(str(item.fspath)).name)
        item.add_marker(getattr(pytest.mark, marker))


@pytest.fixture
def hsqc_experiment() -> Experiment:
    """Construct a 2D HSQC style minimal Experiment (used in the skeleton stage)."""
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
    """Real NMRPipe 2D single file fid layout template (does not rely on any development machine
    file). The real conversion product is "2048-byte parameter header + real part block /
    imaginary part block per trace": in the header ``FDDIMCOUNT=2``, ``FDSIZE``= direct
    dimension complex points, ``FDSPECNUM``=trace number, ``FDQUADFLAG=0``. Key details: nmrglue
    only converts it when ``FDF2QUADFLAG=0`` ``(specnum, 2*fdsize)``'s real data is decoded back
    into ``(specnum, fdsize)``; keeping the default value of 1 in ``create_empty_dic()`` will
    read as real, while ``workflow.direct_diagnostics._read_fid_raw``'s two-dimensional branch
    requires a copy -- this is why the synthesized template was never accepted before (it has
    nothing to do with the parser). The data is handed over to ``ng.pipe.write``:real part/The
    imaginary block is expanded into using complex64 "2048 byte header + float32", which is the
    same as the real file The layout is the same byte by byte (the size is smaller for testing
    speed, and the layout is consistent with the real file of 862 traces)."""
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

    # The template must really be accepted by the diagnostic parser, otherwise the fixture itself is
    # wrong (the previous synthetic writing method failed twice).
    from workflow.direct_diagnostics import _read_fid_raw

    parsed = _read_fid_raw(path)
    assert parsed is not None, (
        "Synthetic fid templates must be parsable by _read_fid_raw (real layout)"
    )
    _data, got_fdsize, got_specnum, header = parsed
    assert (got_fdsize, got_specnum) == (fdsize, specnum)
    assert header == 2048, header
    return path



@pytest.fixture
def bruker_dir(tmp_path: Path) -> Path:
    """Bruker test dataset fixture directory (one copy for each test). Directly linking to the
    shared fixture will cause the number of file hard links to accumulate to the NTFS upper
    limit (1024), causing os.link to fail; the complex data ensures that the link is built on an
    independent inode for each test (0.2.162-patch13)."""
    copy = tmp_path / "bruker"
    if not copy.exists():
        shutil.copytree(FIXTURES_BRUKER, copy)
    return copy


@pytest.fixture(autouse=True)
def _clear_cancel_between_tests() -> None:
    """0.2.199-patch29hg: Clear the backend cancellation flag before each test starts to avoid the
    previous test (such as GUI stop button) from leaking _CANCEL to the next processing/phase
    search test causing "task canceled" false alarm."""
    from backend.runtime import clear_cancel

    clear_cancel()
    yield


def _qt_widgets_loaded() -> bool:
    """Whether Qt has really been loaded in this session (check sys.modules, no new import will be
    triggered)."""
    return any(name in sys.modules for name in ("PySide6.QtWidgets", "PyQt6.QtWidgets"))


@pytest.fixture(scope="session", autouse=True)
def _close_gui_windows_at_session_end() -> None:
    """Close all remaining top-level windows and handle events before the end of the session. Under
    the offscreen platform, the remaining top-level windows (including 0.2.194 Restored
    import/Between-group analysis drop-down Tool windows) are destroyed in an uncertain order
    when the interpreter exits, which will intermittently trigger Qt access violations
    (0xC0000005); explicit closing can eliminate this jitter. Only end the session when Qt has
    actually been used: CI's release-readiness job only runs plain text Use case, if the Qt
    runtime library is not installed, unconditional import will throw ``ImportError:
    libEGL.so.1`` in the teardown and make the entire report turn red (measured on 2026-09-17)."""
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
        except RuntimeError:  # pragma: no cover - Destroyed.
            pass
    app.processEvents()
