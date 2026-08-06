"""Create the research-group model and link approved COMMONS Lab people.

The default mode is a read-only preflight. Commit mode applies an additive,
idempotent schema migration, creates/reuses the COMMONS Lab group under the
existing University of Fribourg, and creates the nine approved memberships.
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
from directus_explorer.research_groups import plan_group_memberships

OUTPUT_DIR = Path("data/working/research_groups")

RESEARCH_GROUP_FIELDS: dict[str, dict[str, Any]] = {
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
    "group_name": {
        "field": "group_name",
        "type": "string",
        "schema": {"is_nullable": False, "is_unique": True},
        "meta": {"interface": "input", "required": True, "sort": 2},
    },
    "university_ref": {
        "field": "university_ref",
        "type": "integer",
        "schema": {"is_nullable": False},
        "meta": {
            "interface": "select-dropdown-m2o",
            "display": "related-values",
            "display_options": {"template": "{{university_name}}"},
            "required": True,
            "sort": 3,
        },
    },
    "people": {
        "field": "people",
        "type": "alias",
        "meta": {
            "special": ["m2m"],
            "interface": "list-m2m",
            "display": "related-values",
            "display_options": {"template": "{{person_ref.full_name}}"},
            "sort": 4,
        },
    },
}

MEMBERSHIP_FIELDS: dict[str, dict[str, Any]] = {
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
    "person_ref": {
        "field": "person_ref",
        "type": "integer",
        "schema": {"is_nullable": False},
        "meta": {
            "interface": "select-dropdown-m2o",
            "display": "related-values",
            "display_options": {"template": "{{full_name}}"},
            "required": True,
            "sort": 2,
        },
    },
    "research_group_ref": {
        "field": "research_group_ref",
        "type": "integer",
        "schema": {"is_nullable": False},
        "meta": {
            "interface": "select-dropdown-m2o",
            "display": "related-values",
            "display_options": {"template": "{{group_name}}"},
            "required": True,
            "sort": 3,
        },
    },
}

PEOPLE_GROUPS_ALIAS = {
    "field": "research_groups",
    "type": "alias",
    "meta": {
        "special": ["m2m"],
        "interface": "list-m2m",
        "display": "related-values",
        "display_options": {"template": "{{research_group_ref.group_name}}"},
        "sort": 7,
    },
}


def main() -> None:
    args = _parse_args()
    client = DirectusClient(load_settings(env_file=args.env_file))
    client._authenticate()
    university = _university_of_fribourg(client)
    people = client._get_items(collection="People", fields="id,full_name")
    before = _schema_state(client)
    actions = _schema_actions(before)
    group, memberships = _current_group_data(client, before)
    group_id = int(group["id"]) if group else None
    if group and _relation_id(group.get("university_ref")) != str(university["id"]):
        raise DirectusError("Existing COMMONS Lab is linked to a different university")
    planned = plan_group_memberships(people, memberships, group_id=group_id)

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    preview_path = args.output_dir / f"commons_lab_memberships_{timestamp}.tsv"
    summary_path = args.output_dir / f"commons_lab_summary_{timestamp}.json"
    _write_tsv(preview_path, planned, list(planned[0]) if planned else ["person_id"])
    summary: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "commit" if args.commit else "read_only_preflight",
        "university": university,
        "schema_before": before,
        "schema_actions": actions,
        "commons_lab_exists": group is not None,
        "existing_membership_count": len(memberships),
        "planned_membership_count": len(planned),
        "preview": str(preview_path),
    }
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"Schema actions required: {len(actions)}")
    print(f"Memberships to create: {len(planned)}")
    print(f"Summary: {summary_path}")
    if not args.commit:
        print("No Directus schema or data was changed.")
        return

    _apply_schema(client, actions)
    after_schema = _schema_state(client)
    remaining = _schema_actions(after_schema)
    if remaining:
        raise DirectusResponseError(
            "Research-group schema migration did not converge: " + "; ".join(remaining)
        )
    group, memberships = _current_group_data(client, after_schema)
    if group is None:
        group = client._post_item(
            "Research_Groups",
            {"group_name": "COMMONS Lab", "university_ref": int(university["id"])},
        )
    elif _relation_id(group.get("university_ref")) != str(university["id"]):
        raise DirectusError("Existing COMMONS Lab is linked to a different university")
    group_id = int(group["id"])
    planned = plan_group_memberships(people, memberships, group_id=group_id)

    journal_path = args.output_dir / f"commons_lab_execution_{timestamp}.tsv"
    journal: list[dict[str, str]] = []
    for membership in planned:
        created = client._post_item(
            "People_Research_Groups",
            {
                "person_ref": int(membership["person_id"]),
                "research_group_ref": group_id,
            },
        )
        journal.append(
            {
                "created_at": datetime.now(UTC).isoformat(),
                "membership_id": str(created["id"]),
                **membership,
                "research_group_id": str(group_id),
                "status": "linked",
            }
        )
        _write_tsv(journal_path, journal, list(journal[0]))

    verified_group, verified_memberships = _current_group_data(client, after_schema)
    if verified_group is None:
        raise DirectusResponseError("COMMONS Lab was not found after creation")
    remaining_memberships = plan_group_memberships(people, verified_memberships, group_id=group_id)
    if remaining_memberships:
        raise DirectusResponseError("Not every approved COMMONS Lab membership was created")
    summary.update(
        {
            "schema_after": after_schema,
            "research_group_id": group_id,
            "membership_count_after": len(verified_memberships),
            "created_membership_count": len(journal),
            "execution_journal": str(journal_path),
        }
    )
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"COMMONS Lab id: {group_id}")
    print(f"Created and verified {len(journal)} memberships.")


def _schema_state(client: DirectusClient) -> dict[str, Any]:
    collections = client._get("/collections", {"limit": "-1"}).get("data") or []
    names = {row.get("collection") for row in collections if isinstance(row, dict)}
    group_fields = _fields(client, "Research_Groups") if "Research_Groups" in names else {}
    membership_fields = (
        _fields(client, "People_Research_Groups") if "People_Research_Groups" in names else {}
    )
    people_fields = _fields(client, "People")
    relations = client._get("/relations", {"limit": "-1"}).get("data") or []
    relation_keys = {
        (row.get("collection"), row.get("field"), row.get("related_collection"))
        for row in relations
        if isinstance(row, dict)
    }
    return {
        "research_groups_collection": "Research_Groups" in names,
        "memberships_collection": "People_Research_Groups" in names,
        "research_group_fields": group_fields,
        "membership_fields": membership_fields,
        "people_groups_alias": "research_groups" in people_fields,
        "university_relation": (
            "Research_Groups",
            "university_ref",
            "Universities",
        )
        in relation_keys,
        "person_relation": (
            "People_Research_Groups",
            "person_ref",
            "People",
        )
        in relation_keys,
        "group_relation": (
            "People_Research_Groups",
            "research_group_ref",
            "Research_Groups",
        )
        in relation_keys,
    }


def _schema_actions(state: dict[str, Any]) -> list[str]:
    actions: list[str] = []
    if not state["research_groups_collection"]:
        actions.append("create_collection:Research_Groups")
    if not state["memberships_collection"]:
        actions.append("create_collection:People_Research_Groups")
    actions.extend(
        f"create_field:Research_Groups.{field}"
        for field in RESEARCH_GROUP_FIELDS
        if field not in state["research_group_fields"]
    )
    actions.extend(
        f"create_field:People_Research_Groups.{field}"
        for field in MEMBERSHIP_FIELDS
        if field not in state["membership_fields"]
    )
    if not state["people_groups_alias"]:
        actions.append("create_field:People.research_groups")
    if not state["university_relation"]:
        actions.append("create_relation:Research_Groups.university_ref->Universities.id")
    if not state["person_relation"]:
        actions.append("create_relation:People_Research_Groups.person_ref->People.id")
    if not state["group_relation"]:
        actions.append(
            "create_relation:People_Research_Groups.research_group_ref->Research_Groups.id"
        )
    return actions


def _apply_schema(client: DirectusClient, actions: list[str]) -> None:
    for action in actions:
        if action == "create_collection:Research_Groups":
            _post(
                client,
                "/collections",
                {
                    "collection": "Research_Groups",
                    "schema": {"name": "Research_Groups"},
                    "meta": {
                        "icon": "groups",
                        "display_template": "{{group_name}}",
                        "note": "Research groups affiliated with universities.",
                    },
                },
            )
        elif action == "create_collection:People_Research_Groups":
            _post(
                client,
                "/collections",
                {
                    "collection": "People_Research_Groups",
                    "schema": {"name": "People_Research_Groups"},
                    "meta": {"icon": "group_add", "hidden": True},
                },
            )
        elif action.startswith("create_field:Research_Groups."):
            field = action.rsplit(".", 1)[1]
            if field not in _fields(client, "Research_Groups"):
                _post(client, "/fields/Research_Groups", RESEARCH_GROUP_FIELDS[field])
        elif action.startswith("create_field:People_Research_Groups."):
            field = action.rsplit(".", 1)[1]
            if field not in _fields(client, "People_Research_Groups"):
                _post(
                    client,
                    "/fields/People_Research_Groups",
                    MEMBERSHIP_FIELDS[field],
                )
        elif action == "create_field:People.research_groups":
            if "research_groups" not in _fields(client, "People"):
                _post(client, "/fields/People", PEOPLE_GROUPS_ALIAS)
        elif action == "create_relation:Research_Groups.university_ref->Universities.id":
            _create_relation(
                client,
                collection="Research_Groups",
                field="university_ref",
                related_collection="Universities",
                one_field=None,
                junction_field=None,
                on_delete="RESTRICT",
            )
        elif action == "create_relation:People_Research_Groups.person_ref->People.id":
            _create_relation(
                client,
                collection="People_Research_Groups",
                field="person_ref",
                related_collection="People",
                one_field="research_groups",
                junction_field="research_group_ref",
                on_delete="CASCADE",
            )
        elif action == (
            "create_relation:People_Research_Groups.research_group_ref->Research_Groups.id"
        ):
            _create_relation(
                client,
                collection="People_Research_Groups",
                field="research_group_ref",
                related_collection="Research_Groups",
                one_field="people",
                junction_field="person_ref",
                on_delete="CASCADE",
            )


def _create_relation(
    client: DirectusClient,
    *,
    collection: str,
    field: str,
    related_collection: str,
    one_field: str | None,
    junction_field: str | None,
    on_delete: str,
) -> None:
    state = _schema_state(client)
    key = {
        ("Research_Groups", "university_ref"): "university_relation",
        ("People_Research_Groups", "person_ref"): "person_relation",
        ("People_Research_Groups", "research_group_ref"): "group_relation",
    }[(collection, field)]
    if state[key]:
        return
    _post(
        client,
        "/relations",
        {
            "collection": collection,
            "field": field,
            "related_collection": related_collection,
            "schema": {"on_delete": on_delete},
            "meta": {
                "many_collection": collection,
                "many_field": field,
                "one_collection": related_collection,
                "one_field": one_field,
                "junction_field": junction_field,
                "one_deselect_action": "delete" if on_delete == "CASCADE" else "nullify",
            },
        },
    )


def _current_group_data(
    client: DirectusClient, state: dict[str, Any]
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    if not state["research_groups_collection"]:
        return None, []
    groups = client._get_items(collection="Research_Groups", fields="id,group_name,university_ref")
    matching = [row for row in groups if str(row.get("group_name") or "").strip() == "COMMONS Lab"]
    if len(matching) > 1:
        raise DirectusError("Multiple COMMONS Lab records exist")
    group = matching[0] if matching else None
    if group is None or not state["memberships_collection"]:
        return group, []
    memberships = client._get_items(
        collection="People_Research_Groups",
        fields="id,person_ref,research_group_ref",
    )
    group_id = str(group["id"])
    return group, [
        row for row in memberships if _relation_id(row.get("research_group_ref")) == group_id
    ]


def _university_of_fribourg(client: DirectusClient) -> dict[str, Any]:
    rows = client._get_items(collection="Universities", fields="id,university_name")
    matching = [
        row
        for row in rows
        if str(row.get("university_name") or "").strip() == "University of Fribourg"
    ]
    if len(matching) != 1:
        raise DirectusError(
            f"Expected exactly one University of Fribourg record, found {len(matching)}"
        )
    return matching[0]


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
