"""Plan People records and relations for canonical collector names."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any


def build_collector_people_plan(
    field_rows: list[dict[str, Any]], people_rows: list[dict[str, Any]]
) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    """Return missing people, safe links, and conflicting existing relations."""

    people_by_name = {_text(row.get("full_name")): row for row in people_rows}
    fields_by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in field_rows:
        if name := _text(row.get("collector_fullname")):
            fields_by_name[name].append(row)

    missing_people: list[dict[str, str]] = []
    links: list[dict[str, str]] = []
    conflicts: list[dict[str, str]] = []
    for name, rows in sorted(fields_by_name.items()):
        person = people_by_name.get(name)
        desired = {
            "full_name": name,
            "email": _text(person.get("email")) if person else "",
            "orcid": _single_identifier(rows, "collector_orcid", name),
            "inat": _single_inat(rows, name),
            "role": "Collector",
        }
        if person is None:
            missing_people.append(desired)
            person_id = ""
        else:
            person_id = _text(person.get("id"))
            if _text(person.get("role")) != "Collector":
                raise ValueError(f"People record {name!r} does not have Collector role")
            for field in ("orcid", "inat"):
                existing = _text(person.get(field))
                if (
                    existing
                    and desired[field]
                    and _identifier_key(existing) != _identifier_key(desired[field])
                ):
                    raise ValueError(
                        f"People record {name!r} has conflicting {field}: "
                        f"{existing!r} != {desired[field]!r}"
                    )

        for row in rows:
            current_ref = _relation_id(row.get("collector_ref"))
            change = {
                "field_data_id": _text(row.get("id")),
                "sample_id": _text(row.get("sample_id")),
                "project": _text(row.get("qfield_project")),
                "collector_fullname": name,
                "current_collector_ref": current_ref,
                "person_id": person_id,
            }
            if current_ref:
                if person_id and current_ref != person_id:
                    conflicts.append({**change, "conflict_reason": "wrong_collector_ref"})
                continue
            links.append(change)
    links.sort(key=lambda row: int(row["field_data_id"]))
    conflicts.sort(key=lambda row: int(row["field_data_id"]))
    return missing_people, links, conflicts


def validate_collector_links(
    links: list[dict[str, str]], current_by_id: dict[str, dict[str, Any]]
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Revalidate names and empty relations immediately before linking."""

    ready: list[dict[str, str]] = []
    conflicts: list[dict[str, str]] = []
    for link in links:
        row = current_by_id.get(link["field_data_id"])
        reasons: list[str] = []
        if row is None:
            reasons.append("field_data_not_found")
        else:
            if _text(row.get("sample_id")) != link["sample_id"]:
                reasons.append("sample_id_changed")
            if _text(row.get("qfield_project")) != link["project"]:
                reasons.append("project_changed")
            if _text(row.get("collector_fullname")) != link["collector_fullname"]:
                reasons.append("collector_fullname_changed")
            if _relation_id(row.get("collector_ref")):
                reasons.append("collector_ref_not_empty")
        if reasons:
            conflicts.append({**link, "conflict_reason": ";".join(reasons)})
        else:
            ready.append({**link, "preflight_status": "ready"})
    return ready, conflicts


def _single_identifier(rows: list[dict[str, Any]], field: str, name: str) -> str:
    values = {_text(row.get(field)) for row in rows if _text(row.get(field))}
    keys = {_identifier_key(value) for value in values}
    if len(keys) > 1:
        raise ValueError(f"Collector {name!r} has conflicting {field} values: {sorted(values)}")
    return min(values, default="")


def _single_inat(rows: list[dict[str, Any]], name: str) -> str:
    counts = Counter(_text(row.get("collector_inat")) for row in rows)
    counts.pop("", None)
    keys = {_identifier_key(value) for value in counts}
    if len(keys) > 1:
        raise ValueError(
            f"Collector {name!r} has conflicting collector_inat values: {sorted(counts)}"
        )
    if not counts:
        return ""
    return min(counts, key=lambda value: (-counts[value], not value.startswith("@"), value))


def _identifier_key(value: str) -> str:
    return value.strip().casefold().lstrip("@")


def _relation_id(value: object) -> str:
    return _text(value.get("id")) if isinstance(value, dict) else _text(value)


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""
