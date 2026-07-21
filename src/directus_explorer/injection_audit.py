"""Audit acquisition injection lists against Directus sample lineage data."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .ms_converted_check import strip_mzml_suffix
from .ms_metadata import CsvScalar, MsMetadataTable, write_ms_metadata_csv

INJECTION_AUDIT_FIELDNAMES = (
    "csv_row_number",
    "filename",
    "normalized_filename",
    "file_type",
    "injection",
    "sample_id",
    "container_id",
    "csv_ionization_mode",
    "ms_parent_level",
    "is_sample_file",
    "has_required_csv_identifiers",
    "dried_material_present",
    "dried_samples_data_id",
    "original_sample_container_id",
    "extraction_sample_container_present",
    "extraction_data_id",
    "extraction_sample_container_id",
    "expected_extraction_container_id",
    "aliquot_sample_container_present",
    "aliquoting_data_id",
    "aliquot_sample_container_id",
    "target_ms_parent_container_id",
    "existing_ms_data_id",
    "ready_for_ms_data_import",
    "status",
    "reason",
)


@dataclass(frozen=True, slots=True)
class InjectionListRow:
    """One acquisition-list row normalized for Directus lineage checks."""

    csv_row_number: int
    filename: str
    normalized_filename: str
    file_type: str
    injection: str
    sample_id: str
    container_id: str
    ionization_mode: str


def load_injection_list(csv_path: Path) -> tuple[InjectionListRow, ...]:
    """Read an acquisition CSV and return normalized injection-list rows."""

    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {
            "filename",
            "injection",
            "file_type",
            "sample_id",
            "container_id",
            "Ionization.mode",
        }
        missing = sorted(required - set(reader.fieldnames or ()))
        if missing:
            joined = ", ".join(missing)
            raise ValueError(f"{csv_path} is missing required column(s): {joined}")

        rows: list[InjectionListRow] = []
        for index, row in enumerate(reader, start=2):
            filename = _clean_cell(row.get("filename"))
            rows.append(
                InjectionListRow(
                    csv_row_number=index,
                    filename=filename,
                    normalized_filename=strip_mzml_suffix(filename),
                    file_type=_clean_cell(row.get("file_type")),
                    injection=_clean_cell(row.get("injection")),
                    sample_id=_clean_cell(row.get("sample_id")),
                    container_id=_clean_cell(row.get("container_id")),
                    ionization_mode=_clean_cell(row.get("Ionization.mode")),
                )
            )
    return tuple(rows)


def build_injection_audit_table(
    *,
    injection_rows: tuple[InjectionListRow, ...],
    dried_rows: list[dict[str, Any]],
    extraction_rows: list[dict[str, Any]],
    aliquot_rows: list[dict[str, Any]],
    ms_rows: list[dict[str, Any]],
    required_file_type: str = "sample",
    ms_parent_level: str = "aliquot",
) -> MsMetadataTable:
    """Return one audit row per acquisition-list row."""

    if ms_parent_level not in {"aliquot", "extraction"}:
        raise ValueError("ms_parent_level must be either 'aliquot' or 'extraction'")

    dried_by_sample_id = _index_first_by_nested_string(
        dried_rows,
        ("field_data", "sample_id"),
    )
    extraction_by_parent = _index_first_by_nested_string(
        extraction_rows,
        ("parent_sample_container", "container_id"),
    )
    extraction_by_sample_container = _index_first_by_nested_string(
        extraction_rows,
        ("sample_container", "container_id"),
    )
    aliquot_by_sample_container = _index_first_by_nested_string(
        aliquot_rows,
        ("sample_container", "container_id"),
    )
    ms_by_filename = _index_first_by_scalar_string(ms_rows, "filename")

    audit_rows: list[dict[str, CsvScalar]] = []
    for injection_row in injection_rows:
        is_sample_file = injection_row.file_type == required_file_type
        has_required_csv_identifiers = (
            bool(injection_row.sample_id)
            and injection_row.sample_id.upper() != "NA"
            and bool(injection_row.container_id)
            and injection_row.container_id.upper() != "NA"
        )

        dried_row = dried_by_sample_id.get(injection_row.sample_id)
        original_sample_container_id = _read_nested_string(
            dried_row,
            "sample_container",
            "container_id",
        )
        extraction_row = extraction_by_parent.get(original_sample_container_id or "")
        expected_extraction_container_id = _expected_extraction_container_id(
            injection_row.container_id
        )
        if extraction_row is None and expected_extraction_container_id is not None:
            extraction_row = extraction_by_sample_container.get(expected_extraction_container_id)
        extraction_sample_container_id = _read_nested_string(
            extraction_row,
            "sample_container",
            "container_id",
        )
        aliquot_row = aliquot_by_sample_container.get(injection_row.container_id)
        target_ms_parent_container_id = (
            extraction_sample_container_id
            if ms_parent_level == "extraction"
            else _read_nested_string(aliquot_row, "sample_container", "container_id")
        )
        ms_row = ms_by_filename.get(injection_row.normalized_filename)

        reasons: list[str] = []
        if not is_sample_file:
            reasons.append("non_sample_file_type")
        if not has_required_csv_identifiers:
            reasons.append("missing_csv_sample_or_container_id")
        if is_sample_file and has_required_csv_identifiers and dried_row is None:
            reasons.append("missing_dried_material")
        if is_sample_file and has_required_csv_identifiers and extraction_row is None:
            reasons.append("missing_extraction_sample_container")
        if (
            ms_parent_level == "aliquot"
            and is_sample_file
            and has_required_csv_identifiers
            and aliquot_row is None
        ):
            reasons.append("missing_aliquot_sample_container")
        if is_sample_file and ms_row is not None:
            reasons.append("ms_data_already_exists")

        ready = (
            is_sample_file
            and has_required_csv_identifiers
            and dried_row is not None
            and extraction_row is not None
            and (ms_parent_level == "extraction" or aliquot_row is not None)
            and ms_row is None
        )
        status = "ready" if ready else "blocked"
        if not is_sample_file:
            status = "skipped"
        elif ms_row is not None:
            status = "already_imported"

        audit_rows.append(
            {
                "csv_row_number": injection_row.csv_row_number,
                "filename": injection_row.filename,
                "normalized_filename": injection_row.normalized_filename,
                "file_type": injection_row.file_type,
                "injection": injection_row.injection,
                "sample_id": injection_row.sample_id,
                "container_id": injection_row.container_id,
                "csv_ionization_mode": injection_row.ionization_mode,
                "ms_parent_level": ms_parent_level,
                "is_sample_file": str(is_sample_file).lower(),
                "has_required_csv_identifiers": str(has_required_csv_identifiers).lower(),
                "dried_material_present": str(dried_row is not None).lower(),
                "dried_samples_data_id": _read_scalar(dried_row, "id"),
                "original_sample_container_id": original_sample_container_id,
                "extraction_sample_container_present": str(extraction_row is not None).lower(),
                "extraction_data_id": _read_scalar(extraction_row, "id"),
                "extraction_sample_container_id": extraction_sample_container_id,
                "expected_extraction_container_id": expected_extraction_container_id,
                "aliquot_sample_container_present": str(aliquot_row is not None).lower(),
                "aliquoting_data_id": _read_scalar(aliquot_row, "id"),
                "aliquot_sample_container_id": _read_nested_string(
                    aliquot_row,
                    "sample_container",
                    "container_id",
                ),
                "target_ms_parent_container_id": target_ms_parent_container_id,
                "existing_ms_data_id": _read_scalar(ms_row, "id"),
                "ready_for_ms_data_import": str(ready).lower(),
                "status": status,
                "reason": ";".join(reasons),
            }
        )

    return MsMetadataTable(
        fieldnames=INJECTION_AUDIT_FIELDNAMES,
        rows=tuple(audit_rows),
    )


def write_injection_audit_tsv(table: MsMetadataTable, output_path: Path) -> None:
    """Write an injection audit table as TSV."""

    write_ms_metadata_csv(table, output_path, delimiter="\t")


def summarize_injection_audit(table: MsMetadataTable) -> dict[str, int]:
    """Return compact counts for an injection audit table."""

    summary = {
        "row_count": len(table.rows),
        "sample_file_count": 0,
        "ready_count": 0,
        "blocked_count": 0,
        "already_imported_count": 0,
        "skipped_count": 0,
        "missing_dried_material_count": 0,
        "missing_extraction_sample_container_count": 0,
        "missing_aliquot_sample_container_count": 0,
        "missing_csv_identifier_count": 0,
    }
    for row in table.rows:
        if row.get("is_sample_file") == "true":
            summary["sample_file_count"] += 1
        status = row.get("status")
        if status == "ready":
            summary["ready_count"] += 1
        elif status == "blocked":
            summary["blocked_count"] += 1
        elif status == "already_imported":
            summary["already_imported_count"] += 1
        elif status == "skipped":
            summary["skipped_count"] += 1

        reason = str(row.get("reason") or "")
        if "missing_dried_material" in reason:
            summary["missing_dried_material_count"] += 1
        if "missing_extraction_sample_container" in reason:
            summary["missing_extraction_sample_container_count"] += 1
        if "missing_aliquot_sample_container" in reason:
            summary["missing_aliquot_sample_container_count"] += 1
        if "missing_csv_sample_or_container_id" in reason:
            summary["missing_csv_identifier_count"] += 1
    return summary


def _clean_cell(value: str | None) -> str:
    """Return a stripped CSV cell value."""

    if value is None:
        return ""
    return value.strip()


def _expected_extraction_container_id(container_id: str) -> str | None:
    """Infer the extraction container id from a DBGI aliquot container code."""

    parts = container_id.split("_")
    if len(parts) < 4:
        return None
    return "_".join(parts[:-1])


def _index_first_by_nested_string(
    rows: list[dict[str, Any]],
    path: tuple[str, ...],
) -> dict[str, dict[str, Any]]:
    """Index rows by a nested string field, keeping the first row for duplicate keys."""

    mapping: dict[str, dict[str, Any]] = {}
    for row in rows:
        value = _read_nested_string(row, *path)
        if value is None:
            continue
        mapping.setdefault(value, row)
    return mapping


def _index_first_by_scalar_string(
    rows: list[dict[str, Any]],
    field: str,
) -> dict[str, dict[str, Any]]:
    """Index rows by a top-level string field, keeping the first row for duplicate keys."""

    mapping: dict[str, dict[str, Any]] = {}
    for row in rows:
        value = row.get(field)
        if not isinstance(value, str) or not value:
            continue
        mapping.setdefault(value, row)
    return mapping


def _read_nested_string(row: dict[str, Any] | None, *path: str) -> str | None:
    """Read a nested Directus relation field as a non-empty string."""

    current: Any = row
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    if current is None:
        return None
    normalized = str(current).strip()
    return normalized or None


def _read_scalar(row: dict[str, Any] | None, field: str) -> CsvScalar:
    """Read a scalar value from a Directus row."""

    if not isinstance(row, dict):
        return None
    value = row.get(field)
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)
