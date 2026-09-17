# viewer/

The spectrum viewer: axes, contour layers, slices and the 3D panel. The GUI embeds it, but it is
written so that it does not import `gui/`.

| Module | Role |
| --- | --- |
| `spectrum.py` | the read-only spectrum model (`Spectrum`, `Spectrum3D`, axis conventions) |
| `spectrum_viewer.py` | the main view widget: zooming, contouring, slices, peak overlays |
| `contour_layer.py`, `nmr_viewbox.py` | contour rendering and the interactive view box |
| `axis_labels.py` | axis labelling and unit handling |
| `phase_panel.py` | interactive phase adjustment |
| `spectrum3d_panel.py` | 3D views |
| `app.py`, `__main__.py` | running the viewer standalone |

Boundary: `viewer/` may use `core/`, `ui_support/` and `qtcompat/`, and must not import `gui/`,
`workflow/` or `backend/`. Enforced by
[`tests/test_qt_independence.py`](../tests/test_qt_independence.py) and
[`tests/test_ownership.py`](../tests/test_ownership.py).
