"""Tests for expanding reviewed taxon groups into update manifests."""

from __future__ import annotations

import pytest

from directus_explorer.taxon_curation_manifest import build_taxon_update_manifest


def _review_row(*, decision: str = "accept_proposal") -> dict[str, str]:
    return {
        "review_id": "abc123",
        "project": "jbp",
        "round": "1",
        "field_data_ids": "10;11",
        "sample_ids": "sample-a;sample-b",
        "proposal_source": "taxon_name",
        "proposal_verbatim_name": "Erica arborea",
        "col_accepted_name": "Erica arborea",
        "col_accepted_usage_id": "6GL83",
        "col_accepted_rank": "species",
        "decision": decision,
        "confidence": "high",
        "reviewed_at": "2026-08-03",
        "review_note": "Project rule confirmed.",
    }


def test_build_manifest_expands_grouped_decision() -> None:
    audit = {
        "10": {"stored_taxon_name_unified": ""},
        "11": {"stored_taxon_name_unified": ""},
    }

    manifest, skipped = build_taxon_update_manifest([_review_row()], audit)

    assert not skipped
    assert [row["field_data_id"] for row in manifest] == ["10", "11"]
    assert {row["new_taxon_name_unified"] for row in manifest} == {"Erica arborea"}


def test_build_manifest_refuses_blank_decision() -> None:
    with pytest.raises(ValueError, match="blank decisions: abc123"):
        build_taxon_update_manifest([_review_row(decision="")], {})


def test_build_manifest_uses_explicit_override_target() -> None:
    review = _review_row(decision="override")
    review.update(
        {
            "chosen_source_field": "name_proposition",
            "chosen_verbatim_name": "Trollius asiaticus",
            "chosen_col_accepted_name": "Trollius asiaticus",
            "chosen_col_accepted_usage_id": "7CVTQ",
            "chosen_col_accepted_rank": "species",
        }
    )
    audit = {
        "10": {"stored_taxon_name_unified": ""},
        "11": {"stored_taxon_name_unified": ""},
    }

    manifest, _ = build_taxon_update_manifest([review], audit)

    assert manifest[0]["authoritative_source_field"] == "name_proposition"
    assert manifest[0]["new_taxon_name_unified"] == "Trollius asiaticus"
    assert manifest[0]["col_accepted_usage_id"] == "7CVTQ"
