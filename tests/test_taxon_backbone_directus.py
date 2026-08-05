"""Tests for normalized Directus CoL backbone artifacts."""

from __future__ import annotations

import pytest

from directus_explorer.taxon_backbone_directus import (
    build_safe_field_data_backfill,
    build_taxa_seed,
    plan_field_data_backfill,
    plan_taxa_seed,
)


def test_collapses_name_variants_to_one_taxon_and_selects_safe_rows() -> None:
    backbone = [
        {
            "taxon_col_id": "ABC",
            "col_accepted_name": "Bellis perennis",
            "col_accepted_authorship": "L.",
            "col_accepted_rank": "species",
            "taxon_backbone_version": "2026-07-17 XR",
            "taxon_backbone_dataset_key": "315834",
            "taxon_backbone_doi": "10.48580/dgykv",
        },
        {
            "taxon_col_id": "ABC",
            "col_accepted_name": "Bellis perennis",
            "col_accepted_authorship": "L.",
            "col_accepted_rank": "species",
            "taxon_backbone_version": "2026-07-17 XR",
            "taxon_backbone_dataset_key": "315834",
            "taxon_backbone_doi": "10.48580/dgykv",
        },
    ]
    mapping = [
        {
            "field_data_id": "1",
            "taxon_name_unified": "Bellis perennis",
            "taxon_col_id": "ABC",
            "taxon_resolution_status": "accepted_exact",
            "taxon_backbone_version": "2026-07-17 XR",
            "requires_review": "false",
        },
        {
            "field_data_id": "2",
            "taxon_name_unified": "Taraxacum officinale",
            "taxon_col_id": "XYZ",
            "taxon_resolution_status": "ambiguous_match",
            "taxon_backbone_version": "2026-07-17 XR",
            "requires_review": "true",
        },
    ]

    assert len(build_taxa_seed(backbone)) == 1
    assert [row["field_data_id"] for row in build_safe_field_data_backfill(mapping)] == ["1"]


def test_rejects_conflicting_metadata_for_one_col_id() -> None:
    rows = [
        {"taxon_col_id": "ABC", "col_accepted_name": "Bellis perennis"},
        {"taxon_col_id": "ABC", "col_accepted_name": "Bellis annua"},
    ]
    with pytest.raises(ValueError, match="Conflicting accepted metadata"):
        build_taxa_seed(rows)


def test_plans_missing_exact_and_conflicting_taxa() -> None:
    seed = [
        {"col_id": "A", "scientific_name": "Aster alpha"},
        {"col_id": "B", "scientific_name": "Bellis beta"},
        {"col_id": "C", "scientific_name": "Carex gamma"},
    ]
    existing = [
        {"col_id": "A", "scientific_name": "Aster alpha"},
        {"col_id": "C", "scientific_name": "Carex different"},
    ]

    missing, exact, conflicts = plan_taxa_seed(seed, existing)

    assert [row["col_id"] for row in missing] == ["B"]
    assert [row["col_id"] for row in exact] == ["A"]
    assert [row["col_id"] for row in conflicts] == ["C"]


def test_plans_safe_field_data_backfill() -> None:
    proposed = [
        {
            "field_data_id": "1",
            "sample_id": "sample-1",
            "current_taxon_name_unified": "Bellis perennis",
            "taxon_col_id": "ABC",
            "taxon_resolution_status": "accepted_exact",
            "taxon_backbone_version": "2026-07-17 XR",
        }
    ]
    taxa = [{"id": 9, "col_id": "ABC"}]
    empty_current = [{"id": 1, "taxon_name_unified": "Bellis perennis"}]

    ready, exact, conflicts = plan_field_data_backfill(proposed, empty_current, taxa)

    assert len(ready) == 1
    assert exact == []
    assert conflicts == []
    assert ready[0]["new_taxon_ref"] == "9"

    populated = [
        {
            "id": 1,
            "taxon_name_unified": "Bellis perennis",
            "taxon_col_id": "ABC",
            "taxon_resolution_status": "accepted_exact",
            "taxon_backbone_version": "2026-07-17 XR",
            "taxon_ref": 9,
        }
    ]
    ready, exact, conflicts = plan_field_data_backfill(proposed, populated, taxa)
    assert ready == []
    assert len(exact) == 1
    assert conflicts == []


def test_plans_explicit_unresolved_field_data_without_taxon_reference() -> None:
    proposed = [
        {
            "field_data_id": "1",
            "sample_id": "sample-1",
            "current_taxon_name_unified": "Phyteuma orbiculare ou hemisphaericum",
            "taxon_col_id": "",
            "taxon_resolution_status": "alternative_identification_unresolved",
            "taxon_backbone_version": "2026-07-17 XR",
        }
    ]
    current = [
        {
            "id": 1,
            "taxon_name_unified": "Phyteuma orbiculare ou hemisphaericum",
            "taxon_col_id": None,
            "taxon_ref": None,
        }
    ]

    ready, exact, conflicts = plan_field_data_backfill(proposed, current, [])

    assert len(ready) == 1
    assert exact == []
    assert conflicts == []
    assert ready[0]["new_taxon_col_id"] == ""
    assert ready[0]["new_taxon_ref"] == ""


def test_plans_hybrid_parent_col_ids_as_normalized_json() -> None:
    proposed = [
        {
            "field_data_id": "1",
            "sample_id": "sample-1",
            "current_taxon_name_unified": "Salvia greggii x microphylla",
            "taxon_col_id": "8W39F",
            "taxon_resolution_status": "hybrid_formula_genus_fallback",
            "taxon_backbone_version": "2026-07-17 XR",
            "taxon_parent_col_ids": '["79H2B", "6XGYF"]',
        }
    ]
    current = [{"id": 1, "taxon_name_unified": "Salvia greggii x microphylla"}]
    taxa = [{"id": 9, "col_id": "8W39F"}]

    ready, exact, conflicts = plan_field_data_backfill(proposed, current, taxa)

    assert len(ready) == 1
    assert exact == []
    assert conflicts == []
    assert ready[0]["new_taxon_parent_col_ids"] == '["6XGYF","79H2B"]'
