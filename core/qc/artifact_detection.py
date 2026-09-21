"""Artefact detection: mirror peaks / isolated peak clusters / ridge artefacts /
reconstruction artefacts (framework §66).

Phase 1: isolated-peak detection -- normalised by the peak density of the spectrum itself,
so only "abnormally isolated" peaks lose score.
0.2.199-patch29el: the old implementation used an absolute distance threshold
(0.15 x max(shape)) for isolation, so sparse/3D spectra, whose peaks are naturally far
apart, were systematically misjudged as isolated peak clusters (artefact scores of 30-69
while looking perfectly normal); and peaks with no comparable peak at all were skipped
(so genuinely isolated strong false peaks were missed). It now works like this:
  expected spacing per peak s = (spectrum volume / comparable peak count)^(1/ndim);
  flagged only when dmin > 5 x s and (within 2% of the axis edge or extremely isolated
  >8 x s);
  a strong peak with no comparable peak is flagged only when the total peak count is
  >= 20 (a dense-spectrum context);
  weak peaks (snr<5) are never flagged (a noise peak is not an artefact).
Every isolated peak loses 5-10 points according to how far it exceeds the expected
spacing (at most 10 for a single one, user 2026-09-09).
Ordinary sparse/3D distributions of real spectra no longer raise false alarms, while a
strong isolated false peak injected into a dense spectrum is still caught.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from core.qc import peak_detection


@dataclass
class ArtifactReport:
    mirror_peaks: int = 0
    isolated_peak_clusters: int = 0
    ridge_artifacts: int = 0
    warnings: list[str] = field(default_factory=list)
    score: float = 0.0


def detect(data: Any) -> ArtifactReport:
    """Detect spectrum artefacts (isolated peaks, normalised by the peak density of the
    spectrum itself, 0.2.199-patch29el)."""
    arr = np.asarray(data)
    peaks = peak_detection.detect(arr)
    isolated = 0
    penalty = 0.0
    if len(peaks) > 1:
        positions = np.array([p.position for p in peaks])
        heights = np.array([p.height for p in peaks])
        snrs = np.array([p.snr for p in peaks])
        ndim = arr.ndim
        volume = float(np.prod(arr.shape))
        total = len(peaks)
        for i, pos in enumerate(positions):
            if snrs[i] < 5.0:
                continue  # a weak (noise) peak is no evidence of an artefact
            comparable = heights >= 0.5 * heights[i]
            comparable[i] = False
            m = int(comparable.sum())
            edge_dist = float(
                min(min(p, arr.shape[a] - 1 - p) for a, p in enumerate(pos))
            )
            if m == 0:
                # no comparable peak at all: suspicious only when the overall peak count is
                # high enough (a dense-spectrum context); in a sparse spectrum isolation is
                # simply the normal distribution and no longer reported
                if total >= 20:
                    isolated += 1
                    penalty += 10.0
                continue
            dists = np.linalg.norm(positions[comparable] - pos, axis=1)
            dmin = float(np.min(dists))
            spacing = (volume / m) ** (1.0 / ndim)
            near_edge = edge_dist <= 0.02 * max(arr.shape)
            if dmin > 5.0 * spacing and (
                near_edge or dmin > 8.0 * spacing
            ):
                isolated += 1
                excess = min(
                    1.0,
                    (dmin - 5.0 * spacing) / max(5.0 * spacing, 1e-9),
                )
                penalty += 10.0 * (0.5 + 0.5 * excess)
    score = float(max(0.0, 100.0 - penalty))
    return ArtifactReport(isolated_peak_clusters=isolated, score=score)
