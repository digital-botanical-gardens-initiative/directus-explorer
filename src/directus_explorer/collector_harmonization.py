"""Plan deterministic harmonization of collector names."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from typing import Any


def collector_identity_key(value: object) -> tuple[str, ...]:
    """Return an accent-, case-, punctuation-, and token-order-neutral name key."""

    text = _text(value)
    if not text:
        return ()
    ascii_text = "".join(
        character
        for character in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(character)
    )
    return tuple(sorted(re.findall(r"[^\W_]+", ascii_text.casefold())))


def build_collector_harmonization_plan(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Return variant groups and row-level changes inferred from observed frequency."""

    grouped: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if key := collector_identity_key(row.get("collector_fullname")):
            grouped[key].append(row)

    groups: list[dict[str, str]] = []
    changes: list[dict[str, str]] = []
    for identity_key, group_rows in sorted(grouped.items()):
        counts = Counter(_text(row.get("collector_fullname")) for row in group_rows)
        if len(counts) < 2:
            continue
        canonical = min(
            counts,
            key=lambda value: (-counts[value], -_diacritic_count(value), value.casefold(), value),
        )
        variants = sorted(counts)
        groups.append(
            {
                "identity_key": " ".join(identity_key),
                "canonical_collector_fullname": canonical,
                "variants": "; ".join(f"{variant} ({counts[variant]})" for variant in variants),
                "row_count": str(len(group_rows)),
                "update_count": str(len(group_rows) - counts[canonical]),
            }
        )
        for row in group_rows:
            current = _text(row.get("collector_fullname"))
            if current == canonical:
                continue
            changes.append(
                {
                    "field_data_id": _text(row.get("id")),
                    "sample_id": _text(row.get("sample_id")),
                    "project": _text(row.get("qfield_project")),
                    "current_collector_fullname": current,
                    "new_collector_fullname": canonical,
                    "collector_orcid": _text(row.get("collector_orcid")),
                    "collector_inat": _text(row.get("collector_inat")),
                }
            )
    changes.sort(key=lambda row: int(row["field_data_id"]))
    return groups, changes


def validate_collector_changes(
    changes: list[dict[str, str]], current_by_id: dict[str, dict[str, Any]]
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Split a plan into safe rows and rows that no longer match live state."""

    ready: list[dict[str, str]] = []
    conflicts: list[dict[str, str]] = []
    for change in changes:
        current = current_by_id.get(change["field_data_id"])
        reasons: list[str] = []
        if current is None:
            reasons.append("field_data_not_found")
        else:
            if _text(current.get("sample_id")) != change["sample_id"]:
                reasons.append("sample_id_changed")
            if _text(current.get("qfield_project")) != change["project"]:
                reasons.append("project_changed")
            if _text(current.get("collector_fullname")) != change["current_collector_fullname"]:
                reasons.append("collector_fullname_changed")
        if reasons:
            conflicts.append({**change, "conflict_reason": ";".join(reasons)})
        else:
            ready.append({**change, "preflight_status": "ready"})
    return ready, conflicts


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _diacritic_count(value: str) -> int:
    return sum(
        unicodedata.combining(character) != 0 for character in unicodedata.normalize("NFD", value)
    )
