from directus_explorer.collector_path_backfill import (
    build_collector_path_backfill,
    dcim_layers,
    people_from_plan,
    validate_collector_path_backfill,
)


def test_extracts_first_dcim_layer_across_picture_fields() -> None:
    row = {
        "picture_panel": "files/unrelated.jpg",
        "picture_detail": r"root\DCIM\Teo_Valentino\sample.jpg",
    }

    assert dcim_layers(row) == {"Teo_Valentino"}


def test_builds_only_approved_empty_collector_changes() -> None:
    rows = [
        {
            "id": 1,
            "sample_id": "a",
            "qfield_project": "jbn",
            "collector_fullname": None,
            "collector_ref": None,
            "picture_panel": "DCIM/Teo_Valentino/a.jpg",
        },
        {
            "id": 2,
            "sample_id": "b",
            "qfield_project": "jbn",
            "collector_fullname": "Already Known",
            "picture_panel": "DCIM/Teo_Valentino/b.jpg",
        },
        {
            "id": 3,
            "sample_id": "c",
            "qfield_project": "jbn",
            "collector_fullname": None,
            "picture_panel": "DCIM/unapproved/c.jpg",
        },
    ]

    changes = build_collector_path_backfill(rows)

    assert len(changes) == 1
    assert changes[0]["new_collector_fullname"] == "Lëndita Schwegler"
    assert changes[0]["person_email"] == "lendita.kashtanjeva@unifr.ch"


def test_validation_blocks_changed_name_or_path() -> None:
    change = build_collector_path_backfill(
        [
            {
                "id": 1,
                "sample_id": "a",
                "qfield_project": "jbn",
                "picture_panel": "DCIM/teo_valentino/a.jpg",
            }
        ]
    )[0]

    ready, conflicts = validate_collector_path_backfill(
        [change],
        {
            "1": {
                "id": 1,
                "sample_id": "a",
                "qfield_project": "jbn",
                "collector_fullname": "Someone",
                "picture_panel": "DCIM/changed/a.jpg",
            }
        },
    )

    assert ready == []
    assert conflicts[0]["conflict_reason"] == (
        "collector_fullname_not_empty;dcim_path_evidence_changed"
    )


def test_people_from_plan_deduplicates_shared_layers() -> None:
    changes = build_collector_path_backfill(
        [
            {
                "id": 1,
                "picture_panel": "DCIM/Audrey_layer/a.jpg",
            },
            {
                "id": 2,
                "picture_panel": "DCIM/Audrey_le_cabec/b.jpg",
            },
        ]
    )

    assert people_from_plan(changes) == [
        {
            "full_name": "Audrey Le Cabec",
            "email": "",
            "orcid": "",
            "inat": "",
            "role": "Collector",
        }
    ]
