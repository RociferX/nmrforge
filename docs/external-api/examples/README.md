# Example

| file | content |
| --- | --- |
| `run_study.py` | One-step: data + combination table -> reference + all workflow + records (supports A/B) |
| `step_by_step.py` | Step by step: open_study -> add_dataset -> build_reference -> ensure_reference_peaks -> plan_sweep -> run_sweep -> write_records |
| `measure_only.py` | Only use the measurement layer: use the same batch of reference peaks for existing spectra to generate two unified peak tables of parabolic / gaussian |
| `combos.csv` | Explicit combination table example (one workflow per line) |
| `grid.yaml` | Axis grid example (interface expansion full factor) |

Before running, please confirm that the data is the decompressed Bruker directory; real machine processing requires NMRPipe.

```bash
python docs/external-api/examples/run_study.py \
    --study ~/studies/s1 --a ~/data/apo --b ~/data/holo \
    --combos docs/external-api/examples/combos.csv
```

Output (excerpt): workflow list, workflow x condition status, two peak table paths, and.
Lists and long lists under `study/records/`.
