"""Seed every canonical collector in People and link all named Field_Data rows.

The default mode is a GET-only preflight. Commit mode creates missing Collector
people, revalidates every empty relation, updates only ``collector_ref``, and
verifies the resulting links.
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

from directus_explorer.collector_people import (
    build_collector_people_plan,
    validate_collector_links,
)
from directus_explorer.config import SettingsError, load_settings
from directus_explorer.directus import DirectusClient, DirectusError, DirectusResponseError

OUTPUT_DIR = Path("data/working/collector_people")
FIELD_NAMES = (
    "id",
    "sample_id",
    "qfield_project",
    "collector_fullname",
    "collector_orcid",
    "collector_inat",
    "collector_ref",
)


def main() -> None:
    args = _parse_args()
    client = DirectusClient(load_settings(env_file=args.env_file))
    client._authenticate()
    fields = client._get_items(collection="Field_Data", fields=",".join(FIELD_NAMES))
    people = client._get_items(collection="People", fields="*")
    missing_people, links, relation_conflicts = build_collector_people_plan(fields, people)
    ready, state_conflicts = validate_collector_links(
        links, {str(row["id"]): row for row in fields}
    )
    conflicts = relation_conflicts + state_conflicts

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    people_path = args.output_dir / f"missing_people_{timestamp}.tsv"
    preview_path = args.output_dir / f"collector_links_preview_{timestamp}.tsv"
    backup_path = args.output_dir / f"collector_links_backup_{timestamp}.tsv"
    conflicts_path = args.output_dir / f"collector_links_conflicts_{timestamp}.tsv"
    summary_path = args.output_dir / f"collector_people_summary_{timestamp}.json"
    _write_tsv(
        people_path,
        missing_people,
        list(missing_people[0]) if missing_people else ["full_name"],
    )
    _write_tsv(preview_path, ready, list(ready[0]) if ready else ["field_data_id"])
    _write_tsv(backup_path, links, list(links[0]) if links else ["field_data_id"])
    _write_tsv(
        conflicts_path,
        conflicts,
        list(conflicts[0]) if conflicts else ["field_data_id"],
    )
    summary: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "commit" if args.commit else "directus_get_only_preflight",
        "canonical_collector_count": len(
            {
                str(row.get("collector_fullname") or "").strip()
                for row in fields
                if str(row.get("collector_fullname") or "").strip()
            }
        ),
        "existing_people_count": len(people),
        "missing_people_count": len(missing_people),
        "planned_link_count": len(links),
        "ready_count": len(ready),
        "conflict_count": len(conflicts),
        "links_by_collector": dict(
            sorted(Counter(row["collector_fullname"] for row in ready).items())
        ),
        "missing_people": str(people_path),
        "preview": str(preview_path),
        "backup": str(backup_path),
        "conflicts": str(conflicts_path),
    }
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"Canonical collectors: {summary['canonical_collector_count']}")
    print(f"Missing People records: {len(missing_people)}")
    print(f"Links ready: {len(ready)}; conflicts: {len(conflicts)}")
    print(f"Summary: {summary_path}")
    if not args.commit:
        print("No Directus data was changed.")
        return
    if conflicts:
        raise DirectusError("Collector relation conflicts found; no changes attempted")

    people_by_name = _ensure_people(client, missing_people)
    people_by_name.update(
        {
            str(row["full_name"]).strip(): int(row["id"])
            for row in people
            if str(row.get("full_name") or "").strip()
        }
    )
    links_with_ids = [
        {**link, "person_id": str(people_by_name[link["collector_fullname"]])} for link in links
    ]
    current = _fetch_field_rows(client, [row["field_data_id"] for row in links_with_ids])
    ready, conflicts = validate_collector_links(
        links_with_ids, {str(row["id"]): row for row in current}
    )
    if conflicts or len(ready) != len(links_with_ids):
        details = "; ".join(f"{row['field_data_id']}={row['conflict_reason']}" for row in conflicts)
        raise DirectusError("Live state changed after preflight; no links attempted. " + details)

    journal_path = args.output_dir / f"collector_links_execution_{timestamp}.tsv"
    journal: list[dict[str, str]] = []
    for link in ready:
        item_id = int(link["field_data_id"])
        person_id = int(link["person_id"])
        updated = client._patch_item(
            collection="Field_Data",
            item_id=item_id,
            payload={"collector_ref": person_id},
        )
        if _relation_id(updated.get("collector_ref")) != str(person_id):
            raise DirectusResponseError(f"Unexpected collector_ref returned for {item_id}")
        journal.append(
            {
                "updated_at": datetime.now(UTC).isoformat(),
                "field_data_id": link["field_data_id"],
                "sample_id": link["sample_id"],
                "project": link["project"],
                "collector_fullname": link["collector_fullname"],
                "old_collector_ref": "",
                "new_collector_ref": str(person_id),
                "status": "linked",
            }
        )
        _write_tsv(journal_path, journal, list(journal[0]))

    verified = _fetch_field_rows(client, [row["field_data_id"] for row in ready])
    verified_by_id = {str(row["id"]): row for row in verified}
    failures = [
        row["field_data_id"]
        for row in ready
        if _relation_id(verified_by_id[row["field_data_id"]].get("collector_ref"))
        != row["person_id"]
    ]
    if failures:
        raise DirectusResponseError("Post-link verification failed: " + ", ".join(failures))
    summary.update(
        {
            "people_count_after": len(people_by_name),
            "linked_count": len(journal),
            "execution_journal": str(journal_path),
        }
    )
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"People records now cover {len(people_by_name)} canonical collectors.")
    print(f"Linked and verified {len(journal)} additional Field_Data rows.")


def _ensure_people(client: DirectusClient, missing_people: list[dict[str, str]]) -> dict[str, int]:
    created: dict[str, int] = {}
    for person in missing_people:
        payload = {key: value or None for key, value in person.items()}
        row = client._post_item("People", payload)
        created[person["full_name"]] = int(row["id"])
    return created


def _fetch_field_rows(client: DirectusClient, ids: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for offset in range(0, len(ids), 100):
        payload = client._get(
            "/items/Field_Data",
            {
                "limit": "-1",
                "fields": ",".join(FIELD_NAMES),
                "filter[id][_in]": ",".join(ids[offset : offset + 100]),
            },
        )
        data = payload.get("data")
        if not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
            raise DirectusResponseError("Directus Field_Data query did not return a row list")
        rows.extend(data)
    return rows


def _write_tsv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def _relation_id(value: object) -> str:
    if isinstance(value, dict):
        value = value.get("id")
    return str(value).strip() if value is not None else ""


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env.admin"))
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--commit", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    try:
        main()
    except (DirectusError, DirectusResponseError, SettingsError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    except requests.RequestException as exc:
        raise SystemExit(f"Directus request failed: {exc}") from exc
