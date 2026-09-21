"""The ProcessingBackend protocol.

The upper layers (GUI/workflow/optimiser) depend on this protocol only and never touch NMRPipe
semantics. NMRPipe semantics live only in the backend implementations and the runtime
(framework §49-50).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from core.data.internal_data_model import Experiment
from core.planning.processing_plan import ProcessingPlan


@dataclass
class BackendCapabilities:
    """Declaration of backend capabilities."""

    provider: str = "nmrpipe"
    supports_nus: bool = True
    supports_phase_optimization: bool = False
    features: list[str] = field(default_factory=list)


@runtime_checkable
class ProcessingBackend(Protocol):
    """The unified processing-backend interface."""

    capabilities: BackendCapabilities

    def health_check(self) -> dict[str, Any]:
        """Return the backend health status (tool availability/version)."""
        ...

    def process(
        self,
        experiment: Experiment,
        plan: ProcessingPlan,
        *,
        params: dict[str, Any] | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        """Run the processing according to the plan and return the outputs and metrics.

        params keys: extract (bool, True by default) / ext_lo (str, "10.5") / ext_hi (str, "6.5")
        (G2B-006, effective on the uniform path).
        """
        ...

    def convert_to_fid(
        self,
        experiment: Experiment,
        data_dir: Any,
        progress: Callable[[str], None] | None = None,
        fid_com_overrides: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Convert a Bruker data directory into an NMRPipe fid (a stage of its own, no spectrum).

        fid_com_overrides: manual parameter overrides (0.2.163-patch13), applied segment by
        segment to
        fid.com, while conversion/slicing/merging/bad-point repair still follow the automatic path.
        Returns stable keys: {success, fid_path, message, logs} (API_CONTRACT §8.3).
        """
        ...

    def reconstruct_nus(
        self,
        experiment: Experiment,
        params: dict[str, Any] | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        """Run the NUS reconstruction (params supports extract, True by default)."""
        ...
