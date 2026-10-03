"""Conversion provenance is evidence collection, not a numerical correction decision."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from backend.conversion_provenance import conversion_audit, read_conversion_provenance
from nmrforge_api.records import _reference_record
from nmrforge_api.reference import ReferenceSpectrum


def test_conversion_audit_collects_acquisition_values_and_actual_script(
    tmp_path: Path,
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    script = work / "fid.com"
    script_bytes = (
        b"#!/bin/csh\n"
        b"bruk2pipe \\\n"
        b"  -in ser -out sample.fid \\\n"
        b"  -AMX -decim 16 -dspfvs 12 -grpdly 67.984 -ext\n"
    )
    script.write_bytes(script_bytes)
    segment_script = work / "seg_002" / "fid.com"
    segment_script.parent.mkdir()
    segment_script.write_text("bruk2pipe -in ser2 -out part.fid\n", encoding="utf-8")
    raw_script = work / "raw" / "fid.com"
    raw_script.parent.mkdir()
    raw_script.write_text("bruk2pipe -in raw.ser -out raw.fid\n", encoding="utf-8")

    experiment = SimpleNamespace(
        acquisition_parameters={
            "acqus": {
                "GRPDLY": 67.984,
                "DECIM": 16,
                "DSPFVS": 12,
                "BYTORDA": 0,
                "DTYPA": 0,
            },
            "acqu2s": {"GRPDLY": -1, "DECIM": 32},
        }
    )

    audit = conversion_audit(experiment, work)

    assert audit["digital_filter"]["status"] == "unknown"
    assert audit["digital_filter"]["method"] is None
    assert audit["digital_filter"]["acquisition_parameters"] == {
        "acqus": {
            "GRPDLY": 67.984,
            "DECIM": 16,
            "DSPFVS": 12,
            "BYTORDA": 0,
            "DTYPA": 0,
        },
        "acqu2s": {
            "GRPDLY": -1,
            "DECIM": 32,
            "DSPFVS": None,
            "BYTORDA": None,
            "DTYPA": None,
        },
    }
    scripts = {item["path"]: item for item in audit["scripts"]}
    assert set(scripts) == {"fid.com", "seg_002/fid.com"}
    assert scripts["fid.com"]["sha256"] == hashlib.sha256(script_bytes).hexdigest()
    command = scripts["fid.com"]["bruk2pipe_commands"][0]
    assert command["arguments"] == [
        "-in",
        "ser",
        "-out",
        "sample.fid",
        "-AMX",
        "-decim",
        "16",
        "-dspfvs",
        "12",
        "-grpdly",
        "67.984",
        "-ext",
    ]
    assert command["parsed_values"] == {
        "decim": "16",
        "dspfvs": "12",
        "grpdly": "67.984",
        "amx": True,
        "ext": True,
    }


def test_read_conversion_provenance_keeps_sidecar_snapshot_over_current_script(
    tmp_path: Path,
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    (work / "fid.com").write_text("changed since conversion\n", encoding="utf-8")
    original_audit = {
        "digital_filter": {"status": "unknown", "method": None},
        "scripts": [{"path": "fid.com", "sha256": "original"}],
    }
    sidecar = work / "sample.fid.conversion.json"
    sidecar.write_text(
        json.dumps({"conversion_provenance": original_audit}), encoding="utf-8"
    )

    result = read_conversion_provenance(work)

    assert result["sidecars"][0]["path"] == "sample.fid.conversion.json"
    assert result["sidecars"][0]["conversion_provenance"] == original_audit
    assert result["sidecars"][0]["conversion_provenance"]["scripts"][0]["sha256"] == "original"

    reference_record = _reference_record(
        ReferenceSpectrum(
            dataset_key="sample", exp_id="exp", data_id="sample", work_dir=str(work)
        )
    )
    assert reference_record["conversion_provenance"] == result
