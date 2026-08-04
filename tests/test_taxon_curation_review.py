"""Tests for preserving and recording taxon review decisions."""

from __future__ import annotations

from directus_explorer.taxon_curation_review import (
    accept_direct_col_matches,
    preserve_review_decisions,
)


def test_accept_direct_col_matches_leaves_unresolved_rows_pending() -> None:
    rows = [
        {
            "review_id": "direct",
            "decision": "",
            "col_accepted_name": "Erica arborea",
            "col_accepted_usage_id": "6GL83",
        },
        {
            "review_id": "fuzzy",
            "decision": "",
            "col_accepted_name": "",
            "col_accepted_usage_id": "",
        },
    ]

    updated, count = accept_direct_col_matches(rows, reviewed_at="2026-08-03")

    assert count == 1
    assert updated[0]["decision"] == "accept_proposal"
    assert updated[0]["confidence"] == "high"
    assert updated[1]["decision"] == ""


def test_preserve_review_decisions_copies_only_human_fields() -> None:
    generated: list[dict[str, str | int]] = [
        {"review_id": "abc", "col_accepted_name": "New backbone result", "decision": ""}
    ]
    existing = [
        {
            "review_id": "abc",
            "col_accepted_name": "Old backbone result",
            "decision": "accept_proposal",
            "confidence": "high",
            "review_note": "Confirmed.",
        }
    ]

    result = preserve_review_decisions(generated, existing)

    assert result[0]["col_accepted_name"] == "New backbone result"
    assert result[0]["decision"] == "accept_proposal"
    assert result[0]["review_note"] == "Confirmed."
