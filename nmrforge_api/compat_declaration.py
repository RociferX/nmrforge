"""Behaviour-level declaration (**a pure data module**, excluded from ``behavior_digest``).

After changing ``core/`` / ``backend/`` / ``workflow/`` / ``nmrforge_api/`` or the shipped
data (``config/nmrforge.yaml``, ``presets/``), ``tests/test_compat.py``'s
``test_behavior_digest_matches_the_declaration`` fails -- update in this order:

1. **Decide the level**: ``same`` (comments/wording or docstrings only) / ``additive`` (new
   entry points or optional fields only, downstream need not re-run) / ``behavior_changed``
   (**numbers change**) / ``contract_changed`` (columns/fields/error codes changed);
2. ``behavior_changed`` / ``contract_changed`` must name ``affected`` (values in
   ``nmrforge_api.compat.AFFECTED_STEPS``); downstream uses it to decide what to re-run;
3. **Recompute the fingerprints**: copy ``behavior_digest`` / ``token_digest`` from
   ``python -m nmrforge_api compat --out compat.json`` back into this file;
4. **Golden vector**: when behaviour or recipes change, sync the ``golden`` hashes
   (``python -m nmrforge_api compat --golden`` prints the actual values);
5. ``compat_level=same`` requires ``token_digest`` to be unchanged (proof that the code
   tokens did not move and only comments/docstrings did).
"""

from __future__ import annotations

from typing import Any

#: declared behaviour level (see the module doc; meaning matches nmrforge_api.compat)
DECLARATION: dict[str, Any] = {'schema': 'nmrforge_api.compat.declaration.v1',
 'digest': '8d66e3a9096cf737559cb7ec4039bc93c9be863d7df140cbe11402e4f2ba4f3d',
 'token_digest': 'ed82686e64574c2e6245556c78198ac0d08339d1d7d12e215a4589f987d8ca70',
 'compat_level': 'behavior_changed',
 'affected': ['processing'],
 'updated': '2026-09-22',
 'note': '2026-09-22 NUS reuse unification: reconstruct_nus reuses converted/merged '
         'products on the raw input fingerprint (same criterion as process()) and '
         'records provenance; source-level cleanup no longer needs a per-run event '
         'flag',
 'golden': {'name': 'conformance_v1',
            'spectrum_sha256': '382b330926da93d856feef40ca33c2df1eec21b61e50db471bdf7aa4bb2fb311',
            'peak_table_sha256': '7f8180662220c83db225fa6b2bf0f5b4c71196fcdaebebe7e8c172a4861657ab',
            'n_peaks': 3}}

__all__ = ["DECLARATION"]
