"""Preflight or seed accepted CoL concepts into the Directus Taxa collection.

The default is GET-only. In --commit mode, only missing rows are created;
existing rows are never modified and any metadata conflict blocks all creates.
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
from directus_explorer.taxon_backbone_directus import plan_taxa_seed

DEFAULT_SEED = Path(
    "data/working/taxonomy_backbone/col_2026_07_17_xr/taxa_seed.tsv"
)


def main() -> None:
    args = _parse_args()
    seed = _read_tsv(args.seed)
    client = DirectusClient(load_settings(env_file=args.env_file))
    client._authenticate()
    _validate_schema(client)
    existing = client._get_items(
        collection="Taxa",
        fields=(
            "id,col_id,scientific_name,authorship,rank,backbone_version,"
            "backbone_dataset_key,backbone_doi"
        ),
    )
    missing, exact, conflicts = plan_taxa_seed(seed, existing)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_dir = args.seed.parent
    preview_path = output_dir / f"taxa_seed_preview_{timestamp}.tsv"
    conflicts_path = output_dir / f"taxa_seed_conflicts_{timestamp}.tsv"
    summary_path = output_dir / f"taxa_seed_preflight_{timestamp}.json"
    _write_tsv(preview_path, missing)
    _write_tsv(conflicts_path, conflicts)
    summary_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "mode": "commit" if args.commit else "directus_get_only_preflight",
                "seed": str(args.seed),
                "seed_count": len(seed),
                "existing_exact_count": len(exact),
                "missing_count": len(missing),
                "conflict_count": len(conflicts),
                "preview": str(preview_path),
                "conflicts": str(conflicts_path),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Seed rows: {len(seed)}; already exact: {len(exact)}")
    print(f"Missing: {len(missing)}; blocking conflicts: {len(conflicts)}")
    print(f"Preview: {preview_path}")
    print(f"Summary: {summary_path}")
    if conflicts:
        raise SystemExit(f"Conflicts: {conflicts_path}. No Taxa rows were created.")
    if not args.commit:
        print("No Directus data was changed. Re-run with --commit to create missing taxa.")
        return

    journal_path = output_dir / f"taxa_seed_execution_{timestamp}.tsv"
    journal: list[dict[str, str]] = []
    for index, row in enumerate(missing, start=1):
        created = client._post_item("Taxa", _create_payload(row))
        if str(created.get("col_id", "")).strip() != row["col_id"]:
            raise DirectusResponseError(
                f"Directus returned an unexpected Taxa row for CoL ID {row['col_id']}"
            )
        journal.append(
            {
                "created_at": datetime.now(UTC).isoformat(),
                "directus_id": str(created.get("id", "")),
                "col_id": row["col_id"],
                "scientific_name": row["scientific_name"],
                "status": "created",
            }
        )
        _write_tsv(journal_path, journal)
        if index % args.progress_every == 0 or index == len(missing):
            print(f"Created {index}/{len(missing)} missing Taxa rows", flush=True)

    verified = client._get_items(
        collection="Taxa",
        fields=(
            "id,col_id,scientific_name,authorship,rank,backbone_version,"
            "backbone_dataset_key,backbone_doi"
        ),
    )
    remaining, verified_exact, verify_conflicts = plan_taxa_seed(seed, verified)
    if remaining or verify_conflicts or len(verified_exact) != len(seed):
        raise DirectusResponseError(
            "Post-seed verification failed: "
            f"missing={len(remaining)}, conflicts={len(verify_conflicts)}, "
            f"exact={len(verified_exact)}"
        )
    print(f"Created and verified {len(journal)} Taxa rows.")
    print(f"Execution journal: {journal_path}")


def _validate_schema(client: DirectusClient) -> None:
    payload = client._get("/fields/Taxa", {"limit": "-1"})
    data = payload.get("data")
    if not isinstance(data, list):
        raise DirectusResponseError("Directus Taxa fields endpoint did not return a list")
    present = {str(row.get("field")) for row in data if isinstance(row, dict)}
    required = {
        "id",
        "col_id",
        "scientific_name",
        "authorship",
        "rank",
        "backbone_version",
        "backbone_dataset_key",
        "backbone_doi",
    }
    missing = sorted(required - present)
    if missing:
        raise DirectusResponseError("Directus Taxa schema lacks fields: " + ", ".join(missing))


def _create_payload(row: dict[str, str]) -> dict[str, Any]:
    return {
        "col_id": row["col_id"],
        "scientific_name": row["scientific_name"],
        "authorship": row["authorship"] or None,
        "rank": row["rank"] or None,
        "backbone_version": row["backbone_version"],
        "backbone_dataset_key": int(row["backbone_dataset_key"]),
        "backbone_doi": row["backbone_doi"],
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
    parser.add_argument("--seed", type=Path, default=DEFAULT_SEED)
    parser.add_argument("--env-file", type=Path, default=Path(".env.admin"))
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--progress-every", type=int, default=100)
    args = parser.parse_args()
    if args.progress_every < 1:
        parser.error("--progress-every must be positive")
    return args


if __name__ == "__main__":
    try:
        main()
    except (DirectusError, DirectusResponseError, SettingsError) as exc:
        raise SystemExit(str(exc)) from exc
    except requests.RequestException as exc:
        raise SystemExit(f"Directus request failed: {exc}") from exc
