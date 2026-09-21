"""Spectrum domain object: Read NMRPipe 2D spectrum and construct ppm coordinate axes. Axis
convention (consistent with the old project NMRFlow): ``data[0]`` = F1(OK/y axis), ``data[1]`` =
F2(List/x axis). ppm axes are represented by NMRPipe head FDF*ORIG Define (only update ORIG
after EXT), fall back to CAR when missing."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

import numpy as np

from ui_support.i18n import tr
from viewer.axis_labels import axis_labels_from_nuclei, infer_nucleus

logger = logging.getLogger("nmrforge.viewer.spectrum")

# Chemical shift ranges of common nuclei (ppm),Spectral loading axis sequence/Reference for self-
# test(0.2.122).
_NUCLEUS_PPM_RANGES: dict[str, tuple[float, float]] = {
    "1H": (-5.0, 20.0),
    "15N": (90.0, 140.0),
    "13C": (10.0, 190.0),
}
_NUCLEI = set(_NUCLEUS_PPM_RANGES) | {"2H", "19F", "31P", "23Na", "29Si"}

# The abscissa shows the priority (0.2.153, user rule): H > N > C; unknown cores do not participate
# in transposition.
_NUCLEUS_X_PRIORITY: dict[str, int] = {"1H": 0, "15N": 1, "13C": 2}

# NMRPipe/Sparky Common non-standard LABEL Alias (0.2.199-patch29dh): Real data 1H axis is often
# written as "HN" (sampleC, cc, etc. measured), single letters are common abbreviations.
_NMRPIPE_LABEL_ALIASES: dict[str, str] = {
    "HN": "1H",
    "H": "1H",
    "N": "15N",
    "C": "13C",
    "P": "31P",
    "F": "19F",
    "D": "2H",
    "NA": "23Na",
    "SI": "29Si",
}


def _parse_nmrpipe_label(label: str) -> str:
    """NMRPipe FDF*LABEL('N15'/'H1'/'C13', same core index '15Nx'/'1Hy', alias 'HN') -> core name
    ('15N'/'1H'/'13C'); failure returns ''. 0.2.199-patch29ai: same core unique label
    (15Nx/1Hy/1Hz) remove the tail first x/y/z Then match the core name to avoid LABEL relying
    on OBS to get the bottom of things after the parsing fails."""
    text = str(label or "").strip().upper()
    if not text:
        return ""
    if text in _NUCLEI:
        return text
    if text in _NMRPIPE_LABEL_ALIASES:
        return _NMRPIPE_LABEL_ALIASES[text]
    if text[-1:] in ("X", "Y", "Z") and text[:-1] in _NUCLEI:
        return text[:-1]
    digits = "".join(ch for ch in text if ch.isdigit())
    letters = "".join(ch for ch in text if ch.isalpha())
    candidate = f"{digits}{letters}" if digits and letters else ""
    return candidate if candidate in _NUCLEI else ""


def _storage_nuclei(dic: dict, prefixes: tuple[str, ...]) -> list[str]:
    """Press the NMRPipe header to infer the core of each storage axis: LABEL gives priority, OBS
    takes the bottom."""
    nuclei: list[str] = []
    for prefix in prefixes:
        nucleus = _parse_nmrpipe_label(dic.get(prefix + "LABEL", ""))
        if not nucleus:
            try:
                obs = float(dic.get(prefix + "OBS", 0) or 0)
            except (TypeError, ValueError):
                obs = 0.0
            nucleus = infer_nucleus(obs)
        nuclei.append(nucleus)
    return nuclei



def _fdf_prefix_for_axis(dic: dict, ndim: int, axis_idx: int) -> str:
    """The data axis axis_idx corresponds to the FDF parameter block prefix ('FDF1'/'FDF2'/...).
    The data axis order returned by nmrglue pipe.read is opposite to the NMRPipe storage order,
    and the logical dimension number corresponding to each axis is given by the header
    FDDIMORDER (same origin as nmrglue make_uc/guess_udic: axis i ↔
    FDF{FDDIMORDER[ndim-1-i]});FDDIMORDER Missing/When it is illegal Roll back the old position
    formula FDF{axis_idx+1}(0.2.151 previous line)."""
    try:
        order = [int(v) for v in dic.get("FDDIMORDER") or []]
    except (TypeError, ValueError):
        order = []
    if len(order) >= ndim:
        dim = order[ndim - 1 - axis_idx]
        if 1 <= dim <= 4:
            return f"FDF{dim}"
    return f"FDF{axis_idx + 1}"




def _logical_nuclei_from_order(
    dic: dict, ndim: int, storage_nuclei: list[str]
) -> list[str] | None:
    """Press FDDIMORDER to infer the logical sequence core list (F1/F2/F3 sequence); cannot form an
    arrangement and return None. The logical dimension number of nmrglue data axis i =
    FDDIMORDER[ndim-1-i]; based on this, the storage sequence core is mapped back to the logical
    sequence, so that it can be rearranged by the head when opened directly without metadata
    (0.2.152)."""
    try:
        order = [int(v) for v in dic.get("FDDIMORDER") or []]
    except (TypeError, ValueError):
        return None
    if len(order) < ndim:
        return None
    logical: list[str | None] = [None] * ndim
    for axis_idx, nucleus in enumerate(storage_nuclei):
        dim = order[ndim - 1 - axis_idx]
        if not (1 <= dim <= ndim) or logical[dim - 1] is not None:
            return None
        logical[dim - 1] = nucleus
    if any(n is None for n in logical):
        return None
    return [n for n in logical if n is not None]  # type: ignore[return-value]


def _labels_from_nuclei(
    nuclei: list[str], labels: tuple[str, ...]
) -> tuple[str, ...]:
    """When the core is known, it is replaced by the core symbol label (N/H/C, Hx/Hy, etc.); when
    the core is unknown, only F* placeholders are accepted. 0.2.152: When the independent viewer
    is opened directly without metadata, the axis label is generated by the core inferred from
    the head LABEL/OBS (consistent with nmrDraw NAME), and is no longer displayed F1/F2/F3.
    0.2.199-patch29dh(user):axis sequence/tag not used metadata -- metadata is rejected when the
    core is unknown Derive labels and fall back to F* position occupancy to avoid mislabeling."""
    if not labels or len(nuclei) != len(labels):
        return labels
    if all(n for n in nuclei):
        derived = axis_labels_from_nuclei(nuclei)
        if derived and len(derived) == len(labels):
            return derived
        return labels
    if any(
        not str(label).startswith("F") or not str(label)[1:].isdigit()
        for label in labels
    ):
        return tuple(f"F{i + 1}" for i in range(len(nuclei)))
    return labels




def orient_x_priority(spectrum: Spectrum) -> Spectrum:
    """The abscissa coordinate of the two-dimensional spectrum is oriented according to the core
    priority H > N > C (0.2.153 display rule). When the abscissa core priority is lower than the
    ordinate, the data is transposed and the axes are exchanged (dim_indices synchronous
    exchange); the two axes have the same core, and the original direction is maintained when
    the core is unknown or has met the priority."""
    if spectrum.data.ndim != 2:
        return spectrum
    x_nuc = infer_nucleus(spectrum.x_axis.obs_mhz)
    y_nuc = infer_nucleus(spectrum.y_axis.obs_mhz)
    px = _NUCLEUS_X_PRIORITY.get(x_nuc, 100)
    py = _NUCLEUS_X_PRIORITY.get(y_nuc, 100)
    if px <= py:
        return spectrum
    transposed = Spectrum(
        np.asarray(spectrum.data).T,
        [spectrum.x_axis, spectrum.y_axis],
        source=spectrum.source,
    )
    dims = getattr(spectrum, "dim_indices", None)
    if dims:
        transposed.dim_indices = (dims[1], dims[0])
    robust = getattr(spectrum, "robust_max", None)
    if robust is not None:
        transposed.robust_max = robust
    floor = getattr(spectrum, "noise_floor", None)
    if floor is not None:
        transposed.noise_floor = floor
    return transposed


def _relabel_axes(
    axes: list[SpectrumAxis], labels: tuple[str, ...]
) -> list[SpectrumAxis]:
    """Rebuild axis objects in logical order (SpectrumAxis is frozen, label needs to be rebuilt)."""
    return [
        SpectrumAxis(
            label=labels[i] if i < len(labels) else axis.label,
            size=axis.size,
            sw_hz=axis.sw_hz,
            obs_mhz=axis.obs_mhz,
            carrier_ppm=axis.carrier_ppm,
            orig_hz=axis.orig_hz,
        )
        for i, axis in enumerate(axes)
    ]


def _permutation_to_logical(
    storage_nuclei: list[str], logical_nuclei: list[str]
) -> list[int] | None:
    """Storage axis -> logical position arrangement; cannot form an arrangement (length/unknown
    core/no match) returns None. 0.2.168: The same core (such as two 1Hs of 1H/13C/1H) is
    matched according to the dimension position semantics -- Core type The same position can be
    interchanged, and the rearrangement results are equivalent at the display level (Hx/Hy is
    determined by the position)."""
    n = len(storage_nuclei)
    if n != len(logical_nuclei) or n == 0:
        return None
    if any(not s for s in storage_nuclei) or any(not t for t in logical_nuclei):
        return None
    perm: list[int | None] = [None] * n
    used = [False] * n
    for lpos, target in enumerate(logical_nuclei):
        for spos, source in enumerate(storage_nuclei):
            if source == target and not used[spos]:
                perm[spos] = lpos
                used[spos] = True
                break
        else:
            return None
    return [int(p) for p in perm]  # type: ignore[arg-type]


def _reorder_to_logical(
    data: np.ndarray,
    axes: list[SpectrumAxis],
    storage_nuclei: list[str],
    logical_nuclei: list[str],
    path: Path | str,
) -> tuple[np.ndarray, list[SpectrumAxis], list[str]]:
    """Rearrange the storage axis order to logical order (F1, F2, F3); maintain the status quo and
    alarm if it cannot be confirmed."""
    perm = _permutation_to_logical(storage_nuclei, logical_nuclei)
    if perm is None:
        logger.warning(
            (
                tr(
                "Axis sequence calibration: Unable to confirm logical axis sequence, maintain "
                "storage sequence (storage %s, logic %s): "
                "%s",
            )
            ),
            storage_nuclei, logical_nuclei, path,
        )
        return data, axes, storage_nuclei
    if perm == list(range(len(perm))):
        return data, axes, storage_nuclei
    inv = [0] * len(perm)
    for spos, lpos in enumerate(perm):
        inv[lpos] = spos
    reordered = np.transpose(data, inv)
    reordered_axes = [axes[spos] for spos in inv]
    reordered_nuclei = [storage_nuclei[spos] for spos in inv]
    logger.info(
        tr("Axis rearrangement: Storage (%s) -> Logic (%s): %s"),
        " ".join(storage_nuclei), " ".join(logical_nuclei), path,
    )
    return reordered, reordered_axes, reordered_nuclei


def _warn_ppm_range_mismatch(
    axes: list[SpectrumAxis], nuclei: list[str], path: Path | str
) -> None:
    """Spectrum loading self-test: alarm when each axis core does not match the common chemical
    shift range (Possible axis order/citation issues)."""
    for axis, nucleus in zip(axes, nuclei):
        rng = _NUCLEUS_PPM_RANGES.get(nucleus)
        if rng is None or axis.size == 0:
            continue
        lo = float(np.min(axis.ppm))
        hi = float(np.max(axis.ppm))
        if hi < rng[0] or lo > rng[1]:
            logger.warning(
                tr(
                    "axis order/reference self-check: %s axis (%s) ppm range [%.1f, %.1f] exceeds "
                    "the usual range %s: "
                    "%s",
                ),
                axis.label, nucleus, lo, hi, rng, path,
            )


@dataclass(frozen=True)
class SpectrumAxis:
    """One-dimensional spectral axis: data point index <-> ppm."""

    label: str
    size: int
    sw_hz: float
    obs_mhz: float
    carrier_ppm: float
    orig_hz: float = 0.0

    @cached_property
    def ppm(self) -> np.ndarray:
        """Ppm coordinates of each data point (NMRPipe convention: ppm decreases with the index,
        ORIG is the axis end point frequency)."""
        idx = np.arange(self.size)
        if self.orig_hz:
            return self.orig_hz / self.obs_mhz + (self.size - 1 - idx) * (
                self.sw_hz / (self.size * self.obs_mhz)
            )
        return self.carrier_ppm + (self.size / 2 - idx) * self.sw_hz / (
            self.size * self.obs_mhz
        )

    def index_at(self, ppm_value: float) -> int:
        return int(np.argmin(np.abs(self.ppm - ppm_value)))

    def index_at_f(self, ppm_value: float) -> float:
        """Subpixel index: linear inverse interpolation (peak marker subpixel positioning,
        0.2.199-patch29eo)."""
        p = self.ppm
        n = self.size
        if n < 2:
            return 0.0
        i = int(np.argmin(np.abs(p - ppm_value)))
        if i <= 0 or i >= n - 1 or p[i] == ppm_value:
            return float(i)
        between_next = (
            (p[i + 1] <= ppm_value <= p[i])
            or (p[i] <= ppm_value <= p[i + 1])
        )
        j = i + 1 if between_next else i - 1
        denom = p[i] - p[j]
        if abs(denom) < 1e-15:
            return float(i)
        return float(j) + (ppm_value - p[j]) * (i - j) / denom

    def ppm_at(self, index: int) -> float:
        return float(self.ppm[index])

    def ppm_at_f(self, value: float) -> float:
        """Subpixel ppm: linear interpolation (used for peak centroid positioning)."""
        i0 = max(0, int(np.floor(value)))
        i1 = min(i0 + 1, self.size - 1)
        i0 = min(i0, i1)
        frac = value - i0
        return float(self.ppm[i0] * (1.0 - frac) + self.ppm[i1] * frac)


class Spectrum:
    """Two-dimensional spectrum:``data`` shape (F1, F2);axes[0]=F1,axes[1]=F2."""

    def __init__(
        self,
        data: np.ndarray,
        axes: list[SpectrumAxis],
        source: Path | str | None = None,
    ) -> None:
        self.data = np.asarray(data, dtype=float)
        self.axes = list(axes)
        self.source = Path(source) if source else None

    @property
    def x_axis(self) -> SpectrumAxis:
        return self.axes[1]

    @property
    def y_axis(self) -> SpectrumAxis:
        return self.axes[0]

    @property
    def max_intensity(self) -> float:
        return float(np.max(self.data)) if self.data.size else 0.0

    @staticmethod
    def _normalize_data(data: np.ndarray, source: str) -> np.ndarray:
        """The real part of the complex number is taken, flattened to pseudo three dimensions (the
        first dimension is 1), and forced to two dimensions."""
        data = np.asarray(data)
        if np.iscomplexobj(data):
            data = data.real
        while data.ndim > 2 and data.shape[0] == 1:
            data = data[0]
        if data.ndim != 2:
            raise ValueError(
                    tr(
                    "Only supports 2D spectrum (currently {p0} dimension, shape {p1}): "
                    "{p2}",
                    p0=data.ndim,
                    p1=data.shape,
                    p2=source,
                )
            )
        return data

    def estimate_noise(self, fraction: float = 0.1) -> float:
        """Estimate the noise level using the standard deviation of the lower right area."""
        ny, nx = self.data.shape
        r0 = int(ny * (1 - fraction))
        c0 = int(nx * (1 - fraction))
        region = self.data[r0:, c0:]
        return float(np.std(region)) if region.size else 0.0

    @classmethod
    def load_from_ft2(
        cls,
        path: Path | str,
        labels: tuple[str, str] = ("F1", "F2"),
        nuclei: list[str] | None = None,
    ) -> Spectrum:
        """Use nmrglue to read NMRPipe two-dimensional.ft2 and construct ppm axis. The nuclei are
        metadata logical axis cores (F1/F2 order); when non-empty, infer the storage axis cores
        according to the storage head FDF*LABEL/FDF*OBS and rearrange them into logical order,
        and do ppm range self-test (0.2.122); the default is to keep the position order (old
        behaviour). 0.2.151: Data axis -> FDF block mapping is established according to the head
        FDDIMORDER (same origin as nmrglue guess_udic). When there is no FDDIMORDER, the old
        position formula is rolled back. 0.2.152: When there is no metadata, press FDDIMORDER to
        rearrange the logical order, and the axis label is derived from the head core (F1/F2/F3
        is no longer displayed)."""
        import nmrglue as ng

        dic, data = ng.pipe.read(str(path))
        data = cls._normalize_data(data, str(path))
        if int(dic.get("FDDIMCOUNT", 2)) < 2:
            raise ValueError(tr("Only supports 2D spectrum (FDDIMCOUNT<2): {p0}", p0=path))

        def _axis(prefix: str, label: str, size: int) -> SpectrumAxis:
            return SpectrumAxis(
                label=label,
                size=size,
                sw_hz=float(dic[prefix + "SW"]),
                obs_mhz=float(dic[prefix + "OBS"]),
                carrier_ppm=float(dic[prefix + "CAR"]),
                orig_hz=float(dic.get(prefix + "ORIG", 0.0) or 0.0),
            )

        prefixes = tuple(
            _fdf_prefix_for_axis(dic, data.ndim, i) for i in range(data.ndim)
        )
        axes = [
            _axis(prefix, labels[i], int(data.shape[i]))
            for i, prefix in enumerate(prefixes)
        ]
        storage = _storage_nuclei(dic, prefixes)
        # 0.2.199-patch29dh(user): The axis sequence is only based on the.ft3 file header
        # (FDDIMORDER+LABEL/OBS), and does not use metadata to cover the details. -- The software
        # processing flow has axis rearrangement. Metadata's Bruker F1/F2/F3 is the acquisition
        # sequence and does not represent the final.ft3 axis sequence.
        logical = _logical_nuclei_from_order(dic, data.ndim, storage)
        if logical is not None:
            data, axes, storage = _reorder_to_logical(
                data, axes, storage, logical, path
            )
            labels = axis_labels_from_nuclei(logical)
        elif all(storage):
            # Header without FDDIMORDER: processed in storage order (positional formula
            # FDF{i}=F{i}), when all cores are known, the label is directly generated by the storage
            # core.
            labels = axis_labels_from_nuclei(storage)
        axes = _relabel_axes(axes, _labels_from_nuclei(storage, labels))
        _warn_ppm_range_mismatch(axes, storage, path)
        logger.info(tr("Load spectrum: %s (%s)"), path, data.shape)
        return cls(data, axes, source=Path(path))

class Spectrum3D:
    """3D Spectrum (Contract §10.1):``data`` shape (F1, F2, F3);axes=[F1,F2,F3]."""

    def __init__(
        self,
        data: np.ndarray,
        axes: list[SpectrumAxis],
        source: Path | str | None = None,
    ) -> None:
        self.data = np.asarray(data, dtype=float)
        self.axes = list(axes)
        self.source = Path(source) if source else None
        self._lazy = False
        self._global_noise: float | None = None
        self._full_noise: float | None = None

    @property
    def max_intensity(self) -> float:
        if getattr(self, "_lazy", False):
            # 0.2.199-patch29dd: Lazy loading does not read the full amount, use sample plane
            # estimation.
            try:
                plane = self._lazy_cached_plane(0, 0)
                return float(np.max(plane)) if plane.size else 0.0
            except Exception:  # noqa: BLE001 - Estimation failure returns 0.
                return 0.0
        size = int(np.prod(self.data.shape)) if hasattr(self.data, "shape") else 0
        return float(np.max(self.data)) if size else 0.0

    @staticmethod
    def _normalize_data(data: np.ndarray, source: str) -> np.ndarray:
        """Complex numbers take the real part and force three dimensions."""
        data = np.asarray(data)
        if np.iscomplexobj(data):
            data = data.real
        if data.ndim != 3:
            raise ValueError(
                    tr(
                    "Only supports 3D spectrum (currently {p0} dimension, shape {p1}): "
                    "{p2}",
                    p0=data.ndim,
                    p1=data.shape,
                    p2=source,
                )
            )
        return data

    @staticmethod
    def _header_int(dic: dict, key: str) -> int:
        try:
            return int(float(dic.get(key, 0) or 0))
        except (TypeError, ValueError):
            return 0

    @classmethod
    def load_from_ft3(
        cls,
        path: Path | str,
        labels: tuple[str, str, str] = ("F1", "F2", "F3"),
        nuclei: list[str] | None = None,
        *,
        lazy: bool = False,
    ) -> Spectrum3D:
        """Use nmrglue to read NMRPipe three-dimensional.ft3 and build ppm axis (contract §10.1).
        lazy=True(0.2.199-patch29dd): streaming 3D (FDPIPEFLAG!=0) using read_lowmem Read only
        header/axis, read the corresponding 2D plane from file on demand when slicing -- does
        not load the full 3D body, Opening Dapu is no longer slow/Occupies memory; non-stream
        file falls back to full read. nuclei are metadata logical axis cores (F1/F2/F3 order);
        when not empty, infer the storage axis cores according to the storage head
        FDF*LABEL/FDF*OBS, if inconsistent, rearrange data/axes to logical order and alert log
        "axis order rearrangement", and do ppm range self-test (0.2.122); default Preserve
        positional order (old behaviour). 0.2.151: Data axis -> FDF block mapping is established
        by head FDDIMORDER (same origin as nmrglue guess_udic; real 3D output ORDER 2 3 1 =
        store (F2,F3,F1)), fallback to the old positional formula without FDDIMORDER. 0.2.152:
        Press without metadata FDDIMORDER rearranges the logical order and derives axis labels
        from the head kernel (F1/F2/F3 is no longer shown). The single file 3D stream (xyz2pipe
        product, FDPIPEFLAG=1) reads back the shape (F1, F2, F3), where F1=FDF3SIZE,
        F2=FDSPECNUM, F3=FDSIZE; non-streaming files are reshaped according to the same
        convention."""
        import nmrglue as ng

        if lazy:
            try:
                dic, lazy_data = ng.pipe.read_lowmem(str(path))
            except Exception:  # noqa: BLE001 - read_lowmem Not supported (incomplete header, etc.).
                return cls.load_from_ft3(path, labels=labels, nuclei=nuclei)
            if cls._header_int(dic, "FDDIMCOUNT") < 3:
                raise ValueError(tr(
                    "Only supports three-dimensional spectrum (FDDIMCOUNT<3): "
                    "{p0}",
                    p0=path,
                ))
            flag = float(dic.get("FDPIPEFLAG", 0.0) or 0.0)
            if flag == 0:
                # Non-streaming single file: different layout, unreliable lazy reading, full
                # rollback (usually a small file).
                return cls.load_from_ft3(path, labels=labels, nuclei=nuclei)
            return cls._build_lazy(path, dic, lazy_data, labels, nuclei)

        dic, data = ng.pipe.read(str(path))
        data = np.asarray(data)
        if cls._header_int(dic, "FDDIMCOUNT") < 3:
            raise ValueError(tr(
                "Only supports three-dimensional spectrum (FDDIMCOUNT<3): "
                "{p0}",
                p0=path,
            ))
        if data.ndim == 2:
            f1 = cls._header_int(dic, "FDF3SIZE")
            f3 = cls._header_int(dic, "FDSIZE")
            if f1 <= 0 or f3 <= 0 or data.shape[0] % f1 or data.shape[1] != f3:
                raise ValueError(
                    tr(
                        "Unable to restore 3D spectrum from 2D storage (FDF3SIZE={p0}, "
                        "FDSIZE={p1}): "
                        "{p2}",
                        p0=f1,
                        p1=f3,
                        p2=path,
                    )
                )
            data = data.reshape((f1, data.shape[0] // f1, f3))
        data = cls._normalize_data(data, str(path))

        def _axis(prefix: str, label: str, size: int) -> SpectrumAxis:
            return SpectrumAxis(
                label=label,
                size=size,
                sw_hz=float(dic[prefix + "SW"]),
                obs_mhz=float(dic[prefix + "OBS"]),
                carrier_ppm=float(dic[prefix + "CAR"]),
                orig_hz=float(dic.get(prefix + "ORIG", 0.0) or 0.0),
            )

        prefixes = tuple(
            _fdf_prefix_for_axis(dic, data.ndim, i) for i in range(data.ndim)
        )
        axes = [
            _axis(prefix, labels[i], int(data.shape[i]))
            for i, prefix in enumerate(prefixes)
        ]
        storage = _storage_nuclei(dic, prefixes)
        # 0.2.199-patch29dh(user): The axis order is only based on the file header and does not use
        # metadata (same as load_from_ft2, see above).
        logical = _logical_nuclei_from_order(dic, data.ndim, storage)
        if logical is not None:
            data, axes, storage = _reorder_to_logical(
                data, axes, storage, logical, path
            )
            labels = axis_labels_from_nuclei(logical)
        elif all(storage):
            labels = axis_labels_from_nuclei(storage)
        axes = _relabel_axes(axes, _labels_from_nuclei(storage, labels))
        _warn_ppm_range_mismatch(axes, storage, path)
        logger.info(tr("Load 3D spectrum: %s (%s)"), path, data.shape)
        return cls(data, axes, source=Path(path))

    @classmethod
    def _build_lazy(
        cls,
        path: Path | str,
        dic: dict,
        lazy_data,
        labels: tuple[str, str, str],
        nuclei: list[str] | None,
    ) -> Spectrum3D:
        """Lazy loading construction: Read only header/axis, data is a streaming lazy object
        (0.2.199-patch29dd)."""

        def _axis(prefix: str, label: str, size: int) -> SpectrumAxis:
            return SpectrumAxis(
                label=label,
                size=size,
                sw_hz=float(dic[prefix + "SW"]),
                obs_mhz=float(dic[prefix + "OBS"]),
                carrier_ppm=float(dic[prefix + "CAR"]),
                orig_hz=float(dic.get(prefix + "ORIG", 0.0) or 0.0),
            )

        data_shape = tuple(int(v) for v in lazy_data.shape)
        prefixes = tuple(
            _fdf_prefix_for_axis(dic, 3, i) for i in range(3)
        )
        axes = [
            _axis(prefix, labels[i], data_shape[i])
            for i, prefix in enumerate(prefixes)
        ]
        storage_nuclei = _storage_nuclei(dic, prefixes)
        # 0.2.199-patch29dh(user): The axis order is only based on the file header and does not use
        # metadata (same as load_from_ft3, see above).
        logical_nuclei = _logical_nuclei_from_order(dic, 3, storage_nuclei)
        if logical_nuclei is None:
            logical_nuclei = storage_nuclei
            if all(storage_nuclei):
                labels = axis_labels_from_nuclei(storage_nuclei)
        else:
            labels = axis_labels_from_nuclei(logical_nuclei)
        perm = _permutation_to_logical(storage_nuclei, logical_nuclei)
        inv = list(range(3))
        if perm is not None and perm != [0, 1, 2]:
            inv = [0] * 3
            for spos, lpos in enumerate(perm):
                inv[lpos] = spos
            logger.info(
                tr("Axis order rearrangement (lazy loading): Storage (%s) -> Logic (%s): %s"),
                " ".join(storage_nuclei), " ".join(logical_nuclei), path,
            )
        logical_axes = [axes[inv[dim]] for dim in range(3)]
        logical_storage = [storage_nuclei[inv[dim]] for dim in range(3)]
        logical_axes = _relabel_axes(
            logical_axes, _labels_from_nuclei(logical_storage, labels)
        )
        _warn_ppm_range_mismatch(logical_axes, logical_storage, path)
        obj = cls.__new__(cls)
        obj.data = lazy_data  # Streaming lazy objects (storage order).
        obj.axes = logical_axes
        obj.source = Path(path)
        obj._lazy = True
        obj._lazy_inv = tuple(inv)
        obj._plane_cache: dict[tuple[int, int], np.ndarray] = {}
        obj._global_noise: float | None = None
        obj._full_noise: float | None = None
        logger.info(tr("Load 3D spectrum (lazy loading): %s (%s)"), path, data_shape)
        return obj

    def _lazy_read_plane(self, axis_idx: int, index: int) -> np.ndarray:
        """Lazy loading: Read a frame of fixed logical axis from streaming storage and transpose it
        to the logical remaining axis order."""
        inv = self._lazy_inv
        spos = inv[axis_idx]
        idx: list = [slice(None), slice(None), slice(None)]
        idx[spos] = int(index)
        arr = np.asarray(self.data[tuple(idx)])
        if np.iscomplexobj(arr):
            arr = arr.real
        plane = np.asarray(arr, dtype=float)
        logical_remaining = [dim for dim in range(3) if dim != axis_idx]
        storage_remaining = [s for s in range(3) if s != spos]
        axes_perm = [
            storage_remaining.index(inv[dim])
            for dim in logical_remaining
        ]
        if axes_perm != [0, 1]:
            plane = np.transpose(plane, axes_perm)
        return plane

    def _lazy_cached_plane(self, axis_idx: int, index: int) -> np.ndarray:
        """Lazy slicing + small cache (last 4 frames, scrolling back without repeated disk
        reading)."""
        key = (int(axis_idx), int(index))
        cache = self._plane_cache
        if key in cache:
            return cache[key]
        plane = self._lazy_read_plane(axis_idx, index)
        cache[key] = plane
        while len(cache) > 4:
            cache.pop(next(iter(cache)))
        return plane

    def _compute_global_noise(self) -> float | None:
        """Full-spectrum noise level (corner area RMS; lazy loading sampling plane takes the
        median). It is used as the lower limit of contour for the viewer: contours below this
        level (x multiples) are not drawn, and pure noise sections will not display noise on the
        screen because they are based on their own maximum values."""
        if getattr(self, "_full_noise", None) is not None:
            return self._full_noise
        import numpy as np

        def _plane_noise(plane: np.ndarray) -> float:
            a = np.asarray(plane, dtype=float)
            if a.size == 0:
                return 0.0
            # Four-corner small block sideband removal, robust noise estimation.
            h, w = a.shape
            if h < 4 or w < 4:
                return float(np.std(a))
            ch = max(2, int(h * 0.08))
            cw = max(2, int(w * 0.08))
            corners = np.concatenate(
                [
                    a[:ch, :cw].ravel(),
                    a[:ch, -cw:].ravel(),
                    a[-ch:, :cw].ravel(),
                    a[-ch:, -cw:].ravel(),
                ]
            )
            med = float(np.median(corners))
            return float(np.sqrt(np.mean((corners - med) ** 2)))

        if getattr(self, "_lazy", False):
            try:
                axis = 2
                size = self.axes[axis].size
                step = max(1, size // 10)
                vals = [
                    _plane_noise(self._lazy_read_plane(axis, i))
                    for i in range(0, size, step)
                ]
                vals = [v for v in vals if v > 0]
                noise = float(np.median(vals)) if vals else 0.0
            except Exception:  # noqa: BLE001
                return None
        else:
            try:
                data = np.asarray(self.data, dtype=float)
                s0 = max(2, int(data.shape[0] * 0.08))
                s1 = max(2, int(data.shape[1] * 0.08))
                s2 = max(2, int(data.shape[2] * 0.08))
                region = data[-s0:, -s1:, -s2:]
                med = float(np.median(region))
                noise = float(np.sqrt(np.mean((region - med) ** 2)))
            except Exception:  # noqa: BLE001
                return None
        if noise > 0:
            self._global_noise = noise
            self._full_noise = noise
        return self._global_noise

    def index_at(self, axis_idx: int, ppm_value: float) -> int:
        """The axis_idx dimension is positioned by ppm at index (for the slider to be positioned by
        ppm)."""
        return self.axes[axis_idx].index_at(ppm_value)

    def slice(self, axis_idx: int, index: int) -> Spectrum:
        """Fix the index of the axis_idxth dimension and return the two-dimensional Spectrum of the
        remaining two axes. The axis order is consistent with the remaining axes after the fixed
        dimension: axis 0 -> (F2,F3); axis 1 -> (F1,F3); axis 2 -> (F1,F2)."""
        if getattr(self, "_lazy", False):
            index = int(index)
            if not (0 <= index < self.axes[axis_idx].size):
                raise IndexError(
                    tr(
                        "Slice index out of bounds: No. {p0} dimension index={p1} "
                        "(size={p2})",
                        p0=axis_idx,
                        p1=index,
                        p2=self.axes[axis_idx].size,
                    )
                )
            data2d = self._lazy_cached_plane(axis_idx, index)
            remaining = [i for i in range(3) if i != axis_idx]
            out = Spectrum(
                data2d, [self.axes[i] for i in remaining],
                source=self.source,
            )
            _noise = self._compute_global_noise()
            if _noise:
                out.noise_floor = 3.0 * _noise
            return orient_x_priority(out)
        index = int(index)
        size = self.data.shape[axis_idx]
        if not (0 <= index < size):
            raise IndexError(
                tr(
                    "Slice index out of bounds: No. {p0} dimension index={p1} "
                    "(size={p2})",
                    p0=axis_idx,
                    p1=index,
                    p2=size,
                )
            )
        remaining = [i for i in range(3) if i != axis_idx]
        if axis_idx == 0:
            data2d = self.data[index, :, :]
        elif axis_idx == 1:
            data2d = self.data[:, index, :]
        else:
            data2d = self.data[:, :, index]
        out = Spectrum(
            np.asarray(data2d), [self.axes[i] for i in remaining],
            source=self.source,
        )
        _noise = self._compute_global_noise()
        if _noise:
            out.noise_floor = 3.0 * _noise
        return orient_x_priority(out)

    def project(self, axis_idx: int, mode: str = "max") -> Spectrum:
        """Projection along the axis_idxth dimension: MIP(max)/ sum (sum), the axis order is the
        same as slice."""
        if mode == "sum":
            data2d = np.sum(self.data, axis=axis_idx)
        else:
            data2d = np.max(self.data, axis=axis_idx)
        remaining = [i for i in range(3) if i != axis_idx]
        out = Spectrum(
            np.asarray(data2d), [self.axes[i] for i in remaining],
            source=self.source,
        )
        _noise = self._compute_global_noise()
        if _noise:
            out.noise_floor = 3.0 * _noise
        return orient_x_priority(out)

    def estimate_noise(self, fraction: float = 0.1) -> float:
        """Estimate the noise level using the standard deviation of the corner patches (3D)."""
        if getattr(self, "_lazy", False):
            # 0.2.199-patch29dd: Sample plane corner estimation for lazy loading (do not read the
            # full amount).
            try:
                sample = self._lazy_cached_plane(0, 0)
                s0 = max(1, int(sample.shape[0] * fraction))
                s1 = max(1, int(sample.shape[1] * fraction))
                region = sample[-s0:, -s1:]
                return float(np.std(region)) if region.size else 0.0
            except Exception:  # noqa: BLE001 - Estimation failure returns 0.
                return 0.0
        size = int(np.prod(self.data.shape)) if hasattr(self.data, "shape") else 0
        if size == 0:
            return 0.0
        s0 = max(1, int(self.data.shape[0] * fraction))
        s1 = max(1, int(self.data.shape[1] * fraction))
        s2 = max(1, int(self.data.shape[2] * fraction))
        region = self.data[-s0:, -s1:, -s2:]
        return float(np.std(region)) if region.size else 0.0

    def project_nmrpipe(self, axis_idx: int, thresh: float) -> Spectrum:
        """NmrPipe projZ-style projection: Points below the threshold are set to zero and then
        summed along the axis. The method of projZ.M is: each plane is first truncated by +/-
        threshold (noise is set to zero), and then the planes are accumulated -- the peak
        intensity is retained, the noise is not accumulated, and the projection spectrum looks
        close to the conventional two-dimensional spectrum (for example, HNCA is projected along
        13C to obtain a HN plane similar to HSQC)."""
        data = np.asarray(self.data, dtype=float)
        if thresh > 0:
            data = np.where(np.abs(data) < thresh, 0.0, data)
        data2d = np.sum(data, axis=axis_idx)
        remaining = [i for i in range(3) if i != axis_idx]
        return orient_x_priority(
            Spectrum(
                np.asarray(data2d), [self.axes[i] for i in remaining],
                source=self.source,
            )
        )

class Spectrum1D:
    """1D spectrum (time domain FID or 2D slice): ``data`` shape (N,), a SpectrumAxis."""

    def __init__(
        self,
        data: np.ndarray,
        axis: SpectrumAxis,
        source: Path | str | None = None,
    ) -> None:
        self.data = np.asarray(data, dtype=float)
        self.axis = axis
        self.source = Path(source) if source else None

    @property
    def max_intensity(self) -> float:
        return float(np.max(self.data)) if self.data.size else 0.0

    @property
    def ppm_valid(self) -> bool:
        """Is there a ppm axis available (sw/obs head complete)."""
        return self.axis.sw_hz > 0 and self.axis.obs_mhz > 0

    def x_values(self) -> np.ndarray:
        """Drawing x coordinate: valid ppm, use ppm for axis, otherwise use point number."""
        if self.ppm_valid:
            return self.axis.ppm
        return np.arange(self.axis.size, dtype=float)

    @staticmethod
    def _axis_from_dic(dic: dict, label: str, size: int) -> SpectrumAxis:
        """Take the direct dimension frequency parameter (FDF2*/FS*) from the NMRPipe header, which
        degenerates into a point axis when missing."""

        def _first(*keys: str) -> float:
            for key in keys:
                value = dic.get(key)
                if value in (None, "", 0, 0.0):
                    continue
                try:
                    return float(value)
                except (TypeError, ValueError):
                    continue
            return 0.0

        return SpectrumAxis(
            label=label,
            size=size,
            sw_hz=_first("FDF2SW", "FSSW", "FSW"),
            obs_mhz=_first("FDF2OBS", "FSOBS"),
            carrier_ppm=_first("FDF2CAR", "FSCAR"),
            orig_hz=_first("FDF2ORIG"),
        )

    @classmethod
    def load_from_ft1(cls, path: Path | str, label: str = "ppm") -> Spectrum1D:
        """Read NMRPipe 1D frequency domain spectrum (.ft1): single-dimensional real array + head
        ppm axis (patch29gj). The direct dimension x parameter is read according to FDF2*
        (consistent with _axis_from_dic); when the head is missing, it degenerates into a point
        axis (sw/obs=0), and the trace can still be displayed."""
        import nmrglue as ng

        _dic, data = ng.pipe.read(str(path))
        data = np.asarray(data)
        if np.iscomplexobj(data):
            data = data.real
        if data.ndim != 1:
            raise ValueError(tr("Non-1D spectrum: {p0} shape={p1}", p0=path, p1=data.shape))
        axis_label = str(
            _dic.get("FDF2LABEL")
            or _dic.get("FDF1LABEL")
            or _dic.get("FDLABEL")
            or label
        )
        axis = cls._axis_from_dic(_dic, label=axis_label, size=int(data.shape[0]))
        return cls(data, axis, source=Path(path))

    @classmethod
    def load_from_fid(
        cls, path: Path | str, label: str = "FID"
    ) -> Spectrum1D | Spectrum:
        """Use nmrglue to read NMRPipe.fid (time domain). - One-dimensional FID: return Spectrum1D
        (real part trace); - two-dimensional and above FID: display the entire two-dimensional
        time domain plane in nmrDraw mode (row = each FID/ indirect dimension increment, column
        = direct dimension time point), return Spectrum; 3D+ FID Displays the two-dimensional
        plane of the first indirect increment (consistent with nmrDraw). The time domain takes
        the data point (serial number) as the axis, and does not use ppm (ppm is only meaningful
        for frequency domain spectra)."""
        import nmrglue as ng

        _dic, data = ng.pipe.read(str(path))
        data = np.asarray(data)
        if np.iscomplexobj(data):
            data = data.real
        while data.ndim > 2:
            # Two-dimensional time domain plane showing the first indirect increment.
            data = data[0]
        if data.ndim == 1:
            axis = SpectrumAxis(
                label=label or tr("FID data points"),
                size=int(data.shape[0]),
                sw_hz=0.0,
                obs_mhz=0.0,
                carrier_ppm=0.0,
                orig_hz=0.0,
            )
            logger.info(tr("Loading FID: %s (%s)"), path, data.shape)
            return cls(data, axis, source=Path(path))
        fid_axis = SpectrumAxis(
            label=label or "FID",
            size=int(data.shape[0]),
            sw_hz=0.0,
            obs_mhz=0.0,
            carrier_ppm=0.0,
            orig_hz=0.0,
        )
        point_axis = SpectrumAxis(
            label="Points",
            size=int(data.shape[1]),
            sw_hz=0.0,
            obs_mhz=0.0,
            carrier_ppm=0.0,
            orig_hz=0.0,
        )
        spectrum = Spectrum(data, [fid_axis, point_axis], source=Path(path))
        # FID has a large dynamic range (ADC cumulative value), and the default benchmark of the
        # contour line is a high quantile to avoid being overwhelmed by individual spikes and unable
        # to see most of the FID time domain envelope.
        robust_max = float(np.percentile(np.abs(data), 99.0))
        if robust_max > 0:
            spectrum.robust_max = robust_max
        logger.info(tr("Loading FID (two-dimensional time domain): %s (%s)"), path, data.shape)
        return spectrum
