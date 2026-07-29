"""Audit the stored Directus Field_Data.taxon_name_unified column."""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from directus_explorer.config import load_settings
from directus_explorer.directus import DirectusClient, DirectusError, DirectusResponseError

OUTPUT_DIR = Path("data/output/directus_integrity/taxon_name_unified_audit")

PRIMARY_TAXON_FIELDS = ("taxon_name_final", "taxon_name", "sample_name")
FALLBACK_TAXON_FIELDS = ("name_proposition", "match_name", "MatchedCanonical", "species_name")
SOURCE_TAXON_FIELDS = (*PRIMARY_TAXON_FIELDS, *FALLBACK_TAXON_FIELDS)
PLACEHOLDER_VALUES = {
    "-",
    "--",
    "?",
    "na",
    "n.a.",
    "n/a",
    "nan",
    "none",
    "null",
    "unknown",
    "unkown",
    "not known",
    "undefined",
    "tbd",
    "inconnu",
    "inconnue",
    "indet",
    "indet.",
    "indetermined",
    "non identifie",
    "non identifié",
}
PLACEHOLDER_PATTERN = re.compile(r"(?:unknown|unkown|undefined|inconnu)", re.I)


def main() -> None:
    args = _parse_args()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    client = DirectusClient(load_settings(env_file=args.env_file))
    client._authenticate()
    rows = client._get_items(collection="Field_Data", fields="*")

    audit_rows = [_audit_row(row) for row in rows]
    issue_rows = [row for row in audit_rows if row["issues"]]
    missing_rows = [
        row
        for row in audit_rows
        if "missing_unified_taxon_name" in row["issues"].split(";")
    ]
    placeholder_rows = [
        row
        for row in audit_rows
        if "placeholder_unified_taxon_name" in row["issues"].split(";")
    ]
    conflict_rows = [
        row
        for row in audit_rows
        if "source_taxon_field_conflict" in row["issues"].split(";")
    ]
    mismatch_rows = [
        row
        for row in audit_rows
        if "stored_unified_differs_from_expected" in row["issues"].split(";")
    ]
    missing_expected_no_conflict_rows = [
        row
        for row in missing_rows
        if "stored_unified_missing_but_expected_available" in row["issues"].split(";")
        and "source_taxon_field_conflict" not in row["issues"].split(";")
    ]
    missing_expected_conflict_rows = [
        row
        for row in missing_rows
        if "source_taxon_field_conflict" in row["issues"].split(";")
    ]
    missing_no_information_rows = [
        row
        for row in missing_rows
        if "stored_unified_missing_but_expected_available" not in row["issues"].split(";")
    ]

    _write_tsv(OUTPUT_DIR / "taxon_name_unified_audit_all.tsv", audit_rows)
    _write_tsv(OUTPUT_DIR / "taxon_name_unified_issues.tsv", issue_rows)
    _write_tsv(OUTPUT_DIR / "taxon_name_unified_missing.tsv", missing_rows)
    _write_tsv(
        OUTPUT_DIR / "taxon_name_unified_missing_expected_no_conflict.tsv",
        missing_expected_no_conflict_rows,
    )
    _write_tsv(
        OUTPUT_DIR / "taxon_name_unified_missing_expected_conflict.tsv",
        missing_expected_conflict_rows,
    )
    _write_tsv(
        OUTPUT_DIR / "taxon_name_unified_missing_no_taxon_information.tsv",
        missing_no_information_rows,
    )
    _write_tsv(OUTPUT_DIR / "taxon_name_unified_placeholders.tsv", placeholder_rows)
    _write_tsv(OUTPUT_DIR / "taxon_name_unified_source_conflicts.tsv", conflict_rows)
    _write_tsv(OUTPUT_DIR / "taxon_name_unified_mismatches.tsv", mismatch_rows)
    _write_raw_issue_metadata(rows, {row["field_data_id"] for row in issue_rows})
    _write_summary(audit_rows)

    print(f"Wrote taxon_name_unified audit to {OUTPUT_DIR}")
    print(f"Rows audited: {len(audit_rows)}")
    print(f"Rows with issues: {len(issue_rows)}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    return parser.parse_args()


def _audit_row(row: dict[str, Any]) -> dict[str, str]:
    field_data_id = _clean(row.get("id"))
    project = _clean(row.get("qfield_project")) or "(missing)"
    sample_id = _clean(row.get("sample_id"))
    stored_unified = _clean(row.get("taxon_name_unified"))
    expected = _expected_unified_taxon(row)
    source_values = _source_values(row)

    issues: list[str] = []
    placeholder_status = _placeholder_status(stored_unified)
    if not stored_unified:
        issues.append("missing_unified_taxon_name")
    elif placeholder_status:
        issues.append("placeholder_unified_taxon_name")

    if expected["conflict"]:
        issues.append("source_taxon_field_conflict")

    if (
        stored_unified
        and expected["name"]
        and not expected["conflict"]
        and _normalize_taxon_value(stored_unified) != _normalize_taxon_value(expected["name"])
    ):
        issues.append("stored_unified_differs_from_expected")

    if not stored_unified and expected["name"]:
        issues.append("stored_unified_missing_but_expected_available")

    return {
        "project": project,
        "field_data_id": field_data_id,
        "sample_id": sample_id,
        "stored_taxon_name_unified": stored_unified,
        "stored_placeholder_status": placeholder_status,
        "expected_unified_taxon_name": expected["name"],
        "expected_unified_taxon_source": expected["source"],
        "expected_unified_taxon_status": expected["status"],
        "source_taxon_fields_present": expected["candidate_fields_present"],
        "source_taxon_field_conflict": expected["conflict"],
        "taxon_name_final": _clean(row.get("taxon_name_final")),
        "taxon_name": _clean(row.get("taxon_name")),
        "sample_name": _clean(row.get("sample_name")),
        "name_proposition": _clean(row.get("name_proposition")),
        "match_name": _clean(row.get("match_name")),
        "MatchedCanonical": _clean(row.get("MatchedCanonical")),
        "species_name": _clean(row.get("species_name")),
        "date": _clean(row.get("date")),
        "latitude": _clean(row.get("latitude")),
        "longitude": _clean(row.get("longitude")),
        "source_values_normalized": ";".join(
            f"{field}={_normalize_taxon_value(value)}" for field, value in source_values
        ),
        "issues": ";".join(issues),
    }


def _expected_unified_taxon(row: dict[str, Any]) -> dict[str, str]:
    values = _source_values(row)
    if not values:
        return {
            "name": "",
            "source": "",
            "status": "missing_all_taxon_information",
            "candidate_fields_present": "",
            "conflict": "",
        }

    source, name = values[0]
    normalized_values = {_normalize_taxon_value(value) for _, value in values}
    conflict = ""
    if len(normalized_values) > 1:
        conflict = ";".join(f"{field}={value}" for field, value in values)
    status = (
        "resolved_from_primary_taxon_field"
        if source in PRIMARY_TAXON_FIELDS
        else "resolved_from_fallback_taxon_field"
    )
    return {
        "name": name,
        "source": source,
        "status": status,
        "candidate_fields_present": ";".join(field for field, _ in values),
        "conflict": conflict,
    }


def _source_values(row: dict[str, Any]) -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = []
    for field in SOURCE_TAXON_FIELDS:
        value = _clean(row.get(field))
        if value and not _placeholder_status(value):
            values.append((field, value))
    return values


def _placeholder_status(value: str) -> str:
    normalized = _normalize_taxon_value(value)
    if not normalized:
        return ""
    if normalized in PLACEHOLDER_VALUES:
        return "exact_placeholder"
    if PLACEHOLDER_PATTERN.search(normalized):
        return "contains_placeholder_token"
    return ""


def _write_summary(rows: list[dict[str, str]]) -> None:
    project_rows: list[dict[str, Any]] = []
    rows_by_project: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        rows_by_project[row["project"]].append(row)

    for project, project_records in sorted(rows_by_project.items()):
        issue_counts = Counter(
            issue
            for row in project_records
            for issue in row["issues"].split(";")
            if issue
        )
        project_rows.append(
            {
                "project": project,
                "field_data_rows": len(project_records),
                "rows_with_any_issue": sum(bool(row["issues"]) for row in project_records),
                "missing_unified_taxon_name": issue_counts["missing_unified_taxon_name"],
                "placeholder_unified_taxon_name": issue_counts[
                    "placeholder_unified_taxon_name"
                ],
                "stored_unified_missing_but_expected_available": issue_counts[
                    "stored_unified_missing_but_expected_available"
                ],
                "source_taxon_field_conflict": issue_counts["source_taxon_field_conflict"],
                "stored_unified_differs_from_expected": issue_counts[
                    "stored_unified_differs_from_expected"
                ],
                "stored_unified_present_without_source_taxon_fields": sum(
                    bool(row["stored_taxon_name_unified"])
                    and not row["expected_unified_taxon_name"]
                    for row in project_records
                ),
            }
        )

    _write_tsv(OUTPUT_DIR / "taxon_name_unified_summary_by_project.tsv", project_rows)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "row_count": len(rows),
        "rows_with_any_issue": sum(bool(row["issues"]) for row in rows),
        "issue_counts": dict(
            Counter(issue for row in rows for issue in row["issues"].split(";") if issue)
        ),
        "stored_unified_present_without_source_taxon_fields_count": sum(
            bool(row["stored_taxon_name_unified"]) and not row["expected_unified_taxon_name"]
            for row in rows
        ),
        "placeholder_values": sorted(PLACEHOLDER_VALUES),
        "project_summary": project_rows,
    }
    (OUTPUT_DIR / "taxon_name_unified_summary.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _write_raw_issue_metadata(rows: list[dict[str, Any]], issue_ids: set[str]) -> None:
    raw_fields = sorted({key for row in rows for key in row})
    fieldnames = ["field_data_id", *[f"field.{field}" for field in raw_fields], "directus_row_json"]
    output_rows: list[dict[str, Any]] = []
    for row in rows:
        field_data_id = _clean(row.get("id"))
        if field_data_id not in issue_ids:
            continue
        output_rows.append(
            {
                "field_data_id": field_data_id,
                **{f"field.{key}": _tsv_value(value) for key, value in row.items()},
                "directus_row_json": json.dumps(row, ensure_ascii=False, sort_keys=True),
            }
        )
    _write_tsv_with_fieldnames(
        OUTPUT_DIR / "taxon_name_unified_issues_full_metadata.tsv",
        fieldnames,
        output_rows,
    )


def _write_tsv(path: Path, rows: list[dict[str, Any]]) -> None:
    if rows:
        fieldnames = list(rows[0])
    else:
        fieldnames = ["message"]
        rows = [{"message": "no_rows"}]
    _write_tsv_with_fieldnames(path, fieldnames, rows)


def _write_tsv_with_fieldnames(
    path: Path,
    fieldnames: list[str],
    rows: list[dict[str, Any]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def _normalize_taxon_value(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _tsv_value(value: Any) -> str:
    if isinstance(value, dict | list):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return _clean(value)


if __name__ == "__main__":
    try:
        main()
    except (DirectusError, DirectusResponseError) as exc:
        raise SystemExit(str(exc)) from exc
