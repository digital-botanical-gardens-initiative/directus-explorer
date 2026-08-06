# directus-explorer

Fetch and summarize data from a Directus instance.

## Setup

1. Copy the example environment file:

```bash
cp .env.example .env
```

2. Fill in the Directus credentials in `.env`:

```dotenv
DIRECTUS_INSTANCE=https://your-directus-instance.example/directus
DIRECTUS_USERNAME=your-user@example.com
DIRECTUS_PASSWORD=your-directus-password
```

3. Install the package and development dependencies with `uv`:

```bash
uv sync --dev
```

## CLI

Count samples for an exact `Field_Data.qfield_project` value:

```bash
uv run directus-explorer samples count jbuf
```

Render the same result as JSON:

```bash
uv run directus-explorer samples count jbuf --format json
```

List all distinct qfield project keys:

```bash
uv run directus-explorer utils projects
```

Use a non-default env file:

```bash
uv run directus-explorer samples count jbuf --env-file .env.local
```

List original samples that have been profiled in MS, with their resolved polarity mode:

```bash
uv run directus-explorer samples profiled
```

Restrict profiled samples to a single qfield project:

```bash
uv run directus-explorer samples profiled --project jbuf
```

Only keep samples profiled in both positive and negative mode:

```bash
uv run directus-explorer samples profiled --mode both
```

Count profiled samples instead of listing them:

```bash
uv run directus-explorer samples profiled --mode both --count
```

Render the profiled sample list as JSON:

```bash
uv run directus-explorer samples profiled --format json
```

Summarize collected and profiled samples per qfield project:

```bash
uv run directus-explorer samples summary --group-by project
```

The default `text` output is an aligned terminal table with a profiled
percentage and progress bar. Use TSV when you want the previous copy-friendly
tab-separated output:

```bash
uv run directus-explorer samples summary --group-by project --format tsv
```

Count distinct species instead of samples per qfield project:

```bash
uv run directus-explorer samples summary --group-by project --count species
```

Species summaries first collect distinct non-empty values from both
`Field_Data.taxon_name` and `Field_Data.sample_name`, resolve them with
`gnverifier` against Catalogue of Life (`--sources 1`), and aggregate on the
resolved Catalogue of Life taxon ids. Unresolved names are not counted in the
species-level summary.

Generate a read-only, human-review queue for conflicting taxon fields:

```bash
uv run python scripts/generate_taxon_curation_review.py \
  --project jbp \
  --round 1
```

The curation command groups repeated conflicts, parses botanical annotations,
and resolves names against the pinned Catalogue of Life release recorded in
the output metadata. `gnverifier` supplies secondary fuzzy suggestions; every
such suggestion is checked again against the pinned CoL release before it is
shown. It never writes to Directus. Reviewers fill `decision` with one of
`accept_proposal`, `override`, `unresolved`, or `defer`; an override also needs
`chosen_verbatim_name`. Generated rounds are stored under
`data/working/taxonomy_curation/`.

After every grouped decision has been reviewed, expand the accepted groups into
a row-level dry-run manifest:

```bash
uv run python scripts/prepare_taxon_curation_manifest.py \
  --review data/working/taxonomy_curation/col_2026_07_17_xr/jbp_round_001/jbp_round_001_review.tsv
```

The manifest command refuses blank or invalid decisions and still performs no
Directus writes.

When a project has only placeholder values or records without any taxonomic
evidence left, create an explicit unresolved cleanup round:

```bash
uv run python scripts/generate_unresolved_taxon_curation_review.py \
  --project jbuf \
  --round 3
```

This groups placeholder values under `clear_placeholder` and evidence-free
NULL rows under `unresolved`. Preparing the resulting manifest includes only
the placeholder clears; unresolved rows already stored as NULL are registered
without a Directus write. Placeholder previews use the explicit action
`clear_value` and serialize the patch as `{"taxon_name_unified": null}`.

Before applying a reviewed manifest, compare it with the live Directus rows and
create a timestamped backup and exact write preview:

```bash
uv run python scripts/preflight_taxon_curation_directus_update.py \
  --manifest data/working/taxonomy_curation/col_2026_07_17_xr/jbp_round_001/jbp_round_001_update_manifest.tsv \
  --env-file .env
```

The preflight uses GET requests only. Rows whose project, sample id, source
name, or current unified value changed since the audit are reported as blocking
conflicts and excluded from the write preview.

Apply an approved preview only after inspecting its matching backup:

```bash
uv run python scripts/apply_taxon_curation_directus_update.py \
  --preview PATH_TO_WRITE_PREVIEW.tsv \
  --backup PATH_TO_MATCHING_BACKUP.tsv \
  --project jbp \
  --env-file .env.admin \
  --commit
```

The apply command repeats the live-state checks before writing, changes only
`Field_Data.taxon_name_unified`, records each successful update in an execution
journal, and verifies all resulting values.

To avoid copying long artifact paths, select the newest matching preflight pair
for a project automatically:

```bash
uv run python scripts/apply_taxon_curation_directus_update.py \
  -p jbuf -l -e .env.admin -c
```

Refresh the standalone taxonomy curation dashboard after an audit, review, or
update run:

```bash
uv run python scripts/generate_taxon_curation_dashboard.py
```

When `DIRECTUS_INSTANCE` is available in `.env`, each decision lists its sample
IDs and links directly to the corresponding `Field_Data` item in the Directus
admin interface. The dashboard embeds only the instance URL, never credentials.

Open `data/working/taxonomy_curation/taxonomy_curation_dashboard.html` in a web
browser. The report is self-contained and reads only local audit, review,
preflight, and execution artifacts; it makes no network requests.

Audit spelling, capitalization, accent, and name-order variants in
`Field_Data.collector_fullname` and prepare a backup and write preview:

```bash
uv run python scripts/harmonize_collector_fullname.py --env-file .env
```

The most frequent spelling in each identity group is selected as canonical.
The default mode uses GET requests only. After reviewing the artifacts under
`data/working/collector_harmonization/`, apply and verify the updates with an
account that can update `Field_Data`:

```bash
uv run python scripts/harmonize_collector_fullname.py \
  --env-file .env.admin \
  --commit
```

Backfill explicitly approved collector names from the first directory below
`DCIM`, create Collector records in the `People` collection, and link each
updated `Field_Data` row through `collector_ref`:

```bash
uv run python scripts/backfill_collectors_from_dcim_paths.py
uv run python scripts/backfill_collectors_from_dcim_paths.py --commit
```

The first command is a read-only preflight. The commit mode performs an
additive, idempotent schema migration, revalidates the path evidence and empty
target fields, journals every update, and verifies both the name and relation.

Seed `People` from every canonical non-empty collector name and link all other
named `Field_Data` rows that still have an empty `collector_ref`:

```bash
uv run python scripts/link_collectors_to_people.py
uv run python scripts/link_collectors_to_people.py --commit
```

Observed ORCID and iNaturalist identifiers are copied to new People records
only when they are internally consistent. Existing non-empty relations are
never overwritten.

Create the reusable research-group affiliation model and link the approved
COMMONS Lab people to the existing University of Fribourg record:

```bash
uv run python scripts/link_commons_lab_affiliations.py
uv run python scripts/link_commons_lab_affiliations.py --commit
```

This creates `Research_Groups` and the `People_Research_Groups` many-to-many
junction when absent. Building and current/former membership status are left
unset unless separately curated.

Create PEMG under the existing Université de Neuchâtel, link its approved
people, and expand Lendita to the canonical full name Lëndita Schwegler:

```bash
uv run python scripts/link_pemg_affiliations.py
uv run python scripts/link_pemg_affiliations.py --commit
```

Create the accurate IOCB Prague institution and Tomáš Pluskal Group, link its
approved people, and add Jade Dandois to COMMONS Lab:

```bash
uv run python scripts/link_pluskal_affiliations.py
uv run python scripts/link_pluskal_affiliations.py --commit
```

Summarize the DBGI megaproject (`jbc`, `jbn`, `jbp`, `jbuf`, and
`kew-botanical-gardens`) as one row:

```bash
uv run directus-explorer samples summary --project-group dbgi
uv run directus-explorer samples summary --project-group dbgi --count species
```

Export one compact metadata row per collected DBGI sample with positive/negative
profile flags and Catalogue of Life taxonomic resolution metadata:

```bash
uv run directus-explorer ms export-metadata \
  --output sample_metadata_dbgi_sample_compact.tsv \
  --project-group dbgi \
  --view sample-compact
```

To prepare a timestamped DBGI-only export containing only samples whose curated
Catalogue of Life ID is already stored in Directus, use the canonical `dbgi` project group defined in
`src/directus_explorer/samples.py`. The timestamp is UTC and makes each export
artefact traceable to its preparation time:

```bash
export DBGI_EXPORT_TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
export DBGI_EXPORT_PATH="data/output/sample_metadata_dbgi_col_${DBGI_EXPORT_TIMESTAMP}.tsv"

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

Treat the resulting timestamped file as immutable. If its content needs to be
corrected, run the export again with a new timestamp and publish a new Zenodo
version; never overwrite the earlier path.

This produces one row per `original_sample_id`, limits the source projects to the
repository-maintained DBGI list, and excludes rows whose stored
`field_taxon_col_id` is empty. It does not query Catalogue of Life: the export
uses the taxonomic resolution previously curated and applied to `Field_Data` in
Directus. It dereferences `field_taxon_ref` through the verified Taxa seed
execution artifacts and includes the final accepted name, authorship, rank,
backbone release, dataset key, and DOI. The export also retains collection
metadata and positive/negative profiling flags.

The recommended release workflow is one command:

```bash
cargo run --manifest-path tools/zenodo-publisher/Cargo.toml -- \
  release zenodo/dbgi-samples-metadata-release.toml
```

It resolves the latest record, increments `vN`, creates a fresh immutable
timestamped export, validates required taxonomy and sample uniqueness, generates
the archive README and frozen release manifest, verifies the Zenodo draft by
size and MD5, and publishes it. Use `--draft-only` to stop before publication:

```bash
cargo run --manifest-path tools/zenodo-publisher/Cargo.toml -- \
  release zenodo/dbgi-samples-metadata-release.toml --draft-only
```

The publisher reads `ZENODO_TOKEN`, creates or reuses the editable new-version
draft, replaces and verifies its files by size and MD5, and publishes only after
verification. Re-running the same manifest is safe: an already-published `vN`
is verified and returned instead of creating another version. See
`tools/zenodo-publisher/README.md` for details.

Export one wide TSV row per collected sample lineage. Samples without any MS records are still included; samples with MS rows are enriched with the linked lineage metadata:

```bash
uv run directus-explorer ms export-metadata --output sample_metadata.tsv
```

Export a curated compact metadata view instead of the full rich table:

```bash
uv run directus-explorer ms export-metadata --output sample_metadata_compact.tsv --view compact
```

The metadata export no longer includes the physical storage hierarchy. Use the dedicated
sample locations command for container/storage lookup.

Restrict the metadata export to a single qfield project and enrich exact-matching MS rows with metadata from one or more `mzmlwatcher` TSV artefacts:

```bash
uv run directus-explorer ms export-metadata \
  --output sample_metadata_jbuf.tsv \
  --project jbuf \
  --watcher-tsv /home/allardpm/git_repos/oolonek/mzmlwatcher/output/mzmlwatcher_qehfx.tsv \
  --watcher-tsv /home/allardpm/git_repos/oolonek/mzmlwatcher/output/mzmlwatcher_qeplus.tsv

uv run directus-explorer ms export-metadata \
  --output sample_metadata_compact.tsv \
  --watcher-tsv /home/allardpm/git_repos/oolonek/mzmlwatcher/output/mzmlwatcher_qehfx.tsv \
  --watcher-tsv /home/allardpm/git_repos/oolonek/mzmlwatcher/output/mzmlwatcher_qeplus.tsv
```

Resolve the physical sample containers and storage hierarchy for one sample or container code:

```bash
uv run directus-explorer samples locations --sample-id dbgi_003187_01_01
```

Render the same result as top-down retrieval instructions:

```bash
uv run directus-explorer samples locations --sample-id dbgi_003187_01_01 --format pretty
```

Export all physical sample locations for one qfield project:

```bash
uv run directus-explorer samples locations \
  --project jbuf \
  --format tsv \
  --output sample_locations_jbuf.tsv
```

Compare an exported MS metadata CSV against the mzML watcher inventory and write per-row CSV results:

```bash
uv run directus-explorer ms check-converted \
  --metadata-csv /tmp/ms_metadata_jbuf.csv \
  --watcher-tsv /home/allardpm/git_repos/oolonek/mzmlwatcher/output/mzmlwatcher_qehfx.tsv \
  --watcher-tsv /home/allardpm/git_repos/oolonek/mzmlwatcher/output/mzmlwatcher_qeplus.tsv \
  --format csv \
  --output converted_check_jbuf.csv
```

Export only analyses with an exact converted-file match:

```bash
uv run directus-explorer ms check-converted \
  --metadata-csv /tmp/ms_metadata_jbuf.csv \
  --watcher-tsv /home/allardpm/git_repos/oolonek/mzmlwatcher/output/mzmlwatcher_qehfx.tsv \
  --watcher-tsv /home/allardpm/git_repos/oolonek/mzmlwatcher/output/mzmlwatcher_qeplus.tsv \
  --format csv \
  --output converted_check_matches_jbuf.csv \
  --matches-only
```

Export only analyses missing any exact converted-file match:

```bash
uv run directus-explorer ms check-converted \
  --metadata-csv /tmp/ms_metadata_jbuf.csv \
  --watcher-tsv /home/allardpm/git_repos/oolonek/mzmlwatcher/output/mzmlwatcher_qehfx.tsv \
  --watcher-tsv /home/allardpm/git_repos/oolonek/mzmlwatcher/output/mzmlwatcher_qeplus.tsv \
  --format csv \
  --output converted_check_missing_jbuf.csv \
  --missing-only
```

The per-row report is exact-filename based. It also carries the Directus-sourced base
`dbgi_#####` sample identifier and the more specific profiled sample/aliquot code such as
`dbgi_001195_01_01` as contextual columns, but those identifiers are not used to create secondary
matches. For exact matches, the report also includes the watcher-exported mzML `polarity`.

Correct filenames on existing `MS_Data` rows from a CSV containing
`ms_data_id` and `ms_filename`. Optional `ms_parent_container_id` and
`injection_method` columns are checked against Directus before a row becomes
eligible. The command is a dry run unless `--commit` is supplied:

```bash
uv run directus-explorer ms update-filenames filename_updates.csv \
  --output filename_update_audit.tsv

uv run directus-explorer ms update-filenames filename_updates.csv \
  --output filename_update_audit.tsv \
  --commit
```

## Champex Metadata Sanitization

The Champex massive metadata CSV is kept immutable in `data/input`. Work from a copy and write
all derived files under `data/output/champex`:

```bash
mkdir -p data/working/champex_original data/output/champex
cp -n data/input/metadata_massive_champex.csv \
  data/working/champex_original/metadata_massive_champex.csv
```

Run the local sanitizer:

```bash
uv run python scripts/sanitize_champex_metadata.py
```

This writes:

```text
data/output/champex/metadata_massive_champex_sanitized.csv
data/output/champex/metadata_massive_champex_sanitization_issues.tsv
data/output/champex/metadata_massive_champex_sanitization_summary.json
```

The sanitizer converts the semicolon-delimited source into a comma-delimited CSV, normalizes
headers to snake case, converts `0`/`1` flags to booleans, coerces numeric columns, blanks
`QC`/`Blank` markers in typed metadata fields, preserves the damaged source date as `raw_date`,
and adds `normalized_date`, `date_parse_status`, and `filename_date_token`.

Run Frictionless profiling and validation:

```bash
uv run python -c "
from frictionless import Resource, validate
from frictionless.formats.csv import CsvControl
import json

specs = [
    (
        'data/working/champex_original/metadata_massive_champex.csv',
        'data/output/champex/metadata_massive_champex_raw_frictionless_schema.json',
        'data/output/champex/metadata_massive_champex_raw_frictionless_report.json',
        ';',
    ),
    (
        'data/output/champex/metadata_massive_champex_sanitized.csv',
        'data/output/champex/metadata_massive_champex_sanitized_frictionless_schema.json',
        'data/output/champex/metadata_massive_champex_sanitized_frictionless_report.json',
        ',',
    ),
]

for source, schema_path, report_path, delimiter in specs:
    resource = Resource(path=source, control=CsvControl(delimiter=delimiter))
    resource.infer(stats=True)
    descriptor = resource.to_descriptor()
    open(schema_path, 'w', encoding='utf-8').write(
        json.dumps(descriptor, indent=2, ensure_ascii=False)
    )
    report = validate(resource)
    open(report_path, 'w', encoding='utf-8').write(
        json.dumps(report.to_descriptor(), indent=2, ensure_ascii=False)
    )
    print(source, 'valid=', report.valid, 'errors=', report.stats.get('errors'))
"
```

Expected current result:

```text
data/working/champex_original/metadata_massive_champex.csv valid= False errors= 42
data/output/champex/metadata_massive_champex_sanitized.csv valid= True errors= 0
```

The `date` values in the source, such as `2,02506E+13`, are spreadsheet-damaged and are not
recoverable as full day-level dates from this CSV alone. The sanitizer therefore leaves
`normalized_date` empty for those rows and records `ambiguous_spreadsheet_scientific_date` in
`date_parse_status` and the issues TSV.
