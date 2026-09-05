"""对话框组件测试:导入实验类型/项目表单/信息/确认(offscreen)。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

from gui.dialogs import ConfirmDialog, ImportExperimentDialog, InfoDialog, SampleDialog


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
    assert dialog.result() != 1  # 未接受
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
    """0.2.108:容器目录导入校验通过并自动勾选分段采集。"""
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


def test_sample_dialog_result_data(qapp: QApplication) -> None:
    dialog = SampleDialog(None, sample_id="S001")
    dialog.name_edit.setText("sample B")
    dialog.protein_edit.setText("Ubq")
    dialog.concentration_edit.setText("100.5")
    dialog.buffer_edit.setText("PBS")
    data = dialog.result_data()
    assert data["name"] == "sample B"
    assert data["protein_name"] == "Ubq"
    assert data["concentration_um"] == 100.5
    assert data["buffer"] == "PBS"
    dialog.close()


def test_sample_dialog_requires_name(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    messages: list[str] = []
    monkeypatch.setattr(
        "gui.dialogs.InfoDialog.show_info",
        staticmethod(lambda parent, title, text: messages.append(text)),
    )
    dialog = SampleDialog(None)
    dialog._validate_and_accept()
    assert messages and "名称" in messages[0]
    assert dialog.result() != 1
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
    """0.2.112:设置对话框移除 SMILE 线程/填零,保留线宽并保存。"""
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
    saved: dict = {}

    def fake_save(settings: dict) -> None:
        saved.update(settings)

    monkeypatch.setattr("gui.settings.save_settings", fake_save)
    dialog._on_accept()
    assert "linewidth_hz" in saved
    assert "alignment_tolerance_ppm" in saved
    assert saved["alignment_tolerance_ppm"]["1H"] == 0.02
    assert "data_root" in saved  # 0.2.199-补29gg
    assert "points_per_line" not in saved
    assert "smile_thread_cap" not in saved
    dialog.close()


def test_dialog_centered_on_screen(qapp: QApplication) -> None:
    """0.2.112:应用级过滤器把弹窗移到所在屏幕中心。"""
    from PyQt6.QtCore import QEventLoop, QTimer
    from PyQt6.QtWidgets import QDialog

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
    """0.2.199-补29gg:导入「浏览...」默认起点=数据总目录。"""
    from PyQt6.QtWidgets import QFileDialog

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
