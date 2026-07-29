# JBP MS Import Preparation

These files were prepared from immutable copies under
`data/input/jbp/ms_import/`. Their external source was `/media/nvme8tb/jbp`.
Live Directus checks resolved all 198 distinct samples exclusively to the
`jbp` qfield project.

The shared MS settings recovered from existing JBP records are:

- injection method: `general_ms2_pos` (`Injection_Methods.id=52`)
- polarity: positive
- injection volume: `1 µl` (`SI_Units.id=18`)
- instrument: `inst_000003`, LCMS Orbitrap ID-X (`Instruments.id=4`)

## Batch 004

All 85 rows are new `MS_Data` records parented to extraction containers. The
lineage audit has 85 ready rows and no blocked rows.

Dry run:

```bash
uv run directus-explorer ms import-ready-injection-runs \
  data/output/jbp/ms_import/batch004/jbp_batch004_injection_import.csv \
  --ms-parent-level extraction \
  --injection-volume 1 \
  --injection-volume-unit-id 18 \
  --injection-method-id 52 \
  --instrument-id 4
```

Commit after reviewing the dry run:

```bash
uv run directus-explorer ms import-ready-injection-runs \
  data/output/jbp/ms_import/batch004/jbp_batch004_injection_import.csv \
  --ms-parent-level extraction \
  --injection-volume 1 \
  --injection-volume-unit-id 18 \
  --injection-method-id 52 \
  --instrument-id 4 \
  --commit
```

## Batch 005

All 38 rows are new `MS_Data` records parented to extraction containers. The
lineage audit has 38 ready rows and no blocked rows.

Dry run:

```bash
uv run directus-explorer ms import-ready-injection-runs \
  data/output/jbp/ms_import/batch005/jbp_batch005_injection_import.csv \
  --ms-parent-level extraction \
  --injection-volume 1 \
  --injection-volume-unit-id 18 \
  --injection-method-id 52 \
  --instrument-id 4
```

Commit after reviewing the dry run:

```bash
uv run directus-explorer ms import-ready-injection-runs \
  data/output/jbp/ms_import/batch005/jbp_batch005_injection_import.csv \
  --ms-parent-level extraction \
  --injection-volume 1 \
  --injection-volume-unit-id 18 \
  --injection-method-id 52 \
  --instrument-id 4 \
  --commit
```

No `--batch-id` is supplied for batches 004 and 005 because the generic,
unused Directus MS batch rows cannot be mapped to these source batches
unambiguously.

## Batch 006

Batch 006 already has `MS_Data` records and must not use the create importer.
The update manifest maps physical mzML names onto 77 existing Directus IDs,
parents, and methods. It includes one file omitted from the source CSV:
`20260427_79_144110_KK_general_ms2_pos_dbgi_011800_02_01_1uL.mzML`
(`MS_Data.id=3483`).

Dry run:

```bash
uv run directus-explorer ms update-filenames \
  data/output/jbp/ms_import/batch006/jbp_batch006_filename_updates.csv \
  --output data/output/jbp/ms_import/batch006/jbp_batch006_filename_update_audit.tsv
```

Commit after reviewing the dry run:

```bash
uv run directus-explorer ms update-filenames \
  data/output/jbp/ms_import/batch006/jbp_batch006_filename_updates.csv \
  --output data/output/jbp/ms_import/batch006/jbp_batch006_filename_update_audit.tsv \
  --commit
```

`summary.json` records source paths, row counts, project resolution, and the
Directus relationship IDs used above.
