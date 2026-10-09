"""Regression checks for the BMRB expected-peak CSV converter."""

import csv
import json

from scripts.bmrb_expected_to_csv import main


def _write_source(path, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "sequence",
                "chem_comp_ID",
                "X_shift",
                "Y_shift",
                "X_atom_name",
                "Y_atom_name",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


def _peak(sequence="1", residue="ALA", h="8.1", n="120.2"):
    return {
        "sequence": sequence,
        "chem_comp_ID": residue,
        "X_shift": h,
        "Y_shift": n,
        "X_atom_name": "H",
        "Y_atom_name": "N",
    }


def test_non_finite_rows_are_skipped_and_reported(tmp_path):
    source = tmp_path / "source.csv"
    output = tmp_path / "expected.csv"
    report = tmp_path / "report.json"
    _write_source(source, [_peak(h="nan"), _peak(sequence="2", h="inf"), _peak(sequence="3")])

    assert main(["--input", str(source), "--output", str(output), "--report", str(report)]) == 0

    with output.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["peak_id"] for row in rows] == ["BMRB_3_ALA"]
    assert json.loads(report.read_text(encoding="utf-8"))["skipped"] == 2


def test_all_non_finite_rows_fail_without_output(tmp_path):
    source = tmp_path / "source.csv"
    output = tmp_path / "expected.csv"
    _write_source(source, [_peak(h="nan"), _peak(sequence="2", n="-inf")])

    assert main(["--input", str(source), "--output", str(output)]) == 1
    assert not output.exists()


def test_input_output_report_paths_must_be_distinct(tmp_path):
    source = tmp_path / "source.csv"
    _write_source(source, [_peak()])
    original = source.read_bytes()

    assert main(["--input", str(source), "--output", str(source)]) == 1
    assert source.read_bytes() == original

    output = tmp_path / "expected.csv"
    assert main(
        ["--input", str(source), "--output", str(output), "--report", str(output)]
    ) == 1
    assert not output.exists()


def test_peak_ids_remain_unique_when_suffix_collides(tmp_path):
    source = tmp_path / "source.csv"
    output = tmp_path / "expected.csv"
    _write_source(source, [_peak(), _peak(), _peak(residue="ALA#2")])

    assert main(["--input", str(source), "--output", str(output)]) == 0
    with output.open(encoding="utf-8-sig", newline="") as handle:
        ids = [row["peak_id"] for row in csv.DictReader(handle)]
    assert len(ids) == len(set(ids)) == 3


def test_header_only_input_fails_without_output(tmp_path):
    source = tmp_path / "source.csv"
    output = tmp_path / "expected.csv"
    _write_source(source, [])

    assert main(["--input", str(source), "--output", str(output)]) == 1
    assert not output.exists()
