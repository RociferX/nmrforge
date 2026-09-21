"""Bruker data reading: locate a dataset, read ser/fid and build the internal data model from
the parameters.

Follows the Bruker integration of NMRFlow:
- metadata: acqus/acqu2s/acqu3s are the authoritative parameter source; PARMODE plus the
  presence heuristic decides the dimensionality;
- binary: read_data reads ser/fid into a complex FID matrix (axis order F1,F2[,F3]; the
  hypercomplex components of the indirect dimension are a leading factor on that axis and are
  combined by core/processing/hypercomplex).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from core.data.bruker_dtype import UnknownBrukerDtype, point_bytes, sample_dtype
from core.data.internal_data_model import (
    AxisRole,
    Dimension,
    Experiment,
    SamplingMode,
)
from core.experiment.bruker_parser import parse_dataset_params
from core.experiment.experiment_classifier import classify
from core.experiment.sampling_detector import detect
from ui_support.i18n import tr

# files that identify a directory as Bruker data (any one of them makes it a data directory;
# used at import time to ignore non-data subfolders, 2026-08-19 Task F)
DATA_KEY_FILES = ("acqus", "acqu2s", "acqu3s", "ser", "fid", "nuslist")


def is_data_directory(path: Path | str) -> bool:
    """A directory counts as a data directory when it holds any key Bruker data file
    (non-data folders return False)."""
    p = Path(path)
    return any((p / name).is_file() for name in DATA_KEY_FILES)



class BrukerDataError(Exception):
    """Bruker data read/validation error (carrying an actionable hint)."""


@dataclass
class DimensionLayout:
    td: int
    mult: int
    fnmode: int
    n_fids: int


@dataclass
class BrukerData:
    matrix: np.ndarray
    layout: dict[str, DimensionLayout]
    data_file: str = ""
    byte_order: str = "little"

    @property
    def layout_summary(self) -> str:
        return ",".join(f"{axis}={d.td}(x{d.mult})" for axis, d in self.layout.items())


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


def _mult_for(fnmode: int) -> int:
    """Number of hypercomplex components: 2 for States/TPPI/States-TPPI/Echo-Antiecho, 1 for QF."""
    return 2 if fnmode in (0, 1, 2, 4, 5, 6) else 1


def _dim(experiment: Experiment, logical_axis: str) -> Dimension:
    for dim in experiment.dimensions:
        if dim.logical_axis == logical_axis:
            return dim
    raise BrukerDataError(tr("Data model is missing {p0} Dimensions", p0=logical_axis))


def _read_complex(path: Path, dtype: np.dtype) -> np.ndarray:
    """Read real/imaginary interleaved data into a complex array (Bruker DQD storage; the element
    type comes from DTYPE)."""
    raw = np.fromfile(path, dtype=dtype)
    pairs = raw.reshape(-1, 2)
    return pairs[:, 0] + 1j * pairs[:, 1]



def _check_size(path: Path, expected_bytes: int, is_nus: bool) -> None:
    actual = path.stat().st_size
    if actual == expected_bytes:
        return
    if is_nus:
        return  # NUS: the FID count follows the sampling schedule; Phase 3 validates it strictly
    raise BrukerDataError(
        tr(
            "{p0} size mismatch: actual {p1} bytes, expected {p2} bytes (TD/dimensionality may not "
            "match)",
            p0=path.name,
            p1=actual,
            p2=expected_bytes,
        )
    )


def read_data(experiment: Experiment) -> BrukerData:
    """Read the Bruker binary data (ser/fid) into a complex FID matrix."""
    ndim = experiment.ndim
    data_file = experiment.source_path / ("ser" if ndim >= 2 else "fid")
    if not data_file.is_file():
        raise BrukerDataError(tr(
            "Missing data file {p0}: "
            "{p1}",
            p0=data_file.name,
            p1=experiment.source_path,
        ))
    acqus = experiment.acquisition_parameters.get("acqus", {})
    try:
        byterda = int(acqus.get("BYTORDA", 0) or 0)
    except (TypeError, ValueError):
        byterda = 0
    byte_order = "little" if byterda == 0 else "big"
    try:
        # DTYPE: 0=int32 / 1=float64 / 2=float32 (an unknown value is reported, never guessed)
        dt = sample_dtype(acqus)
        complex_bytes = point_bytes(acqus)
    except UnknownBrukerDtype as exc:
        raise BrukerDataError(str(exc)) from exc
    is_nus = experiment.sampling.mode is SamplingMode.NUS

    if ndim == 1:
        f2 = _dim(experiment, "F2")
        _check_size(data_file, f2.td * complex_bytes, is_nus)
        matrix = _read_complex(data_file, dt).reshape(-1)
        layout = {"F2": DimensionLayout(td=f2.td, mult=1, fnmode=0, n_fids=1)}
        return BrukerData(
            matrix=matrix, layout=layout, data_file=data_file.name, byte_order=byte_order
        )

    if ndim == 2:
        f1 = _dim(experiment, "F1")
        f2 = _dim(experiment, "F2")
        m1 = _mult_for(_fnmode(experiment, "F1"))
        n_fids = f1.td * m1
        points = f2.td
        _check_size(data_file, n_fids * points * complex_bytes, is_nus)
        matrix = _read_complex(data_file, dt).reshape(n_fids, points)
        layout = {
            "F1": DimensionLayout(
                td=f1.td, mult=m1, fnmode=_fnmode(experiment, "F1"), n_fids=n_fids
            ),
            "F2": DimensionLayout(
                td=f2.td, mult=1, fnmode=_fnmode(experiment, "F2"), n_fids=n_fids
            ),
        }
        return BrukerData(
            matrix=matrix, layout=layout, data_file=data_file.name, byte_order=byte_order
        )

    f1 = _dim(experiment, "F1")
    f2 = _dim(experiment, "F2")
    f3 = _dim(experiment, "F3")
    m1 = _mult_for(_fnmode(experiment, "F1"))
    m2 = _mult_for(_fnmode(experiment, "F2"))
    n_f1 = f1.td * m1
    n_f2 = f2.td * m2
    n_fids = n_f1 * n_f2
    points = f3.td
    _check_size(data_file, n_fids * points * complex_bytes, is_nus)
    matrix = np.transpose(
        _read_complex(data_file, dt).reshape(n_f2, n_f1, points),
        (1, 0, 2),
    )
    layout = {
        "F1": DimensionLayout(td=f1.td, mult=m1, fnmode=_fnmode(experiment, "F1"), n_fids=n_f1),
        "F2": DimensionLayout(td=f2.td, mult=m2, fnmode=_fnmode(experiment, "F2"), n_fids=n_f2),
        "F3": DimensionLayout(td=f3.td, mult=1, fnmode=_fnmode(experiment, "F3"), n_fids=n_fids),
    }
    return BrukerData(
        matrix=matrix, layout=layout, data_file=data_file.name, byte_order=byte_order
    )


def _param_float(block: dict, *keys: str, default: float = 0.0) -> float:
    """Return the first non-empty numeric parameter in the given order."""
    for key in keys:
        value = block.get(key)
        if value in (None, "", 0, 0.0):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return default


def _detect_ndim(params: dict, acqus: dict) -> int:
    """PARMODE plus the presence heuristic: 0=1D, 1=2D, 2=3D."""
    try:
        parmode = int(acqus.get("PARMODE", 0) or 0)
    except (TypeError, ValueError):
        parmode = 0
    if parmode > 0:
        return parmode + 1
    if "acqu3s" in params:
        return 3
    if "acqu2s" in params:
        return 2
    return 1


def _build_dimensions(params: dict, ndim: int) -> list[Dimension]:
    if ndim >= 3:
        entries = [
            ("F3", "acqus", AxisRole.DIRECT),
            ("F2", "acqu2s", AxisRole.INDIRECT),
            ("F1", "acqu3s", AxisRole.INDIRECT),
        ]
    elif ndim == 2:
        entries = [
            ("F2", "acqus", AxisRole.DIRECT),
            ("F1", "acqu2s", AxisRole.INDIRECT),
        ]
    else:
        entries = [("F2", "acqus", AxisRole.DIRECT)]

    dims: list[Dimension] = []
    for logical, filename, role in entries:
        block = params.get(filename)
        if block is None:
            continue
        sf = _param_float(block, "SFO1")
        o1 = _param_float(block, "O1")
        o1p = _param_float(block, "O1P")
        if not o1p:
            # TopSpin defines O1P = O1/BF1 (the offset relative to the base frequency); the old
            # calculation used O1/SFO1 (relative to the actual carrier SFO1=BF1+O1), which differs
            # by about O1P^2/1e6 (117 ppm -> 0.014 ppm)
            bf1 = _param_float(block, "BF1")
            if o1 and bf1:
                o1p = o1 / bf1
            elif o1 and sf:
                o1p = o1 / sf  # with BF1 missing, stay compatible with old data (relative to SFO1)
        td = int(block.get("TD", 0) or 0)
        if td == 0 and filename == "acqus" and "acqu" in params:
            # 0.2.199-patch29gk: when acqus has TD=0 fall back to the authoritative TD in acqu
            # (otherwise the conversion hangs)
            td = int(params["acqu"].get("TD", 0) or 0)
        dims.append(
            Dimension(
                logical_axis=logical,
                nucleus=str(block.get("NUC1", "")),
                sf=sf,
                sw=_param_float(block, "SW_h", "SW"),
                o1=o1,
                o1p=o1p,
                td=td,
                acquisition_mode=str(block.get("FnMODE", "")),
                axis_direction="increasing",
                role=role,
            )
        )
    return dims


def read_segments(paths: list[Path | str]) -> Experiment:
    """Read several datasets of one experiment into a multi-segment Experiment (their
    parameters must match)."""
    if len(paths) < 2:
        raise ValueError(tr("Multi-segment experiments require at least 2 dataset directories"))
    dirs = [Path(p).resolve() for p in paths]
    base = read_dataset(dirs[0])
    base.segments = dirs

    def _effective_td(exp: Experiment) -> list[int]:
        """For NUS the indirect dimension uses NusTD (the sampling grid), otherwise the declared
        TD."""
        td = [d.td for d in exp.dimensions]
        if exp.sampling.mode is SamplingMode.NUS:
            for index, filename in ((1, "acqu2s"), (2, "acqu3s")):
                if len(td) <= index:
                    continue
                block = exp.acquisition_parameters.get(filename, {})
                try:
                    nus_td = int(block.get("NusTD", 0) or 0)
                except (TypeError, ValueError):
                    continue
                if nus_td:
                    td[index] = nus_td
        return td

    def _key(exp: Experiment) -> tuple:
        """Dimensionality/nuclei/TD must match exactly (the sweep width is compared separately with
        a relative tolerance, 0.2.199-patch29cs)."""
        return (
            exp.ndim,
            [d.nucleus for d in exp.dimensions],
            _effective_td(exp),
        )

    def _same_sw(a: Experiment, b: Experiment) -> bool:
        """Relative tolerance for the sweep width (0.2.199-patch29cs): different segments may write
        SW_h with different precision (e.g. 11904.762 vs 11904.7619047619) while the physical sweep
        width is the same; genuinely different experiments differ by far more than the 1e-4
        relative tolerance."""
        if len(a.dimensions) != len(b.dimensions):
            return False
        for da, db in zip(a.dimensions, b.dimensions):
            if abs(da.sw - db.sw) > 1e-4 * max(abs(da.sw), abs(db.sw), 1.0):
                return False
        return True

    for extra in dirs[1:]:
        other = read_dataset(extra)
        if _key(other) != _key(base) or not _same_sw(other, base):
            raise ValueError(
                tr(
                    "dataset segment parameters disagree: {p0} vs {p1} (dimensionality / nuclei / "
                    "TD / sweep width must all "
                    "match)",
                    p0=dirs[0],
                    p1=extra,
                )
            )
    if base.sampling.mode is SamplingMode.NUS:
        base.sampling.evidence.append(
            tr(
            "multi-segment dataset ({p0} directories): the datasets and their sampling points were "
            "merged and produced by the "
            "backend",
            p0=len(dirs),
        )
        )
    return base


def discover_segment_dirs(container: Path | str) -> list[Path]:
    """Subdirectories of a container directory that directly hold acqus (sorted by name), taken as
    the segments of one segmented acquisition.

    Different from a batch import: a batch import creates a separate entry for every independent
    dataset, while these are the segments into which one experiment was split by acquisition time
    and are merged into a single data entry (chosen explicitly by the caller, never guessed).
    """
    root = Path(container)
    if not root.is_dir():
        raise ValueError(tr("directory does not exist: {p0}", p0=root))
    return sorted(
        p for p in root.iterdir() if p.is_dir() and (p / "acqus").is_file()
    )


def classify_segment_kind(paths: list[Path | str]) -> str:
    """Identify the nature of a multi-segment container dataset (0.2.199-patch29cu, user rule).

    Returns:
    - "repeat_uniform": conventional (uniform) sampling with identical sampling parameters ->
      repeated experiments to be summed for noise reduction;
    - "repeat_nus": NUS with the same sampling-point set in every segment -> repeated experiments
      to be summed;
    - "segmented_nus": NUS with different sampling-point sets -> segments (the complementary
      points complete the grid); a missing nuslist or mixed/uncertain sampling modes are
      conservatively treated as segmented.
    """
    dirs = [Path(p) for p in paths]
    if len(dirs) < 2:
        raise ValueError(tr("At least 2 dataset directories are required"))
    exps = [read_dataset(d) for d in dirs]
    modes = {e.sampling.mode for e in exps}
    if SamplingMode.NUS in modes:
        if len(modes) > 1:
            return "segmented_nus"  # mixed/uncertain: conservatively treat it as segmented
        point_sets = [set(e.sampling.nus_list) for e in exps]
        if any(not ps for ps in point_sets):
            return "segmented_nus"  # no nuslist, so identical points cannot be confirmed
        if all(ps == point_sets[0] for ps in point_sets[1:]):
            return "repeat_nus"
        return "segmented_nus"
    return "repeat_uniform"


def read_dataset_container(path: Path | str) -> tuple[Experiment, list[Path]]:
    """Read a dataset container: a single Bruker directory is read directly (segments=[]); a
    container directory (subdirectories that directly hold acqus) is read as segments
    (read_segments checks that they match)."""
    p = Path(path)
    if (p / "acqus").is_file():
        return read_dataset(p), []
    segments = discover_segment_dirs(p)
    if not segments:
        raise ValueError(
            tr(
                "the selected directory is neither a Bruker dataset nor does any subdirectory hold "
                "a data segment (acqus/acqu2s/acqu3s/ser/fid/nuslist): "
                "{p0}",
                p0=p,
            )
        )
    if len(segments) == 1:
        raise ValueError(
            tr(
                "only 1 subdirectory holds data files ({p0}); a segmented import needs at least 2; "
                "these subdirectories look independent: "
                "{p1}",
                p0=segments[0].name,
                p1=p,
            )
        )
    try:
        return read_segments(segments), segments
    except ValueError as exc:
        if tr("dataset segment parameters disagree") in str(exc):
            # 0.2.198: mismatching subdirectory parameters mean the subdirectories are independent
            # datasets rather than the segments of one experiment; say so explicitly rather than
            # reporting something vague
            raise ValueError(
                tr(
                    "the selected directory is not a segmented experiment (the subdirectory "
                    "parameters disagree, so these look like independent datasets): "
                    "{p0}",
                    p0=exc,
                )
            ) from exc
        raise


def _pdata_title(dataset_dir: Path) -> str:
    """Read pdata/<procno>/title from the Bruker processing results (patch29fd)."""
    try:
        pdata = dataset_dir / "pdata"
        if not pdata.is_dir():
            return ""
        procs = sorted(
            (p for p in pdata.iterdir() if p.is_dir() and p.name.isdigit()),
            key=lambda p: int(p.name),
            reverse=True,
        )
        for proc in procs:
            title = proc / "title"
            if title.is_file():
                text = title.read_text(encoding="latin-1", errors="replace").strip()
                if text:
                    return text
    except Exception:
        pass
    return ""


def read_dataset(path: Path) -> Experiment:
    """Read one Bruker dataset directory and build an Experiment (metadata only, no semantics)."""
    dataset_dir = path
    if not (dataset_dir / "acqus").is_file():
        raise ValueError(tr(
            "Not a Bruker dataset directory (acqus is "
            "missing):{p0}",
            p0=dataset_dir,
        ))
    params = parse_dataset_params(dataset_dir)
    acqus = params.get("acqus", {})
    ndim = _detect_ndim(params, acqus)
    experiment = Experiment(
        dataset_id=dataset_dir.name,
        source_path=dataset_dir,
        ndim=ndim,
        acquisition_order=[name for name in ("acqus", "acqu2s", "acqu3s") if name in params],
        dimensions=_build_dimensions(params, ndim),
        acquisition_parameters=params,
    )
    experiment.sampling = detect(experiment)
    experiment.experiment_type = classify(
        experiment, user_title=_pdata_title(dataset_dir)
    )
    return experiment
