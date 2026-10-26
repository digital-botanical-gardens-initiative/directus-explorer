"""Classify live Field_Data taxon curation against an audit and pinned CoL Taxa."""

from __future__ import annotations


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _relation_id(value: object) -> str:
    return _text(value.get("id")) if isinstance(value, dict) else _text(value)


def _normalized_name(value: str) -> str:
    return " ".join(value.split()).casefold()


def build_worklist(
    audit_rows: list[dict[str, str]],
    field_rows: list[dict],
    taxa_rows: list[dict],
    project: str,
    version: str,
) -> dict:
    """Return all project rows and their live completion status, without side effects.

    Audit and live Field_Data must describe the same selected-project records.
    A mismatch means the inputs must be refreshed together, never silently omitted.
    """
    projects = sorted({
        _text(row.get("project")) for row in audit_rows if _text(row.get("project"))
    })
    audits: dict[str, dict[str, str]] = {}
    for row in audit_rows:
        if _text(row.get("project")) != project:
            continue
        record_id = _text(row.get("field_data_id"))
        if not record_id or record_id in audits:
            raise ValueError(
                f"Invalid or duplicate audit Field_Data ID {record_id!r}; Refresh again."
            )
        audits[record_id] = row

    taxa_by_id: dict[str, str] = {}
    for row in taxa_rows:
        taxon_id = _text(row.get("id"))
        if taxon_id:
            taxa_by_id[taxon_id] = _text(row.get("col_id"))

    records: list[dict] = []
    seen: set[str] = set()
    for raw in field_rows:
        if _text(raw.get("qfield_project")) != project:
            continue
        record_id = _text(raw.get("id"))
        audit = audits.get(record_id)
        if not record_id or record_id in seen or audit is None:
            raise ValueError(
                f"Field_Data {record_id!r} has no unique matching audit row; Refresh again."
            )
        seen.add(record_id)
        name = _text(raw.get("taxon_name_unified"))
        if name != _text(audit.get("stored_taxon_name_unified")) or (
            "sample_id" in audit and _text(raw.get("sample_id")) != _text(audit.get("sample_id"))
        ):
            raise ValueError(f"Field_Data {record_id} changed since audit; Refresh again.")

        col_id = _text(raw.get("taxon_col_id"))
        ref_id = _relation_id(raw.get("taxon_ref"))
        status = _text(raw.get("taxon_resolution_status"))
        pinned = _text(raw.get("taxon_backbone_version")) == version
        if not name:
            state, reason = "missing_name", "Unified taxon name is missing."
        elif pinned and col_id and ref_id and taxa_by_id.get(ref_id) == col_id:
            state, reason = "complete", "CoL ID and linked Taxa match the pinned backbone."
        elif pinned and status == "reviewed_unresolved" and not col_id and not ref_id:
            state, reason = (
                "complete", "Explicitly reviewed unresolved against the pinned backbone."
            )
        else:
            state, reason = "pending_link", "CoL Taxa link or pinned resolution is incomplete."
        records.append({"audit": audit, "raw": raw, "name": name, "state": state, "reason": reason})

    if seen != audits.keys():
        missing = sorted(audits.keys() - seen)
        raise ValueError(
            f"Audit Field_Data rows {missing} missing from live project; Refresh again."
        )
    records.sort(key=lambda record: (
        _normalized_name(record["name"]),
        _text(record["raw"].get("sample_id")),
        _text(record["raw"].get("id")),
    ))
    complete_count = sum(record["state"] == "complete" for record in records)
    return {
        "projects": projects,
        "records": records,
        "pending_count": len(records) - complete_count,
        "complete_count": complete_count,
        "project_count": len(records),
    }
