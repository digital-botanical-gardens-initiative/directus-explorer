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
