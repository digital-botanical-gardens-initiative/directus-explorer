"""Helpers for linking People to research groups."""

from __future__ import annotations

from typing import Any

COMMONS_LAB_PEOPLE = (
    "Edouard Brülhart",
    "Héloïse Coen",
    "Lëndita Schwegler",
    "Simon Rérat",
    "Pierre-Marie Allard",
    "Audrey Le Cabec",
    "Maëlle Wannier",
    "Marco Andreas Stanley Visani",
    "Stephanie Tandem Guetchueng",
)

PEMG_PEOPLE = (
    "Alexandra Slotte",
    "Alicia Requena",
    "Chloé Blanc",
    "Clement Duckert",
    "Emmanuel Defossez",
    "Lise Lebrun",
    "Loic Chalmandrier",
    "Lysandre Journiac",
    "Mazzarine Laboureau",
    "Émilie Lab",
)

PLUSKAL_GROUP_PEOPLE = (
    "Alžběta Kadlecová",
    "Anna Maliukova",
    "Davide Rovelli",
    "Federico Brigante",
    "Kateřina Kučerová",
    "Tito Damiani",
    "Tomáš Iliaš",
)


def plan_group_memberships(
    people_rows: list[dict[str, Any]],
    membership_rows: list[dict[str, Any]],
    *,
    group_id: int | None,
) -> list[dict[str, str]]:
    """Return missing memberships for the approved COMMONS Lab people."""

    return plan_memberships(
        people_rows,
        membership_rows,
        approved_names=COMMONS_LAB_PEOPLE,
        group_id=group_id,
        group_name="COMMONS Lab",
        university_name="University of Fribourg",
    )


def plan_memberships(
    people_rows: list[dict[str, Any]],
    membership_rows: list[dict[str, Any]],
    *,
    approved_names: tuple[str, ...],
    group_id: int | None,
    group_name: str,
    university_name: str,
) -> list[dict[str, str]]:
    """Return missing memberships for an approved set of people."""

    people_by_name = {_text(row.get("full_name")): row for row in people_rows}
    missing_people = [name for name in approved_names if name not in people_by_name]
    if missing_people:
        raise ValueError("People records not found: " + ", ".join(missing_people))

    existing_pairs = {
        (_relation_id(row.get("person_ref")), _relation_id(row.get("research_group_ref")))
        for row in membership_rows
    }
    planned: list[dict[str, str]] = []
    for name in approved_names:
        person_id = _text(people_by_name[name].get("id"))
        pair = (person_id, str(group_id)) if group_id is not None else None
        if pair is not None and pair in existing_pairs:
            continue
        planned.append(
            {
                "person_id": person_id,
                "full_name": name,
                "research_group_id": str(group_id) if group_id is not None else "pending",
                "research_group_name": group_name,
                "university_name": university_name,
            }
        )
    return planned


def _relation_id(value: object) -> str:
    return _text(value.get("id")) if isinstance(value, dict) else _text(value)


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""
