"""Build explicit unresolved outcomes from taxon integrity audit rows."""

from __future__ import annotations

import hashlib

SOURCE_FIELDS = (
    "taxon_name_final",
    "taxon_name",
    "sample_name",
    "name_proposition",
    "match_name",
    "MatchedCanonical",
    "species_name",
)


def build_unresolved_review_rows(
    audit_rows: list[dict[str, str]],
    *,
    project: str,
    round_number: int,
    excluded_field_ids: set[str] | None = None,
    reviewed_at: str,
    reviewer: str = "",
) -> list[dict[str, str | int]]:
    """Group placeholder clears and evidence-free nulls into reviewed outcomes."""

    excluded = excluded_field_ids or set()
    project_rows = [
        row
        for row in audit_rows
        if row.get("project") == project and row.get("field_data_id") not in excluded
    ]
    placeholders = [
        row
        for row in project_rows
        if "placeholder_unified_taxon_name" in _issues(row)
    ]
    no_evidence = [
        row
        for row in project_rows
        if "missing_unified_taxon_name" in _issues(row)
        and not any(_usable_name(row.get(field, "")) for field in SOURCE_FIELDS)
    ]

    output: list[dict[str, str | int]] = []
    if placeholders:
        output.append(
            _review_group(
                placeholders,
                project=project,
                round_number=round_number,
                category="placeholder_clear",
                decision="clear_placeholder",
                proposal_source="taxon_name_unified",
                proposal_verbatim_name="aaunknown (case variants)",
                reviewed_at=reviewed_at,
                reviewer=reviewer,
                note=(
                    "Normalize explicit unknown placeholders to NULL; no taxonomic "
                    "identification is inferred."
                ),
            )
        )
    if no_evidence:
        output.append(
            _review_group(
                no_evidence,
                project=project,
                round_number=round_number,
                category="no_taxonomic_evidence",
                decision="unresolved",
                proposal_source="",
                proposal_verbatim_name="",
                reviewed_at=reviewed_at,
                reviewer=reviewer,
                note=(
                    "Reviewed unresolved: every taxonomic source field is empty; "
                    "taxon_name_unified remains NULL."
                ),
            )
        )
    return output


def _review_group(
    rows: list[dict[str, str]],
    *,
    project: str,
    round_number: int,
    category: str,
    decision: str,
    proposal_source: str,
    proposal_verbatim_name: str,
    reviewed_at: str,
    reviewer: str,
    note: str,
) -> dict[str, str | int]:
    field_ids = [row["field_data_id"] for row in rows]
    review_id = hashlib.sha256(
        f"{project}:{category}:{';'.join(field_ids)}".encode()
    ).hexdigest()[:12]
    return {
        "review_id": review_id,
        "project": project,
        "round": round_number,
        "affected_row_count": len(rows),
        "field_data_ids": ";".join(field_ids),
        "sample_ids": ";".join(row["sample_id"] for row in rows),
        **{field: "" for field in SOURCE_FIELDS},
        "proposal_source": proposal_source,
        "proposal_verbatim_name": proposal_verbatim_name,
        "proposal_basis": category,
        "col_match_type": "not_applicable",
        "col_matched_name": "",
        "col_matched_status": "",
        "col_accepted_name": "",
        "col_accepted_usage_id": "",
        "col_accepted_rank": "",
        "decision": decision,
        "chosen_source_field": "",
        "chosen_verbatim_name": "",
        "chosen_col_accepted_name": "",
        "chosen_col_accepted_usage_id": "",
        "chosen_col_accepted_rank": "",
        "confidence": "high",
        "reviewer": reviewer,
        "reviewed_at": reviewed_at,
        "review_note": note,
    }


def _issues(row: dict[str, str]) -> set[str]:
    return {value for value in row.get("issues", "").split(";") if value}


def _usable_name(value: str) -> bool:
    normalized = " ".join(value.casefold().split())
    return bool(normalized) and normalized not in {
        "-",
        "?",
        "aaunknown",
        "indet",
        "indet.",
        "n/a",
        "na",
        "none",
        "null",
        "unknown",
    }
