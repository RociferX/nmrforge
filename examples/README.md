# examples/

Two small, self-contained scripts.

| Script | What it does |
| --- | --- |
| `make_synthetic_dataset.py` | writes a synthetic Bruker dataset (2D or 3D) so the pipeline can be exercised without spectrometer data |
| `quickstart.py` | walks through what nmrForge understands about a dataset: dimensions, nuclei, experiment type and its evidence, sampling classification, the time-domain layout, and whether NMRPipe/SMILE are available |

```bash
python examples/make_synthetic_dataset.py --out example_data/hsqc_2d
python examples/quickstart.py example_data/hsqc_2d
```

`quickstart.py` deliberately stops before anything that needs NMRPipe, so it runs on a machine
with only the Python dependencies installed. Actually processing a spectrum does require NMRPipe;
see [`docs/getting-started.md`](../docs/getting-started.md).
