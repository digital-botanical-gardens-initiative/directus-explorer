"""Sanitize jbp-new batch006 metadata and prepare a Directus MS import audit CSV."""

from __future__ import annotations

import csv
import json
import re
import shutil
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from frictionless import Resource, validate
from frictionless.formats.csv import CsvControl

RAW_PATH = Path("data/working/jbp-new/jbp_batch006_metadata.csv")
OUTPUT_DIR = Path("data/output/jbp-new/batch006")
ORIGINAL_DIR = OUTPUT_DIR / "original"
ORIGINAL_COPY_PATH = ORIGINAL_DIR / RAW_PATH.name
SANITIZED_PATH = OUTPUT_DIR / "jbp_batch006_metadata_sanitized.csv"
IMPORT_PATH = OUTPUT_DIR / "jbp_batch006_injection_import.csv"
ISSUES_PATH = OUTPUT_DIR / "jbp_batch006_sanitization_issues.tsv"
SUMMARY_PATH = OUTPUT_DIR / "jbp_batch006_sanitization_summary.json"
RAW_SCHEMA_PATH = OUTPUT_DIR / "jbp_batch006_raw_frictionless_schema.json"
RAW_REPORT_PATH = OUTPUT_DIR / "jbp_batch006_raw_frictionless_report.json"
SANITIZED_SCHEMA_PATH = OUTPUT_DIR / "jbp_batch006_sanitized_frictionless_schema.json"
SANITIZED_REPORT_PATH = OUTPUT_DIR / "jbp_batch006_sanitized_frictionless_report.json"

DEFAULT_IONIZATION_MODE = "positive"
EXPECTED_METHOD = "general_ms2_pos"


@dataclass(frozen=True, slots=True)
class Issue:
    """One row-level or cell-level sanitization issue."""

    row_number: int
    field: str
    raw_value: str
    issue: str


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ORIGINAL_DIR.mkdir(parents=True, exist_ok=True)
    if not ORIGINAL_COPY_PATH.exists():
        shutil.copy2(RAW_PATH, ORIGINAL_COPY_PATH)

    rows, headers = _read_raw_rows(RAW_PATH)
    _write_frictionless_artifacts(
        RAW_PATH,
        delimiter=",",
        schema_path=RAW_SCHEMA_PATH,
        report_path=RAW_REPORT_PATH,
    )

    issues: list[Issue] = []
    sanitized_rows: list[dict[str, str]] = []
    for row_number, row in enumerate(rows, start=2):
        sanitized_row = {header: _clean(row.get(header)) for header in headers}
        _augment_row(sanitized_row, row_number=row_number, issues=issues)
        sanitized_rows.append(sanitized_row)

    output_headers = (
        *headers,
        "file_type",
        "injection",
        "container_id",
        "ionization_mode",
        "normalized_filename",
        "filename_has_supported_extension",
        "filename_date_token",
    )
    _write_csv(SANITIZED_PATH, output_headers, sanitized_rows)
    _write_import_csv(IMPORT_PATH, sanitized_rows)
    _write_issues(ISSUES_PATH, issues)
    _write_summary(SUMMARY_PATH, sanitized_rows, issues)
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


def _augment_row(row: dict[str, str], *, row_number: int, issues: list[Issue]) -> None:
    filename = row.get("ms_filename", "")
    sample_id = row.get("sample_id", "")
    parent_container_id = row.get("ms_parent_container_id", "")
    injection_method = row.get("injection_method", "")

    row["file_type"] = "sample" if sample_id and parent_container_id else "unknown"
    row["injection"] = _filename_injection(filename)
    row["container_id"] = parent_container_id
    row["ionization_mode"] = DEFAULT_IONIZATION_MODE
    row["normalized_filename"] = _strip_ms_extension(filename)
    row["filename_has_supported_extension"] = str(
        filename.lower().endswith((".mzml", ".mzxml"))
    ).lower()
    row["filename_date_token"] = _filename_date_token(filename)

    if injection_method != EXPECTED_METHOD:
        issues.append(
            Issue(row_number, "injection_method", injection_method, "unexpected_injection_method")
        )
    if row["filename_has_supported_extension"] != "true":
        issues.append(Issue(row_number, "ms_filename", filename, "unsupported_ms_extension"))
    if sample_id and parent_container_id and not parent_container_id.startswith(f"{sample_id}_"):
        issues.append(
            Issue(
                row_number,
                "ms_parent_container_id",
                parent_container_id,
                "parent_container_not_derived_from_sample_id",
            )
        )


def _filename_injection(filename: str) -> str:
    match = re.match(r"^\d{8}_(\d+)_", filename.strip())
    if match is None:
        return ""
    return str(int(match.group(1)))


def _strip_ms_extension(filename: str) -> str:
    return re.sub(r"\.(mzML|mzXML)$", "", filename.strip(), flags=re.IGNORECASE)


def _filename_date_token(filename: str) -> str:
    match = re.match(r"^(\d{8})", filename.strip())
    return "" if match is None else match.group(1)


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
                    "filename": row["ms_filename"],
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


def _write_summary(path: Path, rows: list[dict[str, str]], issues: list[Issue]) -> None:
    summary: dict[str, Any] = {
        "raw_path": str(RAW_PATH),
        "original_copy_path": str(ORIGINAL_COPY_PATH),
        "sanitized_path": str(SANITIZED_PATH),
        "import_path": str(IMPORT_PATH),
        "issues_path": str(ISSUES_PATH),
        "row_count": len(rows),
        "qfield_project_counts": dict(Counter(row.get("qfield_project", "") for row in rows)),
        "file_type_counts": dict(Counter(row.get("file_type", "") for row in rows)),
        "injection_method_counts": dict(
            Counter(row.get("injection_method", "") for row in rows)
        ),
        "sample_id_count": len({row.get("sample_id", "") for row in rows if row.get("sample_id")}),
        "duplicate_sample_id_count": sum(
            1
            for _sample_id, count in Counter(
                row.get("sample_id", "") for row in rows if row.get("sample_id")
            ).items()
            if count > 1
        ),
        "ms_filename_count": len(
            {row.get("normalized_filename", "") for row in rows if row.get("normalized_filename")}
        ),
        "duplicate_ms_filename_count": sum(
            1
            for _filename, count in Counter(
                row.get("normalized_filename", "")
                for row in rows
                if row.get("normalized_filename")
            ).items()
            if count > 1
        ),
        "ms_data_id_count": len(
            {row.get("ms_data_id", "") for row in rows if row.get("ms_data_id")}
        ),
        "filename_supported_extension_counts": dict(
            Counter(row.get("filename_has_supported_extension", "") for row in rows)
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


def _clean(value: str | None) -> str:
    return "" if value is None else value.strip()


if __name__ == "__main__":
    main()
