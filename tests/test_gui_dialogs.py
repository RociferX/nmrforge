"""对话框组件测试:导入实验/样本表单/信息/确认(offscreen)。"""

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
