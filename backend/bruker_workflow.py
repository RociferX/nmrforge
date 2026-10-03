"""NMRPipe bruker workflow helpers: fid.com parameter parsing and the acqus cross-check.

Ported from NMRFlow's processing/bruker_workflow.py and adapted to the NMRForge internal data
model. Used to verify and patch the fid.com that bruker -AUTO generates (acqus is the
authoritative parameter source).
"""

from __future__ import annotations

import logging
import math
import re
from pathlib import Path
from typing import Any

from core.data.bruker_dtype import UnknownBrukerDtype, sample_itemsize
from core.data.internal_data_model import AxisRole, Experiment, SamplingMode
from core.data.ser_layout import solve_row_points
from core.experiment.acquisition_mode_detector import (
    DIRECT_BRUK2PIPE_MODE,
    KNOWN_FNMODE_MAX,
    bruk2pipe_mode_for,
    same_mode_family,
    time_domain_points,
)
from core.experiment.pulse_pathways import (
    canonical_negated,
    handedness_for,
    mode_symbol_audit,
)
from ui_support.i18n import tr

_KEY_RE = re.compile(
    r"-(xN|yN|zN|xT|yT|zT|xSW|ySW|zSW|xOBS|yOBS|zOBS|xCAR|yCAR|zCAR|"
    r"xLAB|yLAB|zLAB|xMODE|yMODE|zMODE|decim|dspfvs|grpdly)\s+(\S+)"
)

_OUT_RE = re.compile(r"(-out\s+)(\S+)")


logger = logging.getLogger("nmrforge.backend.bruker_workflow")

#: bruk2pipe acquisition mode keys, plus the E-A spellings bruker -AUTO parses out of the
#: pulse program (com/pprog.tcl).
_MODE_KEYS = ("xMODE", "yMODE", "zMODE")
_EA_MODES = ("Echo-AntiEcho", "Rance-Kay")
_ROW_GEOMETRY_KEYS = ("xN", "yN", "zN", "xT", "yT", "zT")
_DSP_KEYS = ("decim", "dspfvs", "grpdly")

# ----------------------------------------------------------- acquisition mode (MODE) convention
# 2026-09-24 (maintainer's parameter-source table + real-data review): the indirect dimensions'
# ``-yMODE``/``-zMODE`` take acqNs ``FnMODE`` as their single source
# (``acquisition_mode_detector.bruk2pipe_mode_for``). bruker -AUTO's E-A decision also comes
# from FnMODE, but it lives in the "pulse program parsing" of ``com/pprog.tcl``
# (``FnMODE==6`` -> Echo-AntiEcho, ``FnMODE`` 1/7 -> Real, 2..5 -> Complex) and that part only
# runs when the pulse program file exists and can be parsed (the ``rdPProg``/``parsePProg``
# call sites in ``com/nih.tcl``). Real-data review:
#   * all 33 deposited datasets with a ``pulseprogram`` (FnMODE=6): AUTO writes Echo-AntiEcho,
#     matching our rule -- the earlier conclusion that "AUTO always writes Complex for E-A
#     data" came from probe copies with the pulse program stripped; corrected 2026-09-24;
#   * another 19 synthetic probes with FnMODE=6 but only ``acqus+acqu2s+ser`` in the directory
#     (Synth*/TwoPeaks/SingleEA etc.): 17 of them have no readable pulse program, so AUTO
#     degrades to ``Complex`` (our rule still gives E-A).
# => the FnMODE single source gives the correct header annotation in both cases; but never
# silently downgrade an ``Echo-AntiEcho`` AUTO already recognised into ``Complex`` (see
# patch_fid_com): that would make downstream process the whole dataset with a wrong encoding.

# ------------------------------------------------------------------- carrier (CAR) convention
# 2026-09-24 (software design fixed by the maintainer): CAR always comes from that dimension's
# acqus ``O1/BF1`` -- the computed spectral centre the operator set -- so spectra of one
# experiment acquired at different times share one referencing convention. ``bruker -AUTO``'s
# "water peak (TE) + gamma ratio" value is corroborating evidence only (it is exact only when
# the transmitter sits on the water peak; measured 13C deviations reach 2.6-3.1 ppm) and is
# used in the report to say which convention the script's present value came from. Differences
# from that present value within ``CAR_WRITE_TOLERANCE`` count as display precision (untouched,
# not reported); manually edited keys win.

#: The gamma-ratio table of NMRPipe ``com/conv.tcl`` (``gammaList``; copied from
#: ``~/pipe/com/conv.tcl``). For an **indirect dimension**, ``bruker -AUTO`` does not take
#: ``acquNs(O1)/BF1``; it maps the direct-dimension 1H carrier onto that nucleus' ppm axis by
#: ``CARy = (1e6*SFO1y - (SFO1x*1e6 - SFO1x*CARx) * gy/gx) / SFO1y``
#: the gamma ratio (checked one by one on 6 indirect dimensions of real data, reproduction
#: error <= 0.001 ppm).
GAMMA_RATIOS: dict[str, float] = {
    "H": 1.0,
    "1H": 1.0,
    "H1": 1.0,
    "2H": 0.153506088,
    "H2": 0.153506088,
    "C": 0.251449530,
    "13C": 0.251449530,
    "C13": 0.251449530,
    "N": 0.101329118,
    "15N": 0.101329118,
    "N15": 0.101329118,
    "P": 0.40480864,
    "31P": 0.40480864,
    "P31": 0.40480864,
}

#: Default referencing convention (software design fixed by the maintainer 2026-09-24): CAR
#: always comes from that dimension's acqus ``O1/BF1`` -- i.e. **the computed spectral centre
#: the operator set**. Rationale: spectra of one experiment acquired at different times share
#: one convention and are not dragged around by ``bruker -AUTO``'s "water peak (TE) + gamma
#: ratio" approximation (exact only when the transmitter sits exactly on the water peak;
#: measured 1H carrier/water-peak difference 0.04-0.13 ppm, 3.14 ppm for d_018, and up to
#: 2.6-3.1 ppm on measured 13C dimensions). The written value, the script's present value and
#: their difference are reported per dimension in the "generate FID" report
#: (``status: REFERENCE_OVERRIDE``).
CAR_REFERENCE_RULE = "acquisition_center"
#: Tolerance (ppm) between the script's present value and this dimension's ``O1/BF1``: the
#: present value is itself a `%.3f` quantised number, so one last digit (0.001 ppm) is display
#: precision -- not an override and not reported.
CAR_WRITE_TOLERANCE = 0.0015
#: Match tolerance (ppm) between the script's present value and the "water peak + gamma ratio"
#: derived value: used **only** in the report to say which convention the present value came
#: from (water peak / elsewhere); it does not decide which value is used.
CAR_MATCH_TOLERANCE = 0.02
#: Reference statuses (report design by the maintainer 2026-09-24): overridden / present value
#: already equals the spectral centre / manual value / key missing from the script / spectral
#: centre not computable.
REFERENCE_OVERRIDE = "REFERENCE_OVERRIDE"
REFERENCE_KEPT = "REFERENCE_KEPT"
REFERENCE_MANUAL = "REFERENCE_MANUAL"
REFERENCE_MISSING = "REFERENCE_MISSING"
REFERENCE_UNKNOWN = "REFERENCE_UNKNOWN"
#: Statuses that changed a value in the report (written into fid.com, listed as corrections).
_REFERENCE_WRITTEN = (REFERENCE_OVERRIDE, REFERENCE_MISSING)


def gamma_ratio(nucleus: str) -> float | None:
    """Nucleus name (``<15N>`` style also accepted) -> gamma ratio; None when unknown."""
    return GAMMA_RATIOS.get(str(nucleus).strip("<>").strip().upper())


def water_shift_ppm(temperature: float) -> float:
    """NMRPipe ``getH2Oppm``: water-peak curve vs DSS as a function of temperature (K)."""
    return -0.009552 * (float(temperature) - 273.0) + 5.011718


def gamma_mapped_car(
    reference_ppm: float,
    reference_sf: float,
    target_sf: float,
    reference_nucleus: str,
    target_nucleus: str,
) -> float | None:
    """Map the direct-dimension carrier (ppm) onto the target nucleus' ppm axis by the gamma
    ratio (equivalent to the ``conv.tcl`` algorithm).
    """
    gx = gamma_ratio(reference_nucleus)
    gy = gamma_ratio(target_nucleus)
    if not gx or not gy or not target_sf:
        return None
    offset_hz = float(reference_sf) * 1.0e6 - float(reference_sf) * float(reference_ppm)
    return (1.0e6 * float(target_sf) - offset_hz * (gy / gx)) / float(target_sf)


def _is_proton(nucleus: str) -> bool:
    return gamma_ratio(nucleus) == GAMMA_RATIOS["1H"]


def _axis_letters(ndim: int) -> dict[str, str]:
    """Logical axis -> bruk2pipe keyword prefix (same mapping as script_generator)."""
    if ndim >= 3:
        return {"F3": "x", "F2": "y", "F1": "z"}
    return {"F2": "x", "F1": "y"}


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _temperature_kelvin(value: Any) -> float | None:
    """``acqus(TE)`` -> temperature (K); treated as missing when it is Celsius or clearly off.

    ``water_shift_ppm`` is a linear temperature formula, so feeding it 0/25/2981.5 would give a
    non-physical water peak; this only normalises (``<298.1>`` / Celsius) and sanity-checks the
    value (150-400 K), returning None otherwise.
    """
    kelvin = _float_or_none(value)
    if kelvin is None:
        return None
    if kelvin < 200.0:  # Celsius form
        kelvin += 273.15
    if not 150.0 <= kelvin <= 400.0:
        return None
    return kelvin


def carrier_values(experiment: Experiment) -> dict[str, Any]:
    """The referencing convention and the "adopted CAR" (ppm) per dimension: always that
    dimension's acqus ``O1/BF1``.

    **Software design (maintainer 2026-09-24)**: CAR uses **the computed spectral centre the
    operator set** (``O1/BF1``), so spectra of one experiment acquired at different times share
    one referencing convention; the "water peak (TE) + gamma ratio" value of ``bruker -AUTO``
    is kept as **corroborating evidence** in ``water_values`` only, used to say in the report
    which convention the script's present value came from; it never decides the value.

    Returns ``{"convention": "o1bf1", "rule": CAR_REFERENCE_RULE, "values": {axis: ppm},
    "water": ..., "te": ..., "water_values": {axis: ppm}, "direct_axis": ...,
    "direct_o1bf1": ...}``.
    """
    letters = _axis_letters(experiment.ndim)
    dims = [dim for dim in experiment.dimensions if letters.get(dim.logical_axis)]
    empty: dict[str, Any] = {
        "convention": "o1bf1",
        "rule": CAR_REFERENCE_RULE,
        "values": {},
        "water": None,
        "te": None,
        "water_values": {},
        "direct_axis": "",
        "direct_o1bf1": 0.0,
    }
    if not dims:
        return empty
    direct = next((dim for dim in dims if dim.role is AxisRole.DIRECT), dims[0])
    direct_letter = letters[direct.logical_axis]
    acqus = experiment.acquisition_parameters.get("acqus", {}) or {}
    temperature = _temperature_kelvin(acqus.get("TE"))
    water = water_shift_ppm(temperature) if temperature is not None else None
    direct_o1bf1 = float(direct.o1p or 0.0)
    values: dict[str, float] = {}
    water_values: dict[str, float] = {}
    for dim in dims:
        letter = letters[dim.logical_axis]
        values[letter] = float(dim.o1p or 0.0)
        if water is None or not _is_proton(direct.nucleus):
            continue
        if letter == direct_letter:
            water_values[letter] = float(water)
            continue
        mapped = gamma_mapped_car(water, direct.sf, dim.sf, direct.nucleus, dim.nucleus)
        if mapped is not None:
            water_values[letter] = float(mapped)
    return {
        "convention": "o1bf1",
        "rule": CAR_REFERENCE_RULE,
        "values": values,
        "water": water,
        "te": temperature,
        "water_values": water_values,
        "direct_axis": direct_letter,
        "direct_o1bf1": direct_o1bf1,
    }


def carrier_reference_block(
    label: str,
    acquisition: float | None,
    configured: float | None,
    *,
    status: str,
) -> list[str]:
    """The per-dimension "referencing convention" notification block (report design 2026-09-24):

    ``13C acquisition center : 54.28 ppm`` / ``Configured target CAR  : 56.00 ppm`` /
    ``Δ                       : +1.72 ppm`` / ``status: REFERENCE_OVERRIDE``.

    ``acquisition`` = this dimension's acqus ``O1/BF1`` (the computed spectral centre the
    operator set, the adopted value);
    ``configured`` = the present value in the conversion script (``fid.com``);
    ``Δ = configured − acquisition``.
    """
    label_acq = tr("{p0} acquisition center", p0=label)
    label_cfg = tr("Configured target CAR")
    label_delta = tr("Δ")
    label_status = tr("status")
    width = max(len(label_acq), len(label_cfg), len(label_delta), len(label_status)) + 1
    acquisition_text = f"{acquisition:.2f} ppm" if acquisition is not None else tr("unknown")
    configured_text = f"{configured:.2f} ppm" if configured is not None else tr("absent")
    if acquisition is not None and configured is not None:
        delta_text = f"{configured - acquisition:+.2f} ppm"
    else:
        delta_text = tr("unknown")
    return [
        f"{label_acq:<{width}}: {acquisition_text}",
        f"{label_cfg:<{width}}: {configured_text}",
        f"{label_delta:<{width}}: {delta_text}",
        f"{label_status}: {status}",  # status line as designed: not part of the width padding
    ]


def carrier_fix_note(key: str, acquisition: float, configured: float | None, status: str) -> str:
    """One line for the correction list of a dimension whose CAR was overridden (same wording in
    the log and the report).
    """
    return tr(
        "{p0}: fid.com={p1} -> acqus O1/BF1={p2:.3f} (CAR reference = the computed "
        "acquisition centre; status: {p3})",
        p0=key,
        p1=f"{configured:g}" if configured is not None else tr("absent"),
        p2=float(acquisition),
        p3=status,
    )


def carrier_audit(
    experiment: Experiment,
    fid_params: dict[str, str],
    *,
    manual_keys: set[str] | frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Per-dimension carrier (CAR) decision, record and report (2026-09-24; the convention and
    report design are described below).

    **Adopted convention (software design fixed by the maintainer 2026-09-24)**: CAR always
    comes
    from that dimension's acqus ``O1/BF1`` -- i.e. **the computed spectral centre the operator
    set**; spectra of one experiment acquired at different times therefore share one referencing
    convention. The "water peak (TE) + gamma ratio" value of ``bruker -AUTO`` is corroborating
    evidence only: it tells the user which convention the script's present value came from and
    never decides the value (that approximation is exact only when the transmitter sits on the
    water peak -- measured 1H carrier/water-peak difference 0.04-0.13 ppm, and 13C dimension
    deviations up to 2.6-3.1 ppm).

    **Telling the user (report design)**: an overridden dimension is shown in report and log as

    ``13C acquisition center : 54.28 ppm`` / ``Configured target CAR  : 56.00 ppm`` /
    ``Δ                       : +1.72 ppm`` / ``status: REFERENCE_OVERRIDE``.

    (``Δ = configured − acquisition``). Status constants ``REFERENCE_*``: ``REFERENCE_OVERRIDE``
    overridden / ``REFERENCE_KEPT`` the present value already is the spectral centre /
    ``REFERENCE_MANUAL`` a manual value wins / ``REFERENCE_MISSING`` the script lacks the key
    (the spectral centre was written) / ``REFERENCE_UNKNOWN`` this dimension's ``O1/BF1`` cannot
    be obtained (the script value is kept).

    Returns ``{"rule": ..., "convention": "o1bf1", "summary": ..., "notes": [...],
    "fix_lines": [...], "blocks": [...], "dims": [...]}``:

    - ``summary``: the sentence in the log/report saying CAR uses the computed spectral centre
      plus the adopted value per dimension;
    - ``notes``: corroborating evidence for where the script's present value came from (which
      dimensions follow the water-peak convention) and why no spectral centre was available;
    - ``fix_lines``: overridden/backfilled dimensions, one sentence each (into the "parameter
      corrections" list and the report);
    - ``blocks``: the referencing-convention block of each overridden dimension (multi-line
    text,
      rendered indented in report and log);
    - ``dims``: per dimension ``axis``/``logical_axis``/``nucleus``/``acquisition_center``/
      ``configured_target``/``delta_ppm``(configured − acquisition)/``status``/
      ``water_value`` (the value the water-peak convention would give)/``configured_source``
      (``water_gamma`` / ``script``)/``target``/``decision`` (legacy field mapped from status
      for old readers).
    """
    letters = _axis_letters(experiment.ndim)
    dims = [dim for dim in experiment.dimensions if letters.get(dim.logical_axis)]
    if not dims:
        return {}
    reference = carrier_values(experiment)
    water_values = dict(reference["water_values"] or {})
    records: list[dict[str, Any]] = []
    blocks: list[str] = []
    for dim in dims:
        letter = letters[dim.logical_axis]
        key = f"{letter}CAR"
        configured = _float_or_none(fid_params.get(key))
        acquisition = float(reference["values"].get(letter, dim.o1p or 0.0) or 0.0)
        water_value = water_values.get(letter)
        if key in manual_keys:
            status = REFERENCE_MANUAL
        elif not acquisition:
            status = REFERENCE_UNKNOWN
        elif configured is None:
            status = REFERENCE_MISSING
        elif abs(configured - acquisition) <= CAR_WRITE_TOLERANCE:
            status = REFERENCE_KEPT
        else:
            status = REFERENCE_OVERRIDE
        records.append(
            {
                "axis": letter,
                "logical_axis": dim.logical_axis,
                "nucleus": dim.nucleus,
                "acquisition_center": round(acquisition, 6) if acquisition else None,
                "configured_target": (round(configured, 6) if configured is not None else None),
                "delta_ppm": (
                    round(configured - acquisition, 6)
                    if configured is not None and acquisition
                    else None
                ),
                "water_value": (round(water_value, 6) if water_value is not None else None),
                "configured_source": (
                    "water_gamma"
                    if water_value is not None
                    and configured is not None
                    and abs(configured - water_value) <= CAR_MATCH_TOLERANCE
                    else "script"
                ),
                "target": round(acquisition, 6) if acquisition else None,
                "status": status,
                "decision": {
                    REFERENCE_KEPT: "keep",
                    REFERENCE_MANUAL: "manual",
                    REFERENCE_MISSING: "missing",
                }.get(status, "write"),
            }
        )
    #
    #
    notes: list[str] = []
    grouped: dict[str, list[str]] = {
        "manual": [],
        "water": [],
        "centre": [],
        "script": [],
        "missing": [],
        "unknown": [],
    }
    for rec in records:
        axis = str(rec["axis"])
        status = str(rec["status"])
        if status == REFERENCE_MANUAL:
            grouped["manual"].append(axis)
        elif status == REFERENCE_MISSING:
            grouped["missing"].append(axis)
        elif status == REFERENCE_UNKNOWN:
            grouped["unknown"].append(axis)
        elif status == REFERENCE_KEPT:
            grouped["centre"].append(axis)
        elif rec["configured_source"] == "water_gamma":
            grouped["water"].append(axis)
        else:
            grouped["script"].append(axis)

    clauses: list[str] = []
    if grouped["manual"]:
        clauses.append(tr("{p0}: manually set value", p0=", ".join(grouped["manual"])))
    if grouped["water"]:
        clauses.append(
            tr(
                "{p0}: conversion script's water-peak + gamma-ratio value",
                p0=", ".join(grouped["water"]),
            )
        )
    if grouped["centre"]:
        clauses.append(
            tr(
                "{p0}: acquisition centre (acqus O1/BF1)",
                p0=", ".join(grouped["centre"]),
            )
        )
    if grouped["script"]:
        clauses.append(tr("{p0}: conversion script value", p0=", ".join(grouped["script"])))
    if grouped["missing"]:
        clauses.append(
            tr(
                "{p0}: CAR is missing from the conversion script",
                p0=", ".join(grouped["missing"]),
            )
        )
    if grouped["unknown"]:
        clauses.append(
            tr(
                "{p0}: conversion script value (acquisition centre unavailable)",
                p0=", ".join(grouped["unknown"]),
            )
        )
    summary = (
        tr(
            "CAR currently uses {p0}. To use another reference, edit CAR manually in the "
            "Spectrum step",
            p0="; ".join(clauses),
        )
        if clauses
        else ""
    )
    return {
        "rule": CAR_REFERENCE_RULE,
        "convention": "o1bf1",
        "summary": summary,
        "notes": notes,
        "fix_lines": [],
        "blocks": blocks,
        "dims": records,
    }


def carrier_overrides(audit: dict[str, Any]) -> dict[str, str]:
    """CAR parameters to write into fid.com (dimensions whose status is overridden/backfilled,
    ``%.3f``).

    **Adopted convention (maintainer 2026-09-24)**: always write that dimension's acqus
    ``O1/BF1`` (the computed spectral centre); dimensions within ``CAR_WRITE_TOLERANCE``
    (display precision) are left alone and are not corrections; manually edited keys
    (``REFERENCE_MANUAL``) are excluded, and dimensions without a spectral centre
    (``REFERENCE_UNKNOWN``) are not written either.
    """
    values: dict[str, str] = {}
    for record in audit.get("dims") or []:
        status = record.get("status")
        if status is not None:
            if status not in _REFERENCE_WRITTEN:
                continue
        elif record.get("decision") not in ("write", "o1bf1"):  # legacy record
            continue
        target = record.get("target")
        if target is None:
            continue
        values[f"{record['axis']}CAR"] = f"{float(target):.3f}"
    return values


def carrier_patch_notes(audit: dict[str, Any]) -> list[str]:
    """Overridden / backfilled dimensions (patch warnings; same sentence as ``fix_lines``)."""
    lines: list[str] = []
    lines += [str(line) for line in audit.get("notes") or [] if str(line)]
    lines += [str(line) for line in audit.get("conflicts") or [] if str(line)]
    return lines


#: Acquisition mode (MODE) conflict table (round two 2026-09-24, table from the maintainer;
#: single entry point mode_audit).
#:   FnMODE=6 (explicit) + script Complex      -> force Echo-AntiEcho + warn (force_ea)
#:   FnMODE=6 (explicit) + script EA/Rance-Kay -> keep (ok)
#:   FnMODE 1..5 (explicit) + agrees with FnMODE -> keep (ok)
#:   FnMODE 1..5 (explicit) + script `-N` variant -> **kept as written** (canonical negated
#:                                          imaginaries); reported (conflict) when it
#:                                          contradicts the coherence-pathway criterion
#:   FnMODE 1..5 (explicit) + script EA/Rance-Kay -> hard conflict: no side is picked
#:                                          automatically, keep the script value + warn
#:   FnMODE 1..5 (explicit) + anything else (AUTO writes Complex for 2..5) -> rewrite from
#:                                          FnMODE to the **specific keyword**
#:                                          (States/States-TPPI/...) + warn
#:   FnMODE missing/0 + script EA              -> keep the script value, mark inferred
#:   FnMODE missing/0 + anything else          -> keep the script value, mark low_confidence
#:   FnMODE missing/0 and no pulse program     -> keep the script value, mark unverified
#: (the direct dimension ``-xMODE`` is not in this table: a Bruker direct dimension is always
#: DQD.)
MODE_EA = "Echo-AntiEcho"
#: Spellings with the same mode code (4) as ``Echo-AntiEcho`` all count as E-A;
#: ``Echo-AntiEcho-N`` is the canonical "imaginaries negated" variant and is **kept as written**
#: once judged E-A (force_ea does not rewrite it into a non-``-N`` form).
MODE_KINDS = (MODE_EA, "Echo-AntiEcho-N", "Rance-Kay")


def mode_audit(
    experiment: Experiment,
    fid_params: dict[str, str],
    *,
    data_dir: Path | str | None = None,
    manual_keys: set[str] | frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Per-dimension acquisition mode decision (round two 2026-09-24, conflict table from the
    maintainer).

    Background: the mode written by bruker -AUTO comes from the pulse-program parsing in
    ``com/pprog.tcl`` (``acquNs(FnMODE)``==6 -> Echo-AntiEcho, 1/7 -> Real, 2..5 -> Complex), so
    **it does not distinguish** States(4)/States-TPPI(5)/TPPI(3); that distinction lives in our
    own ``acquisition_mode_detector`` (the bruk2pipe keyword and the FT -alt/-neg/-real flags
    are
    all derived from FnMODE separately). This only resolves "script present value vs
    FnMODE-derived value" contradictions; see the conflict table comment above for the rules.

    Returns ``{"dims": [...], "fix_lines": [...], "conflicts": [...], "notes": [...]}``:
    ``fix_lines`` = corrections to write into fid.com (force_ea / rewrite from FnMODE);
    ``conflicts`` = hard conflicts (reported only, no side picked automatically);
    ``notes`` = explanations for inferred / low_confidence / unverified / missing.

    The direct dimension (``-xMODE``) is not governed by this table: it is fixed by the way
    Bruker acquires and is always DQD; it is only a fallback when AUTO omitted or mis-wrote it.
    """
    letters = _axis_letters(experiment.ndim)
    dims = [dim for dim in experiment.dimensions if letters.get(dim.logical_axis)]
    if not dims:
        return {}
    pulseprogram: bool | None = None
    if data_dir is not None:
        try:
            pulseprogram = (Path(data_dir) / "pulseprogram").is_file()
        except OSError:
            pulseprogram = None
    records: list[dict[str, Any]] = []
    fix_lines: list[str] = []
    conflicts: list[str] = []
    notes: list[str] = []
    for dim in dims:
        letter = letters[dim.logical_axis]
        key = f"{letter}MODE"
        script = fid_params.get(key)
        fnmode = _fnmode_raw(experiment, dim.logical_axis)
        # The direct dimension does not take part in the conflict table (review 2026-09-24): a
        # Bruker direct dimension is always DQD (bruk2pipe ``-xMODE DQD``), and the ``acqus``
        # FnMODE there is only a placeholder -- all 59 real datasets show 0. Treating it as
        # "undefined" and running it through the conflict table would add a pointless note to
        # every dataset's report.
        direct = dim.role is AxisRole.DIRECT
        known = not direct and fnmode is not None and 0 < int(fnmode) <= 6
        if direct:
            derived: str | None = DIRECT_BRUK2PIPE_MODE
        else:
            derived = bruk2pipe_mode_for(int(fnmode or 0), axis=letter) if known else None
        script_is_ea = script in MODE_KINDS
        decision = "ok"
        if script is None:
            decision = "missing"
            notes.append(
                tr(
                    "-{p0} is missing from fid.com (this dimension's acquisition "
                    "mode comes from the conversion script only)",
                    p0=key,
                )
            )
        elif key in manual_keys:
            decision = "manual"
        elif direct:
            if script != DIRECT_BRUK2PIPE_MODE:
                decision = "write"
                fix_lines.append(
                    tr(
                        "{p0}: fid.com={p1} -> {p2} (corrected: the direct dimension of a "
                        "Bruker dataset is always DQD)",
                        p0=key,
                        p1=script,
                        p2=DIRECT_BRUK2PIPE_MODE,
                    )
                )
        elif known and int(fnmode) == 6:
            if not script_is_ea:
                decision = "force_ea"
                fix_lines.append(
                    tr(
                        "{p0}: fid.com={p1} -> Echo-AntiEcho (corrected: acquNs FnMODE=6 is "
                        "Echo-Antiecho; the conversion script did not detect it)",
                        p0=key,
                        p1=script,
                    )
                )
        elif known:
            if script_is_ea:
                decision = "conflict"
                conflicts.append(
                    tr(
                        "{p0}: fid.com={p1} vs acquNs FnMODE={p2} - the conversion script and the "
                        "acquisition metadata disagree; kept the conversion script value, confirm "
                        "the acquisition mode",
                        p0=key,
                        p1=script,
                        p2=int(fnmode),
                    )
                )
            elif canonical_negated(script):
                # Final round 2026-09-24: the `-N` variant (Complex-N/States-N/States-TPPI-N)
                # carries the canonical "imaginaries negated" meaning, so it is **kept as
                # written** (rewriting would lose information). If the coherence-pathway
                # criterion says the dimension is normal, the two contradict => report only,
                # pick no side, and ask for a review.
                judged = handedness_for(experiment, dim.logical_axis, fnmode=int(fnmode))
                decision = "negated"
                if judged.determined and not judged.needs_neg:
                    decision = "conflict"
                    conflicts.append(
                        tr(
                            "{p0}: fid.com={p1} says the imaginaries are negated (-N), while the "
                            "coherence-pathway criterion gives {p2} for this dimension - kept the "
                            "conversion script value, confirm the sign",
                            p0=key,
                            p1=script,
                            p2=judged.handedness,
                        )
                    )
                else:
                    notes.append(
                        tr(
                            "{p0}: fid.com={p1} kept as written (canonical -N mode: the "
                            "imaginaries "
                            "are negated; the sign adjustment is applied by the FT flags)",
                            p0=key,
                            p1=script,
                        )
                    )
            elif script != derived:
                decision = "write"
                if same_mode_family(script, derived):
                    # Final round 2026-09-24: even with the same mode code, write the **specific
                    # keyword** (States-TPPI/States/TPPI/Sequential...), no longer Complex --
                    # the keyword only sets the header while the ALT sign adjustment is applied
                    # by the FT flags during processing, so it is never applied twice.
                    fix_lines.append(
                        tr(
                            "{p0}: fid.com={p1} -> {p2} (same bruk2pipe mode code, but "
                            "the specific "
                            "keyword records the acquisition mode; the sign adjustment is "
                            "applied by "
                            "the FT flags, not at conversion)",
                            p0=key,
                            p1=script,
                            p2=derived,
                        )
                    )
                else:
                    fix_lines.append(
                        tr(
                            "{p0}: fid.com={p1} -> acqus={p2} (corrected)",
                            p0=key,
                            p1=script,
                            p2=derived,
                        )
                    )
        else:
            if pulseprogram is False:
                decision = "unverified"
                notes.append(
                    tr(
                        "{p0}: fid.com={p1} kept (acquNs FnMODE is undefined and there is no "
                        "pulse program: the acquisition mode is not metadata-confirmed)",
                        p0=key,
                        p1=script or "-",
                    )
                )
            elif script_is_ea:
                decision = "inferred"
                notes.append(
                    tr(
                        "{p0}: fid.com={p1} kept (acquNs FnMODE is undefined; inferred from "
                        "the pulse program, not metadata-confirmed)",
                        p0=key,
                        p1=script or "-",
                    )
                )
            else:
                decision = "low_confidence"
                notes.append(
                    tr(
                        "{p0}: fid.com={p1} kept (acquNs FnMODE is undefined: low confidence, "
                        "not metadata-confirmed)",
                        p0=key,
                        p1=script or "-",
                    )
                )
        records.append(
            {
                "axis": letter,
                "logical_axis": dim.logical_axis,
                "fnmode": int(fnmode) if known else None,
                "script": script,
                "derived": derived,
                "decision": decision,
            }
        )
    return {
        "dims": records,
        "fix_lines": fix_lines,
        "conflicts": conflicts,
        "notes": notes,
    }


def mode_writes(audit: dict[str, Any]) -> dict[str, str]:
    """MODE keys to write into fid.com (force_ea and rewrites from FnMODE; conflicts, inferences
    and ``-N`` are left alone).
    """
    values: dict[str, str] = {}
    for record in audit.get("dims") or []:
        decision = record.get("decision")
        if decision == "force_ea":
            values[f"{record['axis']}MODE"] = MODE_EA
        elif decision == "write" and record.get("derived"):
            values[f"{record['axis']}MODE"] = str(record["derived"])
    return values


def _fnmode(experiment: Experiment, logical_axis: str) -> int:
    if experiment.ndim >= 3:
        mapping = {"F3": "acqus", "F2": "acqu2s", "F1": "acqu3s"}
    else:
        mapping = {"F2": "acqus", "F1": "acqu2s"}
    block = experiment.acquisition_parameters.get(mapping.get(logical_axis, ""), {})
    try:
        return int(block.get("FnMODE", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _fnmode_raw(experiment: Experiment, logical_axis: str) -> int | None:
    """Raw FnMODE from acqus/acqu2s/acqu3s; missing/invalid returns None (0 = undefined)."""
    if experiment.ndim >= 3:
        mapping = {"F3": "acqus", "F2": "acqu2s", "F1": "acqu3s"}
    else:
        mapping = {"F2": "acqus", "F1": "acqu2s"}
    block = experiment.acquisition_parameters.get(mapping.get(logical_axis, ""), {}) or {}
    raw = block.get("FnMODE")
    if raw is None or raw == "":
        return None
    try:
        return int(str(raw).strip("<>").strip())
    except (TypeError, ValueError):
        return None


def _dim(experiment: Experiment, logical_axis: str):
    for dim in experiment.dimensions:
        if dim.logical_axis == logical_axis:
            return dim
    return None


def _effective_td(experiment: Experiment) -> list[int]:
    """Points per dimension used for conversion/verification: NUS data takes NusTD (the sampling
    grid) on indirect dimensions, otherwise TD.

    Review A7 2026-09-24: in ``script_generator.effective_td`` the 2D NUS grid is
    ``acqu2s TD // hypercomplex components`` (the docs state "NusTD is not trusted"), while
    this function takes ``NusTD`` -- the two may give different grids. This function **does not
    change behaviour** (``-yN/-yT`` in fid.com and the forced nusExpand grid have always
    followed NusTD and were verified end to end on real 2D NUS data), but it logs an
    inconsistency instead of staying silent.
    """
    td = [dim.td for dim in experiment.dimensions]
    if experiment.sampling.mode is SamplingMode.NUS:
        for index, filename in ((1, "acqu2s"), (2, "acqu3s")):
            if len(td) <= index:
                continue
            block = experiment.acquisition_parameters.get(filename, {})
            try:
                nus_td = int(block.get("NusTD", 0) or 0)
            except (TypeError, ValueError):
                continue
            if nus_td:
                td[index] = nus_td
    if experiment.sampling.mode is SamplingMode.NUS and experiment.ndim == 2 and len(td) > 1:
        grid = _two_d_nus_grid(experiment)
        if grid and td[1] and grid != td[1]:
            logger.debug(
                "2D NUS grid: fid.com/nusExpand use NusTD=%s while the fallback script would "
                "use acqu2s TD//mult=%s (%s)",
                td[1],
                grid,
                experiment.dataset_id,
            )
    return td


def _two_d_nus_grid(experiment: Experiment) -> int:
    """Complex-point grid of the indirect dimension of a 2D NUS dataset (the
    ``script_generator.effective_td`` convention, for comparison only).
    """
    dim = _dim(experiment, "F1")
    if dim is None or not dim.td:
        return 0
    mult = 2 if int(_fnmode(experiment, "F1") or 0) in (0, 4, 5, 6) else 1
    return int(dim.td) // mult


#: ser rows are padded to 1024 bytes (single source ``core.data.ser_layout``); no candidate
#: table is kept here any more.


def direct_row_verified(script_value: Any, td: int, value_bytes: int, size: int) -> bool:
    """Whether the ``-xN`` in the script body can be used as is (maintainer's convention
    2026-09-24: verify the present value first, do not rewrite by a fixed rule).

    Conditions: ``-xN >= TD`` (a row cannot be shorter than the declared direct dimension), it
    divides the file size, and the row bytes are a multiple of 1024 (222 of the 224 verifiable
    datasets out of 292 real ones satisfy this). When it holds, **leave it alone** -- the value
    written by ``bruker -AUTO`` was proved correct twice on real data (d_015 ``1664``, real 2D
    NUS ``1024``); recomputing it from the acqus TD or from a fixed pad would break it.
    """
    value = _float_or_none(script_value)
    if value is None or size <= 0 or value_bytes <= 0 or td <= 0:
        return False
    if value < td:
        return False
    row_bytes = value * value_bytes
    if size % row_bytes:
        return False
    return row_bytes % 1024 == 0


def row_geometry_audit(
    experiment: Experiment,
    data_dir: Path | str | None,
    fid_params: dict[str, str],
    manual_keys: set[str] | frozenset[str] = frozenset(),
) -> list[str]:
    """Audit ``xN/yN/zN`` and ``xT/yT/zT`` without changing their values.

    ``-xN`` counts real plus imaginary values; ``-xT`` counts effective points (``N/2`` for
    complex data). ``bruker -AUTO`` derives these values from acquisition parameters, so the
    application must not overwrite them. File-size divisibility is not a reliable alternative:
    a measured dataset had an 8,388,608-byte ``ser`` file, ``TD=356`` and ``DTYPA=2``; AUTO
    returned ``-xN 384``, while requiring exact divisibility would imply 512. In controlled
    comparisons, AUTO was correct when the methods agreed and was the only correct result when
    they differed; a 228-dataset review matched AUTO in all 228 cases.

    Keys listed in ``manual_keys`` are not checked, matching ``carrier_audit`` and
    ``mode_audit``. A user-entered value is explicit intent; reporting it as wrong based on an
    application-side derivation would contradict the manual action.

    Findings are reports only; this function never changes parameters:

    1. Check whether ``N`` is compatible with file geometry without requiring exact divisibility
       (a partial trailing row is allowed). Report only when ``N < TD`` (below the ``bruk2pipe``
       minimum) or the trailing remainder is nearly a full row.
    2. Check that ``T`` is positive and consistent with ``N`` and ``MODE``, using
       ``time_domain_points`` (``N/2`` for complex data and ``N`` for real data).
    3. For NUS, ``nusExpand`` determines ``xN`` through ``serPadSize``; report that no geometry
       check was performed.
    """
    lines: list[str] = []
    if experiment.sampling.mode is SamplingMode.NUS:
        if "xN" in fid_params and "xN" not in manual_keys:
            lines.append(
                tr(
                    "xN: {p0} kept as-is; for NUS the row length is set by nusExpand "
                    "(serPadSize), so no geometry check was done",
                    p0=fid_params.get("xN"),
                )
            )
        return lines
    td = _effective_td(experiment)
    if not td or td[0] <= 0:
        return lines
    direct_td = int(td[0])
    xn = _float_or_none(fid_params.get("xN"))
    if xn is not None and xn > 0 and "xN" not in manual_keys:
        if xn < direct_td:
            lines.append(
                tr(
                    "xN: {p0} is smaller than the direct TD {p1} - the row length must be at "
                    "least TD; check this script",
                    p0=fid_params.get("xN"),
                    p1=direct_td,
                )
            )
        elif data_dir is not None:
            try:
                value_bytes = sample_itemsize(experiment.acquisition_parameters.get("acqus", {}))
            except UnknownBrukerDtype:
                value_bytes = 0
            data_file = Path(data_dir) / ("ser" if experiment.ndim >= 2 else "fid")
            try:
                size = data_file.stat().st_size
            except OSError:
                size = 0
            if size > 0 and value_bytes > 0:
                per_row = xn * value_bytes
                whole = int(size // per_row)
                remainder = size - whole * per_row
                if whole > 0 and remainder >= per_row * 0.9:
                    lines.append(
                        tr(
                            "xN: {p0} leaves {p1} trailing byte(s) in {p2}, nearly a whole "
                            "{p3}-byte row - the row length may be one row short; check this "
                            "script",
                            p0=fid_params.get("xN"),
                            p1=remainder,
                            p2=size,
                            p3=int(per_row),
                        )
                    )
    xt = _float_or_none(fid_params.get("xT"))
    if xt is not None and "xT" not in manual_keys:
        fnmode = _fnmode(experiment, "F3" if experiment.ndim >= 3 else "F2")
        expected_t = float(time_domain_points(fnmode, direct_td))
        if xt <= 0:
            lines.append(
                tr(
                    "xT: {p0} is not positive; check this script",
                    p0=fid_params.get("xT"),
                )
            )
        elif abs(xt - expected_t) > 0.5:
            lines.append(
                tr(
                    "xT: fid.com={p0} vs {p1} expected from TD={p2} (MODE {p3}); check this script",
                    p0=fid_params.get("xT"),
                    p1=int(expected_t),
                    p2=direct_td,
                    p3=fnmode,
                )
            )
    return lines


def direct_row_points(
    experiment: Experiment,
    data_dir: Path | str | None,
    script_value: Any = None,
) -> tuple[int | None, str]:
    """Direct-dimension row length + its source (``verified`` / ``derived`` / ``unknown``).

    Maintainer's convention 2026-09-24: the row length and sample word size of ``ser`` are
    decided by TopSpin case by case, **not by a fixed byte rule**. Therefore: (1) if the present
    value passes :func:`direct_row_verified` it is used as is (``verified``); (2) otherwise it
    is
    solved from the file size (``derived``, single source ``core.data.ser_layout``); (3) if
    neither is available -> ``(None, "unknown")`` and the caller keeps the original value.
    """
    td = _effective_td(experiment)
    try:
        word_bytes = sample_itemsize(experiment.acquisition_parameters.get("acqus", {}))
    except UnknownBrukerDtype:
        return None, "unknown"
    size = 0
    if data_dir is not None:
        data_file = Path(data_dir) / ("ser" if experiment.ndim >= 2 else "fid")
        try:
            size = data_file.stat().st_size
        except OSError:
            size = 0
    if td and td[0] > 0 and direct_row_verified(script_value, int(td[0]), word_bytes, size):
        return int(float(script_value)), "verified"
    derived = physical_direct_points(experiment, data_dir)
    if derived is not None:
        return derived, "derived"
    if script_value is not None:
        return None, "unknown"
    return None, "unknown"


def physical_direct_points(experiment: Experiment, data_dir: Path | str | None) -> int | None:
    """Complex points per row of the direct dimension (physical file view, may exceed the acqus
    TD).

    Bruker pads every row to a multiple of 1024 bytes when writing ser (8 bytes per complex
    point -> 128 complex points, 16 bytes -> 64 complex points), and ``bruk2pipe -xN`` must
    match the **physical row length**: measured on d_015 on 2026-09-23, acqus TD=1612 while the
    row is **1664** (2,795,520 bytes = 210 rows x 1664 complex points x 8 bytes). Changing it to
    the acqus 1612 makes bruk2pipe read the file with a wrong stride: same output size, exit
    code 0, no warning in the log, but the contents are wrong (measured by the maintainer).

    Single source ``core.data.ser_layout.solve_row_points`` (row length = the smallest pad
    multiple not below TD that divides the file size; on the real corpus it agrees with the
    ``-xN`` of 14/14 deposited ``fid.com`` files). When it cannot be derived (missing file, not
    divisible, unknown DTYPE) it returns None and the caller keeps the value computed by
    ``bruker -AUTO`` instead of guessing.
    """
    if data_dir is None:
        return None
    td = _effective_td(experiment)
    if not td or td[0] <= 0:
        return None
    data_file = Path(data_dir) / ("ser" if experiment.ndim >= 2 else "fid")
    try:
        # Unit: ``-xN``/row length counts "real+imaginary values" => use bytes per **value**
        # (DTYPA/DTYPE convention), not bytes per complex point
        value_bytes = sample_itemsize(experiment.acquisition_parameters.get("acqus", {}))
        size = data_file.stat().st_size
    except (OSError, UnknownBrukerDtype):
        return None
    return solve_row_points(td[0], value_bytes, size)


_NUSEXPAND_RE = re.compile(r"nusExpand\.tcl[^\n]*?-sampleCount\s+(\d+)")


def patch_nus_expand_count(text: str, nuslist_count: int) -> tuple[str, list[str]]:
    """Correct the -sampleCount of nusExpand.tcl to the real number of nuslist lines.

    For some datasets (e.g. acqu2s TD contradicting NusTD) bruker -AUTO misjudges the number of
    sampling points, so ser_full expands only a few slices and bruk2pipe hangs while reading the
    data (measured 2026-08-11).
    """
    warnings: list[str] = []

    def replace(match: re.Match) -> str:
        current = int(match.group(1))
        if current == nuslist_count:
            return match.group(0)
        warnings.append(
            tr("sampleCount: fid.com={p0} → nuslist={p1} (corrected)", p0=current, p1=nuslist_count)
        )
        return match.group(0).replace(f"-sampleCount {current}", f"-sampleCount {nuslist_count}", 1)

    patched = _NUSEXPAND_RE.sub(replace, text)
    return patched, warnings


def parse_fid_com(text: str) -> dict[str, str]:
    """Extract the key bruk2pipe parameters from the fid.com generated by the bruker command."""
    return {match.group(1): match.group(2) for match in _KEY_RE.finditer(text)}


def _display_tolerance(value: Any) -> float:
    """Display-precision tolerance of a number: quantities with 4-6 significant digits such as
    SW/OBS are judged on a relative 1e-5.

    A fixed 1e-3 would mark a legitimate script's two-decimal spelling (``-ySW 1824.53`` vs
    1824.5346) as needing correction and add a pointless correction line to the report, while a
    real convention error (2000 vs 1824.5) is far larger than that.
    """
    magnitude = abs(_float_or_none(value) or 0.0)
    return max(1e-3, magnitude * 1e-5)


def _num(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None


def expected_values(
    experiment: Experiment, data_dir: Path | str | None = None
) -> dict[str, tuple[Any, float | None]]:
    """fid.com target parameters (value, tolerance): shared by cross_check_fid_com and
    patch_fid_com.

    String parameters (LAB/MODE) have tolerance None; N/T/decim/dspfvs match exactly;
    SW/OBS/CAR/grpdly use a tolerance of 1e-3.

    ``xN`` is decided by :func:`direct_row_points` (verify the fid.com present value first and
    only solve from the file if it fails; ``patch_fid_com`` takes a dedicated path, there is no
    generic replacement here).

    When ``data_dir`` is given, ``xN`` is the row length derived from the **physical file**
    (physical_direct_points), no longer the acqus TD; if it cannot be derived no xN target is
    given (the caller keeps the fid.com value). ``xT``/``yT``/``zT`` are "valid point" counts,
    independent of padding (bruk2pipe drops the padded/oversampled part there); the values come
    from the same source as the conversion script:
    ``acquisition_mode_detector.time_domain_points`` -- N/2 for complex classes and ``T = N``
    for real classes (QF/QSEQ/TPPI) (same rule as NMRPipe `nih.tcl`, review C1 2026-09-24).

    ``xCAR``/``yCAR``/``zCAR`` are still given from acqus (for `carrier_audit` to cross-check),
    but they are not override targets here: CAR follows the :func:`carrier_audit` policy (by
    default written as that dimension's ``O1/BF1`` = the operator's spectral centre, and the
    difference from the script's present value is reported per dimension).

    ``xMODE``/``yMODE``/``zMODE`` all come from
    ``core.experiment.acquisition_mode_detector.bruk2pipe_mode_for`` (the same source as
    script_generator since 2026-09-23; before that this function rewrote the fid.com of
    FnMODE 1/2/3/4 datasets incorrectly).
    """
    td = _effective_td(experiment)
    direct = physical_direct_points(experiment, data_dir)
    if data_dir is None:
        xN: float | None = float(td[0])  # no file information: keep the old convention
    elif direct is not None:
        xN = float(direct)
    else:
        xN = None  # file not divisible: no guessing, keep original
    x = _dim(experiment, "F2" if experiment.ndim == 2 else "F3")
    values: dict[str, tuple[Any, float | None]] = {
        "xN": (xN, 0.0),
        # `-xT`/`-yT`/`-zT` share the source of the conversion script: real classes are not
        # halved (review C1 2026-09-24).
        "xT": (
            float(time_domain_points(_fnmode(experiment, x.logical_axis) if x else 0, td[0])),
            0.0,
        ),
        # Review 2026-09-24: SW/OBS have 4-6 significant digits, so a fixed 1e-3 would mark a
        # legitimate two-decimal script spelling (``-ySW 1824.53``) as "already corrected" => use
        # a magnitude-dependent display-precision tolerance
        "xSW": (float(x.sw), _display_tolerance(x.sw)) if x else (None, 1e-3),
        "xOBS": (float(x.sf), _display_tolerance(x.sf)) if x else (None, 1e-3),
        "xCAR": (float(x.o1p), 1e-3) if x else (None, 1e-3),
        "xLAB": (x.nucleus, None) if x else ("", None),
        "xMODE": (DIRECT_BRUK2PIPE_MODE, None),
    }
    if experiment.ndim >= 2 and len(td) > 1:
        y = _dim(experiment, "F1" if experiment.ndim == 2 else "F2")
        y_fnmode = _fnmode(experiment, y.logical_axis) if y else 0
        values.update(
            {
                "yN": (float(td[1]), 0.0),
                "yT": (float(time_domain_points(y_fnmode, td[1])), 0.0),
                "ySW": (float(y.sw), _display_tolerance(y.sw)) if y else (None, 1e-3),
                "yOBS": (float(y.sf), _display_tolerance(y.sf)) if y else (None, 1e-3),
                "yCAR": (float(y.o1p), 1e-3) if y else (None, 1e-3),
                "yLAB": (y.nucleus, None) if y else ("", None),
            }
        )
        if 0 <= y_fnmode <= KNOWN_FNMODE_MAX:
            # FnMODE outside the table (>6) does not claim a conversion mode: leave it to
            # mode_audit to mark unconfirmed (review D 2026-09-24)
            values["yMODE"] = (bruk2pipe_mode_for(y_fnmode), None)
    if experiment.ndim >= 3 and len(td) > 2:
        z = _dim(experiment, "F1")
        z_fnmode = _fnmode(experiment, z.logical_axis) if z else 0
        values.update(
            {
                "zN": (float(td[2]), 0.0),
                "zT": (float(time_domain_points(z_fnmode, td[2])), 0.0),
                "zSW": (float(z.sw), _display_tolerance(z.sw)) if z else (None, 1e-3),
                "zOBS": (float(z.sf), _display_tolerance(z.sf)) if z else (None, 1e-3),
                "zCAR": (float(z.o1p), 1e-3) if z else (None, 1e-3),
                "zLAB": (z.nucleus, None) if z else ("", None),
            }
        )
        if 0 <= z_fnmode <= KNOWN_FNMODE_MAX:
            values["zMODE"] = (bruk2pipe_mode_for(z_fnmode, axis="z"), None)
    acqus = experiment.acquisition_parameters.get("acqus", {})
    if acqus.get("DECIM"):
        values["decim"] = (float(acqus["DECIM"]), 0.0)
    if acqus.get("DSPFVS"):
        values["dspfvs"] = (float(acqus["DSPFVS"]), 0.0)
    grpdly = _float_or_none(acqus.get("GRPDLY"))
    if grpdly is not None and grpdly >= 0:
        # Review 2026-09-24: GRPDLY=0 is also a **real value** (no digital-filter group delay);
        # using truthiness alone would treat integer 0 as missing (the opposite of the
        # "<0 means do not use" rule in _acqus_values)
        values["grpdly"] = (grpdly, 1e-3)
    return values


def cross_check_fid_com(
    fid_params: dict[str, str],
    experiment: Experiment,
    data_dir: Path | str | None = None,
) -> list[str]:
    """Check the fid.com parameters against the acqus/acqu2s metadata (including the MODE/DSP
    flags); returns warnings for the differences.
    """
    warnings: list[str] = []
    for key, (desired, tolerance) in expected_values(experiment, data_dir).items():
        if key not in fid_params:
            continue
        if key.endswith("CAR"):
            # 2026-09-24: CAR is decided by policy (by default written as that dimension's
            # ``O1/BF1`` = the operator's spectral centre, see carrier_audit), and its difference
            # from the script's present value is reported per dimension by carrier_audit (with
            # the delta and the status), so no per-value difference is reported here.
            continue
        if key in _MODE_KEYS and canonical_negated(fid_params[key]):
            # 2026-09-24: the ``-N`` variant (canonical negated imaginaries) is a spelling that
            # is **kept on purpose**; with the same mode code it is not a difference, otherwise
            # every ``States-TPPI-N`` dataset would gain a pointless
            # `yMODE ... vs acqus` warning.
            continue
        if isinstance(desired, str):
            if fid_params[key] != desired:
                warnings.append(f"{key}: fid.com={fid_params[key]} vs acqus={desired}")
            continue
        actual = _num(fid_params[key])
        if actual is None or desired is None:
            continue
        if abs(actual - desired) > (tolerance if tolerance is not None else 0.0):
            warnings.append(f"{key}: fid.com={fid_params[key]} vs acqus={desired:g}")
    return warnings


def _acqus_values(experiment: Experiment, data_dir: Path | str | None = None) -> dict[str, str]:
    values: dict[str, str] = {}
    acqus = experiment.acquisition_parameters.get("acqus", {}) or {}
    grpdly = _float_or_none(acqus.get("GRPDLY"))
    for key, (desired, _tolerance) in expected_values(experiment, data_dir).items():
        if desired is None:
            continue
        if key in _MODE_KEYS:
            # Round two 2026-09-24: acquisition mode is handled separately by mode_audit (the
            # conflict table) and does not go through the generic replacement
            continue
        if key in _ROW_GEOMETRY_KEYS:
            continue
        if key in _DSP_KEYS:
            continue
        if key.endswith("CAR"):
            continue
        if key == "grpdly" and (grpdly is None or grpdly < 0):
            # 2026-09-24 (parameter source table): only GRPDLY >= 0 uses acqus directly; a
            # negative or missing value means the dataset has no usable digital-filter group
            # delay (AUTO only copies acqus as well), so keep the fid.com value instead of
            # overwriting it with a negative number treated as a "known value".
            continue
        if isinstance(desired, str):
            values[key] = desired
        elif key in ("xSW", "ySW", "zSW", "xOBS", "yOBS", "zOBS", "xCAR", "yCAR", "zCAR"):
            values[key] = f"{desired:.3f}"
        elif key in ("decim", "dspfvs", "grpdly"):
            values[key] = f"{desired:g}"
        else:
            values[key] = str(int(desired))
    if experiment.sampling.mode is SamplingMode.NUS:
        # NUS: xN/xT are the ser row size after nusExpand pads to serPadSize (e.g. 908 -> 1024),
        # not the acqus TD, so they must not be overridden (0.2.195); yN/yT/zN/zT are still
        # corrected to the NusTD grid and forced to agree with nusExpand
        values.pop("xN", None)
        values.pop("xT", None)
    return values


def sweep_width_audit(experiment: Experiment) -> list[dict[str, Any]]:
    """Audit every axis, including consistent values and explicit Hz overrides.

    Conversion records retain raw and adopted values, their source, and the
    SW_h/(SW(ppm) x SFO1) consistency ratio for later review.
    """
    entries: list[dict[str, Any]] = []
    for dim in experiment.dimensions:
        ppm_hz = float(dim.sw_ppm) * float(dim.sf)
        ratio = float(dim.sw_hz_raw) / ppm_hz if ppm_hz > 0 and math.isfinite(ppm_hz) else None
        entries.append(
            {
                "axis": dim.logical_axis,
                "nucleus": dim.nucleus,
                "sw_hz_raw": round(float(dim.sw_hz_raw), 6),
                "sw_ppm": round(float(dim.sw_ppm), 9),
                "sfo_mhz": round(float(dim.sf), 9),
                "sw_hz_used": round(float(dim.sw), 6),
                "source": dim.sw_source,
                "note": dim.sw_note,
                "ppm_x_sfo_hz": ppm_hz if math.isfinite(ppm_hz) else None,
                "consistency_ratio": ratio if ratio is None or math.isfinite(ratio) else None,
                "relative_difference": (
                    abs(ratio - 1.0) if ratio is not None and math.isfinite(ratio) else None
                ),
            }
        )
    return entries


def sweep_width_log_lines(experiment: Experiment) -> list[str]:
    """Log lines describing the sweep-width convention (same source and same sentence as
    :func:`sweep_width_audit`).
    """
    return [dim.sw_note for dim in experiment.dimensions if dim.sw_note]


def patch_fid_com(
    text: str,
    experiment: Experiment,
    data_dir: Path | str | None = None,
    manual_keys: set[str] | frozenset[str] = frozenset(),
) -> tuple[str, list[str]]:
    """Correct the fid.com parameters that disagree with acqus/acqu2s to the acqus values, and
    rename the single-file output from the bruker default test.fid to {dataset_id}.fid
    (0.2.163-patch13: automatic and manual paths use the same fid name, so the fid.com output
    name is final).

    ``manual_keys`` are keys edited by hand (``apply_fid_com_overrides`` writes the manual
    values
    again later): they are marked ``manual`` here and are not rewritten from acqus/derived
    values, so that the log and the report do not say "corrected" while the final script still
    holds the manual value (round two 2026-09-24).

    Returns (the corrected text, the list of corrections).
    """
    target = _acqus_values(experiment, data_dir)
    # 2026-09-24: per-key tolerances (the same table as cross_check_fid_com). When the difference
    # is inside the tolerance nothing is rewritten and nothing is reported: for example acqus
    # GRPDLY=67.9841 and the 67.9841461181641 computed by AUTO are the same quantity, so it must
    # not be shown as corrected in the report.
    tolerances = {
        key: tolerance
        for key, (_value, tolerance) in expected_values(experiment, data_dir).items()
        if tolerance
    }
    warnings: list[str] = []

    def replace(match: re.Match) -> str:
        key = match.group(1)
        current = match.group(2)
        if key == "xN":
            # 2026-09-24 (maintainer): ``-xN`` does not go through the generic replacement -- the
            # script's present value is verified first (see the dedicated path below)
            return match.group(0)
        if key in manual_keys:
            # Review 2026-09-24 (2.4): for a key edited by hand, **no** key is rewritten here --
            # previously this applied to CAR/MODE only, so keys such as -ySW ended up with "the
            # log says corrected while the final script still holds the manual value"
            return match.group(0)
        desired = target.get(key)
        if desired is None or desired == "":
            return match.group(0)
        if current != desired:
            tolerance = tolerances.get(key) or 0.0
            if tolerance:
                current_value = _num(current)
                desired_value = _num(desired)
                if (
                    current_value is not None
                    and desired_value is not None
                    and abs(current_value - desired_value) <= tolerance
                ):
                    return match.group(0)
            warnings.append(
                tr(
                    "{p0}: fid.com={p1} → acqus={p2} (corrected)",
                    p0=key,
                    p1=current,
                    p2=desired,
                )
            )
            return f"-{key} {desired}"
        return match.group(0)

    patched = _KEY_RE.sub(replace, text)
    warnings += row_geometry_audit(
        experiment, data_dir, parse_fid_com(text), manual_keys=manual_keys
    )
    audit = carrier_audit(experiment, parse_fid_com(text), manual_keys=manual_keys)

    warnings += carrier_patch_notes(audit)
    # Round two 2026-09-24 (conflict table from the maintainer): acquisition mode is only handled
    # by mode_audit and the generic replacement no longer touches MODE keys (see _acqus_values);
    # here its rewrites are applied, together with the conflict/inference notes.
    mode_plan = mode_audit(
        experiment, parse_fid_com(text), data_dir=data_dir, manual_keys=manual_keys
    )

    mode_targets = mode_writes(mode_plan)
    if mode_targets:
        patched = _KEY_RE.sub(
            lambda match: (
                f"-{match.group(1)} {mode_targets[match.group(1)]}"
                if match.group(1) in mode_targets
                else match.group(0)
            ),
            patched,
        )
    mode_lines = (
        list(mode_plan.get("fix_lines") or [])
        + list(mode_plan.get("conflicts") or [])
        + list(mode_plan.get("notes") or [])
    )
    for line in mode_lines:
        if str(line):
            warnings.append(str(line))
    # Final round 2026-09-24: dimensions that cannot be judged (``F1EA``/unlisted sequences/
    # missing pulse program/family conflict) do **not** get ``FT -neg``, and the **same
    # sentence** is given in three places: the log, the "generate FID" step report and the
    # import warnings (same approach as sweep width and CAR; the single source of the text is
    # pulse_pathways.review_line).
    warnings += [
        str(line)
        for line in (mode_symbol_audit(experiment, data_dir=data_dir).get("lines") or [])
        if str(line)
    ]
    # 0.2.199-patch23: remove the nusExpand -mask stage from fid.com -- SMILE only reads nuslist
    # and does not need a mask, so the stage is simply not generated (instead of deleting the
    # product after conversion). ser_full is kept (bruk2pipe input).
    patched, mask_removed = _MASK_STAGE_RE.subn("", patched)
    if mask_removed:
        warnings.append(
            tr(
                "Removed only the trailing nusExpand -mask generation stage from fid.com; the "
                "earlier nusExpand data-expansion stage is still required before bruk2pipe",
            )
        )
    if experiment.sampling.mode is SamplingMode.NUS:
        patched, grid_warnings = _force_nus_expand_grid(patched, experiment)
        warnings += grid_warnings
        # 0.2.199-patch27: do not force a single file -- bruker decides the output shape itself
        # (single file for TD=1, slices for TD>1) and the program accepts both inputs (see the
        # reconstruct_nus slice fallback)
    patched, out_warnings = patch_fid_out_name(patched, experiment.dataset_id)
    warnings += out_warnings
    return patched, warnings


# 0.2.199-patch23: the whole mask stage of fid.com (nusExpand.tcl -mask plus the xyz2pipe lines
# feeding it) is removed; both the single-file (-out ./mask.fid) and the legacy sliced form
# (-out ./mask/test%03d.fid) are supported. Note: the indented lines after the trailing
# backslash continuation of the nusExpand line must be removed too (a greedy [^\n]* would
# swallow the trailing backslash and the continuation group would fail to match).
_MASK_STAGE_RE = re.compile(
    r"\n(?:[ \t]*\|?[ \t]*xyz2pipe -in [^\n]*? -noWr[ \t]*\\\n)?"
    r"[ \t]*\|?[ \t]*nusExpand\.tcl -mask[^\n]*"
    r"(?:\n[ \t][^\n]*)*"
)

_NUS_EXPAND_RE = re.compile(r"(nusExpand\.tcl[^\n]*?)\\\n")


def _force_nus_expand_grid(text: str, experiment: Experiment) -> tuple[str, list[str]]:
    """Force nusExpand and bruk2pipe onto the same NusTD grid (0.2.195).

    By default nusExpand derives the grid from nuslist (yTNUS/zTNUS); when that disagrees with
    the NusTD-patched bruk2pipe (e.g. cc/63: 83 vs 85) the fid is placed at the wrong offset and
    the reconstruction is wrong. Passing -yT/-zT explicitly puts both on the same grid. Only the
    first (expansion) call is changed; the mask call is left alone.
    """
    td = _effective_td(experiment)
    grid: list[str] = []
    if len(td) > 1:
        grid.append(f"-yT {int(td[1] // 2)}")
    if len(td) > 2:
        grid.append(f"-zT {int(td[2] // 2)}")
    if not grid:
        return text, []
    warnings: list[str] = []

    def replace(match: re.Match) -> str:
        line = match.group(1)
        if "-yT" in line or "-zT" in line:
            return match.group(0)
        warnings.append(tr("NUS data-expansion grid aligned with bruk2pipe: ") + " ".join(grid))
        return line[:-1] + " " + " ".join(grid) + " \\\n"

    patched, _count = _NUS_EXPAND_RE.subn(replace, text, count=1)
    return patched, warnings


def patch_fid_out_name(text: str, dataset_id: str) -> tuple[str, list[str]]:
    """Rewrite the -out single-file output name of fid.com to {dataset_id}.fid.

    The fid.com generated by bruker -AUTO always writes ./test.fid; after the rewrite fid.com
    produces the final name directly, so neither the automatic nor the manual path needs to
    rename it when moving it into place. The sliced output form (fid/test%03d.fid, 3D
    uniform/NUS) keeps bruker's fixed behaviour -- the slice names already agree along the
    automatic and manual paths (test%03d.fid), so nothing is rewritten here.
    Returns (text, list of corrections).
    """
    warnings: list[str] = []

    def replace(match: re.Match) -> str:
        current = match.group(2)
        if "%" in current:
            return match.group(0)  # sliced form: keep bruker's fixed naming
        name = Path(current).name
        if name != "test.fid":
            return match.group(0)
        prefix = current[: -len(name)]
        desired = f"{prefix}{dataset_id}.fid"
        warnings.append(tr("out: {p0} → {p1} (corrected)", p0=current, p1=desired))
        return f"{match.group(1)}{desired}"

    patched = _OUT_RE.sub(replace, text)
    # 0.2.199: after the main output test.fid is renamed, the mask stage's `-in ./test.fid` is
    # renamed with it, otherwise single-file output (e.g. some segmented data) fails to find its
    # input at the mask stage
    patched = re.sub(
        r"(-in\s+)(?:\./)?test\.fid\b",
        r"\g<1>" + f"{dataset_id}.fid",
        patched,
    )
    return patched, warnings


def apply_fid_com_overrides(
    text: str,
    overrides: dict[str, str],
) -> tuple[str, list[str]]:
    """Apply manual fid.com parameter overrides to the script (segment by segment for segmented
    data).

    The manual path only exists so people can tune parameters: what the user changed on the
    reference segment fid.com (the keys of parse_fid_com, e.g. ySW/-yCAR) is applied with the
    same values to every segment's fid.com, while the backend still guarantees consistent
    conversion/slicing/merging. Existing parameters are replaced, never added.
    Returns (text, list of corrections).
    """
    seen: set[str] = set()
    warnings: list[str] = []

    def replace(match: re.Match) -> str:
        key = match.group(1)
        seen.add(key)
        desired = overrides.get(key)
        if desired is None:
            return match.group(0)
        current = match.group(2)
        if current != desired:
            warnings.append(
                tr(
                    "{p0}: fid.com={p1} -> manual={p2} (applied)",
                    p0=key,
                    p1=current,
                    p2=desired,
                )
            )
            return f"-{key} {desired}"
        return match.group(0)

    patched = _KEY_RE.sub(replace, text)
    for key in overrides:
        if key not in seen:
            warnings.append(
                tr(
                    "{p0}: The corresponding parameter was not found in fid.com and has been "
                    "skipped",
                    p0=key,
                )
            )
    return patched, warnings
