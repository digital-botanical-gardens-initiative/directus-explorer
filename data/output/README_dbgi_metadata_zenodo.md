# DBGI samples metadata

This archive contains a timestamped, one-row-per-sample metadata export for the
Digital Botanical Gardens Initiative (DBGI).

## Contents

- `sample_metadata_dbgi_col_20260806T105416Z.tsv`: 7,195 unique DBGI samples
  carrying a curated Catalogue of Life identifier in Directus.
- `README.md`: this file.

The TSV is tab-delimited UTF-8. Its `field_taxon_col_id` values and associated
taxonomic fields come from the taxonomic resolution already curated and stored
in Directus. Preparing the export does not query Catalogue of Life.

## Reproducing the export

Run these commands from the root of the `directus-explorer` repository with a
configured `.env` file providing read access to the DBGI Directus instance:

```bash
export DBGI_EXPORT_TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
uv sync
uv run directus-explorer ms export-metadata \
  --output "data/output/sample_metadata_dbgi_col_${DBGI_EXPORT_TIMESTAMP}.tsv" \
  --project-group dbgi \
  --view sample-compact \
  --require-col-id
```

The `dbgi` project group is maintained in
`src/directus_explorer/samples.py`. At the time of this export it comprises
`jbc`, `jbn`, `jbn-new`, `jbp`, `jbp-new`, `jbuf`, and
`kew-botanical-gardens`.

The `--require-col-id` option keeps only rows with a non-empty stored
`field_taxon_col_id`. The `sample-compact` view collapses sample lineage records
to one row per `original_sample_id` and includes positive/negative profiling
flags.

## Export validation

The 2026-08-06 export contains 7,195 rows, no duplicate or empty
`original_sample_id` values, no empty `field_taxon_col_id` values, and no
projects outside the canonical DBGI group.

SHA-256 of `sample_metadata_dbgi_col_20260806T105416Z.tsv`:

```text
78b108b9bfe9b08ea122bfa512be3283ca27929258839b87ea2a35bf358c4d3f
```
