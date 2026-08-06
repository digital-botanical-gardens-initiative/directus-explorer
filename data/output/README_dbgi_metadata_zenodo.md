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

`field_taxon_ref` is the internal Directus relation to the curated `Taxa` row.
The export dereferences that relation using the verified Taxa seed execution
journals and includes the final accepted name in `field_taxon_accepted_name`,
along with `field_taxon_ref_scientific_name`, authorship, rank, COL ID, backbone
version, ChecklistBank dataset key, and backbone DOI. This makes the archive
self-contained without using `field_taxon_name_unified` as a taxonomic matching
key.

## Reproducing the export

Run these commands from the root of the `directus-explorer` repository with a
configured `.env` file providing read access to the DBGI Directus instance:

```bash
export DBGI_EXPORT_TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
export DBGI_EXPORT_PATH="data/output/sample_metadata_dbgi_col_${DBGI_EXPORT_TIMESTAMP}.tsv"
uv sync
if [[ -e "${DBGI_EXPORT_PATH}" ]]; then
  echo "Refusing to overwrite immutable export: ${DBGI_EXPORT_PATH}" >&2
  false
else
  uv run directus-explorer ms export-metadata \
    --output "${DBGI_EXPORT_PATH}" \
    --project-group dbgi \
    --view sample-compact \
    --require-col-id
fi
shasum -a 256 "${DBGI_EXPORT_PATH}"
```

Timestamped exports are immutable. A corrected export must use a fresh timestamp
and a new Zenodo `vN`; never replace the bytes at an existing timestamped path.

The `dbgi` project group is maintained in
`src/directus_explorer/samples.py`. At the time of this export it comprises
`jbc`, `jbn`, `jbn-new`, `jbp`, `jbp-new`, `jbuf`, and
`kew-botanical-gardens`.

The `--require-col-id` option keeps only rows with a non-empty stored
`field_taxon_col_id`. The `sample-compact` view collapses sample lineage records
to one row per `original_sample_id` and includes positive/negative profiling
flags.

## Publishing a new Zenodo version

The repository includes a manifest-driven Rust publisher built on `zenodo-rs`.
The standard command automatically generates the export, increments the version,
creates the README and frozen manifest, verifies the draft, and publishes it:

```bash
cargo run --manifest-path tools/zenodo-publisher/Cargo.toml -- \
  release zenodo/dbgi-samples-metadata-release.toml
```

The publisher reads `ZENODO_TOKEN`, verifies remote filenames, sizes, and MD5
checksums before publication, and safely resumes the same manifest after a
timeout. It will not create another version when the requested `vN` is already
the latest published version with matching files.

## Export validation

The 2026-08-06 export contains 7,195 rows, no duplicate or empty
`original_sample_id` values, no empty `field_taxon_col_id` values, and no
projects outside the canonical DBGI group. All rows have a populated accepted
name and Taxa relation metadata; the exported relation IDs and COL IDs match the
stored `Field_Data` values without conflicts.

SHA-256 of `sample_metadata_dbgi_col_20260806T105416Z.tsv`:

```text
7a8e0a423a55f934a91b9a2f1ee4344584f594d2764ad68ce7f2ab8722be1f9c
```
