# Command-line tools

Current measurements, reference sources, and reproduction commands are on the
[four-route evidence page](../docs/evidence/real-data-comparison.md).

| Tool | Purpose |
| --- | --- |
| `vm_realdata_report.py` | Normal import, FID conversion, automatic processing, and aggregate run records |
| `vm_make_evidence_nus.py` | Controlled downsampling into a new directory with the seed and source hashes retained |
| `vm_four_path_figure.py` | Signed spectrum comparisons; common 3D crop followed by independent HN/HC/NC projections |
| `vm_projection_report.py` | Per-plane candidate detection, matching, and unmatched coordinates |
| `vm_nus_pair_report.py` | Candidate comparison in a common multidimensional window |
| `vm_qc_score.py` | Product QC metrics |
| `bmrb_expected_to_csv.py` | First-party deposited HSQC positions converted to CSV with provenance |
| `vm_truth_benchmark.py`, `vm_truth_figure.py` | Comparisons against an applicable external expected-position list |

Artificial downsampling is not acquired NUS. Candidate coverage is not assigned-peak recovery.
For current figures, contours start at 7.5%, detection uses an explicit 10% threshold, and same-sign
HSQC/HNCO uses `dominant`. The projection tool's general default threshold is 5%; use the
reproduction commands on the evidence page to obtain the reported settings.

`param_optimize.py` and `smile_optimize.py` provide optional processing-parameter scans.
For source checks and compatibility/UI metadata generation, see the
[development guide](../docs/development.md). Installation and packaging entry points are documented
in [installation](../docs/installation.md) and [packaging](../docs/packaging.md).
