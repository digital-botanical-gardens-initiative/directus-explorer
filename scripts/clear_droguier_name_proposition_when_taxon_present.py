"""Clear Droguier name_proposition when taxon_name is already populated.

This is a targeted Field_Data cleanup:

* qfield_project must be droguier_jbn
* taxon_name must be non-empty
* name_proposition must be non-empty

The script backs up the full affected rows and is dry-run by default. Use
--commit with an update-capable Directus account to patch name_proposition to
null.
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

PROJECT = "droguier_jbn"
OUTPUT_DIR = Path("data/output/directus_integrity")
DEFAULT_BACKUP = OUTPUT_DIR / "droguier_name_proposition_clear_backup.tsv"
DEFAULT_MANIFEST = OUTPUT_DIR / "droguier_name_proposition_clear_manifest.tsv"
DEFAULT_SUMMARY = OUTPUT_DIR / "droguier_name_proposition_clear_summary.json"


def main() -> None:
    args = _parse_args()

    client = DirectusClient(load_settings(env_file=args.env_file))
    client._authenticate()

    field_rows = client._get_items(collection="Field_Data", fields="*")
    target_rows = [
        row
        for row in field_rows
        if _clean(row.get("qfield_project")) == PROJECT
        and _clean(row.get("taxon_name"))
        and _clean(row.get("name_proposition"))
    ]

    if args.expected_count is not None and len(target_rows) != args.expected_count:
        raise SystemExit(
            f"Refusing to continue: found {len(target_rows)} target rows, "
            f"expected {args.expected_count}."
        )

    _write_backup(args.backup, target_rows)
    _write_manifest(args.manifest, target_rows)
    _write_summary(args.summary, target_rows)

    if not args.commit:
        print(
            f"Dry run OK. Prepared {len(target_rows)} Field_Data updates for {PROJECT}. "
            f"Backup: {args.backup}. Re-run with --commit to clear name_proposition."
        )
        return

    _require_update_permission(client, collection="Field_Data")
    updated_ids = _patch_rows(client, target_rows)
    print(f"Cleared name_proposition for {len(updated_ids)} {PROJECT} Field_Data rows.")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup", type=Path, default=DEFAULT_BACKUP)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--expected-count", type=int, default=None)
    parser.add_argument("--commit", action="store_true")
    return parser.parse_args()


def _write_backup(path: Path, rows: list[dict[str, Any]]) -> None:
    directus_fields = sorted({key for row in rows for key in row})
    fieldnames = [
        "backup_kind",
        *[f"field.{field}" for field in directus_fields],
        "directus_row_json",
    ]
    output_rows = [
        {
            "backup_kind": "droguier_name_proposition_clear",
            **{f"field.{key}": _tsv_value(value) for key, value in row.items()},
            "directus_row_json": json.dumps(row, ensure_ascii=False, sort_keys=True),
        }
        for row in rows
    ]
    _write_tsv_with_fieldnames(path, fieldnames, output_rows)


def _write_manifest(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = (
        "field_data_id",
        "sample_id",
        "qfield_project",
        "taxon_name",
        "old_name_proposition",
        "new_name_proposition",
    )
    output_rows = [
        {
            "field_data_id": row.get("id"),
            "sample_id": row.get("sample_id"),
            "qfield_project": row.get("qfield_project"),
            "taxon_name": row.get("taxon_name"),
            "old_name_proposition": row.get("name_proposition"),
            "new_name_proposition": "",
        }
        for row in rows
    ]
    _write_tsv_with_fieldnames(path, fieldnames, output_rows)


def _write_summary(path: Path, rows: list[dict[str, Any]]) -> None:
    summary = {
        "project": PROJECT,
        "target_count": len(rows),
        "old_name_proposition_counts": dict(
            Counter(_clean(row.get("name_proposition")) for row in rows)
        ),
        "taxon_name_counts": dict(Counter(_clean(row.get("taxon_name")) for row in rows)),
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


def _patch_rows(client: DirectusClient, rows: list[dict[str, Any]]) -> list[int]:
    updated_ids: list[int] = []
    for row in rows:
        field_data_id = int(str(row["id"]))
        response = client._session.patch(
            client._url(f"/items/Field_Data/{field_data_id}"),
            json={"name_proposition": None},
            timeout=(10, 60),
        )
        if response.status_code not in {200, 204}:
            raise DirectusError(
                f"Directus PATCH /items/Field_Data/{field_data_id} failed with "
                f"status {response.status_code}: {response.text}"
            )
        updated_ids.append(field_data_id)
    return updated_ids


def _write_tsv_with_fieldnames(
    path: Path,
    fieldnames: list[str] | tuple[str, ...],
    rows: list[dict[str, Any]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _tsv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, int | float | bool):
        return str(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


if __name__ == "__main__":
    try:
        main()
    except (DirectusError, DirectusResponseError) as exc:
        raise SystemExit(str(exc)) from exc
    except requests.RequestException as exc:
        raise SystemExit(f"Directus request failed: {exc}") from exc
