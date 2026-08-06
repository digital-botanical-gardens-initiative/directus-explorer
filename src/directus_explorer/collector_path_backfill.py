"""Plan collector backfills from the first directory below ``DCIM``."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

PICTURE_FIELDS = (
    "picture_panel",
    "picture_general",
    "picture_detail",
    "picture_cut",
    "picture_panel_label",
    "picture_free",
)


@dataclass(frozen=True, slots=True)
class CollectorPerson:
    full_name: str
    email: str = ""
    orcid: str = ""
    inat: str = ""


PATH_LAYER_PEOPLE: dict[str, CollectorPerson] = {
    "edouard_brulhart_mw_2023": CollectorPerson(
        "Edouard Brülhart",
        orcid="0000-0003-3950-4065",
        inat="@edouardbruelhart",
    ),
    "edouard_bruelhart": CollectorPerson(
        "Edouard Brülhart",
        orcid="0000-0003-3950-4065",
        inat="@edouardbruelhart",
    ),
    "heloise_coen": CollectorPerson(
        "Héloïse Coen",
        orcid="0009-0009-8832-7457",
        inat="@hcoen",
    ),
    "emmanuel_defossez": CollectorPerson(
        "Emmanuel Defossez",
        orcid="0000-0002-3279-9190",
        inat="@manu_dfz",
    ),
    "teo_valentino": CollectorPerson(
        "Lëndita Schwegler",
        email="lendita.kashtanjeva@unifr.ch",
    ),
    "clement_duckert": CollectorPerson("Clement Duckert"),
    "audrey_layer": CollectorPerson("Audrey Le Cabec"),
    "audrey_le_cabec": CollectorPerson("Audrey Le Cabec"),
    "stephanie_guetchueng": CollectorPerson("Stephanie Tandem Guetchueng"),
    "stephanie_sampleacer opalus mill.dbgi_001091dcim": CollectorPerson(
        "Stephanie Tandem Guetchueng"
    ),
    "stephanie_samplecitrus limon (l.) osbeckdbgi_001084dcim": CollectorPerson(
        "Stephanie Tandem Guetchueng"
    ),
    "stephanie_samplecitrus reticulata blancodbgi_001087dcim": CollectorPerson(
        "Stephanie Tandem Guetchueng"
    ),
    "stephanie_samplecotinus coggygria scop. 'purpureus'dbgi_000909dcim": CollectorPerson(
        "Stephanie Tandem Guetchueng"
    ),
}


def dcim_layers(row: dict[str, Any]) -> set[str]:
    """Return the first directory under DCIM found in any picture field."""

    layers: set[str] = set()
    for field in PICTURE_FIELDS:
        path = _text(row.get(field)).replace("\\", "/")
        match = re.search(r"(?:^|/)DCIM/([^/]+)/", path, re.IGNORECASE)
        if match:
            layers.add(match.group(1))
    return layers


def build_collector_path_backfill(
    rows: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Build row changes for empty collectors with an approved path mapping."""

    changes: list[dict[str, str]] = []
    for row in rows:
        if _text(row.get("collector_fullname")):
            continue
        matches = {
            (layer, PATH_LAYER_PEOPLE[layer.casefold()])
            for layer in dcim_layers(row)
            if layer.casefold() in PATH_LAYER_PEOPLE
        }
        people = {person for _, person in matches}
        if not people:
            continue
        if len(people) != 1:
            names = ", ".join(sorted(person.full_name for person in people))
            raise ValueError(f"Field_Data {row.get('id')} maps to multiple people: {names}")
        person = people.pop()
        changes.append(
            {
                "field_data_id": _text(row.get("id")),
                "sample_id": _text(row.get("sample_id")),
                "project": _text(row.get("qfield_project")),
                "matched_dcim_layers": ";".join(sorted(layer for layer, _ in matches)),
                "current_collector_fullname": "",
                "new_collector_fullname": person.full_name,
                "person_email": person.email,
                "person_orcid": person.orcid,
                "person_inat": person.inat,
                "current_collector_ref": _text(row.get("collector_ref")),
            }
        )
    changes.sort(key=lambda row: int(row["field_data_id"]))
    return changes


def validate_collector_path_backfill(
    changes: list[dict[str, str]], current_by_id: dict[str, dict[str, Any]]
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Revalidate identity, empty target fields, and approved path evidence."""

    ready: list[dict[str, str]] = []
    conflicts: list[dict[str, str]] = []
    for change in changes:
        row = current_by_id.get(change["field_data_id"])
        reasons: list[str] = []
        if row is None:
            reasons.append("field_data_not_found")
        else:
            if _text(row.get("sample_id")) != change["sample_id"]:
                reasons.append("sample_id_changed")
            if _text(row.get("qfield_project")) != change["project"]:
                reasons.append("project_changed")
            if _text(row.get("collector_fullname")):
                reasons.append("collector_fullname_not_empty")
            if _text(row.get("collector_ref")):
                reasons.append("collector_ref_not_empty")
            current_layers = ";".join(
                sorted(layer for layer in dcim_layers(row) if layer.casefold() in PATH_LAYER_PEOPLE)
            )
            if current_layers != change["matched_dcim_layers"]:
                reasons.append("dcim_path_evidence_changed")
        if reasons:
            conflicts.append({**change, "conflict_reason": ";".join(reasons)})
        else:
            ready.append({**change, "preflight_status": "ready"})
    return ready, conflicts


def people_from_plan(changes: list[dict[str, str]]) -> list[dict[str, str]]:
    """Return unique People records required by a row-level plan."""

    people: dict[str, dict[str, str]] = {}
    for row in changes:
        people[row["new_collector_fullname"]] = {
            "full_name": row["new_collector_fullname"],
            "email": row["person_email"],
            "orcid": row["person_orcid"],
            "inat": row["person_inat"],
            "role": "Collector",
        }
    return [people[name] for name in sorted(people)]


def _text(value: object) -> str:
    if isinstance(value, dict):
        value = value.get("id")
    return str(value).strip() if value is not None else ""
