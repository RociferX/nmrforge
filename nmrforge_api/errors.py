"""Exception hierarchy for the public API (downstream projects can tell failures apart)."""

from __future__ import annotations


class SensitivityError(RuntimeError):
    """Base class of the parameter-sensitivity API."""


class DatasetError(SensitivityError):
    """A dataset could not be imported or identified (not a Bruker directory, unsupported)."""


class ReferenceError(SensitivityError):
    """A reference spectrum or reference script could not be built or loaded."""


class SweepError(SensitivityError):
    """A parameter-sweep plan or run failed."""


class MeasurementError(SensitivityError):
    """Peak-position measurement failed (unreadable spectrum, empty peak table)."""


__all__ = [
    "DatasetError",
    "MeasurementError",
    "ReferenceError",
    "SensitivityError",
    "SweepError",
]
