"""Add the CoL Taxa collection and Field_Data backbone fields to Directus.

The default mode is a read-only schema preflight. Use --commit for the additive,
idempotent migration.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import requests

from directus_explorer.config import SettingsError, load_settings
from directus_explorer.directus import DirectusClient, DirectusError, DirectusResponseError
from directus_explorer.taxon_backbone_directus import expected_schema

DEFAULT_OUTPUT = Path(
    "data/working/taxonomy_backbone/col_2026_07_17_xr/directus_schema_plan.json"
)

TAXA_FIELD_PAYLOADS: dict[str, dict[str, Any]] = {
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
    "col_id": {
        "field": "col_id",
        "type": "string",
        "schema": {"is_nullable": False, "is_unique": True, "max_length": 64},
        "meta": {"interface": "input", "required": True, "sort": 2},
    },
    "scientific_name": {
        "field": "scientific_name",
        "type": "string",
        "schema": {"is_nullable": False},
        "meta": {"interface": "input", "required": True, "sort": 3},
    },
    "authorship": {"field": "authorship", "type": "string", "schema": {"is_nullable": True}},
    "rank": {"field": "rank", "type": "string", "schema": {"is_nullable": True}},
    "backbone_version": {
        "field": "backbone_version",
        "type": "string",
        "schema": {"is_nullable": False},
    },
    "backbone_dataset_key": {
        "field": "backbone_dataset_key",
        "type": "integer",
        "schema": {"is_nullable": False},
    },
    "backbone_doi": {
        "field": "backbone_doi",
        "type": "string",
        "schema": {"is_nullable": False},
    },
}

FIELD_DATA_FIELD_PAYLOADS: dict[str, dict[str, Any]] = {
    "taxon_col_id": {
        "field": "taxon_col_id",
        "type": "string",
        "schema": {"is_nullable": True, "max_length": 64},
        "meta": {"interface": "input", "readonly": True, "note": "Accepted CoL usage ID."},
    },
    "taxon_resolution_status": {
        "field": "taxon_resolution_status",
        "type": "string",
        "schema": {"is_nullable": True, "max_length": 64},
        "meta": {"interface": "input", "readonly": True},
    },
    "taxon_backbone_version": {
        "field": "taxon_backbone_version",
        "type": "string",
        "schema": {"is_nullable": True, "max_length": 64},
        "meta": {"interface": "input", "readonly": True},
    },
    "taxon_ref": {
        "field": "taxon_ref",
        "type": "integer",
        "schema": {"is_nullable": True},
        "meta": {
            "interface": "select-dropdown-m2o",
            "display": "related-values",
            "display_options": {"template": "{{scientific_name}}"},
            "note": "Accepted taxon concept from the pinned Catalogue of Life release.",
        },
    },
    "taxon_parent_col_ids": {
        "field": "taxon_parent_col_ids",
        "type": "json",
        "schema": {"is_nullable": True},
        "meta": {
            "interface": "input-code",
            "options": {"language": "json"},
            "readonly": True,
            "note": "Accepted CoL usage IDs of parents for an explicit hybrid formula.",
        },
    },
}


def main() -> None:
    args = _parse_args()
    client = DirectusClient(load_settings(env_file=args.env_file))
    client._authenticate()
    before = _inspect(client)
    actions = _planned_actions(before)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {
                "mode": "commit" if args.commit else "read_only_preflight",
                "expected_schema": expected_schema(),
                "before": before,
                "planned_actions": actions,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Schema actions required: {len(actions)}")
    for action in actions:
        print(f"- {action}")
    print(f"Plan: {args.output}")
    if not args.commit:
        print("No Directus schema was changed. Re-run with --commit to apply the plan.")
        return

    _apply(client, actions)
    after = _inspect(client)
    remaining = _planned_actions(after)
    if remaining:
        raise DirectusResponseError(
            "Schema migration did not converge; remaining actions: " + "; ".join(remaining)
        )
    print("Directus taxonomy backbone schema created and verified.")


def _inspect(client: DirectusClient) -> dict[str, Any]:
    collections = client._get("/collections", {"limit": "-1"}).get("data")
    collection_names = {
        row.get("collection") for row in collections or [] if isinstance(row, dict)
    }
    taxa_fields = _fields(client, "Taxa") if "Taxa" in collection_names else {}
    field_data_fields = _fields(client, "Field_Data")
    relations = client._get("/relations/Field_Data", {"limit": "-1"}).get("data") or []
    has_relation = any(
        isinstance(row, dict)
        and row.get("field") == "taxon_ref"
        and row.get("related_collection") == "Taxa"
        for row in relations
    )
    return {
        "taxa_collection_exists": "Taxa" in collection_names,
        "taxa_fields": taxa_fields,
        "field_data_fields": field_data_fields,
        "taxon_ref_relation_exists": has_relation,
    }


def _fields(client: DirectusClient, collection: str) -> dict[str, str]:
    data = client._get(f"/fields/{collection}", {"limit": "-1"}).get("data") or []
    return {
        str(row["field"]): str(row["type"])
        for row in data
        if isinstance(row, dict) and row.get("field") and row.get("type")
    }


def _planned_actions(state: dict[str, Any]) -> list[str]:
    actions: list[str] = []
    if not state["taxa_collection_exists"]:
        actions.append("create_collection:Taxa")
    for field in TAXA_FIELD_PAYLOADS:
        if field not in state["taxa_fields"]:
            actions.append(f"create_field:Taxa.{field}")
    for field in FIELD_DATA_FIELD_PAYLOADS:
        if field not in state["field_data_fields"]:
            actions.append(f"create_field:Field_Data.{field}")
    if not state["taxon_ref_relation_exists"]:
        actions.append("create_relation:Field_Data.taxon_ref->Taxa.id")
    return actions


def _apply(client: DirectusClient, actions: list[str]) -> None:
    for action in actions:
        if action == "create_collection:Taxa":
            _post(
                client,
                "/collections",
                {
                    "collection": "Taxa",
                    "schema": {"name": "Taxa"},
                    "meta": {
                        "icon": "account_tree",
                        "note": "Accepted taxon concepts from pinned Catalogue of Life releases.",
                        "display_template": "{{scientific_name}}",
                    },
                },
            )
        elif action.startswith("create_field:Taxa."):
            field = action.split(".", 1)[1]
            if field not in _fields(client, "Taxa"):
                _post(client, "/fields/Taxa", TAXA_FIELD_PAYLOADS[field])
        elif action.startswith("create_field:Field_Data."):
            field = action.split(".", 1)[1]
            if field not in _fields(client, "Field_Data"):
                _post(client, "/fields/Field_Data", FIELD_DATA_FIELD_PAYLOADS[field])
        elif (
            action == "create_relation:Field_Data.taxon_ref->Taxa.id"
            and not _inspect(client)["taxon_ref_relation_exists"]
        ):
            _post(
                client,
                "/relations",
                {
                    "collection": "Field_Data",
                    "field": "taxon_ref",
                    "related_collection": "Taxa",
                    "schema": {"on_delete": "SET NULL"},
                    "meta": {
                        "many_collection": "Field_Data",
                        "many_field": "taxon_ref",
                        "one_collection": "Taxa",
                        "one_field": None,
                        "one_deselect_action": "nullify",
                    },
                },
            )


def _post(client: DirectusClient, path: str, payload: dict[str, Any]) -> None:
    response = client._session.post(client._url(path), json=payload, timeout=(10, 60))
    if response.status_code not in {200, 201, 204}:
        raise DirectusError(
            f"Directus POST {path} failed with status {response.status_code}: {response.text}"
        )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env.admin"))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--commit", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    try:
        main()
    except (DirectusError, DirectusResponseError, SettingsError) as exc:
        raise SystemExit(str(exc)) from exc
    except requests.RequestException as exc:
        raise SystemExit(f"Directus request failed: {exc}") from exc
