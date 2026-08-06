import pytest

from directus_explorer.collector_people import (
    build_collector_people_plan,
    validate_collector_links,
)


def test_plans_missing_people_and_unlinked_rows() -> None:
    fields = [
        {
            "id": 1,
            "sample_id": "a",
            "qfield_project": "p",
            "collector_fullname": "Simon Rérat",
            "collector_orcid": "0009-0005-5891-8580",
            "collector_inat": "@mayomaison",
            "collector_ref": None,
        }
    ]

    people, links, conflicts = build_collector_people_plan(fields, [])

    assert people[0] == {
        "full_name": "Simon Rérat",
        "email": "",
        "orcid": "0009-0005-5891-8580",
        "inat": "@mayomaison",
        "role": "Collector",
    }
    assert links[0]["collector_fullname"] == "Simon Rérat"
    assert conflicts == []


def test_accepts_equivalent_inat_with_or_without_at_sign() -> None:
    fields = [
        {"id": 1, "collector_fullname": "A", "collector_inat": "name"},
        {"id": 2, "collector_fullname": "A", "collector_inat": "@name"},
        {"id": 3, "collector_fullname": "A", "collector_inat": "@name"},
    ]

    people, _, _ = build_collector_people_plan(fields, [])

    assert people[0]["inat"] == "@name"


def test_rejects_conflicting_identifiers() -> None:
    fields = [
        {"id": 1, "collector_fullname": "A", "collector_orcid": "one"},
        {"id": 2, "collector_fullname": "A", "collector_orcid": "two"},
    ]

    with pytest.raises(ValueError, match="conflicting collector_orcid"):
        build_collector_people_plan(fields, [])


def test_validation_blocks_a_relation_filled_after_preflight() -> None:
    link = {
        "field_data_id": "1",
        "sample_id": "a",
        "project": "p",
        "collector_fullname": "Chloé Blanc",
        "current_collector_ref": "",
        "person_id": "",
    }

    ready, conflicts = validate_collector_links(
        [link],
        {
            "1": {
                "id": 1,
                "sample_id": "a",
                "qfield_project": "p",
                "collector_fullname": "Chloé Blanc",
                "collector_ref": 9,
            }
        },
    )

    assert ready == []
    assert conflicts[0]["conflict_reason"] == "collector_ref_not_empty"
