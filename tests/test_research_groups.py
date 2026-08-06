import pytest

from directus_explorer.research_groups import (
    COMMONS_LAB_PEOPLE,
    PEMG_PEOPLE,
    PLUSKAL_GROUP_PEOPLE,
    plan_group_memberships,
    plan_memberships,
)


def _people() -> list[dict[str, object]]:
    return [
        {"id": index, "full_name": name} for index, name in enumerate(COMMONS_LAB_PEOPLE, start=1)
    ]


def test_plans_all_memberships_before_group_creation() -> None:
    planned = plan_group_memberships(_people(), [], group_id=None)

    assert len(planned) == 9
    assert planned[0]["research_group_id"] == "pending"


def test_skips_existing_membership() -> None:
    planned = plan_group_memberships(
        _people(),
        [{"person_ref": 1, "research_group_ref": 4}],
        group_id=4,
    )

    assert len(planned) == 8
    assert planned[0]["full_name"] == "Héloïse Coen"


def test_refuses_missing_people() -> None:
    with pytest.raises(ValueError, match="Stephanie Tandem Guetchueng"):
        plan_group_memberships(_people()[:-1], [], group_id=None)


def test_plans_pemg_memberships() -> None:
    people = [{"id": index, "full_name": name} for index, name in enumerate(PEMG_PEOPLE, start=1)]

    planned = plan_memberships(
        people,
        [],
        approved_names=PEMG_PEOPLE,
        group_id=2,
        group_name="PEMG",
        university_name="Université de Neuchâtel",
    )

    assert len(planned) == 10
    assert {row["research_group_name"] for row in planned} == {"PEMG"}


def test_plans_pluskal_group_memberships() -> None:
    people = [
        {"id": index, "full_name": name} for index, name in enumerate(PLUSKAL_GROUP_PEOPLE, start=1)
    ]

    planned = plan_memberships(
        people,
        [],
        approved_names=PLUSKAL_GROUP_PEOPLE,
        group_id=None,
        group_name="Tomáš Pluskal Group",
        university_name="IOCB Prague",
    )

    assert len(planned) == 7
