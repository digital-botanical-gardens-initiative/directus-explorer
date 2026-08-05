"""Compare a reviewed taxon manifest with Directus and prepare a write preview.

This command performs GET requests only. It creates a timestamped backup of the
current rows and refuses to mark changed or mismatched rows as ready.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

from directus_explorer.config import SettingsError, load_settings
from directus_explorer.directus import DirectusClient, DirectusError, DirectusResponseError
from directus_explorer.taxon_curation_preflight import build_taxon_update_preflight


def main() -> None:
    args = _parse_args()
    manifest_rows = _read_tsv(args.manifest)
    if not manifest_rows:
        raise SystemExit("The reviewed update manifest is empty")

    client = DirectusClient(load_settings(env_file=args.env_file))
    client._authenticate()
    current_rows = _fetch_current_rows(
        client,
        ids=[row["field_data_id"] for row in manifest_rows],
        source_fields={row["authoritative_source_field"] for row in manifest_rows},
    )
    current_by_id = {str(row["id"]): row for row in current_rows}
    backups, previews, conflicts = build_taxon_update_preflight(
        manifest_rows, current_by_id
    )

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    stem = args.manifest.name.removesuffix("_update_manifest.tsv")
    output_dir = args.output_dir or args.manifest.parent
    backup_path = output_dir / f"{stem}_directus_backup_{timestamp}.tsv"
    preview_path = output_dir / f"{stem}_write_preview_{timestamp}.tsv"
    conflicts_path = output_dir / f"{stem}_preflight_conflicts_{timestamp}.tsv"
    summary_path = output_dir / f"{stem}_preflight_{timestamp}.json"
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_tsv(backup_path, backups)
    _write_tsv(preview_path, previews)
    _write_tsv(conflicts_path, conflicts)
    summary_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "mode": "directus_get_only_preflight",
                "directus_writes": False,
                "manifest": str(args.manifest),
                "manifest_sha256": hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
                "requested_count": len(manifest_rows),
                "fetched_count": len(current_rows),
                "ready_count": len(previews),
                "conflict_count": len(conflicts),
                "conflicts_by_reason": dict(
                    sorted(Counter(row["conflict_reason"] for row in conflicts).items())
                ),
                "backup": str(backup_path),
                "backup_sha256": hashlib.sha256(backup_path.read_bytes()).hexdigest(),
                "write_preview": str(preview_path),
                "conflicts": str(conflicts_path),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Fetched {len(current_rows)} of {len(manifest_rows)} requested Directus rows")
    print(f"Ready: {len(previews)}; blocking conflicts: {len(conflicts)}")
    print(f"Backup: {backup_path}")
    print(f"Write preview: {preview_path}")
    print(f"Summary: {summary_path}")
    print("No Directus data was changed.")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--output-dir", type=Path)
    return parser.parse_args()


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
        raise DirectusResponseError("Directus Field_Data preflight did not return a row list")
    return data


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return [
            {key: value or "" for key, value in row.items()}
            for row in csv.DictReader(handle, delimiter="\t")
        ]


def _write_tsv(path: Path, rows: list[dict[str, Any]]) -> None:
    if rows:
        fieldnames = list(rows[0])
    else:
        fieldnames = ["message"]
        rows = [{"message": "no_rows"}]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    try:
        main()
    except (DirectusError, DirectusResponseError, SettingsError) as exc:
        raise SystemExit(str(exc)) from exc
    except requests.RequestException as exc:
        raise SystemExit(f"Directus request failed: {exc}") from exc
