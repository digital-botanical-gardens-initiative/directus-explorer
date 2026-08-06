# DBGI samples metadata v4

Generated at `2026-08-06T17:33:21.115735+00:00` from the DBGI Directus database.

## Contents

- `sample_metadata_dbgi_col_20260806T173321Z.tsv`: 7195 unique DBGI samples with curated Catalogue of
  Life identifiers and accepted taxonomy.
- `README.md`: this file.

The TSV is tab-delimited UTF-8. Taxonomy comes from the resolution already
curated in Directus and the verified Taxa seed execution journals; generation
does not query Catalogue of Life. The export includes `field_taxon_ref`, the
accepted name, authorship, rank, COL identifier, backbone release, ChecklistBank
dataset key, and backbone DOI.

SHA-256 of `sample_metadata_dbgi_col_20260806T173321Z.tsv`:

```text
7a8e0a423a55f934a91b9a2f1ee4344584f594d2764ad68ce7f2ab8722be1f9c
```

## Reproduction and publication

From the repository root, the following command automatically determines the
next `vN`, generates a new immutable timestamped export, validates it, writes
this README and a frozen publication manifest, verifies the Zenodo draft, and
publishes it:

```bash
cargo run --manifest-path tools/zenodo-publisher/Cargo.toml -- \
  release zenodo/dbgi-samples-metadata-release.toml
```

Use `--draft-only` to stop after remote draft verification.
