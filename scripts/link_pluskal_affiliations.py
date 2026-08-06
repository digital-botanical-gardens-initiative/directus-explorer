"""Create IOCB Prague/Pluskal affiliations and add Jade to COMMONS Lab.

The default mode is read-only. Commit mode creates/reuses the accurate IOCB
Prague institution and Tomáš Pluskal Group records, then creates the seven
approved Pluskal memberships and Jade Dandois's COMMONS Lab membership.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

from directus_explorer.config import SettingsError, load_settings
from directus_explorer.directus import DirectusClient, DirectusError, DirectusResponseError
from directus_explorer.research_groups import PLUSKAL_GROUP_PEOPLE, plan_memberships

OUTPUT_DIR = Path("data/working/research_groups")
INSTITUTION_NAME = (
    "Institute of Organic Chemistry and Biochemistry of the Czech Academy of Sciences (IOCB Prague)"
)
GROUP_NAME = "Tomáš Pluskal Group"


def main() -> None:
    args = _parse_args()
    client = DirectusClient(load_settings(env_file=args.env_file))
    client._authenticate()
    universities = client._get_items(collection="Universities", fields="*")
    institution = _zero_or_one(universities, "university_name", INSTITUTION_NAME)
    people = client._get_items(collection="People", fields="id,full_name")
    groups = client._get_items(
        collection="Research_Groups", fields="id,group_name,university_ref,website"
    )
    pluskal_group = _zero_or_one(groups, "group_name", GROUP_NAME)
    commons = _exactly_one(groups, "group_name", "COMMONS Lab")
    memberships = client._get_items(
        collection="People_Research_Groups",
        fields="id,person_ref,research_group_ref",
    )
    if pluskal_group and institution is None:
        raise DirectusError("Tomáš Pluskal Group exists without the expected IOCB record")
    if pluskal_group and _relation_id(pluskal_group.get("university_ref")) != str(
        institution["id"]
    ):
        raise DirectusError("Tomáš Pluskal Group is linked to a different institution")

    pluskal_group_id = int(pluskal_group["id"]) if pluskal_group else None
    pluskal_memberships = _memberships_for(memberships, pluskal_group_id)
    planned_pluskal = plan_memberships(
        people,
        pluskal_memberships,
        approved_names=PLUSKAL_GROUP_PEOPLE,
        group_id=pluskal_group_id,
        group_name=GROUP_NAME,
        university_name=INSTITUTION_NAME,
    )
    commons_id = int(commons["id"])
    planned_jade = plan_memberships(
        people,
        _memberships_for(memberships, commons_id),
        approved_names=("Jade Dandois",),
        group_id=commons_id,
        group_name="COMMONS Lab",
        university_name="University of Fribourg",
    )

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    preview = planned_pluskal + planned_jade
    preview_path = args.output_dir / f"pluskal_memberships_preview_{timestamp}.tsv"
    summary_path = args.output_dir / f"pluskal_summary_{timestamp}.json"
    _write_tsv(preview_path, preview, list(preview[0]) if preview else ["person_id"])
    summary: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "commit" if args.commit else "read_only_preflight",
        "institution_exists": institution is not None,
        "institution_to_create": None
        if institution
        else {
            "university_name": INSTITUTION_NAME,
            "country": "Czech Republic",
            "alpha_two": "CZ",
            "web_pages": "['https://www.uochb.cz/en']",
            "state": "Prague",
            "domains": "['uochb.cz']",
        },
        "pluskal_group_exists": pluskal_group is not None,
        "pluskal_memberships_to_create": len(planned_pluskal),
        "commons_memberships_to_create": len(planned_jade),
        "preview": str(preview_path),
    }
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"Pluskal memberships to create: {len(planned_pluskal)}")
    print(f"COMMONS memberships to create: {len(planned_jade)}")
    print(f"Summary: {summary_path}")
    if not args.commit:
        print("No Directus data was changed.")
        return

    if institution is None:
        institution = client._post_item(
            "Universities",
            {
                "status": "active",
                "university_name": INSTITUTION_NAME,
                "country": "Czech Republic",
                "alpha_two": "CZ",
                "web_pages": "['https://www.uochb.cz/en']",
                "state": "Prague",
                "domains": "['uochb.cz']",
            },
        )
    if pluskal_group is None:
        pluskal_group = client._post_item(
            "Research_Groups",
            {
                "group_name": GROUP_NAME,
                "university_ref": int(institution["id"]),
                "website": "https://pluskal.group.uochb.cz/en",
            },
        )
    elif _text(pluskal_group.get("website")) not in {
        "",
        "https://pluskal.group.uochb.cz/en",
    }:
        raise DirectusError("Existing Tomáš Pluskal Group has a different website")
    elif not _text(pluskal_group.get("website")):
        pluskal_group = client._patch_item(
            collection="Research_Groups",
            item_id=int(pluskal_group["id"]),
            payload={"website": "https://pluskal.group.uochb.cz/en"},
        )

    groups_by_name = {GROUP_NAME: int(pluskal_group["id"]), "COMMONS Lab": commons_id}
    all_memberships = client._get_items(
        collection="People_Research_Groups",
        fields="id,person_ref,research_group_ref",
    )
    ready = plan_memberships(
        people,
        _memberships_for(all_memberships, groups_by_name[GROUP_NAME]),
        approved_names=PLUSKAL_GROUP_PEOPLE,
        group_id=groups_by_name[GROUP_NAME],
        group_name=GROUP_NAME,
        university_name=INSTITUTION_NAME,
    ) + plan_memberships(
        people,
        _memberships_for(all_memberships, commons_id),
        approved_names=("Jade Dandois",),
        group_id=commons_id,
        group_name="COMMONS Lab",
        university_name="University of Fribourg",
    )
    journal_path = args.output_dir / f"pluskal_execution_{timestamp}.tsv"
    journal: list[dict[str, str]] = []
    for membership in ready:
        group_id = groups_by_name[membership["research_group_name"]]
        created = client._post_item(
            "People_Research_Groups",
            {
                "person_ref": int(membership["person_id"]),
                "research_group_ref": group_id,
            },
        )
        journal.append(
            {
                "created_at": datetime.now(UTC).isoformat(),
                "membership_id": str(created["id"]),
                **membership,
                "research_group_id": str(group_id),
                "status": "linked",
            }
        )
        _write_tsv(journal_path, journal, list(journal[0]))

    _verify(
        client,
        people=people,
        pluskal_group_id=int(pluskal_group["id"]),
        commons_id=commons_id,
    )
    summary.update(
        {
            "institution_id": int(institution["id"]),
            "pluskal_group_id": int(pluskal_group["id"]),
            "created_membership_count": len(journal),
            "execution_journal": str(journal_path),
        }
    )
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"IOCB Prague institution id: {institution['id']}")
    print(f"Tomáš Pluskal Group id: {pluskal_group['id']}")
    print(f"Created and verified {len(journal)} memberships.")


def _verify(
    client: DirectusClient,
    *,
    people: list[dict[str, Any]],
    pluskal_group_id: int,
    commons_id: int,
) -> None:
    memberships = client._get_items(
        collection="People_Research_Groups", fields="id,person_ref,research_group_ref"
    )
    pluskal_remaining = plan_memberships(
        people,
        _memberships_for(memberships, pluskal_group_id),
        approved_names=PLUSKAL_GROUP_PEOPLE,
        group_id=pluskal_group_id,
        group_name=GROUP_NAME,
        university_name=INSTITUTION_NAME,
    )
    jade_remaining = plan_memberships(
        people,
        _memberships_for(memberships, commons_id),
        approved_names=("Jade Dandois",),
        group_id=commons_id,
        group_name="COMMONS Lab",
        university_name="University of Fribourg",
    )
    if pluskal_remaining or jade_remaining:
        raise DirectusResponseError("Not every approved affiliation was created")


def _memberships_for(
    memberships: list[dict[str, Any]], group_id: int | None
) -> list[dict[str, Any]]:
    if group_id is None:
        return []
    return [
        row for row in memberships if _relation_id(row.get("research_group_ref")) == str(group_id)
    ]


def _zero_or_one(rows: list[dict[str, Any]], field: str, value: str) -> dict[str, Any] | None:
    matching = [row for row in rows if _text(row.get(field)) == value]
    if len(matching) > 1:
        raise DirectusError(f"Multiple {field}={value!r} rows exist")
    return matching[0] if matching else None


def _exactly_one(rows: list[dict[str, Any]], field: str, value: str) -> dict[str, Any]:
    matching = [row for row in rows if _text(row.get(field)) == value]
    if len(matching) != 1:
        raise DirectusError(f"Expected one {field}={value!r} row, found {len(matching)}")
    return matching[0]


def _write_tsv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def _relation_id(value: object) -> str:
    if isinstance(value, dict):
        value = value.get("id")
    return _text(value)


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env.admin"))
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--commit", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    try:
        main()
    except (DirectusError, DirectusResponseError, SettingsError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    except requests.RequestException as exc:
        raise SystemExit(f"Directus request failed: {exc}") from exc
