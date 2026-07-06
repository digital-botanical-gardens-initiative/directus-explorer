"""Tests for taxonomy name resolution helpers."""

from __future__ import annotations

from directus_explorer.taxonomy import CatalogueOfLifeTaxonResolver


def test_align_results_matches_names_with_folded_whitespace() -> None:
    """Input names with embedded newlines should match gnverifier's normalized names."""

    results = CatalogueOfLifeTaxonResolver._align_results(
        {"Thesium \nlinophyllon": "Thesium linophyllon"},
        [
            {
                "ScientificName": "Thesium linophyllon",
                "MatchedCanonical": "Thesium linophyllon",
                "TaxonId": "7DH4C",
                "MatchedName": "Thesium linophyllon L.",
                "CurrentName": "Thesium linophyllon L.",
                "Synonym": "false",
                "DataSourceId": "1",
                "DataSourceTitle": "Catalogue of Life",
                "Kind": "BestMatch",
                "SortScore": "9.8",
                "MatchType": "Exact",
                "EditDistance": "0",
                "ClassificationPath": "Eukaryota|Plantae|Thesium|Thesium linophyllon",
                "Error": "",
            }
        ],
    )

    resolution = results["Thesium \nlinophyllon"]

    assert resolution.input_name == "Thesium \nlinophyllon"
    assert resolution.canonical_name == "Thesium linophyllon"
    assert resolution.taxon_id == "7DH4C"
