# API contract

This page summarizes the current public Python and command-line contract.
The source API version is 1.1.1 and is versioned separately from the desktop
application and AppImage. This patch preserves the API parameters and 38-column peak-table contract. Detailed function signatures, fields, and errors
are maintained in the [external API documentation](external-api/README.md).

## 11. Public nmrforge_api contract

The public API provides reference construction and parameter-combination
studies for Bruker NMR data. It records processing inputs, outputs,
provenance, status, and warnings. It does not perform statistical inference,
scientific interpretation, or peak matching across spectra.

A reference is built for each condition. Each workflow and condition
independently detects and localizes peaks in its candidate spectrum. The API
does not establish correspondence between reference and candidate peaks or
across conditions. Combination-table reference_peak_id and assignment
fields remain empty.

The unified peak table has 38 columns. It includes logical F1/F2/F3
coordinates and nucleus labels; H/N fields are compatibility aliases only
when the nucleus is unambiguous. Peak IDs are local to one spectrum.
Localization uses the supported three-point parabolic method. See
[output and record fields](external-api/06-outputs-and-records.md) for the
authoritative schema and semantics.

Sampling and reconstruction support depends on dimensionality and input
metadata. NUS schedules must be recoverable from supported metadata; the API
does not guess a schedule from nominal sampling percentage. See
[inputs and data](external-api/05-inputs-and-data.md) and
[limitations](external-api/09-limitations-and-roadmap.md).

## Reference and combination boundary

A combination study requires an explicit reference. It reuses the frozen
reference FID read-only; missing or changed input evidence requires an
explicit reference rebuild. A combination run does not silently reconvert
or merge source data. See [limitations](external-api/09-limitations-and-roadmap.md)
and [troubleshooting](external-api/10-troubleshooting.md) for recovery details.

Each run records requested and resolved parameters, processing scripts,
product hashes, tool versions, status, and warnings. The output reference
defines record fields and product layout; records distinguish requested
values from values actually applied.

## Public entry points

Import the Python interface from nmrforge_api. The command-line help is:

    python -m nmrforge_api --help

Public functions and commands are listed in the
[API reference](external-api/03-api-reference.md) and
[CLI reference](external-api/04-cli-reference.md). Input requirements are in
[inputs and data](external-api/05-inputs-and-data.md); products, records,
and warnings are in [outputs and records](external-api/06-outputs-and-records.md).
Start with the [external API guide](external-api/README.md) for examples and
the full documentation map.

## Compatibility

The compatibility interface reports externally observable behaviour and
contract changes. Changes to public functions, output fields, peak-table
columns, error codes, or processing behaviour must update implementation,
tests, references, and compatibility metadata together. See the external API
guide and the package's public compatibility functions for current details.

Qt and desktop presentation are outside the nmrforge_api contract. Desktop
changes must not be inferred from API version numbers.
