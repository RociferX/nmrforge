"Regression coverage: module."

from __future__ import annotations

from pathlib import Path

from backend.environment_probe import (
    OPTIONAL_TOOLS,
    REQUIRED_TOOLS,
    EnvironmentReport,
    SmileStatus,
    apply_to_settings,
    environment_warning,
    probe_environment,
    resolve_tool,
    tool_path_from_settings,
)


def _full_report() -> EnvironmentReport:
    "Regression coverage:  full report."
    return EnvironmentReport(
        nmrpipe_bin="/opt/nmrpipe/bin",
        tools={
            "nmrPipe": "/opt/nmrpipe/bin/nmrPipe",
            "bruker": "/opt/nmrpipe/com/bruker",
            "proj3D.tcl": "/opt/nmrpipe/com/proj3D.tcl",
        },
        smile=SmileStatus(available=True, version="2.0 beta Rev 2018.094.15.20"),
    )


# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
def test_probe_environment_never_raises() -> None:
    "Regression coverage: test probe environment never raises."
    report = probe_environment()

    assert isinstance(report.nmrpipe_bin, str)
    assert isinstance(report.tools, dict)
    assert isinstance(report.smile, SmileStatus)

    assert set(report.missing_required()) <= set(REQUIRED_TOOLS)
    assert set(report.missing_optional()) <= set(OPTIONAL_TOOLS)


def test_probe_environment_usable_reflects_required_tools() -> None:
    "Regression coverage: test probe environment usable reflects required tools."
    assert _full_report().usable is True

    no_smile = EnvironmentReport(
        nmrpipe_bin="/opt/nmrpipe/bin",
        tools={"nmrPipe": "/x/nmrPipe", "bruker": "/x/bruker"},
    )
    assert no_smile.usable is True

    assert no_smile.missing_optional() == ["proj3D.tcl"]
    assert no_smile.missing_features() == ["SMILE", "proj3D.tcl"]

    no_bruker = EnvironmentReport(
        nmrpipe_bin="/opt/nmrpipe/bin",
        tools={"nmrPipe": "/x/nmrPipe"},
    )
    assert no_bruker.usable is False
    assert no_bruker.missing_required() == ["bruker"]


def test_smile_is_not_probed_as_an_executable() -> None:
    "Regression coverage: test smile is not probed as an executable."
    assert "smile" not in OPTIONAL_TOOLS
    assert "smile" not in REQUIRED_TOOLS


# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
def test_warning_is_empty_when_everything_found() -> None:
    "Regression coverage: test warning is empty when everything found."
    text, required = environment_warning(_full_report())

    assert text == ""
    assert required is False


def test_warning_required_when_bruker_missing() -> None:
    "Regression coverage: test warning required when bruker missing."
    report = EnvironmentReport(
        nmrpipe_bin="/opt/nmrpipe/bin",
        tools={"nmrPipe": "/x/nmrPipe", "proj3D.tcl": "/x/proj3D.tcl"},
        smile=SmileStatus(available=True, version="2.0"),
    )

    text, required = environment_warning(report)

    assert required is True
    assert "bruker" in text


def test_warning_optional_when_smile_plugin_missing() -> None:
    "Regression coverage: test warning optional when smile plugin missing."
    report = EnvironmentReport(
        nmrpipe_bin="/opt/nmrpipe/bin",
        tools={
            "nmrPipe": "/x/nmrPipe",
            "bruker": "/x/bruker",
            "proj3D.tcl": "/x/proj3D.tcl",
        },
        smile=SmileStatus(available=False),
    )

    text, required = environment_warning(report)

    assert required is False
    assert "SMILE" in text

    assert "path" not in text.lower() or "plugin" in text.lower()


def test_required_missing_hides_optional_noise() -> None:
    "Regression coverage: test required missing hides optional noise."
    text, required = environment_warning(EnvironmentReport())

    assert required is True
    assert "SMILE" not in text


# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
def test_apply_to_settings_fills_empty_fields(tmp_path: Path, monkeypatch) -> None:
    "Regression coverage: test apply to settings fills empty fields."
    from gui import settings as gui_settings

    monkeypatch.setattr(gui_settings, "_settings_path", lambda: tmp_path / "s.yaml")

    written = apply_to_settings(_full_report())

    assert written.get("nmrpipe_path") == "/opt/nmrpipe/bin"
    assert written.get("bruker_path") == "/opt/nmrpipe/com/bruker"
    assert written.get("proj3d_path") == "/opt/nmrpipe/com/proj3D.tcl"

    assert written.get("smile_status") == "available"
    assert written.get("smile_version") == "2.0 beta Rev 2018.094.15.20"


def test_apply_to_settings_records_missing_smile(tmp_path: Path, monkeypatch) -> None:
    "Regression coverage: test apply to settings records missing smile."
    from gui import settings as gui_settings

    monkeypatch.setattr(gui_settings, "_settings_path", lambda: tmp_path / "s.yaml")
    report = _full_report()
    report.smile = SmileStatus(available=False, detail="unknown function SMILE")

    written = apply_to_settings(report)

    assert written.get("smile_status") == "missing"
    assert gui_settings.load_settings()["smile_status"] == "missing"


def test_apply_to_settings_never_overwrites_user_value(tmp_path: Path, monkeypatch) -> None:
    "Regression coverage: test apply to settings never overwrites user value."
    from gui import settings as gui_settings

    monkeypatch.setattr(gui_settings, "_settings_path", lambda: tmp_path / "s.yaml")
    gui_settings.save_settings({**gui_settings.load_settings(), "bruker_path": "/my/own/bruker"})

    written = apply_to_settings(_full_report())

    assert "bruker_path" not in written
    assert gui_settings.load_settings()["bruker_path"] == "/my/own/bruker"


def test_apply_to_settings_writes_nothing_when_probe_empty(tmp_path: Path, monkeypatch) -> None:
    "Regression coverage: test apply to settings writes nothing when probe empty."
    from gui import settings as gui_settings

    monkeypatch.setattr(gui_settings, "_settings_path", lambda: tmp_path / "s.yaml")

    written = apply_to_settings(EnvironmentReport())

    assert "bruker_path" not in written
    assert "nmrpipe_path" not in written


# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
def test_tool_path_from_settings_maps_each_tool() -> None:
    "Regression coverage: test tool path from settings maps each tool."
    view = {
        "nmrpipe_path": "/a/bin",
        "bruker_path": "/a/com/bruker",
        "proj3d_path": "/a/com/proj3D.tcl",
    }

    assert tool_path_from_settings(view, "nmrPipe") == "/a/bin"
    assert tool_path_from_settings(view, "bruker") == "/a/com/bruker"
    assert tool_path_from_settings(view, "proj3D.tcl") == "/a/com/proj3D.tcl"
    assert tool_path_from_settings(view, "unknown") == ""

    assert tool_path_from_settings(view, "smile") == ""


def test_resolve_tool_prefers_existing_configured_path(tmp_path: Path) -> None:
    "Regression coverage: test resolve tool prefers existing configured path."
    real = tmp_path / "bruker"
    real.write_text("#!/bin/sh\n", encoding="utf-8")

    found = resolve_tool("bruker", {"bruker_path": str(real)})

    assert found == real.resolve()


def test_resolve_tool_falls_back_when_configured_path_is_gone(
    tmp_path: Path,
) -> None:
    "Regression coverage: test resolve tool falls back when configured path is gone."
    missing = tmp_path / "no-such-bruker"

    found = resolve_tool("bruker", {"bruker_path": str(missing)})

    assert Path(str(found or "")) != missing

    assert found is None or found.is_file()
