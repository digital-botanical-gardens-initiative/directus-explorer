"""Tests for database-wide CoL backbone mapping."""

from __future__ import annotations

from directus_explorer.taxon_backbone import (
    build_backbone_rows,
    build_field_data_mapping,
    inventory_unified_names,
    normalize_global_lookup_name,
    resolution_status,
)
from directus_explorer.taxon_curation import ColNameResolution


def _resolution(**overrides: object) -> ColNameResolution:
    values: dict[str, object] = {
        "verbatim_name": "Bellis perennis",
        "parsed_name": "Bellis perennis",
        "parsed_authorship": "",
        "parsed_rank": "species",
        "parsed_cultivar": "",
        "parse_status": "parsed",
        "match": True,
        "match_type": "exact",
        "matched_usage_id": "ABC",
        "matched_name": "Bellis perennis",
        "matched_authorship": "L.",
        "matched_rank": "species",
        "matched_status": "accepted",
        "accepted_usage_id": "ABC",
        "accepted_name": "Bellis perennis",
        "accepted_authorship": "L.",
        "accepted_rank": "species",
        "issues": "{}",
        "fuzzy_candidates": "",
    }
    values.update(overrides)
    return ColNameResolution(**values)  # type: ignore[arg-type]


def test_inventory_and_row_mapping_preserve_occurrences() -> None:
    rows = [
        {
            "project": "a",
            "field_data_id": "1",
            "sample_id": "x",
            "stored_taxon_name_unified": "Bellis perennis",
        },
        {
            "project": "b",
            "field_data_id": "2",
            "sample_id": "y",
            "stored_taxon_name_unified": "Bellis perennis",
        },
    ]
    inventory = inventory_unified_names(rows)
    resolutions = {"Bellis perennis": _resolution()}

    assert inventory[0]["field_data_count"] == 2
    assert inventory[0]["project_count"] == 2
    assert inventory[0]["projects"] == "a;b"
    assert build_backbone_rows(inventory, resolutions)[0]["taxon_col_id"] == "ABC"
    mapping = build_field_data_mapping(rows, resolutions)
    assert [row["field_data_id"] for row in mapping] == ["1", "2"]
    assert {row["taxon_resolution_status"] for row in mapping} == {"accepted_exact"}


def test_resolution_status_flags_synonyms_and_unsafe_matches() -> None:
    assert resolution_status(_resolution(matched_status="synonym")) == "synonym"
    assert (
        resolution_status(
            _resolution(
                match=False,
                match_type="higherrank",
                accepted_usage_id="",
                accepted_name="",
            )
        )
        == "higher_rank_match"
    )
    assert (
        resolution_status(_resolution(parsed_cultivar="Snowcap"))
        == "cultivar_requires_review"
    )
    assert normalize_global_lookup_name("Juniperus_communis_L") == "Juniperus communis L"
