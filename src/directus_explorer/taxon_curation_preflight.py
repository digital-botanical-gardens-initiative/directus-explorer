"""Validate reviewed taxon updates against current Directus rows."""

from __future__ import annotations

import json
from typing import Any


def build_taxon_update_preflight(
    manifest_rows: list[dict[str, str]],
    current_by_id: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    """Return backups, safe write previews, and blocking conflicts."""

    backups: list[dict[str, str]] = []
    previews: list[dict[str, str]] = []
    conflicts: list[dict[str, str]] = []
    for manifest in manifest_rows:
        field_id = manifest["field_data_id"]
        current = current_by_id.get(field_id)
        if current is None:
            conflicts.append(_conflict(manifest, "field_data_missing_from_directus", ""))
            continue

        current_project = _text(current.get("qfield_project"))
        current_sample = _text(current.get("sample_id"))
        current_source_name = _text(current.get(manifest["authoritative_source_field"]))
        current_unified = _text(current.get("taxon_name_unified"))
        expected_old = manifest["current_taxon_name_unified"].strip()

        reasons: list[str] = []
        if current_project != manifest["project"]:
            reasons.append("project_changed")
        if current_sample != manifest["sample_id"]:
            reasons.append("sample_id_changed")
        if current_source_name != manifest["authoritative_verbatim_name"]:
            reasons.append("authoritative_source_value_changed")
        if current_unified != expected_old:
            reasons.append("taxon_name_unified_changed_since_audit")
        if reasons:
            conflicts.append(_conflict(manifest, ";".join(reasons), current_unified))
            continue

        backups.append(
            {
                "backup_kind": "reviewed_taxon_curation_preflight",
                "review_id": manifest["review_id"],
                "field_data_id": field_id,
                "sample_id": current_sample,
                "target_field": "taxon_name_unified",
                "old_value": current_unified,
                "proposed_new_value": manifest["new_taxon_name_unified"],
                "directus_row_json": json.dumps(
                    current, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ),
            }
        )
        previews.append(
            {
                **manifest,
                "preflight_status": "ready",
                "patch_payload_json": json.dumps(
                    {"taxon_name_unified": manifest["new_taxon_name_unified"]},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            }
        )
    return backups, previews, conflicts


def _conflict(
    manifest: dict[str, str], reason: str, current_unified: str
) -> dict[str, str]:
    return {
        "review_id": manifest["review_id"],
        "field_data_id": manifest["field_data_id"],
        "sample_id": manifest["sample_id"],
        "conflict_reason": reason,
        "audit_taxon_name_unified": manifest["current_taxon_name_unified"],
        "current_taxon_name_unified": current_unified,
        "proposed_taxon_name_unified": manifest["new_taxon_name_unified"],
    }


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""
