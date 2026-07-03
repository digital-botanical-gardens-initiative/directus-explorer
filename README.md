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

Count distinct species instead of samples per qfield project:

```bash
uv run directus-explorer samples summary --group-by project --count species
```

Species summaries first collect distinct non-empty values from both
`Field_Data.taxon_name` and `Field_Data.sample_name`, resolve them with
`gnverifier` against Catalogue of Life (`--sources 1`), and aggregate on the
resolved Catalogue of Life taxon ids. Unresolved names are not counted in the
species-level summary.

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
