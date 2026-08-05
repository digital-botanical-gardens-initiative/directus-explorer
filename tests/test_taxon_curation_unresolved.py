"""Tests for explicit unresolved taxon review outcomes."""

from __future__ import annotations

from directus_explorer.taxon_curation_unresolved import build_unresolved_review_rows


def test_builds_placeholder_clear_and_no_evidence_groups() -> None:
    rows = [
        {
            "project": "jbuf",
            "field_data_id": "1",
            "sample_id": "a",
            "stored_taxon_name_unified": "aaunknown",
            "sample_name": "aaunknown",
            "issues": "placeholder_unified_taxon_name",
        },
        {
            "project": "jbuf",
            "field_data_id": "2",
            "sample_id": "b",
            "stored_taxon_name_unified": "",
            "issues": "missing_unified_taxon_name",
        },
        {
            "project": "jbuf",
            "field_data_id": "3",
            "sample_id": "c",
            "stored_taxon_name_unified": "",
            "name_proposition": "Erica arborea",
            "issues": "missing_unified_taxon_name",
        },
    ]

    review = build_unresolved_review_rows(
        rows,
        project="jbuf",
        round_number=3,
        reviewed_at="2026-08-04",
        reviewer="curator@example.org",
    )

    assert [row["affected_row_count"] for row in review] == [1, 1]
    assert [row["decision"] for row in review] == ["clear_placeholder", "unresolved"]
    assert {row["field_data_ids"] for row in review} == {"1", "2"}
    assert {row["reviewer"] for row in review} == {"curator@example.org"}


def test_excludes_ids_already_reviewed_in_an_earlier_round() -> None:
    rows = [
        {
            "project": "jbuf",
            "field_data_id": "1",
            "sample_id": "a",
            "stored_taxon_name_unified": "aaunknown",
            "issues": "placeholder_unified_taxon_name",
        }
    ]

    review = build_unresolved_review_rows(
        rows,
        project="jbuf",
        round_number=3,
        excluded_field_ids={"1"},
        reviewed_at="2026-08-04",
    )

    assert review == []
