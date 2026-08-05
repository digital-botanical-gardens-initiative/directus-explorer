"""Prepare normalized CoL taxa and safe Field_Data backfill artifacts."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def build_taxa_seed(backbone_rows: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    """Collapse matched names into one accepted taxon row per CoL usage ID."""

    by_id: dict[str, dict[str, str]] = {}
    for row in backbone_rows:
        col_id = row.get("taxon_col_id", "").strip()
        if not col_id:
            continue
        candidate = {
            "col_id": col_id,
            "scientific_name": row.get("col_accepted_name", "").strip(),
            "authorship": row.get("col_accepted_authorship", "").strip(),
            "rank": row.get("col_accepted_rank", "").strip(),
            "backbone_version": row.get("taxon_backbone_version", "").strip(),
            "backbone_dataset_key": row.get("taxon_backbone_dataset_key", "").strip(),
            "backbone_doi": row.get("taxon_backbone_doi", "").strip(),
        }
        existing = by_id.get(col_id)
        if existing and existing != candidate:
            raise ValueError(
                f"Conflicting accepted metadata for CoL ID {col_id}: "
                f"{existing!r} != {candidate!r}"
            )
        by_id[col_id] = candidate
    return [by_id[col_id] for col_id in sorted(by_id)]


def build_safe_field_data_backfill(
    mapping_rows: Iterable[dict[str, str]],
) -> list[dict[str, str]]:
    """Select only automatically safe CoL mappings for the first write batch."""

    output: list[dict[str, str]] = []
    for row in mapping_rows:
        if row.get("requires_review", "").casefold() != "false":
            continue
        col_id = row.get("taxon_col_id", "").strip()
        if not col_id:
            raise ValueError(
                f"Safe mapping lacks a CoL ID for Field_Data {row.get('field_data_id', '')}"
            )
        output.append(
            {
                "project": row.get("project", ""),
                "field_data_id": row.get("field_data_id", ""),
                "sample_id": row.get("sample_id", ""),
                "current_taxon_name_unified": row.get("taxon_name_unified", ""),
                "taxon_col_id": col_id,
                "taxon_resolution_status": row.get("taxon_resolution_status", ""),
                "taxon_backbone_version": row.get("taxon_backbone_version", ""),
            }
        )
    return output


def expected_schema() -> dict[str, Any]:
    """Return the additive Directus schema required by the CoL backbone."""

    return {
        "collection": "Taxa",
        "taxa_fields": {
            "id": {"type": "integer", "required": True, "primary_key": True},
            "col_id": {"type": "string", "required": True, "unique": True},
            "scientific_name": {"type": "string", "required": True},
            "authorship": {"type": "string", "required": False},
            "rank": {"type": "string", "required": False},
            "backbone_version": {"type": "string", "required": True},
            "backbone_dataset_key": {"type": "integer", "required": True},
            "backbone_doi": {"type": "string", "required": True},
        },
        "field_data_fields": {
            "taxon_col_id": {"type": "string", "required": False},
            "taxon_resolution_status": {"type": "string", "required": False},
            "taxon_backbone_version": {"type": "string", "required": False},
            "taxon_ref": {"type": "integer", "required": False},
            "taxon_parent_col_ids": {"type": "json", "required": False},
        },
        "relation": {
            "many_collection": "Field_Data",
            "many_field": "taxon_ref",
            "one_collection": "Taxa",
        },
    }


def plan_taxa_seed(
    seed_rows: Iterable[dict[str, str]],
    existing_rows: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    """Classify Taxa seed rows as missing, already exact, or conflicting."""

    existing_by_col_id: dict[str, dict[str, str]] = {}
    for row in existing_rows:
        normalized = _normalize_taxon_row(row)
        col_id = normalized["col_id"]
        if col_id in existing_by_col_id:
            raise ValueError(f"Directus Taxa contains duplicate CoL ID {col_id!r}")
        existing_by_col_id[col_id] = normalized

    missing: list[dict[str, str]] = []
    exact: list[dict[str, str]] = []
    conflicts: list[dict[str, str]] = []
    for raw_seed in seed_rows:
        seed = _normalize_taxon_row(raw_seed)
        current = existing_by_col_id.get(seed["col_id"])
        if current is None:
            missing.append(seed)
        elif current == seed:
            exact.append(seed)
        else:
            conflicts.append(
                {
                    "col_id": seed["col_id"],
                    "expected": repr(seed),
                    "current": repr(current),
                }
            )
    return missing, exact, conflicts


def _normalize_taxon_row(row: dict[str, Any]) -> dict[str, str]:
    fields = (
        "col_id",
        "scientific_name",
        "authorship",
        "rank",
        "backbone_version",
        "backbone_dataset_key",
        "backbone_doi",
    )
    return {
        field: "" if row.get(field) is None else str(row.get(field)).strip()
        for field in fields
    }


def plan_field_data_backfill(
    backfill_rows: Iterable[dict[str, str]],
    current_rows: Iterable[dict[str, Any]],
    taxa_rows: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    """Classify safe Field_Data mappings as ready, already exact, or conflicting."""

    current_by_id = {str(row.get("id", "")): row for row in current_rows}
    taxa_by_col_id = {
        str(row.get("col_id", "")).strip(): str(row.get("id", "")).strip()
        for row in taxa_rows
        if str(row.get("col_id", "")).strip()
    }
    ready: list[dict[str, str]] = []
    exact: list[dict[str, str]] = []
    conflicts: list[dict[str, str]] = []
    for proposed in backfill_rows:
        field_id = proposed["field_data_id"]
        current = current_by_id.get(field_id)
        reasons: list[str] = []
        proposed_col_id = proposed["taxon_col_id"].strip()
        taxon_id = taxa_by_col_id.get(proposed_col_id, "")
        if current is None:
            reasons.append("field_data_missing")
            current = {}
        if proposed_col_id and not taxon_id:
            reasons.append("taxa_col_id_missing")
        current_name = _text(current.get("taxon_name_unified"))
        if current_name != proposed["current_taxon_name_unified"]:
            reasons.append("taxon_name_unified_changed")

        targets = {
            "taxon_col_id": proposed_col_id,
            "taxon_resolution_status": proposed["taxon_resolution_status"],
            "taxon_backbone_version": proposed["taxon_backbone_version"],
            "taxon_ref": taxon_id,
        }
        if "taxon_parent_col_ids" in proposed:
            targets["taxon_parent_col_ids"] = _json_array_text(
                proposed["taxon_parent_col_ids"]
            )
        current_targets = {
            field: (
                _relation_id(current.get(field))
                if field == "taxon_ref"
                else (
                    _json_array_text(current.get(field))
                    if field == "taxon_parent_col_ids"
                    else _text(current.get(field))
                )
            )
            for field in targets
        }
        for field, target in targets.items():
            if current_targets[field] and current_targets[field] != target:
                reasons.append(f"{field}_conflict")

        output = {
            **proposed,
            **{f"current_{field}": value for field, value in current_targets.items()},
            **{f"new_{field}": value for field, value in targets.items()},
        }
        if reasons:
            output["conflict_reason"] = ";".join(reasons)
            conflicts.append(output)
        elif current_targets == targets:
            exact.append(output)
        else:
            ready.append(output)
    return ready, exact, conflicts


def _relation_id(value: object) -> str:
    if isinstance(value, dict):
        return _text(value.get("id"))
    return _text(value)


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _json_array_text(value: object) -> str:
    """Normalize an optional JSON string or list as a compact sorted array."""

    import json

    if value is None or value == "":
        return ""
    parsed = json.loads(value) if isinstance(value, str) else value
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise ValueError("taxon_parent_col_ids must be a JSON array of strings")
    return json.dumps(sorted(set(parsed)), separators=(",", ":"))
