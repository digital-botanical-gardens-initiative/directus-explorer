"""Prepare or execute deletion of duplicate Field_Data observation rows.

Rule:
  For each duplicate observation fingerprint group, if and only if the group
  contains exactly one canonical sample_id row, keep that canonical row and
  mark all other rows in the group as deletion candidates.

The script is read-mostly and dry-run by default. It writes a full backup TSV,
safe/blocked manifests, and only deletes safe rows when --commit is provided.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import requests

from directus_explorer.config import load_settings
from directus_explorer.directus import DirectusClient, DirectusError, DirectusResponseError

DEFAULT_DUPLICATE_REPORT = Path(
    "data/output/directus_integrity/duplicate_observation_fingerprints.tsv"
)
OUTPUT_DIR = Path("data/output/directus_integrity")
DEFAULT_BACKUP = OUTPUT_DIR / "duplicate_observation_single_canonical_delete_backup.tsv"
DEFAULT_SAFE_MANIFEST = (
    OUTPUT_DIR / "duplicate_observation_single_canonical_safe_delete_manifest.tsv"
)
DEFAULT_BLOCKED_MANIFEST = (
    OUTPUT_DIR / "duplicate_observation_single_canonical_blocked_downstream.tsv"
)
DEFAULT_SUMMARY = OUTPUT_DIR / "duplicate_observation_single_canonical_delete_summary.json"


def main() -> None:
    args = _parse_args()
    duplicate_rows = _read_tsv(args.duplicate_report)
    candidates = _build_candidates(duplicate_rows)
    candidate_ids = [_int_cell(row, "delete_field_data_id") for row in candidates]

    if args.expected_candidates is not None and len(candidates) != args.expected_candidates:
        raise SystemExit(
            f"Refusing to continue: candidate rule produced {len(candidates)} rows, "
            f"expected {args.expected_candidates}."
        )
    if len(set(candidate_ids)) != len(candidate_ids):
        raise SystemExit("Refusing to continue: duplicate candidate Field_Data ids found.")

    client = DirectusClient(load_settings(env_file=args.env_file))
    client._authenticate()

    field_rows = client._get_items(collection="Field_Data", fields="*")
    field_by_id = {_int_id(row): row for row in field_rows}
    missing_ids = sorted(
        candidate_id for candidate_id in candidate_ids if candidate_id not in field_by_id
    )
    if missing_ids:
        raise SystemExit(
            "Refusing to continue: candidate ids missing from Directus Field_Data: "
            + ", ".join(str(value) for value in missing_ids)
        )

    dried_rows = client._get_items(collection="Dried_Samples_Data", fields="id,field_data")
    dried_refs = _referenced_field_data_ids(dried_rows)
    safe_candidates = [
        row for row in candidates if _int_cell(row, "delete_field_data_id") not in dried_refs
    ]
    blocked_candidates = [
        row for row in candidates if _int_cell(row, "delete_field_data_id") in dried_refs
    ]

    _write_backup(args.backup, candidates, field_by_id, dried_refs)
    _write_tsv(args.safe_manifest, safe_candidates)
    _write_tsv(args.blocked_manifest, blocked_candidates)
    _write_summary(
        args.summary,
        candidates=candidates,
        safe_candidates=safe_candidates,
        blocked_candidates=blocked_candidates,
    )

    if not args.commit:
        print(
            f"Dry run OK. Rule produced {len(candidates)} candidates; "
            f"{len(safe_candidates)} have no Dried_Samples_Data reference and "
            f"{len(blocked_candidates)} are blocked. Re-run with --commit to delete safe rows."
        )
        return

    _require_delete_permission(client, collection="Field_Data")
    safe_ids = [_int_cell(row, "delete_field_data_id") for row in safe_candidates]
    deleted_ids = _delete_field_data_rows(client, safe_ids)
    print(
        f"Deleted {len(deleted_ids)} Field_Data rows. Backup: {args.backup}. "
        f"Safe manifest: {args.safe_manifest}."
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duplicate-report", type=Path, default=DEFAULT_DUPLICATE_REPORT)
    parser.add_argument("--backup", type=Path, default=DEFAULT_BACKUP)
    parser.add_argument("--safe-manifest", type=Path, default=DEFAULT_SAFE_MANIFEST)
    parser.add_argument("--blocked-manifest", type=Path, default=DEFAULT_BLOCKED_MANIFEST)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--expected-candidates", type=int, default=None)
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Actually delete safe Field_Data rows. Without this flag, only writes reports.",
    )
    return parser.parse_args()


def _build_candidates(duplicate_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in duplicate_rows:
        groups[row["group_id"]].append(row)

    candidates: list[dict[str, str]] = []
    for group_id, group in sorted(groups.items(), key=lambda item: int(item[0])):
        canonical_rows = [row for row in group if row["sample_id_class"] == "canonical"]
        if len(canonical_rows) != 1:
            continue
        keep_row = canonical_rows[0]
        for row in group:
            if row["field_data_id"] == keep_row["field_data_id"]:
                continue
            candidates.append(
                {
                    "decision": "delete_noncanonical_duplicate_observation",
                    "rationale": (
                        "duplicate observation fingerprint group has exactly one canonical "
                        "sample_id; keep canonical row and delete noncanonical sibling"
                    ),
                    "group_id": group_id,
                    "group_size": row["group_size"],
                    "fingerprint": row["fingerprint"],
                    "project": row["project"],
                    "keep_field_data_id": keep_row["field_data_id"],
                    "keep_sample_id": keep_row["sample_id"],
                    "delete_field_data_id": row["field_data_id"],
                    "delete_sample_id": row["sample_id"],
                    "delete_sample_id_class": row["sample_id_class"],
                    "delete_canonical_candidate": row["canonical_candidate"],
                    "unified_taxon_name": row.get("unified_taxon_name", ""),
                    "date": row["date"],
                    "latitude": row["latitude"],
                    "longitude": row["longitude"],
                    "coordinate_status": row["coordinate_status"],
                }
            )
    return candidates


def _referenced_field_data_ids(rows: list[dict[str, Any]]) -> set[int]:
    references: set[int] = set()
    for row in rows:
        value = row.get("field_data")
        if isinstance(value, dict):
            value = value.get("id")
        if value is None:
            continue
        references.add(int(str(value)))
    return references


def _write_backup(
    path: Path,
    candidates: list[dict[str, str]],
    field_by_id: dict[int, dict[str, Any]],
    dried_refs: set[int],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    directus_fields = sorted({key for row in field_by_id.values() for key in row})
    fieldnames = [
        "backup_kind",
        "downstream_status",
        *list(candidates[0]),
        *[f"field.{field}" for field in directus_fields],
        "directus_row_json",
    ]
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        field_id = _int_cell(candidate, "delete_field_data_id")
        directus_row = field_by_id[field_id]
        rows.append(
            {
                "backup_kind": "duplicate_observation_delete_candidate",
                "downstream_status": (
                    "blocked_dried_samples_data_reference"
                    if field_id in dried_refs
                    else "safe_no_dried_samples_data_reference"
                ),
                **candidate,
                **{f"field.{key}": _tsv_value(value) for key, value in directus_row.items()},
                "directus_row_json": json.dumps(directus_row, ensure_ascii=False, sort_keys=True),
            }
        )
    _write_tsv_with_fieldnames(path, fieldnames, rows)


def _write_summary(
    path: Path,
    *,
    candidates: list[dict[str, str]],
    safe_candidates: list[dict[str, str]],
    blocked_candidates: list[dict[str, str]],
) -> None:
    summary = {
        "candidate_count": len(candidates),
        "safe_delete_count": len(safe_candidates),
        "blocked_downstream_count": len(blocked_candidates),
        "candidate_projects": dict(Counter(row["project"] for row in candidates)),
        "safe_delete_projects": dict(Counter(row["project"] for row in safe_candidates)),
        "blocked_downstream_projects": dict(
            Counter(row["project"] for row in blocked_candidates)
        ),
        "candidate_sample_id_classes": dict(
            Counter(row["delete_sample_id_class"] for row in candidates)
        ),
    }
    path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")


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


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _write_tsv(path: Path, rows: list[dict[str, Any]]) -> None:
    if rows:
        fieldnames = list(rows[0])
    else:
        fieldnames = ("message",)
        rows = [{"message": "no_rows"}]
    _write_tsv_with_fieldnames(path, fieldnames, rows)


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


def _int_cell(row: dict[str, str], key: str) -> int:
    return int(row[key])


def _int_id(row: dict[str, Any]) -> int:
    return int(str(row["id"]))


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
