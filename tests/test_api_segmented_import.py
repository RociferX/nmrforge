"""Explicit multi-segment API import, source binding and shared processing routing."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from core.data.bruker_reader import read_segments
from nmrforge_api import (
    DatasetError,
    add_dataset,
    open_study,
    run_parameter_study,
    run_reference_study,
)
from nmrforge_api.cli import main as cli_main
from nmrforge_api.study import _register_conditions, _resolve_conditions
from workflow.stepwise import read_experiment


def _segments(tmp_path: Path, bruker_dir: Path, dataset: str) -> list[Path]:
    # Separate parents verify that no container/discovery assumption leaks into the API.
    paths = [tmp_path / "first" / "segment", tmp_path / "other" / "segment"]
    for path in paths:
        shutil.copytree(bruker_dir / dataset, path)
    return paths


@pytest.mark.parametrize("dataset", ["hsqc_2d", "nus_2d", "hnca_3d", "nus_3d"])
def test_four_kinds_import_one_condition_and_restore_all_segments(
    dataset: str, tmp_path: Path, bruker_dir: Path,
) -> None:
    paths = _segments(tmp_path, bruker_dir, dataset)
    session = open_study(tmp_path / "study", backend=object())
    ref = add_dataset(session, paths, segmented=True, condition="A")
    assert len(session.datasets) == 1
    assert ref.segmented and ref.segments == [str(p.resolve()) for p in paths]
    entry = session.manager.data(ref.exp_id, ref.data_id)
    assert len(entry.segments) == 2
    assert len(session.manager.project.experiments) == 1
    assert len(session.manager.project.experiments[0].data) == 1
    assert not (Path(ref.raw_dir) / "acqus").exists()
    for index, path in enumerate(entry.segments, 1):
        assert Path(path).name == f"{index:02d}"
        assert (Path(path) / "acqus").read_bytes() == (paths[index - 1] / "acqus").read_bytes()
    meta = json.loads(
        session.manager.data_metadata_path(ref.exp_id, ref.data_id).read_text(encoding="utf-8")
    )
    assert meta["source_segments"] == ref.segments
    assert len(meta["segments"]) == 2
    assert all(f"seg_{i:02d}/acqus" in entry.checksums for i in (1, 2))
    reopened = open_study(session.root, backend=object())
    assert reopened.dataset.to_dict() == ref.to_dict()
    experiment = read_experiment(reopened.manager, ref.exp_id, ref.data_id)
    assert len(experiment.segments) == 2
    assert experiment.ndim == ref.ndim
    assert str(experiment.sampling.mode) == ref.sampling
    assert experiment.dataset_id == ref.data_id


def test_default_single_source_is_unchanged(tmp_path: Path, bruker_dir: Path) -> None:
    session = open_study(tmp_path / "study", backend=object())
    ref = add_dataset(session, bruker_dir / "hsqc_2d")
    assert not ref.segmented and not ref.segments
    assert "segmented" not in ref.to_dict() and "segments" not in ref.to_dict()
    assert not session.manager.data(ref.exp_id, ref.data_id).segments


def test_in_place_segments_manifest_covers_all_selected_trees(
    tmp_path: Path, bruker_dir: Path,
) -> None:
    session = open_study(tmp_path / "study", backend=object())
    paths = [session.root / "source" / "first", tmp_path / "other" / "second"]
    for path in paths:
        shutil.copytree(bruker_dir / "hsqc_2d", path)
    ref = add_dataset(session, paths, segmented=True)
    entry = session.manager.data(ref.exp_id, ref.data_id)
    assert entry.raw_dir == ""
    assert entry.segments == [str(path.resolve()) for path in paths]
    metadata = json.loads(
        session.manager.data_metadata_path(ref.exp_id, ref.data_id).read_text(encoding="utf-8")
    )
    manifest = metadata["manifest"]
    expected = {
        f"segments/{index:02d}/{file.relative_to(path).as_posix()}": file.stat().st_size
        for index, path in enumerate(paths, 1)
        for file in path.rglob("*") if file.is_file()
    }
    assert set(manifest["checksums"]) == set(expected)
    assert manifest["file_count"] == len(expected)
    assert manifest["total_bytes"] == sum(expected.values())
    assert metadata["copied_to"] is None


@pytest.mark.parametrize("value", ["true", "false", 1, 0, None])
def test_boolean_is_not_coerced(value, tmp_path: Path, bruker_dir: Path) -> None:
    session = open_study(tmp_path / "study", backend=object())
    with pytest.raises(DatasetError):
        add_dataset(session, bruker_dir / "hsqc_2d", segmented=value)
    assert not session.datasets and not session.manager.project.experiments
    with pytest.raises(DatasetError):
        run_reference_study(tmp_path / "not_created", segmented=value, backend=object())
    assert not (tmp_path / "not_created").exists()


@pytest.mark.parametrize("mode", ["disabled_list", "empty", "one", "duplicate", "string",
                                 "bad_path", "bad_element"])
def test_invalid_sources_are_rejected_before_registration(
    mode: str, tmp_path: Path, bruker_dir: Path,
) -> None:
    path = bruker_dir / "hsqc_2d"
    source = {
        "disabled_list": [path, path], "empty": [], "one": [path],
        "duplicate": [path, path], "string": str(path),
        "bad_path": [path, tmp_path / "missing"], "bad_element": [path, None],
    }[mode]
    session = open_study(tmp_path / "study", backend=object())
    with pytest.raises(DatasetError):
        add_dataset(session, source, segmented=mode != "disabled_list")
    assert not session.datasets and not session.manager.project.experiments


@pytest.mark.parametrize("dataset", ["nus_2d", "nus_3d"])
def test_any_segment_missing_nuslist_is_rejected(
    dataset: str, tmp_path: Path, bruker_dir: Path,
) -> None:
    paths = _segments(tmp_path, bruker_dir, dataset)
    (paths[1] / "nuslist").unlink()
    session = open_study(tmp_path / "study", backend=object())
    with pytest.raises(DatasetError) as error:
        add_dataset(session, paths, segmented=True)
    assert str(paths[1]) in str(error.value)
    assert not session.manager.project.experiments


def test_kinetics_in_any_segment_is_rejected_before_sampling(
    tmp_path: Path, bruker_dir: Path, monkeypatch,
) -> None:
    paths = _segments(tmp_path, bruker_dir, "hsqc_2d")
    with (paths[1] / "acqus").open("a") as handle:
        handle.write("\n##$VDLIST= <delays>\n")

    def must_not_read(*args, **kwargs):
        raise AssertionError("sampling must not be reached")

    monkeypatch.setattr("core.data.bruker_reader.read_dataset", must_not_read)
    session = open_study(tmp_path / "study", backend=object())
    with pytest.raises(DatasetError):
        add_dataset(session, paths, segmented=True)
    assert not session.manager.project.experiments


@pytest.mark.parametrize("file,old,new", [
    ("acqu2s", "##$TD= 256", "##$TD= 128"),
    ("acqus", "##$SW_h= 10000.000000", "##$SW_h= 12000.000000"),
    ("acqus", "##$SFO1= 599.8937495", "##$SFO1= 799.8937495"),
    ("acqus", "##$O1P= 4.703", "##$O1P= 5.703"),
    ("acqu2s", "##$FnMODE= 5", "##$FnMODE= 4"),
])
def test_incompatible_segments_fail_before_import(
    file: str, old: str, new: str, tmp_path: Path, bruker_dir: Path,
) -> None:
    paths = _segments(tmp_path, bruker_dir, "nus_2d")
    target = paths[1] / file
    text = target.read_text()
    assert old in text
    target.write_text(text.replace(old, new))
    session = open_study(tmp_path / "study", backend=object())
    with pytest.raises(DatasetError):
        add_dataset(session, paths, segmented=True)
    assert not session.manager.project.experiments


@pytest.mark.parametrize("reverse", [False, True])
def test_mixed_sampling_is_rejected_in_both_orders(
    reverse: bool, tmp_path: Path, bruker_dir: Path,
) -> None:
    paths = _segments(tmp_path, bruker_dir, "nus_2d")
    (paths[1] / "nuslist").unlink()
    with (paths[1] / "acqus").open("a") as handle:
        handle.write("\n##$FnTYPE= 0\n")
    if reverse:
        paths.reverse()
    with pytest.raises(ValueError):
        read_segments(paths)


def test_total_nus_coverage_uses_union(tmp_path: Path, bruker_dir: Path) -> None:
    paths = _segments(tmp_path, bruker_dir, "nus_2d")
    (paths[1] / "nuslist").write_text("2\n4\n5\n6\n8\n")
    session = open_study(tmp_path / "study", backend=object())
    ref = add_dataset(session, paths, segmented=True)
    experiment = read_experiment(session.manager, ref.exp_id, ref.data_id)
    assert len(experiment.sampling.nus_list) == 10
    assert experiment.sampling.sampling_fraction == pytest.approx(10 / 128)


def test_conditions_are_preflighted_together_and_sources_bind_all_segments(
    tmp_path: Path, bruker_dir: Path,
) -> None:
    paths = _segments(tmp_path, bruker_dir, "hsqc_2d")
    session = open_study(tmp_path / "study", backend=object())
    with pytest.raises(DatasetError):
        _register_conditions(session, [("A", paths), ("B", [paths[0]])], segmented=True)
    assert not session.datasets and not session.manager.project.experiments
    _register_conditions(session, [("A", paths)], segmented=True)
    _register_conditions(session, [("A", paths)], segmented=True)
    assert len(session.datasets) == 1
    third = tmp_path / "third"
    shutil.copytree(paths[1], third)
    for changed in ([paths[0], third], paths[::-1]):
        with pytest.raises(DatasetError):
            _register_conditions(session, [("A", changed)], segmented=True)
    with pytest.raises(DatasetError):
        _register_conditions(session, [("A", paths[0])])
    assert len(session.datasets) == 1


def test_top_level_directory_list_is_one_condition_only_when_enabled(
    tmp_path: Path, bruker_dir: Path,
) -> None:
    paths = _segments(tmp_path, bruker_dir, "hsqc_2d")
    assert _resolve_conditions(paths, None, segmented=True) == [("", paths)]
    assert _resolve_conditions(paths, None) == [("", p) for p in paths]
    with pytest.raises(DatasetError):
        _resolve_conditions({"A": paths}, paths, segmented=True)


@pytest.mark.parametrize("dataset", ["hsqc_2d", "nus_2d"])
def test_reference_and_combinations_route_all_segments_to_backend(
    dataset: str, tmp_path: Path, bruker_dir: Path,
) -> None:
    from test_nmrforge_api import _FakeSweepBackend

    class CaptureBackend(_FakeSweepBackend):
        seen = []

        def convert_to_fid(self, experiment, *args, **kwargs):
            self.seen.append(("convert", list(experiment.segments)))
            return super().convert_to_fid(experiment, *args, **kwargs)

        def process(self, experiment, *args, **kwargs):
            self.seen.append(("process", list(experiment.segments)))
            return super().process(experiment, *args, **kwargs)

        def reconstruct_nus(self, experiment, params=None, progress=None, script_only=False,
                            out_file=None, script_name=None):
            self.seen.append(("nus", list(experiment.segments)))
            return super().reconstruct_nus(
                experiment, params=params, progress=progress, script_only=script_only,
                out_file=out_file, script_name=script_name,
            )

    paths = _segments(tmp_path, bruker_dir, dataset)
    backend = CaptureBackend()
    result = run_parameter_study(
        tmp_path / "study", paths, segmented=True, backend=backend, phase_route="none",
        combos=[{"zero_fill": 1}], sigma_multiplier=10,
    )
    assert result.conditions == ["A"]
    assert result.reference.spectrum_path and result.peak_table_path
    assert len(result.runs) == 1 and not result.failed_runs
    assert result.runs[0].dataset["segments"] == result.session.dataset.segments
    assert backend.seen and all(len(segments) == 2 for _, segments in backend.seen)
    assert sum(stage == "convert" for stage, _ in backend.seen) == 1
    records = json.loads(Path(result.records["manifest"]).read_text(encoding="utf-8"))
    assert records["datasets"][0]["segments"] == [str(p.resolve()) for p in paths]
    again = run_reference_study(
        result.root, paths, segmented=True, backend=backend,
        phase_route="none", sigma_multiplier=10,
    )
    assert again.reference().run_id == result.reference.run_id
    assert len(again.session.datasets) == 1


def test_cli_real_import_produces_one_segmented_condition(
    tmp_path: Path, bruker_dir: Path, capsys,
) -> None:
    paths = _segments(tmp_path, bruker_dir, "hsqc_2d")
    assert cli_main(["init", "--study", str(tmp_path / "study"), "--segmented",
                     "--dataset", str(paths[0]), "--dataset", str(paths[1])]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dataset"]["segmented"] is True
    assert payload["dataset"]["segments"] == [str(p.resolve()) for p in paths]


def test_multi_condition_mapping_routes_each_own_segments(
    tmp_path: Path, bruker_dir: Path,
) -> None:
    from test_nmrforge_api import _FakeSweepBackend

    paths = _segments(tmp_path, bruker_dir, "hsqc_2d")
    result = run_reference_study(
        tmp_path / "study", datasets={"A": paths, "B": paths[::-1]}, segmented=True,
        backend=_FakeSweepBackend(), phase_route="none", sigma_multiplier=10,
    )
    assert result.conditions == ["A", "B"]
    assert len(result.references) == 2
    assert result.session.datasets[0].segments == result.session.datasets[1].segments[::-1]


def test_single_dataset_reference_reuse_does_not_add_another_condition(
    tmp_path: Path, bruker_dir: Path,
) -> None:
    from test_nmrforge_api import _FakeSweepBackend

    args = dict(dataset=bruker_dir / "hsqc_2d", backend=_FakeSweepBackend(), phase_route="none")
    first = run_reference_study(tmp_path / "study", **args)
    second = run_reference_study(tmp_path / "study", **args)
    assert first.reference().run_id == second.reference().run_id
    assert len(second.session.datasets) == 1
