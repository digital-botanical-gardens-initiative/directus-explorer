"""Build row-level update manifests from reviewed taxon conflict groups."""

from __future__ import annotations

ACCEPT = "accept_proposal"
CLEAR = "clear_placeholder"
NON_UPDATE_DECISIONS = {"unresolved", "defer"}
VALID_DECISIONS = {ACCEPT, CLEAR, "override", *NON_UPDATE_DECISIONS}


def build_taxon_update_manifest(
    review_rows: list[dict[str, str]],
    audit_by_id: dict[str, dict[str, str]],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Validate completed decisions and expand grouped reviews to Field_Data rows."""

    incomplete = [row["review_id"] for row in review_rows if not row.get("decision", "")]
    invalid = [
        row["review_id"]
        for row in review_rows
        if row.get("decision", "") and row["decision"] not in VALID_DECISIONS
    ]
    overrides = [
        row["review_id"]
        for row in review_rows
        if row.get("decision") == "override"
        and not all(
            row.get(field, "").strip()
            for field in (
                "chosen_source_field",
                "chosen_verbatim_name",
                "chosen_col_accepted_name",
                "chosen_col_accepted_usage_id",
                "chosen_col_accepted_rank",
            )
        )
    ]
    if incomplete or invalid or overrides:
        details = []
        if incomplete:
            details.append(f"blank decisions: {', '.join(incomplete)}")
        if invalid:
            details.append(f"invalid decisions: {', '.join(invalid)}")
        if overrides:
            details.append(
                "overrides require an explicitly resolved CoL target: " + ", ".join(overrides)
            )
        raise ValueError("Cannot prepare update manifest; " + "; ".join(details))

    manifest: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    for review in review_rows:
        field_ids = review["field_data_ids"].split(";")
        sample_ids = review["sample_ids"].split(";")
        if len(field_ids) != len(sample_ids):
            raise ValueError(f"Mismatched ids for review {review['review_id']}")
        for field_id, sample_id in zip(field_ids, sample_ids, strict=True):
            if field_id not in audit_by_id:
                raise ValueError(f"Field_Data {field_id} is absent from the audit")
            if review["decision"] in NON_UPDATE_DECISIONS:
                skipped.append(
                    {
                        "review_id": review["review_id"],
                        "field_data_id": field_id,
                        "sample_id": sample_id,
                        "decision": review["decision"],
                    }
                )
                continue
            audit = audit_by_id[field_id]
            if review["decision"] == CLEAR:
                current_value = audit.get("stored_taxon_name_unified", "").strip()
                if not current_value:
                    raise ValueError(
                        f"Cannot clear an empty taxon_name_unified for Field_Data {field_id}"
                    )
                manifest.append(
                    {
                        "review_id": review["review_id"],
                        "project": review["project"],
                        "round": review["round"],
                        "field_data_id": field_id,
                        "sample_id": sample_id,
                        "current_taxon_name_unified": current_value,
                        "authoritative_source_field": "taxon_name_unified",
                        "authoritative_verbatim_name": current_value,
                        "new_taxon_name_unified": "",
                        "update_action": "clear_value",
                        "col_accepted_usage_id": "",
                        "col_accepted_rank": "",
                        "decision": review["decision"],
                        "confidence": review["confidence"],
                        "reviewed_at": review["reviewed_at"],
                        "review_note": review["review_note"],
                    }
                )
                continue
            is_override = review["decision"] == "override"
            has_chosen_col_target = bool(
                review.get("chosen_col_accepted_name", "").strip()
                and review.get("chosen_col_accepted_usage_id", "").strip()
                and review.get("chosen_col_accepted_rank", "").strip()
            )
            manifest.append(
                {
                    "review_id": review["review_id"],
                    "project": review["project"],
                    "round": review["round"],
                    "field_data_id": field_id,
                    "sample_id": sample_id,
                    "current_taxon_name_unified": audit.get(
                        "stored_taxon_name_unified", ""
                    ),
                    "authoritative_source_field": (
                        review["chosen_source_field"]
                        if is_override
                        else review["proposal_source"]
                    ),
                    "authoritative_verbatim_name": (
                        review["chosen_verbatim_name"]
                        if is_override
                        else review["proposal_verbatim_name"]
                    ),
                    "new_taxon_name_unified": (
                        review["chosen_col_accepted_name"]
                        if has_chosen_col_target
                        else review["col_accepted_name"]
                    ),
                    "update_action": "set_value",
                    "col_accepted_usage_id": (
                        review["chosen_col_accepted_usage_id"]
                        if has_chosen_col_target
                        else review["col_accepted_usage_id"]
                    ),
                    "col_accepted_rank": (
                        review["chosen_col_accepted_rank"]
                        if has_chosen_col_target
                        else review["col_accepted_rank"]
                    ),
                    "decision": review["decision"],
                    "confidence": review["confidence"],
                    "reviewed_at": review["reviewed_at"],
                    "review_note": review["review_note"],
                }
            )
    return manifest, skipped
