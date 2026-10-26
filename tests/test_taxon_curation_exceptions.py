"""Consumer-facing decisions for pinned CoL exception projections."""

from __future__ import annotations

from copy import deepcopy

import pytest

from directus_explorer.taxon_curation_exceptions import build_upload, exception_options

TYPO = "Lepechiria hastata"
CULTIVAR = "X Passiflora 'Anastasia'"
VERSION = "2026-07-17 XR"
DATASET_KEY = "315834"
DOI = "10.48580/dgykv"

# Snapshot of the project's safe accepted names; the working artifact is not
# checked into git, so these tests must also run in a clean checkout.
SAFE = (
    ("Brugmansia", "3DFL", "Brugmansia", "Pers.", "genus"),
    ("Drosera capensis", "37QPF", "Drosera capensis", "L.", "species"),
    ("Lepechinia hastata", "3T8YK", "Lepechinia hastata", "(A.Gray) Epling", "species"),
    ("Passiflora", "6JGB", "Passiflora", "L.", "genus"),
    ("Salvia", "8W39F", "Salvia", "L.", "genus"),
    ("Salvia cacaliifolia", "79FGK", "Salvia cacaliifolia", "Benth.", "species"),
    ("Salvia congestiflora", "79FC4", "Salvia congestiflora", "Epling", "species"),
    ("Salvia dorisiana", "79H8C", "Salvia dorisiana", "Standl.", "species"),
    ("Salvia involucrata", "79GXM", "Salvia involucrata", "Cav.", "species"),
    ("Salvia jamensis", "79H9Q", "Salvia × jamensis", "J.Compton", "species"),
    ("Salvia leucantha", "79GNS", "Salvia leucantha", "Cav.", "species"),
    ("Salvia mellifera", "79GQ5", "Salvia mellifera", "Greene", "species"),
    ("Salvia oxyphora", "6XGSC", "Salvia oxyphora", "Briq.", "species"),
    ("Salvia reptans", "6XGT2", "Salvia reptans", "Jacq.", "species"),
    ("Thunbergia erecta", "56NXQ", "Thunbergia erecta", "(Benth.) T.Anderson", "species"),
)
NAMES = (
    "Brugmansia", "Passiflora", "Salvia oxyphora", "Salvia oxyphora",
    "Salvia jamensis", "Passiflora", CULTIVAR, "Passiflora",
    "Salvia involucrata", "Salvia jamensis", "Lepechinia hastata", TYPO,
    "Salvia involucrata", "Salvia leucantha", "Salvia mellifera",
    "Salvia congestiflora", "Salvia congestiflora", "Thunbergia erecta",
    "Thunbergia erecta", "Salvia dorisiana", "Salvia reptans",
    "Salvia reptans", "Drosera capensis", "Salvia", "Salvia",
    "Salvia cacaliifolia",
)


@pytest.fixture
def artifacts() -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    safe = {name: (col_id, accepted, author, rank) for name, col_id, accepted, author, rank in SAFE}
    grouped: dict[str, dict[str, str]] = {}
    mapping: list[dict[str, str]] = []
    for field_id, name in enumerate(NAMES, 77261):
        col_id, accepted, author, rank = safe.get(name, ("", "", "", ""))
        sample_id = f"sample-{field_id}"
        reviewed = name in {TYPO, CULTIVAR}
        if name not in grouped:
            grouped[name] = {
                "taxon_name_unified": name,
                "projects": "bg-unifr",
                "field_data_ids": "",
                "sample_ids": "",
                "taxon_col_id": col_id,
                "taxon_resolution_status": (
                    "cultivar_requires_review" if name == CULTIVAR else "unmatched"
                ) if reviewed else "accepted_variant",
                "taxon_backbone_version": VERSION,
                "taxon_backbone_dataset_key": DATASET_KEY,
                "taxon_backbone_doi": DOI,
                "requires_review": "true" if reviewed else "false",
                "col_matched_status": "accepted" if col_id else "",
                "col_accepted_name": accepted,
                "col_accepted_authorship": author,
                "col_accepted_rank": rank,
            }
        source = grouped[name]
        source["field_data_ids"] += (";" if source["field_data_ids"] else "") + str(field_id)
        source["sample_ids"] += (";" if source["sample_ids"] else "") + sample_id
        mapping.append({
            "project": "bg-unifr",
            "field_data_id": str(field_id),
            "sample_id": sample_id,
            "taxon_name_unified": name,
            "taxon_col_id": col_id,
            "taxon_resolution_status": source["taxon_resolution_status"],
            "taxon_backbone_version": VERSION,
            "taxon_backbone_dataset_key": DATASET_KEY,
            "taxon_backbone_doi": DOI,
            "requires_review": source["requires_review"],
        })
    grouped[TYPO].update({
        "parse_status": "parsed",
        "parsed_rank": "species",
        "col_fuzzy_candidates": (
            '[{"id":"3T8YK","name":"Lepechinia hastata",'
            '"authorship":"(A.Gray) Epling","rank":"species","status":"accepted"},'
            '{"id":"7JWQS","name":"Lepechinia hastata subsp. hastata",'
            '"authorship":"","rank":"subspecies","status":"accepted"}]'
        ),
    })
    grouped[CULTIVAR].update({
        "parsed_cultivar": "Anastasia",
        "parsed_name": "× Passiflora 'Anastasia'",
        "col_match_type": "higherrank",
        "col_matched_usage_id": "6JGB",
        "col_matched_name": "Passiflora",
        "col_matched_rank": "genus",
        "col_matched_status": "accepted",
    })
    return list(grouped.values()), mapping

def test_options_pin_only_safe_accepted_evidence(
    artifacts: tuple[list[dict[str, str]], list[dict[str, str]]]
) -> None:
    backbone, _ = artifacts
    options = exception_options(backbone)

    assert set(options) == {TYPO, CULTIVAR}
    assert len(options[TYPO]) == len(options[CULTIVAR]) == 1
    assert options[TYPO][0] == {
        "col_id": "3T8YK",
        "scientific_name": "Lepechinia hastata",
        "rank": "species",
        "authorship": "(A.Gray) Epling",
        "status": "reviewed_spelling",
        "rationale": options[TYPO][0]["rationale"],
    }
    assert "spelling" in options[TYPO][0]["rationale"]
    assert options[CULTIVAR][0]["col_id"] == "6JGB"
    assert options[CULTIVAR][0]["scientific_name"] == "Passiflora"
    assert options[CULTIVAR][0]["rank"] == "genus"
    assert options[CULTIVAR][0]["authorship"] == "L."
    assert options[CULTIVAR][0]["status"] == "reviewed_cultivar_genus"
    assert "genus" in options[CULTIVAR][0]["rationale"]
    assert {option["col_id"] for group in options.values() for option in group} <= {
        row["taxon_col_id"] for row in backbone if row["requires_review"] == "false"
    }


def test_projects_all_rows_with_reviewed_typo_and_unresolved_cultivar(
    artifacts: tuple[list[dict[str, str]], list[dict[str, str]]],
) -> None:
    backbone, mapping = artifacts
    seed, backfill = build_upload(
        backbone, mapping, {TYPO: "3T8YK", CULTIVAR: "unresolved"}, "bg-unifr"
    )

    assert len(backfill) == len(mapping) == 26
    assert [row["field_data_id"] for row in backfill] == [row["field_data_id"] for row in mapping]
    assert list(backfill[0]) == [
        "project", "field_data_id", "sample_id", "current_taxon_name_unified",
        "taxon_col_id", "taxon_resolution_status", "taxon_backbone_version",
    ]
    assert all(row["project"] == "bg-unifr" for row in backfill)
    typo = next(row for row in backfill if row["current_taxon_name_unified"] == TYPO)
    cultivar = next(row for row in backfill if row["current_taxon_name_unified"] == CULTIVAR)
    assert (typo["field_data_id"], typo["taxon_col_id"], typo["taxon_resolution_status"]) == (
        "77272", "3T8YK", "reviewed_spelling",
    )
    assert (
        cultivar["field_data_id"],
        cultivar["taxon_col_id"],
        cultivar["taxon_resolution_status"],
    ) == (
        "77267", "", "reviewed_unresolved",
    )
    assert len(seed) == 15
    assert list(seed[0]) == [
        "col_id", "scientific_name", "authorship", "rank", "backbone_version",
        "backbone_dataset_key", "backbone_doi",
    ]
    assert seed == [
        {
            "col_id": col_id,
            "scientific_name": accepted,
            "authorship": author,
            "rank": rank,
            "backbone_version": VERSION,
            "backbone_dataset_key": DATASET_KEY,
            "backbone_doi": DOI,
        }
        for _, col_id, accepted, author, rank in sorted(SAFE, key=lambda item: item[1])
    ]
    assert {row["col_id"] for row in seed} == {row["taxon_col_id"] for row in backfill} - {""}


def test_cultivar_genus_is_explicit_and_uses_canonical_safe_seed(
    artifacts: tuple[list[dict[str, str]], list[dict[str, str]]],
) -> None:
    backbone, mapping = artifacts
    seed, backfill = build_upload(
        backbone, mapping, {TYPO: "unresolved", CULTIVAR: "6JGB"}, "bg-unifr"
    )
    cultivar = next(row for row in backfill if row["current_taxon_name_unified"] == CULTIVAR)
    typo = next(row for row in backfill if row["current_taxon_name_unified"] == TYPO)
    assert (cultivar["taxon_col_id"], cultivar["taxon_resolution_status"]) == (
        "6JGB", "reviewed_cultivar_genus",
    )
    assert (typo["taxon_col_id"], typo["taxon_resolution_status"]) == ("", "reviewed_unresolved")
    assert len(seed) == 15
    assert next(row for row in seed if row["col_id"] == "6JGB")["scientific_name"] == "Passiflora"
    assert all(row["scientific_name"] not in {TYPO, CULTIVAR} for row in seed)


@pytest.mark.parametrize("choices", [
    {},
    {TYPO: "3T8YK"},
    {TYPO: "3T8YK", CULTIVAR: ""},
    {TYPO: "3T8YK", CULTIVAR: "unresolved", "Other": "unresolved"},
    {TYPO: "7JWQS", CULTIVAR: "unresolved"},
    {TYPO: "6JGB", CULTIVAR: "unresolved"},
    {TYPO: "3T8YK", CULTIVAR: "533XR"},
])
def test_requires_explicit_valid_decision_per_name(
    artifacts: tuple[list[dict[str, str]], list[dict[str, str]]], choices: dict[str, str],
) -> None:
    with pytest.raises(ValueError):
        build_upload(*artifacts, choices, "bg-unifr")


@pytest.mark.parametrize("mutation", [
    lambda backbone, mapping: mapping.append(deepcopy(mapping[0])),
    lambda backbone, mapping: mapping[0].update(field_data_id="999999"),
    lambda backbone, mapping: mapping[0].update(project="foreign"),
    lambda backbone, mapping: mapping[0].update(taxon_col_id=""),
    lambda backbone, mapping: mapping[0].update(sample_id="wrong"),
    lambda backbone, mapping: mapping[0].update(taxon_backbone_dataset_key="other"),
    lambda backbone, mapping: mapping.pop(),
    lambda backbone, mapping: backbone[0].update(projects="foreign"),
    lambda backbone, mapping: backbone[0].update(taxon_col_id=""),
    lambda backbone, mapping: backbone.append(deepcopy(backbone[0])),
])
def test_rejects_foreign_duplicate_unmatched_or_unsafe_source_rows(
    artifacts: tuple[list[dict[str, str]], list[dict[str, str]]], mutation,
) -> None:
    backbone, mapping = deepcopy(artifacts)
    mutation(backbone, mapping)
    with pytest.raises(ValueError):
        build_upload(backbone, mapping, {TYPO: "3T8YK", CULTIVAR: "unresolved"}, "bg-unifr")


def test_no_fabricated_options_when_candidate_not_in_safe_backbone(
    artifacts: tuple[list[dict[str, str]], list[dict[str, str]]],
) -> None:
    backbone, mapping = artifacts
    reduced = [row for row in backbone if row["taxon_col_id"] not in {"3T8YK", "6JGB"}]
    assert exception_options(reduced) == {TYPO: [], CULTIVAR: []}
    with pytest.raises(ValueError, match="Unpinned or unsafe"):
        build_upload(reduced, [row for row in mapping if row["taxon_name_unified"] not in {
            "Lepechinia hastata", "Passiflora",
        }], {TYPO: "3T8YK", CULTIVAR: "unresolved"}, "bg-unifr")


def test_fuzzy_hits_and_higher_rank_matches_must_agree_with_taxon_evidence(
    artifacts: tuple[list[dict[str, str]], list[dict[str, str]]],
) -> None:
    backbone, _ = deepcopy(artifacts)
    typo = next(row for row in backbone if row["taxon_name_unified"] == TYPO)
    cultivar = next(row for row in backbone if row["taxon_name_unified"] == CULTIVAR)
    # Both IDs are safe, but neither is supported for these names.
    typo["col_fuzzy_candidates"] = (
        '[{"id":"3DFL","name":"Brugmansia","rank":"genus",'
        '"authorship":"Pers.","status":"accepted"}]'
    )
    cultivar["col_matched_usage_id"] = "8W39F"
    cultivar["col_matched_name"] = "Salvia"
    assert exception_options(backbone) == {TYPO: [], CULTIVAR: []}



def test_safe_synonym_seeds_accepted_concept_not_matched_synonym() -> None:
    backbone = [{
        "taxon_name_unified": "Acacia karroo Hayne",
        "projects": "bg-unifr",
        "field_data_ids": "77261",
        "sample_ids": "sample-77261",
        "taxon_col_id": "7F977",
        "taxon_resolution_status": "synonym",
        "taxon_backbone_version": VERSION,
        "taxon_backbone_dataset_key": DATASET_KEY,
        "taxon_backbone_doi": DOI,
        "requires_review": "false",
        "col_matched_status": "synonym",
        "col_matched_usage_id": "8PGC",
        "col_matched_name": "Acacia karroo",
        "col_matched_rank": "species",
        "col_accepted_name": "Vachellia karroo",
        "col_accepted_authorship": "(Hayne) Banfi & Galasso",
        "col_accepted_rank": "species",
    }]
    mapping = [{
        "project": "bg-unifr",
        "field_data_id": "77261",
        "sample_id": "sample-77261",
        "taxon_name_unified": "Acacia karroo Hayne",
        "taxon_col_id": "7F977",
        "taxon_resolution_status": "synonym",
        "taxon_backbone_version": VERSION,
        "taxon_backbone_dataset_key": DATASET_KEY,
        "taxon_backbone_doi": DOI,
        "requires_review": "false",
    }]

    seed, backfill = build_upload(backbone, mapping, {}, "bg-unifr")
    assert seed == [{
        "col_id": "7F977",
        "scientific_name": "Vachellia karroo",
        "authorship": "(Hayne) Banfi & Galasso",
        "rank": "species",
        "backbone_version": VERSION,
        "backbone_dataset_key": DATASET_KEY,
        "backbone_doi": DOI,
    }]
    assert backfill[0]["taxon_col_id"] == "7F977"
    assert backfill[0]["taxon_resolution_status"] == "synonym"
    assert backfill[0]["current_taxon_name_unified"] == "Acacia karroo Hayne"

    for bad_field, bad_value in (
        ("col_matched_usage_id", "7F977"),
        ("col_matched_usage_id", ""),
        ("col_accepted_name", ""),
        ("col_accepted_rank", ""),
    ):
        broken = [dict(backbone[0], **{bad_field: bad_value})]
        with pytest.raises(ValueError):
            build_upload(broken, mapping, {}, "bg-unifr")
