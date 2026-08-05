"""Apply a preflighted taxon curation preview to Directus.

The command is a dry run unless --commit is supplied. Even in commit mode it
revalidates every row against live Directus state before issuing any PATCH and
requires the timestamped preflight backup covering the same Field_Data ids.
"""

from __future__ import annotations

import argparse
import csv
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

from directus_explorer.config import SettingsError, load_settings
from directus_explorer.directus import DirectusClient, DirectusError, DirectusResponseError
from directus_explorer.taxon_curation_preflight import build_taxon_update_preflight


def main() -> None:
    args = _parse_args()
    preview_path, backup_path = _resolve_input_paths(
        preview=args.preview,
        backup=args.backup,
        latest=args.latest,
        project=args.project,
        curation_root=args.curation_root,
    )
    preview_rows = _read_tsv(preview_path)
    backup_rows = _read_tsv(backup_path)
    _validate_inputs(preview_rows, backup_rows, expected_project=args.project)

    client = DirectusClient(load_settings(env_file=args.env_file))
    client._authenticate()
    current_rows = _fetch_current_rows(
        client,
        ids=[row["field_data_id"] for row in preview_rows],
        source_fields={row["authoritative_source_field"] for row in preview_rows},
    )
    current_by_id = {str(row["id"]): row for row in current_rows}
    _, ready_rows, conflicts = build_taxon_update_preflight(preview_rows, current_by_id)
    if conflicts or len(ready_rows) != len(preview_rows):
        details = "; ".join(
            f"{row['field_data_id']}={row['conflict_reason']}" for row in conflicts
        )
        raise DirectusError(
            "Live state changed after preflight; no updates were attempted. " + details
        )

    if not args.commit:
        print(f"Dry run OK: {len(ready_rows)} rows remain safe to update.")
        print("Re-run the same command with --commit to apply them.")
        return

    _require_update_permission(client)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    journal_path = preview_path.with_name(
        preview_path.name.removesuffix(".tsv") + f"_execution_{timestamp}.tsv"
    )
    journal: list[dict[str, str]] = []
    for row in ready_rows:
        field_id = int(row["field_data_id"])
        update_action = row.get("update_action", "set_value")
        new_value = (
            None if update_action == "clear_value" else row["new_taxon_name_unified"]
        )
        updated = client._patch_item(
            collection="Field_Data",
            item_id=field_id,
            payload={"taxon_name_unified": new_value},
        )
        returned_value = _text(updated.get("taxon_name_unified"))
        if returned_value != _text(new_value):
            raise DirectusResponseError(
                f"Directus returned an unexpected value for Field_Data {field_id}: "
                f"{returned_value!r}"
            )
        journal.append(
            {
                "updated_at": datetime.now(UTC).isoformat(),
                "field_data_id": str(field_id),
                "sample_id": row["sample_id"],
                "old_value": row["current_taxon_name_unified"],
                "new_value": _text(new_value),
                "update_action": update_action,
                "status": "updated",
            }
        )
        _write_tsv(journal_path, journal)

    verified_rows = _fetch_current_rows(
        client,
        ids=[row["field_data_id"] for row in preview_rows],
        source_fields=set(),
    )
    verified_by_id = {str(row["id"]): row for row in verified_rows}
    failures = [
        row["field_data_id"]
        for row in preview_rows
        if _text(verified_by_id.get(row["field_data_id"], {}).get("taxon_name_unified"))
        != ("" if row.get("update_action") == "clear_value" else row["new_taxon_name_unified"])
    ]
    if failures:
        raise DirectusResponseError(
            "Post-update verification failed for Field_Data ids: " + ", ".join(failures)
        )
    print(f"Updated and verified {len(journal)} Field_Data rows.")
    print(f"Execution journal: {journal_path}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preview", type=Path)
    parser.add_argument("--backup", type=Path)
    parser.add_argument(
        "-l",
        "--latest",
        action="store_true",
        help="Discover the newest matching preview and backup for --project.",
    )
    parser.add_argument(
        "--curation-root",
        type=Path,
        default=Path("data/working/taxonomy_curation"),
    )
    parser.add_argument("-p", "--project", required=True)
    parser.add_argument("-e", "--env-file", type=Path, default=Path(".env"))
    parser.add_argument("-c", "--commit", action="store_true")
    return parser.parse_args()


def _resolve_input_paths(
    *,
    preview: Path | None,
    backup: Path | None,
    latest: bool,
    project: str,
    curation_root: Path,
) -> tuple[Path, Path]:
    if latest:
        if preview is not None or backup is not None:
            raise ValueError("Use either --latest or explicit --preview/--backup paths")
        round_dirs = sorted(
            path for path in curation_root.glob(f"*/{project}_round_*") if path.is_dir()
        )
        candidates = [
            path
            for round_dir in round_dirs
            for path in round_dir.glob(f"{project}_round_*_write_preview_*.tsv")
            if "_execution_" not in path.name
        ]
        if not candidates:
            raise ValueError(f"No write preview found for project {project!r}")
        preview = max(candidates, key=lambda path: path.stat().st_mtime)
        backup = preview.with_name(preview.name.replace("_write_preview_", "_directus_backup_"))
        if not backup.exists():
            raise ValueError(f"Matching backup not found for {preview}")
        print(f"Selected preview: {preview}")
        print(f"Selected backup: {backup}")
        return preview, backup

    if preview is None or backup is None:
        raise ValueError("Provide both --preview and --backup, or use --latest")
    return preview, backup


def _validate_inputs(
    preview_rows: list[dict[str, str]],
    backup_rows: list[dict[str, str]],
    *,
    expected_project: str,
) -> None:
    if not preview_rows:
        raise ValueError("The write preview is empty")
    wrong_projects = {
        row.get("project", "")
        for row in preview_rows
        if row.get("project", "") != expected_project
    }
    if wrong_projects:
        raise ValueError(
            f"Preview contains rows outside project {expected_project!r}: "
            + ", ".join(sorted(wrong_projects))
        )
    if any(row.get("preflight_status") != "ready" for row in preview_rows):
        raise ValueError("Every preview row must have preflight_status=ready")
    invalid_decisions = {
        row.get("decision", "")
        for row in preview_rows
        if row.get("decision", "")
        not in {"accept_proposal", "override", "clear_placeholder"}
    }
    if invalid_decisions:
        raise ValueError(
            "Every preview row must have an accepted decision; found: "
            + ", ".join(sorted(invalid_decisions))
        )

    backup_by_id = {row.get("field_data_id", ""): row for row in backup_rows}
    preview_ids = {row["field_data_id"] for row in preview_rows}
    if set(backup_by_id) != preview_ids:
        raise ValueError("The backup and preview do not cover the same Field_Data ids")
    for row in preview_rows:
        backup = backup_by_id[row["field_data_id"]]
        if backup.get("target_field") != "taxon_name_unified":
            raise ValueError("The backup contains an unexpected target field")
        if backup.get("old_value", "") != row.get("current_taxon_name_unified", ""):
            raise ValueError(
                f"Backup old value differs for Field_Data {row['field_data_id']}"
            )
        if backup.get("proposed_new_value", "") != row["new_taxon_name_unified"]:
            raise ValueError(
                f"Backup proposed value differs for Field_Data {row['field_data_id']}"
            )


def _fetch_current_rows(
    client: DirectusClient,
    *,
    ids: list[str],
    source_fields: set[str],
) -> list[dict[str, Any]]:
    fields = [
        "id",
        "sample_id",
        "qfield_project",
        "taxon_name_unified",
        *sorted(source_fields - {"taxon_name_unified"}),
    ]
    payload = client._get(
        "/items/Field_Data",
        {
            "limit": "-1",
            "fields": ",".join(fields),
            "filter[id][_in]": ",".join(ids),
        },
    )
    data = payload.get("data")
    if not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
        raise DirectusResponseError("Directus Field_Data validation did not return a row list")
    return data


def _require_update_permission(client: DirectusClient) -> None:
    payload = client._get("/permissions/me", {"limit": "-1"})
    permissions = payload.get("data")
    if not isinstance(permissions, dict):
        raise DirectusResponseError("Directus /permissions/me did not return an object")
    collection_permissions = permissions.get("Field_Data")
    update_permission = (
        collection_permissions.get("update")
        if isinstance(collection_permissions, dict)
        else None
    )
    access = update_permission.get("access") if isinstance(update_permission, dict) else None
    if access == "none":
        raise DirectusError("The configured Directus account cannot update Field_Data")


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return [
            {key: value or "" for key, value in row.items()}
            for row in csv.DictReader(handle, delimiter="\t")
        ]


def _write_tsv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


if __name__ == "__main__":
    try:
        main()
    except (DirectusError, DirectusResponseError, SettingsError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    except requests.RequestException as exc:
        raise SystemExit(f"Directus request failed: {exc}") from exc
