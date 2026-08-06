"""Backfill approved collectors from DCIM paths and link them to People.

The default mode is a read-only preflight. In commit mode the command creates
the additive People schema when needed, seeds Collector people, revalidates all
source rows, updates only empty collector fields, and verifies the result.
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

from directus_explorer.collector_path_backfill import (
    PICTURE_FIELDS,
    build_collector_path_backfill,
    people_from_plan,
    validate_collector_path_backfill,
)
from directus_explorer.config import SettingsError, load_settings
from directus_explorer.directus import DirectusClient, DirectusError, DirectusResponseError

OUTPUT_DIR = Path("data/working/collector_path_backfill")
BASE_FIELDS = (
    "id",
    "sample_id",
    "qfield_project",
    "collector_fullname",
    "collector_orcid",
    "collector_inat",
    *PICTURE_FIELDS,
)

PEOPLE_FIELDS: dict[str, dict[str, Any]] = {
    "id": {
        "field": "id",
        "type": "integer",
        "schema": {
            "is_nullable": False,
            "is_unique": True,
            "is_primary_key": True,
            "has_auto_increment": True,
        },
        "meta": {"hidden": True, "readonly": True, "interface": "input", "sort": 1},
    },
    "full_name": {
        "field": "full_name",
        "type": "string",
        "schema": {"is_nullable": False, "is_unique": True},
        "meta": {"interface": "input", "required": True, "sort": 2},
    },
    "email": {
        "field": "email",
        "type": "string",
        "schema": {"is_nullable": True, "is_unique": True},
        "meta": {"interface": "input", "sort": 3},
    },
    "orcid": {
        "field": "orcid",
        "type": "string",
        "schema": {"is_nullable": True, "is_unique": True, "max_length": 32},
        "meta": {"interface": "input", "sort": 4},
    },
    "inat": {
        "field": "inat",
        "type": "string",
        "schema": {"is_nullable": True, "max_length": 255},
        "meta": {"interface": "input", "sort": 5},
    },
    "role": {
        "field": "role",
        "type": "string",
        "schema": {
            "is_nullable": False,
            "default_value": "Collector",
            "max_length": 64,
        },
        "meta": {
            "interface": "select-dropdown",
            "options": {"choices": [{"text": "Collector", "value": "Collector"}]},
            "required": True,
            "sort": 6,
        },
    },
}

COLLECTOR_REF_FIELD = {
    "field": "collector_ref",
    "type": "integer",
    "schema": {"is_nullable": True},
    "meta": {
        "interface": "select-dropdown-m2o",
        "display": "related-values",
        "display_options": {"template": "{{full_name}}"},
        "note": "Collector linked to the People collection.",
    },
}


def main() -> None:
    args = _parse_args()
    client = DirectusClient(load_settings(env_file=args.env_file))
    client._authenticate()
    schema_before = _schema_state(client)
    schema_actions = _schema_actions(schema_before)
    rows = _fetch_all_field_rows(client, has_collector_ref=schema_before["collector_ref_field"])
    changes = build_collector_path_backfill(rows)
    ready, conflicts = validate_collector_path_backfill(
        changes, {str(row["id"]): row for row in rows}
    )
    people = people_from_plan(changes)

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    preview_path = args.output_dir / f"collector_path_preview_{timestamp}.tsv"
    backup_path = args.output_dir / f"collector_path_backup_{timestamp}.tsv"
    conflicts_path = args.output_dir / f"collector_path_conflicts_{timestamp}.tsv"
    people_path = args.output_dir / f"collector_people_{timestamp}.tsv"
    summary_path = args.output_dir / f"collector_path_summary_{timestamp}.json"
    _write_tsv(preview_path, ready, list(ready[0]) if ready else ["field_data_id"])
    _write_tsv(backup_path, changes, list(changes[0]) if changes else ["field_data_id"])
    _write_tsv(
        conflicts_path,
        conflicts,
        list(conflicts[0]) if conflicts else ["field_data_id"],
    )
    _write_tsv(people_path, people, list(people[0]) if people else ["full_name"])

    summary: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "commit" if args.commit else "read_only_preflight",
        "schema_before": schema_before,
        "schema_actions": schema_actions,
        "planned_people": people,
        "planned_people_count": len(people),
        "planned_update_count": len(changes),
        "ready_count": len(ready),
        "conflict_count": len(conflicts),
        "updates_by_person": dict(
            sorted(Counter(row["new_collector_fullname"] for row in ready).items())
        ),
        "updates_by_project": dict(sorted(Counter(row["project"] for row in ready).items())),
        "preview": str(preview_path),
        "backup": str(backup_path),
        "conflicts": str(conflicts_path),
        "people_preview": str(people_path),
    }
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"Schema actions required: {len(schema_actions)}")
    print(f"People required: {len(people)}")
    print(f"Ready: {len(ready)}; conflicts: {len(conflicts)}")
    print(f"Summary: {summary_path}")
    if not args.commit:
        print("No Directus schema or data was changed.")
        return
    if conflicts:
        raise DirectusError("Preflight conflicts found; no schema or data changes attempted")

    _apply_schema(client, schema_actions)
    schema_after = _schema_state(client)
    remaining = _schema_actions(schema_after)
    if remaining:
        raise DirectusResponseError(
            "People schema migration did not converge: " + "; ".join(remaining)
        )
    people_by_name = _ensure_people(client, people)

    current_rows = _fetch_field_rows(client, [row["field_data_id"] for row in changes])
    ready, conflicts = validate_collector_path_backfill(
        changes, {str(row["id"]): row for row in current_rows}
    )
    if conflicts or len(ready) != len(changes):
        details = "; ".join(f"{row['field_data_id']}={row['conflict_reason']}" for row in conflicts)
        raise DirectusError(
            "Live state changed after preflight; no row updates attempted. " + details
        )

    journal_path = args.output_dir / f"collector_path_execution_{timestamp}.tsv"
    journal: list[dict[str, str]] = []
    for change in ready:
        person_id = people_by_name[change["new_collector_fullname"]]
        item_id = int(change["field_data_id"])
        updated = client._patch_item(
            collection="Field_Data",
            item_id=item_id,
            payload={
                "collector_fullname": change["new_collector_fullname"],
                "collector_ref": person_id,
            },
        )
        if _text(updated.get("collector_fullname")) != change["new_collector_fullname"]:
            raise DirectusResponseError(f"Unexpected collector name returned for {item_id}")
        if _relation_id(updated.get("collector_ref")) != str(person_id):
            raise DirectusResponseError(f"Unexpected collector relation returned for {item_id}")
        journal.append(
            {
                "updated_at": datetime.now(UTC).isoformat(),
                "field_data_id": change["field_data_id"],
                "sample_id": change["sample_id"],
                "project": change["project"],
                "matched_dcim_layers": change["matched_dcim_layers"],
                "old_collector_fullname": "",
                "new_collector_fullname": change["new_collector_fullname"],
                "old_collector_ref": "",
                "new_collector_ref": str(person_id),
                "status": "updated",
            }
        )
        _write_tsv(journal_path, journal, list(journal[0]))

    verified = _fetch_field_rows(client, [row["field_data_id"] for row in ready])
    verified_by_id = {str(row["id"]): row for row in verified}
    failures = [
        row["field_data_id"]
        for row in ready
        if _text(verified_by_id[row["field_data_id"]].get("collector_fullname"))
        != row["new_collector_fullname"]
        or _relation_id(verified_by_id[row["field_data_id"]].get("collector_ref"))
        != str(people_by_name[row["new_collector_fullname"]])
    ]
    if failures:
        raise DirectusResponseError("Post-update verification failed: " + ", ".join(failures))

    summary.update(
        {
            "schema_after": schema_after,
            "people_ids": people_by_name,
            "updated_count": len(journal),
            "execution_journal": str(journal_path),
        }
    )
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"Created/verified {len(people_by_name)} People records.")
    print(f"Updated and verified {len(journal)} Field_Data rows.")


def _schema_state(client: DirectusClient) -> dict[str, Any]:
    collections = client._get("/collections", {"limit": "-1"}).get("data") or []
    collection_names = {row.get("collection") for row in collections if isinstance(row, dict)}
    people_fields = _fields(client, "People") if "People" in collection_names else {}
    field_fields = _fields(client, "Field_Data")
    relations = client._get("/relations/Field_Data", {"limit": "-1"}).get("data") or []
    relation = any(
        isinstance(row, dict)
        and row.get("field") == "collector_ref"
        and row.get("related_collection") == "People"
        for row in relations
    )
    return {
        "people_collection": "People" in collection_names,
        "people_fields": people_fields,
        "collector_ref_field": "collector_ref" in field_fields,
        "collector_ref_relation": relation,
    }


def _schema_actions(state: dict[str, Any]) -> list[str]:
    actions: list[str] = []
    if not state["people_collection"]:
        actions.append("create_collection:People")
    actions.extend(
        f"create_field:People.{field}"
        for field in PEOPLE_FIELDS
        if field not in state["people_fields"]
    )
    if not state["collector_ref_field"]:
        actions.append("create_field:Field_Data.collector_ref")
    if not state["collector_ref_relation"]:
        actions.append("create_relation:Field_Data.collector_ref->People.id")
    return actions


def _apply_schema(client: DirectusClient, actions: list[str]) -> None:
    for action in actions:
        if action == "create_collection:People":
            _post(
                client,
                "/collections",
                {
                    "collection": "People",
                    "schema": {"name": "People"},
                    "meta": {
                        "icon": "people",
                        "note": "People associated with scientific samples and workflows.",
                        "display_template": "{{full_name}}",
                    },
                },
            )
        elif action.startswith("create_field:People."):
            field = action.rsplit(".", 1)[1]
            if field not in _fields(client, "People"):
                _post(client, "/fields/People", PEOPLE_FIELDS[field])
        elif action == "create_field:Field_Data.collector_ref":
            if "collector_ref" not in _fields(client, "Field_Data"):
                _post(client, "/fields/Field_Data", COLLECTOR_REF_FIELD)
        elif (
            action == "create_relation:Field_Data.collector_ref->People.id"
            and not _schema_state(client)["collector_ref_relation"]
        ):
            _post(
                client,
                "/relations",
                {
                    "collection": "Field_Data",
                    "field": "collector_ref",
                    "related_collection": "People",
                    "schema": {"on_delete": "SET NULL"},
                    "meta": {
                        "many_collection": "Field_Data",
                        "many_field": "collector_ref",
                        "one_collection": "People",
                        "one_field": None,
                        "one_deselect_action": "nullify",
                    },
                },
            )


def _ensure_people(client: DirectusClient, people: list[dict[str, str]]) -> dict[str, int]:
    existing = client._get_items(collection="People", fields="*")
    by_name = {_text(row.get("full_name")): row for row in existing}
    result: dict[str, int] = {}
    for person in people:
        current = by_name.get(person["full_name"])
        payload = {key: value or None for key, value in person.items()}
        if current is None:
            current = client._post_item("People", payload)
        elif _text(current.get("role")) != "Collector":
            raise DirectusError(
                f"Existing People record {person['full_name']!r} does not have Collector role"
            )
        result[person["full_name"]] = int(current["id"])
    return result


def _fetch_all_field_rows(
    client: DirectusClient, *, has_collector_ref: bool
) -> list[dict[str, Any]]:
    fields = (*BASE_FIELDS, "collector_ref") if has_collector_ref else BASE_FIELDS
    return client._get_items(collection="Field_Data", fields=",".join(fields))


def _fetch_field_rows(client: DirectusClient, ids: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for offset in range(0, len(ids), 100):
        payload = client._get(
            "/items/Field_Data",
            {
                "limit": "-1",
                "fields": ",".join((*BASE_FIELDS, "collector_ref")),
                "filter[id][_in]": ",".join(ids[offset : offset + 100]),
            },
        )
        data = payload.get("data")
        if not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
            raise DirectusResponseError("Directus Field_Data query did not return a row list")
        rows.extend(data)
    return rows


def _fields(client: DirectusClient, collection: str) -> dict[str, str]:
    data = client._get(f"/fields/{collection}", {"limit": "-1"}).get("data") or []
    return {
        str(row["field"]): str(row["type"])
        for row in data
        if isinstance(row, dict) and row.get("field") and row.get("type")
    }


def _post(client: DirectusClient, path: str, payload: dict[str, Any]) -> None:
    response = client._session.post(client._url(path), json=payload, timeout=(10, 60))
    if response.status_code not in {200, 201, 204}:
        raise DirectusError(
            f"Directus POST {path} failed with status {response.status_code}: {response.text}"
        )


def _write_tsv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def _relation_id(value: object) -> str:
    return _text(value.get("id")) if isinstance(value, dict) else _text(value)


def _text(value: object) -> str:
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
