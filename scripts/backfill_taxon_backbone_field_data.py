"""Preflight or apply the safe CoL metadata backfill to Field_Data.

The default performs GET requests only. --commit re-runs the same checks, writes
a timestamped backup, patches in resumable batches, and verifies all mappings.
It never changes taxon_name_unified.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

from directus_explorer.config import SettingsError, load_settings
from directus_explorer.directus import DirectusClient, DirectusError, DirectusResponseError
from directus_explorer.taxon_backbone_directus import plan_field_data_backfill

DEFAULT_BACKFILL = Path(
    "data/working/taxonomy_backbone/col_2026_07_17_xr/field_data_safe_backfill.tsv"
)
FIELD_PROJECTION = (
    "id,sample_id,qfield_project,taxon_name_unified,taxon_col_id,"
    "taxon_resolution_status,taxon_backbone_version,taxon_ref,taxon_parent_col_ids"
)


def main() -> None:
    args = _parse_args()
    backfill = _read_tsv(args.backfill)
    client = DirectusClient(load_settings(env_file=args.env_file))
    client._authenticate()
    current = client._get_items(collection="Field_Data", fields=FIELD_PROJECTION)
    taxa = client._get_items(collection="Taxa", fields="id,col_id")
    ready, exact, conflicts = plan_field_data_backfill(backfill, current, taxa)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_dir = args.backfill.parent
    backup_path = output_dir / f"field_data_backfill_backup_{timestamp}.tsv"
    preview_path = output_dir / f"field_data_backfill_preview_{timestamp}.tsv"
    conflicts_path = output_dir / f"field_data_backfill_conflicts_{timestamp}.tsv"
    summary_path = output_dir / f"field_data_backfill_preflight_{timestamp}.json"
    _write_tsv(backup_path, [_backup_row(row) for row in ready])
    _write_tsv(preview_path, ready)
    _write_tsv(conflicts_path, conflicts)
    summary_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "mode": "commit" if args.commit else "directus_get_only_preflight",
                "backfill": str(args.backfill),
                "requested_count": len(backfill),
                "ready_count": len(ready),
                "already_exact_count": len(exact),
                "conflict_count": len(conflicts),
                "backup": str(backup_path),
                "preview": str(preview_path),
                "conflicts": str(conflicts_path),
                "taxon_name_unified_changes": 0,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Requested: {len(backfill)}; already exact: {len(exact)}")
    print(f"Ready: {len(ready)}; blocking conflicts: {len(conflicts)}")
    print(f"Backup: {backup_path}")
    print(f"Preview: {preview_path}")
    print(f"Summary: {summary_path}")
    if conflicts:
        raise SystemExit(f"Conflicts: {conflicts_path}. No Field_Data rows were changed.")
    if not args.commit:
        print("No Directus data was changed. Re-run with --commit to apply ready rows.")
        return

    journal_path = output_dir / f"field_data_backfill_execution_{timestamp}.tsv"
    journal: list[dict[str, str]] = []
    for offset in range(0, len(ready), args.batch_size):
        chunk = ready[offset : offset + args.batch_size]
        returned = _patch_batch(client, chunk)
        returned_by_id = {str(row.get("id", "")): row for row in returned}
        for row in chunk:
            field_id = row["field_data_id"]
            if field_id not in returned_by_id:
                raise DirectusResponseError(
                    f"Directus batch response omitted Field_Data {field_id}"
                )
            journal.append(
                {
                    "updated_at": datetime.now(UTC).isoformat(),
                    "field_data_id": field_id,
                    "sample_id": row["sample_id"],
                    "taxon_col_id": row["new_taxon_col_id"],
                    "taxon_ref": row["new_taxon_ref"],
                    "taxon_parent_col_ids": row.get(
                        "new_taxon_parent_col_ids", ""
                    ),
                    "status": "updated",
                }
            )
        _write_tsv(journal_path, journal)
        print(f"Updated {len(journal)}/{len(ready)} ready Field_Data rows", flush=True)

    verified_current = client._get_items(collection="Field_Data", fields=FIELD_PROJECTION)
    remaining, verified_exact, verify_conflicts = plan_field_data_backfill(
        backfill, verified_current, taxa
    )
    if remaining or verify_conflicts or len(verified_exact) != len(backfill):
        raise DirectusResponseError(
            "Post-backfill verification failed: "
            f"ready={len(remaining)}, conflicts={len(verify_conflicts)}, "
            f"exact={len(verified_exact)}"
        )
    print(f"Updated {len(journal)} and verified all {len(backfill)} safe Field_Data rows.")
    print(f"Execution journal: {journal_path}")


def _patch_batch(client: DirectusClient, rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    payload = []
    for row in rows:
        item: dict[str, Any] = {
            "id": int(row["field_data_id"]),
            "taxon_col_id": row["new_taxon_col_id"] or None,
            "taxon_resolution_status": row["new_taxon_resolution_status"],
            "taxon_backbone_version": row["new_taxon_backbone_version"],
            "taxon_ref": int(row["new_taxon_ref"]) if row["new_taxon_ref"] else None,
        }
        if "new_taxon_parent_col_ids" in row:
            item["taxon_parent_col_ids"] = json.loads(row["new_taxon_parent_col_ids"])
        payload.append(item)
    response = client._session.patch(
        client._url("/items/Field_Data"), json=payload, timeout=(10, 120)
    )
    if response.status_code != 200:
        raise DirectusError(
            f"Directus batch PATCH Field_Data failed with status "
            f"{response.status_code}: {response.text}"
        )
    try:
        response_payload = response.json()
    except ValueError as exc:
        raise DirectusResponseError("Directus batch response was not valid JSON") from exc
    data = response_payload.get("data") if isinstance(response_payload, dict) else None
    if not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
        raise DirectusResponseError("Directus batch response did not contain a row list")
    return data


def _backup_row(row: dict[str, str]) -> dict[str, str]:
    return {
        "field_data_id": row["field_data_id"],
        "sample_id": row["sample_id"],
        "taxon_name_unified": row["current_taxon_name_unified"],
        "taxon_col_id": row["current_taxon_col_id"],
        "taxon_resolution_status": row["current_taxon_resolution_status"],
        "taxon_backbone_version": row["current_taxon_backbone_version"],
        "taxon_ref": row["current_taxon_ref"],
        "taxon_parent_col_ids": row.get("current_taxon_parent_col_ids", ""),
    }


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return [
            {key: value or "" for key, value in row.items()}
            for row in csv.DictReader(handle, delimiter="\t")
        ]


def _write_tsv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        rows = [{"message": "no_rows"}]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backfill", type=Path, default=DEFAULT_BACKFILL)
    parser.add_argument("--env-file", type=Path, default=Path(".env.admin"))
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()
    if args.batch_size < 1 or args.batch_size > 500:
        parser.error("--batch-size must be between 1 and 500")
    return args


if __name__ == "__main__":
    try:
        main()
    except (DirectusError, DirectusResponseError, SettingsError) as exc:
        raise SystemExit(str(exc)) from exc
    except requests.RequestException as exc:
        raise SystemExit(f"Directus request failed: {exc}") from exc
