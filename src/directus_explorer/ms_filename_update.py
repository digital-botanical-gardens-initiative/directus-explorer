"""Validate metadata-driven filename corrections for existing Directus MS rows."""

from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .ms_converted_check import strip_mzml_suffix
from .ms_metadata import CsvScalar, MsMetadataTable, write_ms_metadata_csv

MS_FILENAME_UPDATE_FIELDNAMES = (
    "csv_row_number",
    "ms_data_id",
    "current_filename",
    "requested_filename",
    "normalized_filename",
    "csv_parent_container_id",
    "directus_parent_container_id",
    "csv_injection_method",
    "directus_injection_method",
    "status",
    "reason",
)


@dataclass(frozen=True, slots=True)
class MsFilenameUpdate:
    """One requested filename correction with optional relationship assertions."""

    csv_row_number: int
    raw_ms_data_id: str
    ms_data_id: int | None
    requested_filename: str
    normalized_filename: str
    parent_container_id: str
    injection_method: str


def load_ms_filename_updates(csv_path: Path) -> tuple[MsFilenameUpdate, ...]:
    """Read filename corrections from a sanitized metadata CSV."""

    with csv_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = {"ms_data_id", "ms_filename"}
        missing = sorted(required - set(reader.fieldnames or ()))
        if missing:
            joined = ", ".join(missing)
            raise ValueError(f"{csv_path} is missing required column(s): {joined}")

        rows: list[MsFilenameUpdate] = []
        for row_number, row in enumerate(reader, start=2):
            raw_ms_data_id = _clean_cell(row.get("ms_data_id"))
            try:
                ms_data_id = int(raw_ms_data_id)
            except ValueError:
                ms_data_id = None
            requested_filename = _clean_cell(row.get("ms_filename"))
            rows.append(
                MsFilenameUpdate(
                    csv_row_number=row_number,
                    raw_ms_data_id=raw_ms_data_id,
                    ms_data_id=ms_data_id,
                    requested_filename=requested_filename,
                    normalized_filename=strip_mzml_suffix(requested_filename),
                    parent_container_id=_clean_cell(row.get("ms_parent_container_id")),
                    injection_method=_clean_cell(row.get("injection_method")),
                )
            )
    return tuple(rows)


def build_ms_filename_update_table(
    *,
    updates: tuple[MsFilenameUpdate, ...],
    ms_rows: list[dict[str, Any]],
) -> MsMetadataTable:
    """Validate filename corrections against current Directus MS rows."""

    directus_by_id = {
        ms_data_id: row
        for row in ms_rows
        if (ms_data_id := _read_integer(row.get("id"))) is not None
    }
    directus_ids_by_filename: dict[str, set[int]] = {}
    for ms_data_id, row in directus_by_id.items():
        filename = _read_string(row.get("filename"))
        if filename:
            directus_ids_by_filename.setdefault(filename, set()).add(ms_data_id)

    csv_id_counts = Counter(
        update.ms_data_id for update in updates if update.ms_data_id is not None
    )
    csv_filename_counts = Counter(
        update.normalized_filename for update in updates if update.normalized_filename
    )

    rows: list[dict[str, CsvScalar]] = []
    for update in updates:
        directus_row = (
            directus_by_id.get(update.ms_data_id) if update.ms_data_id is not None else None
        )
        current_filename = _read_string(
            directus_row.get("filename") if directus_row is not None else None
        )
        directus_parent = _read_nested_string(
            directus_row,
            "parent_sample_container",
            "container_id",
        )
        directus_method = _read_nested_string(
            directus_row,
            "injection_method",
            "method_name",
        )

        reasons: list[str] = []
        if update.ms_data_id is None:
            reasons.append("invalid_ms_data_id")
        elif csv_id_counts[update.ms_data_id] > 1:
            reasons.append("duplicate_ms_data_id_in_csv")
        if not update.normalized_filename:
            reasons.append("missing_ms_filename")
        elif csv_filename_counts[update.normalized_filename] > 1:
            reasons.append("duplicate_ms_filename_in_csv")
        if update.ms_data_id is not None and directus_row is None:
            reasons.append("ms_data_id_not_found")
        if (
            directus_row is not None
            and update.parent_container_id
            and update.parent_container_id != directus_parent
        ):
            reasons.append("parent_container_mismatch")
        if (
            directus_row is not None
            and update.injection_method
            and update.injection_method != directus_method
        ):
            reasons.append("injection_method_mismatch")
        if update.ms_data_id is not None and update.normalized_filename:
            conflicting_ids = directus_ids_by_filename.get(update.normalized_filename, set()) - {
                update.ms_data_id
            }
            if conflicting_ids:
                reasons.append("ms_filename_already_used")

        if reasons:
            status = "blocked"
        elif current_filename == update.normalized_filename:
            status = "unchanged"
        else:
            status = "ready"

        rows.append(
            {
                "csv_row_number": update.csv_row_number,
                "ms_data_id": update.raw_ms_data_id,
                "current_filename": current_filename,
                "requested_filename": update.requested_filename,
                "normalized_filename": update.normalized_filename,
                "csv_parent_container_id": update.parent_container_id,
                "directus_parent_container_id": directus_parent,
                "csv_injection_method": update.injection_method,
                "directus_injection_method": directus_method,
                "status": status,
                "reason": ";".join(reasons),
            }
        )

    return MsMetadataTable(fieldnames=MS_FILENAME_UPDATE_FIELDNAMES, rows=tuple(rows))


def summarize_ms_filename_updates(table: MsMetadataTable) -> dict[str, int]:
    """Return status counts for a filename update validation table."""

    status_counts = Counter(str(row.get("status") or "") for row in table.rows)
    return {
        "row_count": len(table.rows),
        "ready_count": status_counts["ready"],
        "unchanged_count": status_counts["unchanged"],
        "blocked_count": status_counts["blocked"],
    }


def write_ms_filename_update_tsv(table: MsMetadataTable, output_path: Path) -> None:
    """Write a row-level filename update validation report."""

    write_ms_metadata_csv(table, output_path, delimiter="\t")


def _clean_cell(value: str | None) -> str:
    return "" if value is None else value.strip()


def _read_integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _read_string(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _read_nested_string(row: dict[str, Any] | None, *path: str) -> str | None:
    current: Any = row
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return _read_string(current)
