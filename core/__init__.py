"""nmrforge core domain layer: data understanding, planning, processing primitives,
optimisation, QC and experiment templates.

``__version__`` is the single version source: pyproject.toml reads the same attribute
through ``dynamic = ["version"]`` + ``attr = "core.__version__"``, and
packaging/linux/build_appimage.sh reads it too (PROV-009, 2026-09-12).
"""

__version__ = "1.0.1"
