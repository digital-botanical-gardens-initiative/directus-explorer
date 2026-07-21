"""Sanitize the complete Droguier metadata CSV and prepare MS import/audit inputs."""

from __future__ import annotations

import csv
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from frictionless import Resource, validate
from frictionless.formats.csv import CsvControl

RAW_PATH = Path("data/working/droguier/droguier_metatda_complete.csv")
OUTPUT_DIR = Path("data/output/droguier/complete")
SANITIZED_PATH = OUTPUT_DIR / "droguier_metadata_complete_sanitized.csv"
IMPORT_PATH = OUTPUT_DIR / "droguier_metadata_complete_injection_import.csv"
ISSUES_PATH = OUTPUT_DIR / "droguier_metadata_complete_sanitization_issues.tsv"
SUMMARY_PATH = OUTPUT_DIR / "droguier_metadata_complete_sanitization_summary.json"
RAW_SCHEMA_PATH = OUTPUT_DIR / "droguier_metadata_complete_raw_frictionless_schema.json"
RAW_REPORT_PATH = OUTPUT_DIR / "droguier_metadata_complete_raw_frictionless_report.json"
SANITIZED_SCHEMA_PATH = OUTPUT_DIR / "droguier_metadata_complete_sanitized_frictionless_schema.json"
SANITIZED_REPORT_PATH = OUTPUT_DIR / "droguier_metadata_complete_sanitized_frictionless_report.json"

HEADER_RENAMES = {
    "Filename": "filename",
    "Extension": "extension",
    "Localisation.in.Sciex.OS.project": "localisation_in_sciex_os_project",
    "Rack": "rack",
    "Plate.position": "plate_position",
    "Comment": "comment",
    "geometry.type": "geometry_type",
    "geometry.coordinates": "geometry_coordinates",
    "date": "raw_date",
}
NA_VALUES = {"", "NA", "N/A", "na", "n/a", "NULL", "null"}
BOOLEAN_COLUMNS = {"inat_upload", "is_wild", "no_name_on_list"}
INTEGER_COLUMNS = {"id"}
FLOAT_COLUMNS = {"latitude", "longitude", "x_coord", "y_coord"}
DEFAULT_IONIZATION_MODE = "positive"


@dataclass(frozen=True, slots=True)
class Issue:
    """One cell-level sanitization issue."""

    row_number: int
    field: str
    raw_value: str
    issue: str


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    rows, original_headers = _read_raw_rows(RAW_PATH)
    sanitized_headers = _deduplicate_headers(
        [_sanitize_header(header) for header in original_headers]
    )

    _write_frictionless_artifacts(
        RAW_PATH,
        delimiter=",",
        schema_path=RAW_SCHEMA_PATH,
        report_path=RAW_REPORT_PATH,
    )

    issues: list[Issue] = []
    sanitized_rows: list[dict[str, str]] = []
    for row_number, row in enumerate(rows, start=2):
        sanitized_row: dict[str, str] = {}
        for original_header, sanitized_header in zip(
            original_headers,
            sanitized_headers,
            strict=True,
        ):
            sanitized_row[sanitized_header] = _sanitize_value(
                row.get(original_header),
                field=sanitized_header,
                row_number=row_number,
                issues=issues,
            )
        _augment_row(sanitized_row, row_number=row_number, issues=issues)
        sanitized_rows.append(sanitized_row)

    output_headers = (
        *sanitized_headers,
        "file_type",
        "injection",
        "container_id",
        "ionization_mode",
        "normalized_date",
        "date_parse_status",
        "filename_date_token",
    )
    _write_csv(SANITIZED_PATH, output_headers, sanitized_rows)
    _write_import_csv(IMPORT_PATH, sanitized_rows)
    _write_issues(ISSUES_PATH, issues)
    _write_summary(SUMMARY_PATH, rows, sanitized_rows, issues)
    _write_frictionless_artifacts(
        SANITIZED_PATH,
        delimiter=",",
        schema_path=SANITIZED_SCHEMA_PATH,
        report_path=SANITIZED_REPORT_PATH,
    )


def _read_raw_rows(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        headers = list(reader.fieldnames or ())
        rows = [dict(row) for row in reader]
    if not headers:
        raise ValueError(f"{path} has no header row")
    return rows, headers


def _sanitize_header(header: str) -> str:
    renamed = HEADER_RENAMES.get(header, header)
    normalized = renamed.strip().lower()
    normalized = re.sub(r"[^0-9a-zA-Z]+", "_", normalized)
    normalized = normalized.strip("_")
    return normalized or "column"


def _deduplicate_headers(headers: list[str]) -> list[str]:
    counts: Counter[str] = Counter()
    deduplicated: list[str] = []
    for header in headers:
        counts[header] += 1
        deduplicated.append(header if counts[header] == 1 else f"{header}_{counts[header]}")
    return deduplicated


def _sanitize_value(
    value: str | None,
    *,
    field: str,
    row_number: int,
    issues: list[Issue],
) -> str:
    raw = "" if value is None else value.strip()
    if raw in NA_VALUES:
        return ""

    if field in BOOLEAN_COLUMNS:
        if raw in {"0", "1", "true", "false"}:
            return "true" if raw in {"1", "true"} else "false"
        issues.append(Issue(row_number, field, raw, "invalid_boolean"))
        return raw

    if field in INTEGER_COLUMNS:
        try:
            return str(int(raw))
        except ValueError:
            issues.append(Issue(row_number, field, raw, "invalid_integer"))
            return raw

    if field in FLOAT_COLUMNS:
        try:
            return str(float(raw))
        except ValueError:
            issues.append(Issue(row_number, field, raw, "invalid_number"))
            return raw

    return raw


def _augment_row(row: dict[str, str], *, row_number: int, issues: list[Issue]) -> None:
    filename = row.get("filename", "")
    sample_id = row.get("sample_id", "")
    row["file_type"] = "sample" if "_Sample_" in filename else "unknown"
    row["injection"] = _filename_injection(filename)
    row["container_id"] = f"{sample_id}_01" if row["file_type"] == "sample" and sample_id else ""
    row["ionization_mode"] = DEFAULT_IONIZATION_MODE if row["file_type"] == "sample" else ""
    row["filename_date_token"] = _filename_date_token(filename)
    row["normalized_date"], row["date_parse_status"] = _normalize_compact_datetime(
        row.get("raw_date", "")
    )
    if row["date_parse_status"] not in {"valid", "missing"}:
        issues.append(
            Issue(row_number, "raw_date", row.get("raw_date", ""), row["date_parse_status"])
        )


def _filename_injection(filename: str) -> str:
    match = re.search(r"_(\d+)$", filename.strip())
    if match is None:
        return ""
    return str(int(match.group(1)))


def _filename_date_token(filename: str) -> str:
    match = re.match(r"^(\d{8})", filename.strip())
    return "" if match is None else match.group(1)


def _normalize_compact_datetime(raw: str) -> tuple[str, str]:
    value = raw.strip()
    if not value:
        return "", "missing"
    if re.fullmatch(r"\d{14}", value):
        parsed = datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
        return parsed.isoformat(), "valid"
    return "", "unrecognized_datetime"


def _write_csv(path: Path, headers: tuple[str, ...], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def _write_import_csv(path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames = (
        "filename",
        "injection",
        "file_type",
        "sample_id",
        "container_id",
        "Ionization.mode",
    )
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "filename": row["filename"],
                    "injection": row["injection"],
                    "file_type": row["file_type"],
                    "sample_id": row["sample_id"],
                    "container_id": row["container_id"],
                    "Ionization.mode": row["ionization_mode"],
                }
            )


def _write_issues(path: Path, issues: list[Issue]) -> None:
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
    summary: dict[str, Any] = {
        "raw_path": str(RAW_PATH),
        "sanitized_path": str(SANITIZED_PATH),
        "import_path": str(IMPORT_PATH),
        "issues_path": str(ISSUES_PATH),
        "row_count": len(sanitized_rows),
        "file_type_counts": dict(Counter(row.get("file_type", "") for row in sanitized_rows)),
        "qfield_project_counts": dict(
            Counter(row.get("qfield_project", "") for row in sanitized_rows)
        ),
        "ionization_mode_counts": dict(
            Counter(row.get("ionization_mode", "") for row in sanitized_rows)
        ),
        "date_parse_status_counts": dict(
            Counter(row.get("date_parse_status", "") for row in sanitized_rows)
        ),
        "duplicate_sample_id_count": sum(
            1
            for _sample_id, count in Counter(
                row.get("sample_id", "") for row in sanitized_rows
            ).items()
            if count > 1
        ),
        "issue_counts": dict(Counter(issue.issue for issue in issues)),
    }
    path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_frictionless_artifacts(
    path: Path,
    *,
    delimiter: str,
    schema_path: Path,
    report_path: Path,
) -> None:
    resource = Resource(path, control=CsvControl(delimiter=delimiter))
    resource.infer(stats=True)
    schema_path.write_text(
        json.dumps(resource.schema.to_descriptor(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    report = validate(Resource(path, control=CsvControl(delimiter=delimiter)))
    report_path.write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
