"""Dialog widget tests: import experiment type / project form / info / confirm (offscreen)."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from qtcompat.QtWidgets import QApplication

from gui.dialogs import ConfirmDialog, ImportExperimentDialog, InfoDialog


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    yield app


def test_import_dialog_result_data(qapp: QApplication) -> None:
    dialog = ImportExperimentDialog(None, samples=[("S001", "sample A")])
    dialog.source_edit.setText(str(Path.home()))
    dialog.title_edit.setText("HSQC")
    dialog.sample_combo.setCurrentIndex(1)
    data = dialog.result_data()
    assert data["source"] == str(Path.home())
    assert data["title"] == "HSQC"
    assert data["sample_id"] == "S001"
    dialog.close()


def test_import_dialog_validation_empty(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    messages: list[str] = []
    monkeypatch.setattr(
        "gui.dialogs.InfoDialog.show_info",
        staticmethod(lambda parent, title, text: messages.append(text)),
    )
    dialog = ImportExperimentDialog(None)
    dialog._validate_and_accept()
    assert messages and "目录" in messages[0]
    assert dialog.result() != 1  # not accepted
    dialog.close()


def test_import_dialog_copy_defaults_checked(qapp: QApplication) -> None:
    dialog = ImportExperimentDialog(None)
    assert dialog.copy_check.isChecked() is True
    dialog.copy_check.setChecked(False)
    dialog.source_edit.setText(str(Path.home()))
    assert dialog.result_data()["copy"] is False
    dialog.close()


def test_import_dialog_validation_requires_acqus(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    messages: list[str] = []
    monkeypatch.setattr(
        "gui.dialogs.InfoDialog.show_info",
        staticmethod(lambda parent, title, text: messages.append(text)),
    )
    dialog = ImportExperimentDialog(None)
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    dialog.source_edit.setText(str(dataset))
    dialog._validate_and_accept()
    assert messages and "acqus" in messages[0]
    assert dialog.result() != 1
    (dataset / "acqus").write_text("##SIMPLE 1\n", encoding="utf-8")
    dialog._validate_and_accept()
    assert dialog.result() == 1
    dialog.close()


def test_import_dialog_segmented_container(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.108: the container-directory import validates and auto-checks segmented acquisition."""
    container = tmp_path / "container"
    container.mkdir()
    for seg in ("seg1", "seg2"):
        (container / seg).mkdir()
        (container / seg / "acqus").write_text("x", encoding="utf-8")
    dialog = ImportExperimentDialog(None)
    dialog.source_edit.setText(str(container))
    assert dialog.segmented_check.isChecked()
    monkeypatch.setattr(
        "gui.dialogs.InfoDialog.show_info",
        staticmethod(lambda *args, **kwargs: None),
    )
    dialog._validate_and_accept()
    assert dialog.result() == dialog.DialogCode.Accepted
    assert dialog.result_data()["segmented"] is True
    dialog.close()


def test_confirm_dialog_returns_exec(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ConfirmDialog, "exec", lambda self: 1)
    assert ConfirmDialog.confirm(None, "t", "x") is True
    monkeypatch.setattr(ConfirmDialog, "exec", lambda self: 0)
    assert ConfirmDialog.confirm(None, "t", "x") is False


def test_info_dialog_constructs(qapp: QApplication) -> None:
    dialog = InfoDialog(None, "标题", "内容")
    assert dialog.windowTitle() == "标题"
    dialog.accept()
    assert dialog.result() == 1


def test_settings_dialog_trimmed_and_linewidth_saved(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.112: the settings dialog drops SMILE threads/zero-fill, keeps line width and saves."""
    from gui.dialogs import SettingsDialog

    monkeypatch.setattr(
        "gui.settings.load_settings",
        lambda: {"linewidth_hz": {"1H": 8, "15N": 15, "13C": 20}, "pipeline": {}},
    )
    dialog = SettingsDialog(None)
    assert set(dialog.linewidth_spins) == {"1H", "15N", "13C"}
    assert set(dialog.tolerance_spins) == {"1H", "15N", "13C"}
    assert not hasattr(dialog, "ppl_spin")
    assert not hasattr(dialog, "smile_spin")
    # 2026-09-21 (user): the UI language can be pinned in settings (auto/zh/en)
    assert [dialog.language_combo.itemData(i) for i in range(dialog.language_combo.count())] == [
        "auto",
        "zh",
        "en",
    ]
    assert dialog.language_combo.currentData() == "auto"
    saved: dict = {}

    def fake_save(settings: dict) -> None:
        saved.update(settings)

    monkeypatch.setattr("gui.settings.save_settings", fake_save)
    dialog._on_accept()
    assert saved["language"] == "auto"
    assert "linewidth_hz" in saved
    assert "alignment_tolerance_ppm" in saved
    assert saved["alignment_tolerance_ppm"]["1H"] == 0.02
    assert "data_root" in saved  # 0.2.199-patch29gg
    assert "points_per_line" not in saved
    assert "smile_thread_cap" not in saved
    dialog.close()


def test_settings_dialog_preselects_the_saved_language(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the config says zh/en, the settings dialog preselects that entry."""
    from gui.dialogs import SettingsDialog

    monkeypatch.setattr(
        "gui.settings.load_settings",
        lambda: {"language": "en", "linewidth_hz": {"1H": 8, "15N": 15, "13C": 20}, "pipeline": {}},
    )
    dialog = SettingsDialog(None)
    assert dialog.language_combo.currentData() == "en"
    dialog.close()


def test_settings_dialog_persists_the_chosen_language(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The settings dialog writes the UI language into the config file (user-verified path:
    switch language → restart → takes effect).

    Regression: ``gui.settings._merged_view`` used to drop the scalar key language, so
    saving wrote the old value back: the dialog looked successful but the language never
    changed after a restart.
    """
    import yaml

    from gui import settings as gui_settings
    from gui.dialogs import SettingsDialog

    local = tmp_path / "nmrforge.local.yaml"
    local.write_text("language: zh\n", encoding="utf-8")
    monkeypatch.setattr(gui_settings, "_settings_path", lambda: local)
    # save_settings refreshes the language-layer cache; keep it away from the user's real config
    monkeypatch.setattr("ui_support.i18n.read_user_preference", lambda path=None: None)

    dialog = SettingsDialog(None)
    assert dialog.language_combo.currentData() == "zh"
    dialog.language_combo.setCurrentIndex(dialog.language_combo.findData("en"))
    dialog._on_accept()

    assert yaml.safe_load(local.read_text(encoding="utf-8"))["language"] == "en"
    assert gui_settings.load_settings()["language"] == "en"
    dialog.close()


def test_settings_dialog_preselects_locale_style_language(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When the config says zh_CN, the combo preselects the Chinese entry, not "follow system"."""
    from gui import settings as gui_settings
    from gui.dialogs import SettingsDialog

    local = tmp_path / "nmrforge.local.yaml"
    local.write_text("language: zh_CN\n", encoding="utf-8")
    monkeypatch.setattr(gui_settings, "_settings_path", lambda: local)
    dialog = SettingsDialog(None)
    assert dialog.language_combo.currentData() == "zh"
    dialog.close()


def test_dialog_centered_on_screen(qapp: QApplication) -> None:
    """0.2.112: an application-level filter moves popups to the centre of their screen."""
    from qtcompat.QtCore import QEventLoop, QTimer
    from qtcompat.QtWidgets import QDialog

    from gui.dialogs import install_dialog_centering

    app = QApplication.instance()
    install_dialog_centering(app)
    dialog = QDialog()
    dialog.show()
    center: tuple[int, int] | None = None
    loop = QEventLoop()

    def _check() -> None:
        nonlocal center
        c = dialog.frameGeometry().center()
        center = (c.x(), c.y())
        loop.quit()

    QTimer.singleShot(60, _check)
    loop.exec()
    geo = app.primaryScreen().availableGeometry()
    assert center is not None
    assert abs(center[0] - geo.center().x()) <= 2
    assert abs(center[1] - geo.center().y()) <= 2
    dialog.close()


def test_import_dialog_browse_starts_at_data_root(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.199-patch29gg: the import "Browse..." dialog starts in the data root directory."""
    from qtcompat.QtWidgets import QFileDialog

    from gui import settings as settings_module
    from gui.dialogs import ImportExperimentDialog

    monkeypatch.setattr(
        settings_module,
        "load_settings",
        lambda: {"data_root": str(tmp_path)},
    )
    starts: list[str] = []

    def fake_existing(parent, title, start="", *_a, **_k):
        starts.append(str(start))
        return str(tmp_path)

    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", staticmethod(fake_existing)
    )
    dialog = ImportExperimentDialog(None)
    dialog._browse()
    assert starts == [str(tmp_path)]
    assert dialog.source_edit.text() == str(tmp_path)
    dialog.close()
