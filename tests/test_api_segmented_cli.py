"""CLI dispatch for explicit segmented dataset imports."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from nmrforge_api import cli


def _fake_study(root: Path) -> SimpleNamespace:
    return SimpleNamespace(root=root, datasets=[])


def test_init_single_dataset_keeps_default_add_dataset_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    study_root = tmp_path / "study"
    session = _fake_study(study_root)
    captured: dict[str, object] = {}

    monkeypatch.setattr(cli, "open_study", lambda *args, **kwargs: session)

    def fake_add_dataset(study, source, **kwargs):
        captured.update(study=study, source=source, kwargs=kwargs)
        return SimpleNamespace(condition="A", to_dict=lambda: {"condition": "A"})

    monkeypatch.setattr(cli, "add_dataset", fake_add_dataset)

    assert cli.main(["init", "--study", str(study_root), "--dataset", "part-1"]) == 0
    output = capsys.readouterr()
    assert json.loads(output.out)["condition"] == "A"
    assert output.err == ""
    assert captured == {
        "study": session,
        "source": "part-1",
        "kwargs": {"condition": "", "title": ""},
    }


def test_init_segmented_forwards_ordered_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    study_root = tmp_path / "study"
    session = _fake_study(study_root)
    captured: dict[str, object] = {}

    monkeypatch.setattr(cli, "open_study", lambda *args, **kwargs: session)

    def fake_add_dataset(study, source, **kwargs):
        captured.update(study=study, source=source, kwargs=kwargs)
        return SimpleNamespace(condition="A", to_dict=lambda: {"condition": "A"})

    monkeypatch.setattr(cli, "add_dataset", fake_add_dataset)

    assert cli.main(
        [
            "init", "--study", str(study_root), "--dataset", "part-2", "--dataset",
            "part-1", "--segmented",
        ]
    ) == 0
    output = capsys.readouterr()
    assert json.loads(output.out)["condition"] == "A"
    assert output.err == ""
    assert captured == {
        "study": session,
        "source": ["part-2", "part-1"],
        "kwargs": {"condition": "", "title": "", "segmented": True},
    }


@pytest.mark.parametrize(
    ("dataset_args", "segmented"),
    [(["--dataset", "one", "--dataset", "two"], False), ([], True),
     (["--dataset", "one"], True)],
)
def test_init_rejects_invalid_directory_count_before_creating_project(
    tmp_path: Path,
    dataset_args: list[str],
    segmented: bool,
    capsys: pytest.CaptureFixture[str],
) -> None:
    study_root = tmp_path / "study"
    argv = ["init", "--study", str(study_root), *dataset_args]
    if segmented:
        argv.append("--segmented")

    assert cli.main(argv) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err
    assert not study_root.exists()
