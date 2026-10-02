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
 'digest': '74335b10397d030dc188812e1790559468ba97e9bd6b21bbcb4c0dc53a13ced6',
 'token_digest': 'cba89335b1c3769d602af0c50e3fa67e3b411457cc913a43d04928b5579361e3',
 'compat_level': 'behavior_changed',
 'affected': ['reference',
              'processing',
              'sweep_detection',
              'localization',
              'records',
              'api_surface',
              'qc'],
 'updated': '2026-10-02',
 'note': 'Fix peak axes, signs, detection and safe reference filtering',
 'golden': {'name': 'conformance_v1',
            'spectrum_sha256': '382b330926da93d856feef40ca33c2df1eec21b61e50db471bdf7aa4bb2fb311',
            'peak_table_sha256': 'ba2b7271ed56a8bd30c751066d55af258117f35adae18ed6b6973042b93b68bf',
            'n_peaks': 3}}

__all__ = ["DECLARATION"]
