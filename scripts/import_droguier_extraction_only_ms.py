"""Import Droguier MS_Data rows directly on extraction containers.

This importer is intentionally narrower than the generic importer. It reads the
Droguier readiness TSV and imports only rows with:

    recommendation == possible_with_extraction_only_import_after_schema_decision

That excludes duplicate sample IDs, rows missing Field_Data, rows missing an
extraction container, and rows where MS_Data already exists.
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

DEFAULT_READINESS = Path(
    "data/output/droguier/complete/droguier_metadata_complete_ms_push_readiness.tsv"
)
OUTPUT_DIR = Path("data/output/droguier/complete")
DEFAULT_MANIFEST = OUTPUT_DIR / "droguier_extraction_only_ms_import_manifest.tsv"
DEFAULT_SKIPPED = OUTPUT_DIR / "droguier_extraction_only_ms_import_skipped.tsv"
DEFAULT_SUMMARY = OUTPUT_DIR / "droguier_extraction_only_ms_import_summary.json"
READY_RECOMMENDATION = "possible_with_extraction_only_import_after_schema_decision"


def main() -> None:
    args = _parse_args()
    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit must be greater than zero")

    rows = _read_tsv(args.readiness)
    selected_rows = [row for row in rows if row.get("recommendation") == READY_RECOMMENDATION]
    if args.expected_count is not None and len(selected_rows) != args.expected_count:
        raise SystemExit(
            f"Refusing to continue: selected {len(selected_rows)} rows, "
            f"expected {args.expected_count}."
        )
    if args.limit is not None:
        selected_rows = selected_rows[: args.limit]

    client = DirectusClient(load_settings(env_file=args.env_file))
    client._authenticate()

    extraction_rows = client._get_items(
        collection="Extraction_Data",
        fields="id,sample_container.id,sample_container.container_id",
    )
    ms_rows = client._get_items(collection="MS_Data", fields="id,filename")
    extraction_container_ids = _extraction_container_ids(extraction_rows)
    existing_ms_filenames = {_clean(row.get("filename")) for row in ms_rows}

    manifest_rows: list[dict[str, Any]] = []
    skipped_rows: list[dict[str, Any]] = []
    for row in selected_rows:
        filename = _clean(row.get("filename"))
        container_code = _clean(row.get("container_id"))
        parent_container_id = extraction_container_ids.get(container_code)
        reason = ""
        if not filename:
            reason = "missing_filename"
        elif filename in existing_ms_filenames:
            reason = "ms_data_already_exists"
        elif parent_container_id is None:
            reason = "extraction_container_not_resolved"

        output_row = {
            "filename": filename,
            "sample_id": row.get("sample_id", ""),
            "container_id": container_code,
            "parent_sample_container": parent_container_id or "",
            "injection_volume": args.injection_volume,
            "injection_volume_unit": args.injection_volume_unit_id,
            "injection_method": args.injection_method_id,
            "instrument_used": args.instrument_id,
            "status": args.status,
            "batch": args.batch_id or "",
            "skip_reason": reason,
        }
        if reason:
            skipped_rows.append(output_row)
        else:
            manifest_rows.append(output_row)

    _write_tsv(args.manifest, manifest_rows)
    _write_tsv(args.skipped, skipped_rows)
    _write_summary(args.summary, manifest_rows, skipped_rows, selected_rows, args)

    if not args.commit:
        print(
            f"Dry run OK. Prepared {len(manifest_rows)} MS_Data rows; "
            f"skipped {len(skipped_rows)} selected rows. Re-run with --commit to create rows."
        )
        return

    _require_create_permission(client, collection="MS_Data")
    created_rows = [_post_ms_data(client, row) for row in manifest_rows]
    print(f"Created {len(created_rows)} Droguier MS_Data rows.")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--readiness", type=Path, default=DEFAULT_READINESS)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--skipped", type=Path, default=DEFAULT_SKIPPED)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--injection-volume", type=int, default=1)
    parser.add_argument("--injection-volume-unit-id", type=int, default=18)
    parser.add_argument("--injection-method-id", type=int, required=True)
    parser.add_argument("--instrument-id", type=int, required=True)
    parser.add_argument("--batch-id", type=int, default=None)
    parser.add_argument("--status", default="published")
    parser.add_argument("--expected-count", type=int, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--commit", action="store_true")
    return parser.parse_args()


def _extraction_container_ids(rows: list[dict[str, Any]]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for row in rows:
        container = row.get("sample_container")
        if not isinstance(container, dict):
            continue
        code = _clean(container.get("container_id"))
        container_id = container.get("id")
        if code and isinstance(container_id, int):
            mapping.setdefault(code, container_id)
    return mapping


def _post_ms_data(client: DirectusClient, row: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "filename": row["filename"],
        "parent_sample_container": row["parent_sample_container"],
        "injection_volume": row["injection_volume"],
        "injection_volume_unit": row["injection_volume_unit"],
        "injection_method": row["injection_method"],
        "instrument_used": row["instrument_used"],
        "status": row["status"],
    }
    if row.get("batch"):
        payload["batch"] = row["batch"]
    response = client._session.post(client._url("/items/MS_Data"), json=payload, timeout=(10, 60))
    if response.status_code not in {200, 201}:
        raise DirectusError(
            f"Directus POST /items/MS_Data failed with status {response.status_code}: "
            f"{response.text}"
        )
    data = response.json().get("data")
    if not isinstance(data, dict):
        raise DirectusResponseError(
            "Directus MS_Data create response did not contain a data object."
        )
    return data


def _require_create_permission(client: DirectusClient, collection: str) -> None:
    payload = client._get("/permissions/me", {"limit": "-1"})
    permissions = payload.get("data")
    if not isinstance(permissions, dict):
        raise DirectusResponseError("Directus /permissions/me did not return an object.")
    collection_permissions = permissions.get(collection)
    create_permission = (
        collection_permissions.get("create") if isinstance(collection_permissions, dict) else None
    )
    access = create_permission.get("access") if isinstance(create_permission, dict) else None
    if access == "none":
        raise DirectusError(
            f"Current Directus user has {collection}.create access set to 'none'. "
            "Use an env file with an admin account or ask an admin to grant create permission."
        )


def _write_summary(
    path: Path,
    manifest_rows: list[dict[str, Any]],
    skipped_rows: list[dict[str, Any]],
    selected_rows: list[dict[str, str]],
    args: argparse.Namespace,
) -> None:
    summary = {
        "readiness": str(args.readiness),
        "selected_recommendation": READY_RECOMMENDATION,
        "selected_count": len(selected_rows),
        "prepared_count": len(manifest_rows),
        "skipped_count": len(skipped_rows),
        "dry_run": not args.commit,
        "injection_volume": args.injection_volume,
        "injection_volume_unit_id": args.injection_volume_unit_id,
        "injection_method_id": args.injection_method_id,
        "instrument_id": args.instrument_id,
        "status": args.status,
        "skip_reasons": dict(Counter(row["skip_reason"] for row in skipped_rows)),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")


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
