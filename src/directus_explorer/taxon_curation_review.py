"""Helpers for preserving and recording human taxon review decisions."""

from __future__ import annotations

DECISION_FIELDS = (
    "decision",
    "chosen_source_field",
    "chosen_verbatim_name",
    "chosen_col_accepted_name",
    "chosen_col_accepted_usage_id",
    "chosen_col_accepted_rank",
    "confidence",
    "reviewer",
    "reviewed_at",
    "review_note",
)


def preserve_review_decisions(
    generated_rows: list[dict[str, str | int]],
    existing_rows: list[dict[str, str]],
) -> list[dict[str, str | int]]:
    """Copy human-entered decision fields into regenerated review rows."""

    existing_by_id = {row.get("review_id", ""): row for row in existing_rows}
    for generated in generated_rows:
        existing = existing_by_id.get(str(generated.get("review_id", "")))
        if existing is None:
            continue
        for field in DECISION_FIELDS:
            if existing.get(field, ""):
                generated[field] = existing[field]
    return generated_rows


def accept_direct_col_matches(
    rows: list[dict[str, str]], *, reviewed_at: str, reviewer: str = ""
) -> tuple[list[dict[str, str]], int]:
    """Accept pending rows with an unambiguous direct pinned-CoL target."""

    accepted = 0
    for row in rows:
        if row.get("decision", "").strip():
            continue
        if not row.get("col_accepted_usage_id", "").strip():
            continue
        if not row.get("col_accepted_name", "").strip():
            continue
        if row.get("col_match_type", "").strip().lower() not in {"exact", "variant"}:
            continue
        if row.get("col_matched_status", "").strip().lower() not in {
            "accepted",
            "synonym",
        }:
            continue
        row["decision"] = "accept_proposal"
        row["confidence"] = "high"
        row["reviewer"] = reviewer
        row["reviewed_at"] = reviewed_at
        row["review_note"] = (
            "Accepted single non-conflicting source candidate with direct pinned-CoL match."
        )
        accepted += 1
    return rows, accepted
