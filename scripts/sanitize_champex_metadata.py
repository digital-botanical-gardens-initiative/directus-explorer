"""Sanitize the Champex massive metadata CSV without mutating the original file."""

from __future__ import annotations

import csv
import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

RAW_PATH = Path("data/working/champex_original/metadata_massive_champex.csv")
OUTPUT_DIR = Path("data/output/champex")
SANITIZED_PATH = OUTPUT_DIR / "metadata_massive_champex_sanitized.csv"
ISSUES_PATH = OUTPUT_DIR / "metadata_massive_champex_sanitization_issues.tsv"
SUMMARY_PATH = OUTPUT_DIR / "metadata_massive_champex_sanitization_summary.json"


HEADER_RENAMES = {
    "Extension": "extension",
    "Localisation.in.Sciex.OS.project": "localisation_in_sciex_os_project",
    "ATTRIBUTE_Species": "attribute_species",
    "NCBITaxonomy_ID": "ncbi_taxonomy_id",
    "Ionization mode": "ionization_mode",
    "date": "raw_date",
}

NA_VALUES = {"", "NA", "N/A", "na", "n/a", "NULL", "null"}
ROW_MARKER_VALUES = {"QC", "Blank"}
ROW_MARKER_FIELDS = {"filename", "sample_id", "type", "ionization_mode", "garden"}
BOOLEAN_COLUMNS = {"inat_upload", "is_wild", "no_name_on_list"}
INTEGER_COLUMNS = {"injection", "ncbi_taxonomy_id"}
FLOAT_COLUMNS = {"x_coord", "y_coord"}
DATE_COLUMNS = {"raw_date"}


@dataclass(frozen=True, slots=True)
class Issue:
    """One cell-level sanitization issue."""

    row_number: int
    field: str
    raw_value: str
    issue: str


def main() -> None:
    """Sanitize Champex metadata and write CSV plus issue report."""

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    rows, original_headers = _read_raw_rows(RAW_PATH)
    sanitized_headers = [_sanitize_header(header) for header in original_headers]
    sanitized_headers = _deduplicate_headers(sanitized_headers)

    issues: list[Issue] = []
    sanitized_rows: list[dict[str, str]] = []
    for row_number, row in enumerate(rows, start=2):
        sanitized_row: dict[str, str] = {}
        for original_header, sanitized_header in zip(
            original_headers,
            sanitized_headers,
            strict=True,
        ):
            raw_value = row.get(original_header, "")
            sanitized_row[sanitized_header] = _sanitize_value(
                raw_value,
                field=sanitized_header,
                row_number=row_number,
                issues=issues,
            )
        sanitized_rows.append(sanitized_row)

    output_headers = tuple(sanitized_headers) + (
        "normalized_date",
        "date_parse_status",
        "filename_date_token",
    )
    for row, raw_row in zip(sanitized_rows, rows, strict=True):
        raw_date = raw_row.get("date", "")
        filename = raw_row.get("filename", "")
        normalized_date, date_status = _normalize_date(raw_date)
        row["normalized_date"] = normalized_date
        row["date_parse_status"] = date_status
        row["filename_date_token"] = _filename_date_token(filename)

    _write_csv(SANITIZED_PATH, output_headers, sanitized_rows)
    _write_issues(ISSUES_PATH, issues)
    _write_summary(SUMMARY_PATH, rows, sanitized_rows, issues)


def _read_raw_rows(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    """Read the semicolon-delimited source CSV."""

    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        headers = list(reader.fieldnames or ())
        rows = [dict(row) for row in reader]
    if not headers:
        raise ValueError(f"{path} has no header row")
    return rows, headers


def _sanitize_header(header: str) -> str:
    """Return a snake_case ASCII-ish column name."""

    renamed = HEADER_RENAMES.get(header, header)
    normalized = renamed.strip().lower()
    normalized = re.sub(r"[^0-9a-zA-Z]+", "_", normalized)
    normalized = normalized.strip("_")
    return normalized or "column"


def _deduplicate_headers(headers: list[str]) -> list[str]:
    """Deduplicate sanitized headers by appending numeric suffixes."""

    counts: Counter[str] = Counter()
    deduplicated: list[str] = []
    for header in headers:
        counts[header] += 1
        if counts[header] == 1:
            deduplicated.append(header)
        else:
            deduplicated.append(f"{header}_{counts[header]}")
    return deduplicated


def _sanitize_value(
    value: str | None,
    *,
    field: str,
    row_number: int,
    issues: list[Issue],
) -> str:
    """Normalize one cell to a CSV-safe string."""

    raw = "" if value is None else value.strip()
    if raw in NA_VALUES:
        return ""
    if raw in ROW_MARKER_VALUES and field not in ROW_MARKER_FIELDS:
        return ""

    if field in BOOLEAN_COLUMNS:
        if raw in {"0", "1"}:
            return "true" if raw == "1" else "false"
        issues.append(Issue(row_number, field, raw, "invalid_boolean"))
        return raw

    if field in INTEGER_COLUMNS:
        try:
            return str(int(raw))
        except ValueError:
            issues.append(Issue(row_number, field, raw, "invalid_integer"))
            return raw

    if field in FLOAT_COLUMNS:
        normalized = raw.replace(",", ".")
        try:
            return str(float(normalized))
        except ValueError:
            issues.append(Issue(row_number, field, raw, "invalid_number"))
            return raw

    if field in DATE_COLUMNS:
        normalized_date, date_status = _normalize_date(raw)
        if date_status != "valid":
            issues.append(Issue(row_number, field, raw, date_status))
        return raw

    return raw


def _normalize_date(raw: str) -> tuple[str, str]:
    """Normalize only unambiguous dates, preserving ambiguous spreadsheet damage."""

    value = raw.strip()
    if not value or value in NA_VALUES:
        return "", "missing"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return value, "valid"
    if re.fullmatch(r"\d{8}", value):
        return f"{value[:4]}-{value[4:6]}-{value[6:8]}", "valid"
    if re.fullmatch(r"\d,\d+E\+\d+", value, flags=re.IGNORECASE):
        return "", "ambiguous_spreadsheet_scientific_date"
    if value in {"QC", "Blank"}:
        return "", "non_sample_marker"
    return "", "unrecognized_date"


def _filename_date_token(filename: str) -> str:
    """Extract a leading numeric date-like token from the filename."""

    match = re.match(r"^(\d{8})", filename.strip())
    if match is None:
        return ""
    return match.group(1)


def _write_csv(path: Path, headers: tuple[str, ...], rows: list[dict[str, str]]) -> None:
    """Write sanitized rows as a comma-delimited CSV."""

    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def _write_issues(path: Path, issues: list[Issue]) -> None:
    """Write cell-level sanitization issues."""

    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            delimiter="\t",
            fieldnames=("row_number", "field", "raw_value", "issue"),
        )
        writer.writeheader()
        for issue in issues:
            writer.writerow(
                {
                    "row_number": issue.row_number,
                    "field": issue.field,
                    "raw_value": issue.raw_value,
                    "issue": issue.issue,
                }
            )


def _write_summary(
    path: Path,
    raw_rows: list[dict[str, str]],
    sanitized_rows: list[dict[str, str]],
    issues: list[Issue],
) -> None:
    """Write a small JSON summary of the sanitization."""

    summary: dict[str, Any] = {
        "raw_path": str(RAW_PATH),
        "sanitized_path": str(SANITIZED_PATH),
        "issues_path": str(ISSUES_PATH),
        "row_count": len(sanitized_rows),
        "file_type_counts": dict(Counter(row.get("type", "") for row in raw_rows)),
        "date_parse_status_counts": dict(
            Counter(row.get("date_parse_status", "") for row in sanitized_rows)
        ),
        "issue_counts": dict(Counter(issue.issue for issue in issues)),
    }
    path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
