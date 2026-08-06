from directus_explorer.collector_harmonization import (
    build_collector_harmonization_plan,
    collector_identity_key,
    validate_collector_changes,
)


def test_identity_key_ignores_case_accents_and_token_order() -> None:
    assert collector_identity_key("Émilie Lab") == collector_identity_key("Lab Emilie")


def test_plan_selects_most_frequent_variant_and_preserves_metadata() -> None:
    rows = [
        {"id": 1, "sample_id": "a", "qfield_project": "p", "collector_fullname": "Émilie Lab"},
        {"id": 2, "sample_id": "b", "qfield_project": "p", "collector_fullname": "Émilie Lab"},
        {
            "id": 3,
            "sample_id": "c",
            "qfield_project": "p",
            "collector_fullname": "Lab Emilie",
            "collector_orcid": "0000",
            "collector_inat": "@emilie",
        },
        {"id": 4, "sample_id": "d", "qfield_project": "p", "collector_fullname": "Someone Else"},
    ]

    groups, changes = build_collector_harmonization_plan(rows)

    assert groups == [
        {
            "identity_key": "emilie lab",
            "canonical_collector_fullname": "Émilie Lab",
            "variants": "Lab Emilie (1); Émilie Lab (2)",
            "row_count": "3",
            "update_count": "1",
        }
    ]
    assert changes == [
        {
            "field_data_id": "3",
            "sample_id": "c",
            "project": "p",
            "current_collector_fullname": "Lab Emilie",
            "new_collector_fullname": "Émilie Lab",
            "collector_orcid": "0000",
            "collector_inat": "@emilie",
        }
    ]


def test_validation_blocks_changed_live_rows() -> None:
    change = {
        "field_data_id": "3",
        "sample_id": "c",
        "project": "p",
        "current_collector_fullname": "Lab Emilie",
        "new_collector_fullname": "Émilie Lab",
        "collector_orcid": "",
        "collector_inat": "",
    }

    ready, conflicts = validate_collector_changes(
        [change],
        {
            "3": {
                "id": 3,
                "sample_id": "c",
                "qfield_project": "p",
                "collector_fullname": "Another Name",
            }
        },
    )

    assert ready == []
    assert conflicts[0]["conflict_reason"] == "collector_fullname_changed"
