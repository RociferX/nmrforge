# backend/

External processing engines: NMRPipe and SMILE. This layer turns a processing plan into real
scripts, runs them as subprocesses, and reports what happened. It contains no GUI code and must
not import `gui/`, `viewer/` or `workflow/`.

## Key files

| File | Role |
| --- | --- |
| `base.py` | `ProcessingBackend` protocol and `BackendCapabilities` - the shared contract |
| `factory.py` | picks the backend implementation |
| `nmrpipe_backend.py` | the NMRPipe implementation: uniform processing and NUS reconstruction |
| `script_generator.py` | builds `fid.com` and the processing scripts, including windows and zero filling |
| `bruker_workflow.py` | generates and parses `bruker -AUTO` output |
| `runtime.py` | subprocess execution: timeouts, process-tree kill, streamed stderr |
| `nmrpipe_finder.py`, `nmrpipe_version.py` | locating an NMRPipe installation and recording its version |
| `memory_disk.py` | ramdisk adaptation for intermediate spectra |
| `memory_guard.py` | the SMILE peak-memory guard |
| `config.py` | backend configuration - paths, defaults and `resolve_nthread` |

## Where to read more

- [`docs/external-dependencies.md`](../docs/external-dependencies.md) - NMRPipe and SMILE: search
  order, detection, and what happens when they are missing;
- [`docs/processing-model.md`](../docs/processing-model.md) - understand, plan, execute, document.

The backend is exercised without a real NMRPipe installation by mocking the engine boundary
(`tests/test_nmrpipe_backend.py`, `tests/test_nmrpipe_scripts.py`). Validation against a real
engine runs on a machine that has NMRPipe, through `scripts/vm_test.sh`.
