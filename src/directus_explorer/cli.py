"""Click CLI for querying a Directus instance."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from .config import SettingsError, load_settings
from .directus import DirectusAuthError, DirectusClient, DirectusError, DirectusResponseError
from .samples import ProfiledSample, ProjectSampleSummary


@click.group()
def cli() -> None:
    """Fetch and summarize data from a Directus instance."""


@cli.group()
def ms() -> None:
    """Work with mass-spectrometry-related Directus queries."""


@cli.group()
def utils() -> None:
    """Utility commands for discovering Directus content."""


@cli.group()
def samples() -> None:
    """Work with sample-related Directus queries."""


@samples.command("count")
@click.argument("qfield_project")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(("text", "json"), case_sensitive=False),
    default="text",
    show_default=True,
)
@click.option(
    "--env-file",
    type=click.Path(path_type=Path, dir_okay=False, resolve_path=False),
    default=None,
    help="Override the default local .env file.",
)
def count_samples(qfield_project: str, output_format: str, env_file: Path | None) -> None:
    """Count collected samples for an exact qfield project key."""

    try:
        settings = load_settings(env_file=env_file)
        client = DirectusClient(settings)
        sample_count = client.count_field_samples(qfield_project=qfield_project)
    except (SettingsError, DirectusAuthError, DirectusError, DirectusResponseError) as exc:
        raise click.ClickException(str(exc)) from exc

    if output_format == "json":
        click.echo(
            json.dumps(
                {
                    "qfield_project": qfield_project,
                    "sample_count": sample_count,
                }
            )
        )
        return

    click.echo(f"Project {qfield_project}: {sample_count} samples")


@samples.command("profiled")
@click.option(
    "--mode",
    type=click.Choice(("any", "positive", "negative", "both"), case_sensitive=False),
    default="any",
    show_default=True,
    help="Filter profiled samples by polarity coverage.",
)
@click.option(
    "--project",
    "qfield_project",
    default=None,
    help="Restrict results to one qfield project.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(("text", "json"), case_sensitive=False),
    default="text",
    show_default=True,
)
@click.option(
    "--count",
    "count_only",
    is_flag=True,
    help="Return only the number of matching profiled samples.",
)
@click.option(
    "--env-file",
    type=click.Path(path_type=Path, dir_okay=False, resolve_path=False),
    default=None,
    help="Override the default local .env file.",
)
def profiled_samples(
    mode: str,
    qfield_project: str | None,
    output_format: str,
    count_only: bool,
    env_file: Path | None,
) -> None:
    """List original samples that have been profiled in mass spectrometry."""

    try:
        settings = load_settings(env_file=env_file)
        client = DirectusClient(settings)
        profiled = client.list_profiled_samples(mode=mode, project=qfield_project)
    except (SettingsError, DirectusAuthError, DirectusError, DirectusResponseError) as exc:
        raise click.ClickException(str(exc)) from exc

    if output_format == "json":
        payload: dict[str, Any] = {
            "mode_filter": mode,
            "project_filter": qfield_project,
            "sample_count": len(profiled),
        }
        if not count_only:
            payload["samples"] = [
                {
                    "sample_id": sample.sample_id,
                    "qfield_project": sample.qfield_project,
                    "mode": sample.mode,
                }
                for sample in profiled
            ]
        click.echo(
            json.dumps(payload)
        )
        return

    if count_only:
        if qfield_project is None:
            click.echo(f"Mode {mode}: {len(profiled)} profiled samples")
        else:
            click.echo(f"Project {qfield_project}, mode {mode}: {len(profiled)} profiled samples")
        return

    for sample in profiled:
        click.echo(_render_profiled_sample(sample))


@samples.command("summary")
@click.option(
    "--group-by",
    type=click.Choice(("project",), case_sensitive=False),
    default="project",
    show_default=True,
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(("text", "json"), case_sensitive=False),
    default="text",
    show_default=True,
)
@click.option(
    "--env-file",
    type=click.Path(path_type=Path, dir_okay=False, resolve_path=False),
    default=None,
    help="Override the default local .env file.",
)
def summarize_samples(group_by: str, output_format: str, env_file: Path | None) -> None:
    """Summarize collected and profiled samples grouped by project."""

    try:
        settings = load_settings(env_file=env_file)
        client = DirectusClient(settings)
        summaries = client.summarize_samples_by_project()
    except (SettingsError, DirectusAuthError, DirectusError, DirectusResponseError) as exc:
        raise click.ClickException(str(exc)) from exc

    if output_format == "json":
        click.echo(
            json.dumps(
                {
                    "group_by": group_by,
                    "projects": [
                        {
                            "qfield_project": summary.qfield_project,
                            "collected_count": summary.collected_count,
                            "profiled_count": summary.profiled_count,
                            "positive_count": summary.positive_count,
                            "negative_count": summary.negative_count,
                            "both_count": summary.both_count,
                        }
                        for summary in summaries
                    ],
                }
            )
        )
        return

    click.echo("qfield_project\tcollected\tprofiled\tpositive\tnegative\tboth")
    for summary in summaries:
        click.echo(_render_project_summary(summary))


@ms.command("export-metadata")
@click.option(
    "--output",
    "output_path",
    type=click.Path(path_type=Path, dir_okay=False, resolve_path=False),
    required=True,
    help="Write the flattened metadata table to this CSV path.",
)
@click.option(
    "--project",
    "qfield_project",
    default=None,
    help="Restrict the export to one qfield project.",
)
@click.option(
    "--env-file",
    type=click.Path(path_type=Path, dir_okay=False, resolve_path=False),
    default=None,
    help="Override the default local .env file.",
)
def export_ms_metadata(
    output_path: Path,
    qfield_project: str | None,
    env_file: Path | None,
) -> None:
    """Export one wide CSV row per MS_Data record."""

    try:
        settings = load_settings(env_file=env_file)
        client = DirectusClient(settings)
        row_count = client.export_ms_metadata_csv(output_path=output_path, project=qfield_project)
    except (SettingsError, DirectusAuthError, DirectusError, DirectusResponseError) as exc:
        raise click.ClickException(str(exc)) from exc

    if qfield_project is None:
        click.echo(f"Wrote {row_count} MS metadata rows to {output_path}")
    else:
        click.echo(
            f"Wrote {row_count} MS metadata rows for project {qfield_project} to {output_path}"
        )


@utils.command("projects")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(("text", "json"), case_sensitive=False),
    default="text",
    show_default=True,
)
@click.option(
    "--env-file",
    type=click.Path(path_type=Path, dir_okay=False, resolve_path=False),
    default=None,
    help="Override the default local .env file.",
)
def list_projects(output_format: str, env_file: Path | None) -> None:
    """List all distinct qfield project keys from Field_Data."""

    try:
        settings = load_settings(env_file=env_file)
        client = DirectusClient(settings)
        projects = client.list_projects()
    except (SettingsError, DirectusAuthError, DirectusError, DirectusResponseError) as exc:
        raise click.ClickException(str(exc)) from exc

    if output_format == "json":
        click.echo(json.dumps({"projects": projects}))
        return

    for project in projects:
        click.echo(project)


def _render_profiled_sample(sample: ProfiledSample) -> str:
    """Render a profiled sample in plain text."""

    return f"{sample.sample_id}\t{sample.qfield_project}\t{sample.mode}"


def _render_project_summary(summary: ProjectSampleSummary) -> str:
    """Render a project summary in plain text."""

    return (
        f"{summary.qfield_project}\t{summary.collected_count}\t{summary.profiled_count}\t"
        f"{summary.positive_count}\t{summary.negative_count}\t{summary.both_count}"
    )
