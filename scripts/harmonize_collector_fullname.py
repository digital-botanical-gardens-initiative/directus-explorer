"""Audit and optionally harmonize Directus Field_Data.collector_fullname.

Names are grouped without regard to case, accents, punctuation, or token order.
Only groups containing multiple stored spellings are changed, using their most
frequent spelling as the canonical value. The command is a GET-only dry run
unless --commit is supplied.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

from directus_explorer.collector_harmonization import (
    build_collector_harmonization_plan,
    validate_collector_changes,
)
from directus_explorer.config import SettingsError, load_settings
from directus_explorer.directus import DirectusClient, DirectusError, DirectusResponseError

OUTPUT_DIR = Path("data/working/collector_harmonization")
FIELDS = "id,sample_id,qfield_project,collector_fullname,collector_orcid,collector_inat"


def main() -> None:
    args = _parse_args()
    client = DirectusClient(load_settings(env_file=args.env_file))
    client._authenticate()
    rows = client._get_items(collection="Field_Data", fields=FIELDS)
    groups, changes = build_collector_harmonization_plan(rows)
    current_by_id = {str(row["id"]): row for row in rows}
    ready, conflicts = validate_collector_changes(changes, current_by_id)

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    groups_path = output_dir / f"collector_variant_groups_{timestamp}.tsv"
    backup_path = output_dir / f"collector_directus_backup_{timestamp}.tsv"
    preview_path = output_dir / f"collector_write_preview_{timestamp}.tsv"
    conflicts_path = output_dir / f"collector_conflicts_{timestamp}.tsv"
    summary_path = output_dir / f"collector_summary_{timestamp}.json"
    _write_tsv(
        groups_path,
        groups,
        ["identity_key", "canonical_collector_fullname", "variants", "row_count", "update_count"],
    )
    _write_tsv(backup_path, changes, list(changes[0]) if changes else ["field_data_id"])
    _write_tsv(preview_path, ready, list(ready[0]) if ready else ["field_data_id"])
    _write_tsv(conflicts_path, conflicts, list(conflicts[0]) if conflicts else ["field_data_id"])

    summary: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "commit" if args.commit else "directus_get_only_dry_run",
        "rows_audited": len(rows),
        "nonempty_rows": sum(
            bool(str(row.get("collector_fullname") or "").strip()) for row in rows
        ),
        "variant_group_count": len(groups),
        "planned_update_count": len(changes),
        "ready_count": len(ready),
        "conflict_count": len(conflicts),
        "updates_by_old_value": dict(
            sorted(Counter(row["current_collector_fullname"] for row in ready).items())
        ),
        "groups": str(groups_path),
        "backup": str(backup_path),
        "write_preview": str(preview_path),
        "conflicts": str(conflicts_path),
    }

    if args.commit:
        if conflicts:
            raise DirectusError("Live-state conflicts found; no updates were attempted")
        journal_path = output_dir / f"collector_execution_{timestamp}.tsv"
        journal: list[dict[str, str]] = []
        if ready:
            _require_update_permission(client)
            for change in ready:
                item_id = int(change["field_data_id"])
                updated = client._patch_item(
                    collection="Field_Data",
                    item_id=item_id,
                    payload={"collector_fullname": change["new_collector_fullname"]},
                )
                if _text(updated.get("collector_fullname")) != change["new_collector_fullname"]:
                    raise DirectusResponseError(
                        f"Unexpected collector_fullname returned for Field_Data {item_id}"
                    )
                journal.append(
                    {
                        "updated_at": datetime.now(UTC).isoformat(),
                        **{
                            key: value for key, value in change.items() if key != "preflight_status"
                        },
                        "status": "updated",
                    }
                )
                _write_tsv(journal_path, journal, list(journal[0]))

            verified = _fetch_rows(client, [row["field_data_id"] for row in ready])
            verified_by_id = {str(row["id"]): row for row in verified}
            failures = [
                row["field_data_id"]
                for row in ready
                if _text(verified_by_id.get(row["field_data_id"], {}).get("collector_fullname"))
                != row["new_collector_fullname"]
            ]
            if failures:
                raise DirectusResponseError(
                    "Post-update verification failed for ids: " + ", ".join(failures)
                )
        summary["updated_count"] = len(journal)
        summary["execution_journal"] = str(journal_path) if journal else None

    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"Audited {len(rows)} rows; {len(ready)} updates ready; {len(conflicts)} conflicts.")
    print(f"Summary: {summary_path}")
    if args.commit and ready:
        print(f"Updated and verified {len(ready)} Field_Data rows.")
    elif not ready:
        print("Collector names are already harmonized; no Directus data was changed.")
    else:
        print(
            "No Directus data was changed. Re-run with --commit after reviewing "
            "the preview and backup."
        )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--commit", action="store_true")
    return parser.parse_args()


def _fetch_rows(client: DirectusClient, ids: list[str]) -> list[dict[str, Any]]:
    payload = client._get(
        "/items/Field_Data",
        {"limit": "-1", "fields": FIELDS, "filter[id][_in]": ",".join(ids)},
    )
    data = payload.get("data")
    if not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
        raise DirectusResponseError("Directus Field_Data validation did not return a row list")
    return data


def _require_update_permission(client: DirectusClient) -> None:
    payload = client._get("/permissions/me", {"limit": "-1"})
    permissions = payload.get("data")
    collection = permissions.get("Field_Data") if isinstance(permissions, dict) else None
    update = collection.get("update") if isinstance(collection, dict) else None
    if not isinstance(update, dict) or update.get("access") == "none":
        raise DirectusError("The configured Directus account cannot update Field_Data")


def _write_tsv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


if __name__ == "__main__":
    try:
        main()
    except (DirectusError, DirectusResponseError, SettingsError) as exc:
        raise SystemExit(str(exc)) from exc
    except requests.RequestException as exc:
        raise SystemExit(f"Directus request failed: {exc}") from exc
