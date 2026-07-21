"""Populate a single unified taxon-name field on Directus Field_Data rows.

By default this is a dry run. It reads the integrity report
field_data_unified_taxon.tsv and updates only rows where:

* unified_taxon_name is non-empty
* taxon_unified_conflict is empty
* the target Directus field is empty, unless --overwrite is provided

The target field must already exist in Directus unless --create-field is used.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

import requests

from directus_explorer.config import load_settings
from directus_explorer.directus import DirectusClient, DirectusError, DirectusResponseError

DEFAULT_REPORT = Path("data/output/directus_integrity/field_data_unified_taxon.tsv")
OUTPUT_DIR = Path("data/output/directus_integrity")
DEFAULT_MANIFEST = OUTPUT_DIR / "field_data_unified_taxon_update_manifest.tsv"
DEFAULT_BACKUP = OUTPUT_DIR / "field_data_unified_taxon_update_backup.tsv"
DEFAULT_SKIPPED = OUTPUT_DIR / "field_data_unified_taxon_update_skipped.tsv"
DEFAULT_SUMMARY = OUTPUT_DIR / "field_data_unified_taxon_update_summary.json"
DEFAULT_TARGET_FIELD = "taxon_name_unified"


def main() -> None:
    args = _parse_args()
    report_rows = _read_tsv(args.report)

    client = DirectusClient(load_settings(env_file=args.env_file))
    client._authenticate()

    existing_fields = _field_names(client, "Field_Data")
    target_field_exists = args.target_field in existing_fields
    if not target_field_exists:
        if not args.create_field:
            raise SystemExit(
                f"Field_Data.{args.target_field} does not exist. "
                f"Create it in Directus or rerun with --create-field using an admin account."
            )
        if args.commit:
            _create_string_field(client, collection="Field_Data", field=args.target_field)
            existing_fields = _field_names(client, "Field_Data")
            if args.target_field not in existing_fields:
                raise DirectusResponseError(
                    f"Directus did not expose Field_Data.{args.target_field} after creation."
                )
            target_field_exists = True
        else:
            print(
                f"Dry run: would create Field_Data.{args.target_field} as a string field "
                "because --create-field was provided."
            )

    field_projection = f"id,{args.target_field}" if target_field_exists else "id"
    field_rows = client._get_items(collection="Field_Data", fields=field_projection)
    field_by_id = {_int_id(row): row for row in field_rows}

    manifest_rows, skipped_rows = _build_manifest(
        report_rows=report_rows,
        field_by_id=field_by_id,
        target_field=args.target_field,
        overwrite=args.overwrite,
    )
    _write_tsv(args.manifest, manifest_rows)
    _write_tsv(args.skipped, skipped_rows)
    _write_backup(args.backup, manifest_rows, field_by_id, args.target_field)
    _write_summary(args.summary, manifest_rows, skipped_rows, args.target_field)

    if not args.commit:
        print(
            f"Dry run OK. Prepared {len(manifest_rows)} updates for "
            f"Field_Data.{args.target_field}; skipped {len(skipped_rows)} rows. "
            "Re-run with --commit to patch Directus."
        )
        return

    _require_update_permission(client, collection="Field_Data")
    updated_ids = _patch_unified_taxon(client, manifest_rows, args.target_field)
    print(f"Updated {len(updated_ids)} Field_Data rows in {args.target_field}.")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--target-field", default=DEFAULT_TARGET_FIELD)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--backup", type=Path, default=DEFAULT_BACKUP)
    parser.add_argument("--skipped", type=Path, default=DEFAULT_SKIPPED)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--create-field", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--commit", action="store_true")
    return parser.parse_args()


def _field_names(client: DirectusClient, collection: str) -> set[str]:
    payload = client._get(f"/fields/{collection}", {"limit": "-1"})
    fields = payload.get("data")
    if not isinstance(fields, list):
        raise DirectusResponseError(f"Directus /fields/{collection} did not return a list.")
    return {field["field"] for field in fields if isinstance(field, dict) and "field" in field}


def _create_string_field(client: DirectusClient, *, collection: str, field: str) -> None:
    response = client._session.post(
        client._url(f"/fields/{collection}"),
        json={
            "field": field,
            "type": "string",
            "schema": {"is_nullable": True},
            "meta": {
                "interface": "input",
                "width": "full",
                "note": "Unified taxon name populated from non-conflicting taxon-like fields.",
            },
        },
        timeout=(10, 60),
    )
    if response.status_code not in {200, 201}:
        raise DirectusError(
            f"Directus POST /fields/{collection} failed with status "
            f"{response.status_code}: {response.text}"
        )


def _build_manifest(
    *,
    report_rows: list[dict[str, str]],
    field_by_id: dict[int, dict[str, Any]],
    target_field: str,
    overwrite: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    manifest_rows: list[dict[str, Any]] = []
    skipped_rows: list[dict[str, Any]] = []
    for row in report_rows:
        field_data_id = int(row["field_data_id"])
        unified_name = row.get("unified_taxon_name", "").strip()
        conflict = row.get("taxon_unified_conflict", "").strip()
        current_row = field_by_id.get(field_data_id, {})
        current_value = _clean(current_row.get(target_field))

        skip_reason = ""
        if not unified_name:
            skip_reason = "missing_unified_taxon_name"
        elif conflict:
            skip_reason = "taxon_unified_conflict"
        elif current_value and not overwrite:
            skip_reason = "target_field_already_populated"

        output_row = {
            "field_data_id": field_data_id,
            "sample_id": row.get("sample_id", ""),
            "project": row.get("project", ""),
            "target_field": target_field,
            "current_value": current_value,
            "new_value": unified_name,
            "unified_taxon_source": row.get("unified_taxon_source", ""),
            "unified_taxon_status": row.get("unified_taxon_status", ""),
            "taxon_candidate_fields_present": row.get("taxon_candidate_fields_present", ""),
            "taxon_unified_conflict": conflict,
            "skip_reason": skip_reason,
        }
        if skip_reason:
            skipped_rows.append(output_row)
        else:
            manifest_rows.append(output_row)
    return manifest_rows, skipped_rows


def _write_backup(
    path: Path,
    manifest_rows: list[dict[str, Any]],
    field_by_id: dict[int, dict[str, Any]],
    target_field: str,
) -> None:
    rows = []
    for manifest_row in manifest_rows:
        field_data_id = int(manifest_row["field_data_id"])
        directus_row = field_by_id.get(field_data_id, {})
        rows.append(
            {
                "backup_kind": "field_data_unified_taxon_update",
                "field_data_id": field_data_id,
                "target_field": target_field,
                "old_value": _clean(directus_row.get(target_field)),
                "new_value": manifest_row["new_value"],
                "directus_row_json": json.dumps(directus_row, ensure_ascii=False, sort_keys=True),
            }
        )
    _write_tsv(path, rows)


def _write_summary(
    path: Path,
    manifest_rows: list[dict[str, Any]],
    skipped_rows: list[dict[str, Any]],
    target_field: str,
) -> None:
    summary = {
        "target_field": target_field,
        "update_count": len(manifest_rows),
        "skipped_count": len(skipped_rows),
        "update_projects": dict(Counter(row["project"] for row in manifest_rows)),
        "skipped_reasons": dict(Counter(row["skip_reason"] for row in skipped_rows)),
        "update_sources": dict(Counter(row["unified_taxon_source"] for row in manifest_rows)),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")


def _require_update_permission(client: DirectusClient, collection: str) -> None:
    payload = client._get("/permissions/me", {"limit": "-1"})
    permissions = payload.get("data")
    if not isinstance(permissions, dict):
        raise DirectusResponseError("Directus /permissions/me did not return an object.")
    collection_permissions = permissions.get(collection)
    update_permission = (
        collection_permissions.get("update") if isinstance(collection_permissions, dict) else None
    )
    access = update_permission.get("access") if isinstance(update_permission, dict) else None
    if access == "none":
        raise DirectusError(
            f"Current Directus user has {collection}.update access set to 'none'. "
            "Use an env file with an admin account or ask an admin to grant update permission."
        )


def _patch_unified_taxon(
    client: DirectusClient,
    manifest_rows: list[dict[str, Any]],
    target_field: str,
) -> list[int]:
    updated_ids: list[int] = []
    for row in manifest_rows:
        field_data_id = int(row["field_data_id"])
        response = client._session.patch(
            client._url(f"/items/Field_Data/{field_data_id}"),
            json={target_field: row["new_value"]},
            timeout=(10, 60),
        )
        if response.status_code not in {200, 204}:
            raise DirectusError(
                f"Directus PATCH /items/Field_Data/{field_data_id} failed with "
                f"status {response.status_code}: {response.text}"
            )
        updated_ids.append(field_data_id)
    return updated_ids


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _write_tsv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if rows:
        fieldnames = list(rows[0])
    else:
        fieldnames = ("message",)
        rows = [{"message": "no_rows"}]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def _int_id(row: dict[str, Any]) -> int:
    return int(str(row["id"]))


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


if __name__ == "__main__":
    try:
        main()
    except (DirectusError, DirectusResponseError) as exc:
        raise SystemExit(str(exc)) from exc
    except requests.RequestException as exc:
        raise SystemExit(f"Directus request failed: {exc}") from exc
