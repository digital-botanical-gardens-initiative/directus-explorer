"""Prepare or execute deletion of high-confidence duplicate JBC Field_Data rows.

The script uses the strict-match manifest produced during the JBC inspection,
backs up the full Directus rows to TSV, verifies that none are referenced from
Dried_Samples_Data, and only deletes rows when --commit is provided.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import requests

from directus_explorer.config import load_settings
from directus_explorer.directus import DirectusClient, DirectusError, DirectusResponseError

DEFAULT_MANIFEST = Path("data/output/jbc_inspection/safe_delete_no_downstream_references.tsv")
DEFAULT_BACKUP = Path("data/output/jbc_inspection/jbc_field_data_delete_381_backup.tsv")
DEFAULT_DELETE_MANIFEST = Path("data/output/jbc_inspection/jbc_field_data_delete_381_manifest.tsv")
EXPECTED_COUNT = 381


def main() -> None:
    args = _parse_args()

    client = DirectusClient(load_settings(env_file=args.env_file))
    client._authenticate()

    manifest_rows = _read_manifest(args.manifest)
    candidate_ids = [_int_cell(row, "nonstandard_field_data_id") for row in manifest_rows]
    if len(candidate_ids) != args.expected_count:
        raise SystemExit(
            f"Refusing to continue: manifest contains {len(candidate_ids)} rows, "
            f"expected {args.expected_count}."
        )
    if len(set(candidate_ids)) != len(candidate_ids):
        raise SystemExit(
            "Refusing to continue: deletion manifest contains duplicate Field_Data ids."
        )

    field_rows = _get_items(client, "Field_Data", fields="*")
    field_by_id = {_int_id(row): row for row in field_rows}
    missing_ids = sorted(
        candidate_id for candidate_id in candidate_ids if candidate_id not in field_by_id
    )
    if missing_ids:
        raise SystemExit(
            "Refusing to continue: candidate ids missing from Directus Field_Data: "
            + ", ".join(str(value) for value in missing_ids)
        )

    dried_rows = _get_items(client, "Dried_Samples_Data", fields="id,field_data")
    referenced_ids = _referenced_field_data_ids(dried_rows)
    blocked_ids = sorted(set(candidate_ids) & referenced_ids)
    if blocked_ids:
        raise SystemExit(
            "Refusing to continue: candidate ids are referenced by Dried_Samples_Data.field_data: "
            + ", ".join(str(value) for value in blocked_ids)
        )

    _write_backup(args.backup, manifest_rows, field_by_id)
    _write_delete_manifest(args.delete_manifest, manifest_rows)

    if not args.commit:
        print(
            f"Dry run OK. Backed up {len(candidate_ids)} rows to {args.backup}. "
            f"No Dried_Samples_Data references found. Re-run with --commit to delete."
        )
        return

    _require_delete_permission(client, collection="Field_Data")
    deleted_ids = _delete_field_data_rows(client, candidate_ids)
    print(
        f"Deleted {len(deleted_ids)} Field_Data rows. Backup: {args.backup}. "
        f"Deletion manifest: {args.delete_manifest}."
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--backup", type=Path, default=DEFAULT_BACKUP)
    parser.add_argument("--delete-manifest", type=Path, default=DEFAULT_DELETE_MANIFEST)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--expected-count", type=int, default=EXPECTED_COUNT)
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Actually delete Field_Data rows. Without this flag, only writes backup files.",
    )
    return parser.parse_args()


def _read_manifest(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        rows = list(reader)
    if reader.fieldnames is None or "nonstandard_field_data_id" not in reader.fieldnames:
        raise SystemExit(f"Manifest {path} does not contain nonstandard_field_data_id.")
    return rows


def _int_cell(row: dict[str, str], key: str) -> int:
    try:
        return int(row[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise SystemExit(f"Invalid integer value for {key}: {row.get(key)!r}") from exc


def _get_items(client: DirectusClient, collection: str, fields: str) -> list[dict[str, Any]]:
    return client._get_items(collection=collection, fields=fields)


def _int_id(row: dict[str, Any]) -> int:
    try:
        return int(str(row["id"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise DirectusResponseError(f"Directus row has invalid id: {row!r}") from exc


def _referenced_field_data_ids(rows: Iterable[dict[str, Any]]) -> set[int]:
    references: set[int] = set()
    for row in rows:
        value = row.get("field_data")
        if isinstance(value, dict):
            value = value.get("id")
        if value is None:
            continue
        try:
            references.add(int(str(value)))
        except ValueError as exc:
            raise DirectusResponseError(
                f"Invalid Dried_Samples_Data.field_data value: {value!r}"
            ) from exc
    return references


def _write_backup(
    path: Path,
    manifest_rows: list[dict[str, str]],
    field_by_id: dict[int, dict[str, Any]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest_fields = list(manifest_rows[0])
    directus_fields = sorted({key for row in field_by_id.values() for key in row})
    fieldnames = [
        "backup_kind",
        *manifest_fields,
        *[f"field.{field}" for field in directus_fields],
        "directus_row_json",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fieldnames,
            delimiter="\t",
            extrasaction="ignore",
        )
        writer.writeheader()
        for manifest_row in manifest_rows:
            field_id = _int_cell(manifest_row, "nonstandard_field_data_id")
            directus_row = field_by_id[field_id]
            output_row = {
                "backup_kind": "field_data_delete_candidate",
                **manifest_row,
                **{
                    f"field.{key}": _tsv_value(value)
                    for key, value in directus_row.items()
                },
                "directus_row_json": json.dumps(directus_row, ensure_ascii=False, sort_keys=True),
            }
            writer.writerow(output_row)


def _write_delete_manifest(path: Path, manifest_rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "field_data_id",
        "sample_id",
        "canonical_field_data_ids",
        "canonical_sample_ids",
        "rationale",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        for row in manifest_rows:
            writer.writerow(
                {
                    "field_data_id": row["nonstandard_field_data_id"],
                    "sample_id": row["nonstandard_sample_id"],
                    "canonical_field_data_ids": row["canonical_field_data_ids"],
                    "canonical_sample_ids": row["canonical_sample_ids"],
                    "rationale": row["rationale"],
                }
            )


def _tsv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, int | float | bool):
        return str(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _require_delete_permission(client: DirectusClient, collection: str) -> None:
    payload = client._get("/permissions/me", {"limit": "-1"})
    permissions = payload.get("data")
    if not isinstance(permissions, dict):
        raise DirectusResponseError("Directus /permissions/me did not return an object.")

    collection_permissions = permissions.get(collection)
    if not isinstance(collection_permissions, dict):
        raise DirectusError(
            f"Current Directus user has no visible permissions for {collection}. "
            "Use an env file with an admin account or a role allowed to delete this collection."
        )

    delete_permission = collection_permissions.get("delete")
    access = delete_permission.get("access") if isinstance(delete_permission, dict) else None
    if access == "none":
        raise DirectusError(
            f"Current Directus user has {collection}.delete access set to 'none'. "
            "Use an env file with an admin account or ask an admin to grant delete permission."
        )


def _delete_field_data_rows(client: DirectusClient, candidate_ids: list[int]) -> list[int]:
    deleted_ids: list[int] = []
    for field_data_id in candidate_ids:
        response = client._session.delete(
            client._url(f"/items/Field_Data/{field_data_id}"),
            timeout=(10, 60),
        )
        if response.status_code not in {200, 204}:
            raise DirectusError(
                f"Directus DELETE /items/Field_Data/{field_data_id} failed with "
                f"status {response.status_code}: {response.text}"
            )
        deleted_ids.append(field_data_id)
    return deleted_ids


if __name__ == "__main__":
    try:
        main()
    except (DirectusError, DirectusResponseError) as exc:
        raise SystemExit(str(exc)) from exc
    except requests.RequestException as exc:
        raise SystemExit(f"Directus request failed: {exc}") from exc
