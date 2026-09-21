"""Experiment templates and priors: the single source of truth presets/*.yaml.

Importing this package loads and registers every template from presets/*.yaml
(since 0.2.111, replacing the per-module Python templates).
"""

from core.experiments.registry import load_presets  # noqa: F401

load_presets()
