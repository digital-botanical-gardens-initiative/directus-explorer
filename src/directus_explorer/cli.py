"""Click CLI for querying a Directus instance."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import click

from .config import SettingsError, load_settings
from .directus import DirectusAuthError, DirectusClient, DirectusError, DirectusResponseError
from .injection_audit import summarize_injection_audit
from .ms_converted_check import (
    ConvertedMatchSummary,
    compare_metadata_to_watcher,
    filter_report,
    report_to_json_payload,
    write_report_csv,
)
from .samples import (
    PROJECT_GROUPS,
    ProfiledSample,
    ProjectSampleSummary,
    ProjectSpeciesSummary,
)


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
    "--count",
    "count_by",
    type=click.Choice(("samples", "species"), case_sensitive=False),
    default="samples",
    show_default=True,
    help="Choose whether summary counts represent samples or distinct species.",
)
@click.option(
    "--project-group",
    type=click.Choice(tuple(PROJECT_GROUPS), case_sensitive=False),
    default=None,
    help="Summarize one configured project group instead of individual qfield projects.",
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
def summarize_samples(
    group_by: str,
    count_by: str,
    project_group: str | None,
    output_format: str,
    env_file: Path | None,
) -> None:
    """Summarize collected and profiled samples grouped by project."""

    try:
        settings = load_settings(env_file=env_file)
        client = DirectusClient(settings)
        summaries: Sequence[ProjectSampleSummary | ProjectSpeciesSummary]
        if count_by == "species":
            if project_group is None:
                summaries = client.summarize_species_by_project()
            else:
                summaries = client.summarize_species_by_project_group(project_group)
        elif project_group is None:
            summaries = client.summarize_samples_by_project()
        else:
            summaries = client.summarize_samples_by_project_group(project_group)
    except (SettingsError, DirectusAuthError, DirectusError, DirectusResponseError) as exc:
        raise click.ClickException(str(exc)) from exc

    if output_format == "json":
        count_keys = (
            (
                "collected_species_count",
                "profiled_species_count",
                "positive_species_count",
                "negative_species_count",
                "both_species_count",
            )
            if count_by == "species"
            else (
                "collected_count",
                "profiled_count",
                "positive_count",
                "negative_count",
                "both_count",
            )
        )
        payload: dict[str, Any] = {
            "group_by": "project_group" if project_group is not None else group_by,
            "projects": [
                {
                    (
                        "project_group" if project_group is not None else "qfield_project"
                    ): summary.qfield_project,
                    count_keys[0]: summary.collected_count,
                    count_keys[1]: summary.profiled_count,
                    count_keys[2]: summary.positive_count,
                    count_keys[3]: summary.negative_count,
                    count_keys[4]: summary.both_count,
                }
                for summary in summaries
            ],
        }
        if count_by == "species":
            payload["count_by"] = count_by
        click.echo(json.dumps(payload))
        return

    if count_by == "species":
        click.echo(
            f"{_summary_label_header(project_group)}\tcollected_species\tprofiled_species\t"
            "positive_species\tnegative_species\tboth_species"
        )
    else:
        click.echo(
            f"{_summary_label_header(project_group)}\tcollected\tprofiled\t"
            "positive\tnegative\tboth"
        )
    for summary in summaries:
        click.echo(_render_project_summary(summary))


@samples.command("locations")
@click.option(
    "--sample-id",
    default=None,
    help="Resolve locations for one original/extraction/MS sample container identifier.",
)
@click.option(
    "--project",
    "qfield_project",
    default=None,
    help="Export location rows for all samples in one qfield project.",
)
@click.option(
    "--output",
    "output_path",
    type=click.Path(path_type=Path, dir_okay=False, resolve_path=False),
    default=None,
    help="Write location rows to this TSV path.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(("text", "json", "tsv", "pretty"), case_sensitive=False),
    default="text",
    show_default=True,
)
@click.option(
    "--env-file",
    type=click.Path(path_type=Path, dir_okay=False, resolve_path=False),
    default=None,
    help="Override the default local .env file.",
)
def sample_locations(
    sample_id: str | None,
    qfield_project: str | None,
    output_path: Path | None,
    output_format: str,
    env_file: Path | None,
) -> None:
    """Resolve sample containers and physical storage hierarchy."""

    if (sample_id is None) == (qfield_project is None):
        raise click.ClickException("Exactly one of --sample-id or --project must be provided")
    if output_format == "tsv" and output_path is None:
        raise click.ClickException("--output is required when --format tsv")

    try:
        settings = load_settings(env_file=env_file)
        client = DirectusClient(settings)
        table = client.build_sample_locations_table(sample_id=sample_id, project=qfield_project)
        if output_format == "tsv":
            assert output_path is not None
            from .ms_metadata import write_ms_metadata_csv

            write_ms_metadata_csv(table, output_path, delimiter="\t")
    except (
        SettingsError,
        DirectusAuthError,
        DirectusError,
        DirectusResponseError,
        ValueError,
    ) as exc:
        raise click.ClickException(str(exc)) from exc

    if output_format == "json":
        click.echo(
            json.dumps(
                {
                    "sample_id": sample_id,
                    "qfield_project": qfield_project,
                    "row_count": len(table.rows),
                    "rows": list(table.rows),
                }
            )
        )
        return

    if output_format == "tsv":
        click.echo(f"Wrote {len(table.rows)} location rows to {output_path}")
        return

    if not table.rows:
        return
    if output_format == "pretty":
        click.echo(_render_sample_locations_pretty(table.rows))
        return
    click.echo("\t".join(table.fieldnames))
    for row in table.rows:
        click.echo(
            "\t".join("" if row.get(fieldname) is None else str(row.get(fieldname)) for fieldname in table.fieldnames)
        )


@ms.command("export-metadata")
@click.option(
    "--output",
    "output_path",
    type=click.Path(path_type=Path, dir_okay=False, resolve_path=False),
    required=True,
    help="Write the flattened metadata table to this TSV path.",
)
@click.option(
    "--project",
    "qfield_project",
    default=None,
    help="Restrict the export to one qfield project.",
)
@click.option(
    "--project-group",
    type=click.Choice(tuple(PROJECT_GROUPS), case_sensitive=False),
    default=None,
    help="Restrict the export to one configured project group.",
)
@click.option(
    "--watcher-tsv",
    type=click.Path(path_type=Path, dir_okay=False, resolve_path=False),
    default=(),
    multiple=True,
    help="Optional mzmlwatcher TSV export(s) used to enrich exact-matching MS rows.",
)
@click.option(
    "--view",
    "export_view",
    type=click.Choice(("full", "compact", "sample-compact"), case_sensitive=False),
    default="full",
    show_default=True,
    help="Choose between rich, compact, or one-row-per-sample compact metadata.",
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
    project_group: str | None,
    watcher_tsv: tuple[Path, ...],
    export_view: str,
    env_file: Path | None,
) -> None:
    """Export one wide TSV row per collected sample lineage."""

    try:
        settings = load_settings(env_file=env_file)
        client = DirectusClient(settings)
        row_count = client.export_ms_metadata_csv(
            output_path=output_path,
            project=qfield_project,
            project_group=project_group,
            watcher_tsv_paths=watcher_tsv,
            view=export_view,
        )
    except (SettingsError, DirectusAuthError, DirectusError, DirectusResponseError) as exc:
        raise click.ClickException(str(exc)) from exc

    if project_group is not None:
        click.echo(
            f"Wrote {row_count} metadata rows for project group {project_group} to {output_path}"
        )
    elif qfield_project is None:
        click.echo(f"Wrote {row_count} metadata rows to {output_path}")
    else:
        click.echo(
            f"Wrote {row_count} metadata rows for project {qfield_project} to {output_path}"
        )


@ms.command("audit-injection-list")
@click.argument(
    "input_csv",
    type=click.Path(path_type=Path, dir_okay=False, resolve_path=False),
)
@click.option(
    "--output",
    "output_path",
    type=click.Path(path_type=Path, dir_okay=False, resolve_path=False),
    default=None,
    help="Write the row-level audit report to this TSV path.",
)
@click.option(
    "--file-type",
    "required_file_type",
    default="sample",
    show_default=True,
    help="CSV file_type value considered eligible for MS import.",
)
@click.option(
    "--ms-parent-level",
    type=click.Choice(("aliquot", "extraction"), case_sensitive=False),
    default="aliquot",
    show_default=True,
    help="Directus sample-container level that MS_Data.parent_sample_container should target.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(("text", "json", "tsv"), case_sensitive=False),
    default="text",
    show_default=True,
)
@click.option(
    "--env-file",
    type=click.Path(path_type=Path, dir_okay=False, resolve_path=False),
    default=None,
    help="Override the default local .env file.",
)
def audit_injection_list(
    input_csv: Path,
    output_path: Path | None,
    required_file_type: str,
    ms_parent_level: str,
    output_format: str,
    env_file: Path | None,
) -> None:
    """Audit an acquisition injection list against Directus sample lineage."""

    if output_format == "tsv" and output_path is None:
        raise click.ClickException("--output is required when --format tsv")

    try:
        settings = load_settings(env_file=env_file)
        client = DirectusClient(settings)
        table = client.build_injection_audit_table(
            input_path=input_csv,
            required_file_type=required_file_type,
            ms_parent_level=ms_parent_level,
        )
        if output_path is not None:
            from .injection_audit import write_injection_audit_tsv

            write_injection_audit_tsv(table, output_path)
    except (
        SettingsError,
        DirectusAuthError,
        DirectusError,
        DirectusResponseError,
        ValueError,
    ) as exc:
        raise click.ClickException(str(exc)) from exc

    summary = summarize_injection_audit(table)
    if output_format == "json":
        click.echo(
            json.dumps(
                {
                    "input_csv": str(input_csv),
                    "output": str(output_path) if output_path is not None else None,
                    "ms_parent_level": ms_parent_level,
                    "summary": summary,
                    "rows": list(table.rows),
                }
            )
        )
        return

    if output_format == "tsv":
        click.echo(f"Wrote {summary['row_count']} injection audit rows to {output_path}")
        return

    click.echo(_render_injection_audit_summary(summary))
    if output_path is not None:
        click.echo(f"report\t{output_path}")


@ms.command("import-ready-injection-runs")
@click.argument(
    "input_csv",
    type=click.Path(path_type=Path, dir_okay=False, resolve_path=False),
)
@click.option(
    "--file-type",
    "required_file_type",
    default="sample",
    show_default=True,
    help="CSV file_type value considered eligible for MS import.",
)
@click.option(
    "--ms-parent-level",
    type=click.Choice(("aliquot", "extraction"), case_sensitive=False),
    default="aliquot",
    show_default=True,
    help="Directus sample-container level that MS_Data.parent_sample_container should target.",
)
@click.option(
    "--injection-volume",
    type=int,
    default=1,
    show_default=True,
    help="MS_Data.injection_volume value.",
)
@click.option(
    "--injection-volume-unit-id",
    type=int,
    required=True,
    help="Directus SI_Units.id for the injection volume unit.",
)
@click.option(
    "--injection-method-id",
    type=int,
    required=True,
    help="Directus Injection_Methods.id to assign to every created run.",
)
@click.option(
    "--instrument-id",
    type=int,
    required=True,
    help="Directus Instruments.id to assign to every created run.",
)
@click.option(
    "--batch-id",
    type=int,
    default=None,
    help="Optional Directus Batches.id to assign to every created run.",
)
@click.option(
    "--status",
    "item_status",
    default="published",
    show_default=True,
    help="MS_Data.status value for created rows.",
)
@click.option(
    "--limit",
    type=int,
    default=None,
    help="Import at most this many ready rows.",
)
@click.option(
    "--commit",
    is_flag=True,
    help="Actually create MS_Data rows. Without this flag, only perform a dry run.",
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
def import_ready_injection_runs(
    input_csv: Path,
    required_file_type: str,
    ms_parent_level: str,
    injection_volume: int,
    injection_volume_unit_id: int,
    injection_method_id: int,
    instrument_id: int,
    batch_id: int | None,
    item_status: str,
    limit: int | None,
    commit: bool,
    output_format: str,
    env_file: Path | None,
) -> None:
    """Create MS_Data rows for audit-ready acquisition-list rows."""

    try:
        settings = load_settings(env_file=env_file)
        client = DirectusClient(settings)
        summary = client.import_ready_injection_runs(
            input_path=input_csv,
            required_file_type=required_file_type,
            ms_parent_level=ms_parent_level,
            injection_volume=injection_volume,
            injection_volume_unit_id=injection_volume_unit_id,
            injection_method_id=injection_method_id,
            instrument_id=instrument_id,
            status=item_status,
            batch_id=batch_id,
            limit=limit,
            commit=commit,
        )
    except (
        SettingsError,
        DirectusAuthError,
        DirectusError,
        DirectusResponseError,
        ValueError,
    ) as exc:
        raise click.ClickException(str(exc)) from exc

    if output_format == "json":
        click.echo(json.dumps(summary))
        return
    click.echo(_render_injection_import_summary(summary))


@ms.command("check-converted")
@click.option(
    "--metadata-csv",
    type=click.Path(path_type=Path, dir_okay=False, resolve_path=False),
    required=True,
    help="Read Directus MS metadata rows from this CSV path.",
)
@click.option(
    "--watcher-tsv",
    type=click.Path(path_type=Path, dir_okay=False, resolve_path=False),
    required=True,
    multiple=True,
    help="Read watcher inventory rows from one or more TSV export paths.",
)
@click.option(
    "--output",
    "output_path",
    type=click.Path(path_type=Path, dir_okay=False, resolve_path=False),
    default=None,
    help="Write per-row comparison results to this output CSV path.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(("text", "json", "csv"), case_sensitive=False),
    default="text",
    show_default=True,
)
@click.option(
    "--matches-only",
    is_flag=True,
    help="Only include rows with an exact converted-file match.",
)
@click.option(
    "--missing-only",
    is_flag=True,
    help="Only include rows with no exact converted-file match.",
)
def check_converted(
    metadata_csv: Path,
    watcher_tsv: tuple[Path, ...],
    output_path: Path | None,
    output_format: str,
    matches_only: bool,
    missing_only: bool,
) -> None:
    """Compare Directus MS metadata rows against watcher converted-file inventory."""

    if output_format == "csv" and output_path is None:
        raise click.ClickException("--output is required when --format csv")

    try:
        report = compare_metadata_to_watcher(metadata_csv, watcher_tsv)
        report = filter_report(
            report,
            matches_only=matches_only,
            missing_only=missing_only,
        )
        if output_format == "csv":
            assert output_path is not None
            write_report_csv(report, output_path)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    if output_format == "json":
        click.echo(json.dumps(report_to_json_payload(report)))
        return

    if output_format == "csv":
        click.echo(f"Wrote {report.summary.metadata_row_count} comparison rows to {output_path}")
        return

    click.echo(_render_converted_match_summary(report.summary))


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


def _render_project_summary(summary: ProjectSampleSummary | ProjectSpeciesSummary) -> str:
    """Render a project summary in plain text."""

    return (
        f"{summary.qfield_project}\t{summary.collected_count}\t{summary.profiled_count}\t"
        f"{summary.positive_count}\t{summary.negative_count}\t{summary.both_count}"
    )


def _summary_label_header(project_group: str | None) -> str:
    """Return the label column for summary output."""

    return "project_group" if project_group is not None else "qfield_project"


def _render_converted_match_summary(summary: ConvertedMatchSummary) -> str:
    """Render converted-file comparison summary in plain text."""

    return "\n".join(
        (
            f"metadata_row_count\t{summary.metadata_row_count}",
            f"exact_match_count\t{summary.exact_match_count}",
            f"no_match_count\t{summary.no_match_count}",
        )
    )


def _render_injection_audit_summary(summary: dict[str, int]) -> str:
    """Render an injection audit summary in plain text."""

    return "\n".join(
        (
            f"row_count\t{summary['row_count']}",
            f"sample_file_count\t{summary['sample_file_count']}",
            f"ready_count\t{summary['ready_count']}",
            f"blocked_count\t{summary['blocked_count']}",
            f"already_imported_count\t{summary['already_imported_count']}",
            f"skipped_count\t{summary['skipped_count']}",
            f"missing_dried_material_count\t{summary['missing_dried_material_count']}",
            "missing_extraction_sample_container_count\t"
            f"{summary['missing_extraction_sample_container_count']}",
            "missing_aliquot_sample_container_count\t"
            f"{summary['missing_aliquot_sample_container_count']}",
            f"missing_csv_identifier_count\t{summary['missing_csv_identifier_count']}",
        )
    )


def _render_injection_import_summary(summary: dict[str, Any]) -> str:
    """Render an injection import summary in plain text."""

    mode = "dry_run" if summary.get("dry_run") else "committed"
    return "\n".join(
        (
            f"mode\t{mode}",
            f"ready_count\t{summary['ready_count']}",
            f"selected_count\t{summary['selected_count']}",
            f"created_count\t{summary['created_count']}",
            f"skipped_selected_count\t{summary['skipped_selected_count']}",
            f"blocked_count\t{summary['blocked_count']}",
            f"already_imported_count\t{summary['already_imported_count']}",
        )
    )


def _render_sample_locations_pretty(rows: tuple[dict[str, Any], ...]) -> str:
    """Render one or more sample location rows as retrieval guidance."""

    return "\n\n".join(_render_sample_location_pretty(row) for row in rows)


def _render_sample_location_pretty(row: dict[str, Any]) -> str:
    """Render one sample location row as top-down retrieval instructions."""

    original_sample_id = row.get("original_sample_id") or "unknown_sample"
    qfield_project = row.get("qfield_project")
    header = f"Sample {original_sample_id}"
    if qfield_project:
        header = f"{header} ({qfield_project})"

    lines = [header]

    ms_lines = _render_location_branch_pretty(
        row=row,
        title="MS aliquot",
        container_id_key="ms_container_id",
        container_type_key="ms_container_type",
        storage_prefix="ms_storage",
    )
    extraction_lines = _render_location_branch_pretty(
        row=row,
        title="Extract",
        container_id_key="extraction_container_id",
        container_type_key="extraction_container_type",
        storage_prefix="extraction_storage",
    )
    original_lines = _render_location_branch_pretty(
        row=row,
        title="Original sample",
        container_id_key="original_sample_container_id",
        container_type_key="original_sample_container_type",
        storage_prefix="original_storage",
    )

    if ms_lines:
        lines.extend(ms_lines)
    if extraction_lines:
        lines.extend(extraction_lines)
    if original_lines:
        lines.extend(original_lines)

    return "\n".join(lines)


def _render_location_branch_pretty(
    *,
    row: dict[str, Any],
    title: str,
    container_id_key: str,
    container_type_key: str,
    storage_prefix: str,
) -> list[str]:
    """Render one location branch from top storage down to the sample container."""

    container_id = row.get(container_id_key)
    container_type = row.get(container_type_key)
    if not container_id:
        return []

    storage_chain: list[tuple[str, str | None]] = []
    level = 1
    while True:
        storage_id = row.get(f"{storage_prefix}_level_{level}_container_id")
        storage_type = row.get(f"{storage_prefix}_level_{level}_type")
        if not storage_id:
            break
        storage_chain.append((str(storage_id), storage_type if isinstance(storage_type, str) else None))
        level += 1

    lines = [f"{title}:"]
    for storage_id, storage_type in reversed(storage_chain):
        lines.append(f"- Find {_format_container_label(storage_id, storage_type)}.")
    lines.append(f"- Retrieve {_format_container_label(str(container_id), container_type if isinstance(container_type, str) else None)}.")
    return lines


def _format_container_label(container_id: str, container_type: str | None) -> str:
    """Format one container as `Type ID` when possible."""

    if container_type:
        return f"{container_type} {container_id}"
    return container_id
