# ui_support/

UI helpers shared by the GUI and the viewer, split so that the Qt-free parts stay Qt-free.

| Module | Qt | Role |
| --- | --- | --- |
| `colors.py` | no | the semantic colour palette and experiment-type colours |
| `assets.py` | no | locating and loading bundled icons and images |
| `theme.py` | yes (through `qtcompat`) | applying the palette, the global stylesheet and the icon |

`gui/theme.py` is kept only as a deprecated re-export shim, so that out-of-tree callers and older
branches keep working; new code imports `ui_support.*` directly. See
[`docs/pyside6-migration/migration-plan.md`](../docs/pyside6-migration/migration-plan.md).
