"""Link approved PEMG people and expand Lendita's canonical full name.

The default mode is read-only. Commit mode creates/reuses PEMG under the
existing Université de Neuchâtel, creates its approved memberships, renames the
Lendita People record to Lëndita Schwegler, and synchronizes linked Field_Data
collector names with row-level revalidation and journaling.
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
from directus_explorer.research_groups import PEMG_PEOPLE, plan_memberships

OUTPUT_DIR = Path("data/working/research_groups")
OLD_LENDITA_NAME = "Lendita"
NEW_LENDITA_NAME = "Lëndita Schwegler"


def main() -> None:
    args = _parse_args()
    client = DirectusClient(load_settings(env_file=args.env_file))
    client._authenticate()
    university = _one_by_name(
        client._get_items(collection="Universities", fields="id,university_name"),
        field="university_name",
        value="Université de Neuchâtel",
    )
    people = client._get_items(collection="People", fields="id,full_name,email")
    lendita = _lendita_person(people)
    field_rows = client._get_items(
        collection="Field_Data",
        fields="id,sample_id,qfield_project,collector_fullname,collector_ref",
    )
    lendita_rows = [
        row for row in field_rows if _relation_id(row.get("collector_ref")) == str(lendita["id"])
    ]
    invalid_names = {
        _text(row.get("collector_fullname"))
        for row in lendita_rows
        if _text(row.get("collector_fullname")) not in {OLD_LENDITA_NAME, NEW_LENDITA_NAME}
    }
    if invalid_names:
        raise DirectusError(
            "Lendita-linked rows contain unexpected names: " + ", ".join(sorted(invalid_names))
        )
    rename_rows = [
        {
            "field_data_id": str(row["id"]),
            "sample_id": _text(row.get("sample_id")),
            "project": _text(row.get("qfield_project")),
            "current_collector_fullname": OLD_LENDITA_NAME,
            "new_collector_fullname": NEW_LENDITA_NAME,
            "collector_ref": str(lendita["id"]),
        }
        for row in lendita_rows
        if _text(row.get("collector_fullname")) == OLD_LENDITA_NAME
    ]

    website_field_exists = "website" in _fields(client, "Research_Groups")
    groups = client._get_items(
        collection="Research_Groups",
        fields="id,group_name,university_ref" + (",website" if website_field_exists else ""),
    )
    matching_groups = [row for row in groups if _text(row.get("group_name")) == "PEMG"]
    if len(matching_groups) > 1:
        raise DirectusError("Multiple PEMG research-group records exist")
    group = matching_groups[0] if matching_groups else None
    if group and _relation_id(group.get("university_ref")) != str(university["id"]):
        raise DirectusError("Existing PEMG record is linked to a different university")
    memberships = client._get_items(
        collection="People_Research_Groups",
        fields="id,person_ref,research_group_ref",
    )
    group_id = int(group["id"]) if group else None
    group_memberships = [
        row
        for row in memberships
        if group_id is not None and _relation_id(row.get("research_group_ref")) == str(group_id)
    ]
    planned_memberships = plan_memberships(
        people,
        group_memberships,
        approved_names=PEMG_PEOPLE,
        group_id=group_id,
        group_name="PEMG",
        university_name="Université de Neuchâtel",
    )

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rename_path = args.output_dir / f"lendita_rename_preview_{timestamp}.tsv"
    memberships_path = args.output_dir / f"pemg_memberships_preview_{timestamp}.tsv"
    summary_path = args.output_dir / f"pemg_summary_{timestamp}.json"
    _write_tsv(
        rename_path,
        rename_rows,
        list(rename_rows[0]) if rename_rows else ["field_data_id"],
    )
    _write_tsv(
        memberships_path,
        planned_memberships,
        list(planned_memberships[0]) if planned_memberships else ["person_id"],
    )
    summary: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "commit" if args.commit else "read_only_preflight",
        "university": university,
        "website_field_action": (
            "none" if website_field_exists else "create_field:Research_Groups.website"
        ),
        "pemg_exists": group is not None,
        "planned_membership_count": len(planned_memberships),
        "lendita_person_id": lendita["id"],
        "lendita_current_name": lendita["full_name"],
        "lendita_new_name": NEW_LENDITA_NAME,
        "planned_field_rename_count": len(rename_rows),
        "rename_preview": str(rename_path),
        "memberships_preview": str(memberships_path),
    }
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"PEMG memberships to create: {len(planned_memberships)}")
    print(f"Lendita Field_Data names to update: {len(rename_rows)}")
    print(f"Summary: {summary_path}")
    if not args.commit:
        print("No Directus schema or data was changed.")
        return

    if not website_field_exists:
        _post(
            client,
            "/fields/Research_Groups",
            {
                "field": "website",
                "type": "string",
                "schema": {"is_nullable": True, "max_length": 255},
                "meta": {"interface": "input", "sort": 5},
            },
        )
    if group is None:
        group = client._post_item(
            "Research_Groups",
            {
                "group_name": "PEMG",
                "university_ref": int(university["id"]),
                "website": "https://pemg.ch",
            },
        )
    elif _text(group.get("website")) not in {"", "https://pemg.ch"}:
        raise DirectusError("Existing PEMG record has a different website")
    elif not _text(group.get("website")):
        group = client._patch_item(
            collection="Research_Groups",
            item_id=int(group["id"]),
            payload={"website": "https://pemg.ch"},
        )
    group_id = int(group["id"])

    current_people = client._get_items(collection="People", fields="id,full_name,email")
    current_lendita = _lendita_person(current_people)
    current_fields = _fetch_field_rows(client, [row["field_data_id"] for row in rename_rows])
    current_by_id = {str(row["id"]): row for row in current_fields}
    conflicts = [
        row["field_data_id"]
        for row in rename_rows
        if _text(current_by_id.get(row["field_data_id"], {}).get("collector_fullname"))
        != OLD_LENDITA_NAME
        or _relation_id(current_by_id.get(row["field_data_id"], {}).get("collector_ref"))
        != str(current_lendita["id"])
    ]
    if conflicts:
        raise DirectusError("Lendita rows changed after preflight: " + ", ".join(conflicts))

    if _text(current_lendita.get("full_name")) == OLD_LENDITA_NAME:
        client._patch_item(
            collection="People",
            item_id=int(current_lendita["id"]),
            payload={"full_name": NEW_LENDITA_NAME},
        )
    rename_journal_path = args.output_dir / f"lendita_rename_execution_{timestamp}.tsv"
    rename_journal: list[dict[str, str]] = []
    for row in rename_rows:
        client._patch_item(
            collection="Field_Data",
            item_id=int(row["field_data_id"]),
            payload={"collector_fullname": NEW_LENDITA_NAME},
        )
        rename_journal.append(
            {"updated_at": datetime.now(UTC).isoformat(), **row, "status": "updated"}
        )
        _write_tsv(rename_journal_path, rename_journal, list(rename_journal[0]))

    all_people = client._get_items(collection="People", fields="id,full_name")
    all_memberships = client._get_items(
        collection="People_Research_Groups",
        fields="id,person_ref,research_group_ref",
    )
    group_memberships = [
        row
        for row in all_memberships
        if _relation_id(row.get("research_group_ref")) == str(group_id)
    ]
    planned_memberships = plan_memberships(
        all_people,
        group_memberships,
        approved_names=PEMG_PEOPLE,
        group_id=group_id,
        group_name="PEMG",
        university_name="Université de Neuchâtel",
    )
    membership_journal_path = args.output_dir / f"pemg_execution_{timestamp}.tsv"
    membership_journal: list[dict[str, str]] = []
    for membership in planned_memberships:
        created = client._post_item(
            "People_Research_Groups",
            {
                "person_ref": int(membership["person_id"]),
                "research_group_ref": group_id,
            },
        )
        membership_journal.append(
            {
                "created_at": datetime.now(UTC).isoformat(),
                "membership_id": str(created["id"]),
                **membership,
                "status": "linked",
            }
        )
        _write_tsv(
            membership_journal_path,
            membership_journal,
            list(membership_journal[0]),
        )

    _verify(client, group_id=group_id, lendita_id=int(lendita["id"]))
    summary.update(
        {
            "pemg_id": group_id,
            "created_membership_count": len(membership_journal),
            "updated_field_name_count": len(rename_journal),
            "membership_execution": str(membership_journal_path),
            "rename_execution": str(rename_journal_path),
        }
    )
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"PEMG id: {group_id}; memberships verified: {len(PEMG_PEOPLE)}")
    print(f"Renamed and verified {len(lendita_rows)} Lëndita-linked sample rows.")


def _verify(client: DirectusClient, *, group_id: int, lendita_id: int) -> None:
    people = client._get_items(collection="People", fields="id,full_name")
    memberships = client._get_items(
        collection="People_Research_Groups", fields="id,person_ref,research_group_ref"
    )
    group_memberships = [
        row for row in memberships if _relation_id(row.get("research_group_ref")) == str(group_id)
    ]
    remaining = plan_memberships(
        people,
        group_memberships,
        approved_names=PEMG_PEOPLE,
        group_id=group_id,
        group_name="PEMG",
        university_name="Université de Neuchâtel",
    )
    if remaining:
        raise DirectusResponseError("Not every approved PEMG membership was created")
    lendita = _one_by_name(people, field="id", value=str(lendita_id))
    if _text(lendita.get("full_name")) != NEW_LENDITA_NAME:
        raise DirectusResponseError("Lëndita People rename was not preserved")
    fields = client._get_items(
        collection="Field_Data", fields="id,collector_fullname,collector_ref"
    )
    linked = [row for row in fields if _relation_id(row.get("collector_ref")) == str(lendita_id)]
    if not linked or any(
        _text(row.get("collector_fullname")) != NEW_LENDITA_NAME for row in linked
    ):
        raise DirectusResponseError("Lëndita Field_Data names are not synchronized")


def _lendita_person(people: list[dict[str, Any]]) -> dict[str, Any]:
    matching = [
        row
        for row in people
        if _text(row.get("full_name")) in {OLD_LENDITA_NAME, NEW_LENDITA_NAME}
        or _text(row.get("email")) == "lendita.kashtanjeva@unifr.ch"
    ]
    unique = {str(row["id"]): row for row in matching}
    if len(unique) != 1:
        raise DirectusError(f"Expected one Lendita People record, found {len(unique)}")
    return next(iter(unique.values()))


def _one_by_name(rows: list[dict[str, Any]], *, field: str, value: str) -> dict[str, Any]:
    matching = [row for row in rows if _text(row.get(field)) == value]
    if len(matching) != 1:
        raise DirectusError(f"Expected one {field}={value!r} row, found {len(matching)}")
    return matching[0]


def _fetch_field_rows(client: DirectusClient, ids: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for offset in range(0, len(ids), 100):
        payload = client._get(
            "/items/Field_Data",
            {
                "limit": "-1",
                "fields": "id,sample_id,qfield_project,collector_fullname,collector_ref",
                "filter[id][_in]": ",".join(ids[offset : offset + 100]),
            },
        )
        data = payload.get("data")
        if not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
            raise DirectusResponseError("Directus Field_Data query did not return a row list")
        rows.extend(data)
    return rows


def _fields(client: DirectusClient, collection: str) -> dict[str, str]:
    data = client._get(f"/fields/{collection}", {"limit": "-1"}).get("data") or []
    return {
        str(row["field"]): str(row["type"])
        for row in data
        if isinstance(row, dict) and row.get("field") and row.get("type")
    }


def _post(client: DirectusClient, path: str, payload: dict[str, Any]) -> None:
    response = client._session.post(client._url(path), json=payload, timeout=(10, 60))
    if response.status_code not in {200, 201, 204}:
        raise DirectusError(
            f"Directus POST {path} failed with status {response.status_code}: {response.text}"
        )


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
