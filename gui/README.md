# gui/

The desktop application (PySide6). Everything the user clicks lives here; the work itself is
delegated to `core/`, `workflow/` and `backend/`.

## The shell

`main.py` in the repository root starts the application.

| Module | Role |
| --- | --- |
| `main_window.py` | menus, the four-pane layout, import entry points and the automatic next step |
| `pipeline_panel.py` | the six pipeline steps, their state machine and the run entry points |
| `spectrum_panel.py` | the embedded viewer, peak table, assignment, export/import, slices and projections |
| `project_tree.py` | the project / experiment / data / group tree, its status column and context menus |
| `dashboards.py`, `log_panel.py`, `raw_quality.py`, `per_data_records.py` | status, log and record views |
| `dialogs.py`, `settings.py`, `notes.py`, `group_panel.py`, `center_panel.py`, `welcome_page.py` | dialogs and peripheral panels |

## The cross-boundary adapter

`processing.py` holds `ProcessingController`. It is the single place where the GUI calls into
`workflow/` and `backend/`, and every run entry point goes through it. A small, documented set of
direct imports of `backend.config` / `backend.runtime` also exists (for example in
`main_window.py` and `pipeline_panel.py`), for configuration and cancellation rather than for
running work.

`tests/test_ownership.py` and the GUI test suite cover this split.
