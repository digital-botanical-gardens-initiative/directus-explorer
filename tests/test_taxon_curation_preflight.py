"""Tests for live-state validation of reviewed taxon updates."""

from __future__ import annotations

from directus_explorer.taxon_curation_preflight import build_taxon_update_preflight


def _manifest() -> dict[str, str]:
    return {
        "review_id": "review-1",
        "project": "jbp",
        "round": "1",
        "field_data_id": "42",
        "sample_id": "dbgi_1",
        "current_taxon_name_unified": "",
        "authoritative_source_field": "taxon_name",
        "authoritative_verbatim_name": "Erica arborea",
        "new_taxon_name_unified": "Erica arborea",
        "col_accepted_usage_id": "6GL83",
        "col_accepted_rank": "species",
        "decision": "accept_proposal",
        "confidence": "high",
        "reviewed_at": "2026-08-03",
        "review_note": "Project rule.",
    }


def test_preflight_marks_unchanged_row_ready_and_backs_it_up() -> None:
    current = {
        "42": {
            "id": 42,
            "sample_id": "dbgi_1",
            "qfield_project": "jbp",
            "taxon_name": "Erica arborea",
            "taxon_name_unified": None,
        }
    }

    backups, previews, conflicts = build_taxon_update_preflight([_manifest()], current)

    assert not conflicts
    assert backups[0]["old_value"] == ""
    assert previews[0]["preflight_status"] == "ready"
    assert previews[0]["patch_payload_json"] == '{"taxon_name_unified":"Erica arborea"}'


def test_preflight_blocks_row_changed_since_audit() -> None:
    current = {
        "42": {
            "id": 42,
            "sample_id": "dbgi_1",
            "qfield_project": "jbp",
            "taxon_name": "Erica arborea",
            "taxon_name_unified": "Someone already curated this",
        }
    }

    backups, previews, conflicts = build_taxon_update_preflight([_manifest()], current)

    assert not backups
    assert not previews
    assert conflicts[0]["conflict_reason"] == "taxon_name_unified_changed_since_audit"


def test_preflight_serializes_placeholder_clear_as_json_null() -> None:
    manifest = _manifest()
    manifest.update(
        {
            "current_taxon_name_unified": "aaunknown",
            "authoritative_source_field": "taxon_name_unified",
            "authoritative_verbatim_name": "aaunknown",
            "new_taxon_name_unified": "",
            "update_action": "clear_value",
            "decision": "clear_placeholder",
        }
    )
    current = {
        "42": {
            "id": 42,
            "sample_id": "dbgi_1",
            "qfield_project": "jbp",
            "taxon_name_unified": "aaunknown",
        }
    }

    backups, previews, conflicts = build_taxon_update_preflight([manifest], current)

    assert not conflicts
    assert backups[0]["old_value"] == "aaunknown"
    assert previews[0]["patch_payload_json"] == '{"taxon_name_unified":null}'
