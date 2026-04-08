"""Helpers for flattening sample and MS lineage metadata into tabular rows."""

from __future__ import annotations

import csv
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .samples import classify_profile_mode

CsvScalar = str | int | float | bool | None
CsvRow = dict[str, CsvScalar]


MS_DATA_FIELDS = ",".join(
    (
        "*",
        "injection_volume_unit.symbol",
        "injection_method.*",
        "instrument_used.*",
        "instrument_used.instrument_model.*",
        "instrument_used.instrument_location.*",
        "batch.*",
        "batch.batch_type.*",
        "parent_sample_container.*",
        "parent_sample_container.container_model.*",
        "parent_sample_container.container_model.container_type.*",
    )
)

ALIQUOTING_DATA_FIELDS = ",".join(
    (
        "*",
        "aliquot_volume_unit.symbol",
        "sample_container.*",
        "sample_container.container_model.*",
        "sample_container.container_model.container_type.*",
        "parent_sample_container.*",
        "parent_sample_container.container_model.*",
        "parent_sample_container.container_model.container_type.*",
        "parent_container.*",
        "parent_container.container_model.*",
        "parent_container.container_model.container_type.*",
    )
)

EXTRACTION_DATA_FIELDS = ",".join(
    (
        "*",
        "dried_weight_unit.symbol",
        "solvent_volume_unit.symbol",
        "extraction_method.*",
        "batch.*",
        "batch.batch_type.*",
        "sample_container.*",
        "sample_container.container_model.*",
        "sample_container.container_model.container_type.*",
        "parent_sample_container.*",
        "parent_sample_container.container_model.*",
        "parent_sample_container.container_model.container_type.*",
        "parent_container.*",
        "parent_container.container_model.*",
        "parent_container.container_model.container_type.*",
        "extraction_container.*",
        "extraction_container.volume_unit.symbol",
    )
)

DRIED_SAMPLES_DATA_FIELDS = ",".join(
    (
        "*",
        "sample_container.*",
        "sample_container.container_model.*",
        "sample_container.container_model.container_type.*",
        "parent_container.*",
        "parent_container.container_model.*",
        "parent_container.container_model.container_type.*",
        "batch.*",
        "batch.batch_type.*",
        "field_data.*",
    )
)


@dataclass(frozen=True, slots=True)
class MsMetadataTable:
    """In-memory representation of a wide MS metadata export."""

    fieldnames: tuple[str, ...]
    rows: tuple[CsvRow, ...]


COLUMN_PREFIX_ORDER = (
    "field_data_id",
    "original_sample_id",
    "ms_container_id",
    "ms_container_type",
    "ms_storage_level_",
    "extraction_container_id",
    "extraction_container_type",
    "extraction_storage_level_",
    "original_sample_container_id",
    "original_sample_container_type",
    "original_storage_level_",
    "qfield_project",
    "profile_mode",
    "field_",
    "dried_samples_data_id",
    "dried_",
    "extraction_data_id",
    "extraction_",
    "aliquoting_data_id",
    "aliquot_",
    "ms_data_id",
    "ms_",
    "watcher_exact_filename_match",
    "watcher_",
)

COMPACT_FIELD_PREFIXES = (
    "ms_storage_level_",
    "extraction_storage_level_",
    "original_storage_level_",
)

COMPACT_FIELDNAMES = (
    "original_sample_id",
    "ms_container_id",
    "extraction_container_id",
    "original_sample_container_id",
    "qfield_project",
    "profile_mode",
    "field_sample_name",
    "field_taxon_name",
    "field_date",
    "field_latitude",
    "field_longitude",
    "field_geometry_coordinates",
    "field_collector_fullname",
    "field_weather",
    "field_comment_eco",
    "extraction_dried_weight",
    "extraction_dried_weight_unit_symbol",
    "extraction_solvent_volume",
    "extraction_solvent_volume_unit_symbol",
    "extraction_method_method_name",
    "aliquot_aliquot_volume",
    "aliquot_volume_unit_symbol",
    "ms_filename",
    "ms_injection_volume",
    "ms_injection_volume_unit_symbol",
    "ms_injection_method_method_name",
    "ms_instrument_instrument_id",
    "ms_instrument_model_instrument_model",
    "ms_instrument_location_room_name",
    "watcher_exact_filename_match",
    "watcher_file_name",
    "watcher_file_path",
    "watcher_converted_file_sha1",
    "watcher_polarity",
    "watcher_acquisition_date",
    "watcher_raw_file_sha1",
    "watcher_instrument_model",
    "watcher_ionization_source",
    "watcher_analyzer",
    "watcher_detector",
    "watcher_spectrum_count",
    "watcher_chromatogram_count",
    "watcher_source_file_names",
)


def build_sample_metadata_row(
    *,
    field_row: dict[str, Any] | None,
    ms_row: dict[str, Any] | None,
    aliquot_row: dict[str, Any] | None,
    extraction_row: dict[str, Any] | None,
    dried_row: dict[str, Any] | None,
) -> CsvRow:
    """Build one flattened metadata row for a collected sample lineage."""

    row: CsvRow = {
        "field_data_id": _to_nested_scalar(field_row, "id"),
        "ms_data_id": _to_nested_scalar(ms_row, "id"),
        "profile_mode": classify_profile_mode(
            _read_nested_string(ms_row, "injection_method", "method_name") or ""
        ),
    }

    _flatten_mapping(
        row,
        prefix="ms",
        mapping=ms_row,
        exclude={
            "id",
            "injection_volume_unit",
            "injection_method",
            "instrument_used",
            "batch",
            "parent_sample_container",
        },
    )
    _flatten_mapping(
        row,
        prefix="ms_injection_volume_unit",
        mapping=_read_mapping(ms_row, "injection_volume_unit"),
    )
    _flatten_mapping(row, prefix="ms_injection_method", mapping=_read_mapping(ms_row, "injection_method"))
    _flatten_mapping(
        row,
        prefix="ms_instrument",
        mapping=_read_mapping(ms_row, "instrument_used"),
        exclude={"instrument_model", "instrument_location"},
    )
    _flatten_mapping(
        row,
        prefix="ms_instrument_model",
        mapping=_read_nested_mapping(ms_row, "instrument_used", "instrument_model"),
    )
    _flatten_mapping(
        row,
        prefix="ms_instrument_location",
        mapping=_read_nested_mapping(ms_row, "instrument_used", "instrument_location"),
    )
    _flatten_batch(row, prefix="ms_batch", batch_row=_read_mapping(ms_row, "batch"))
    _flatten_container(
        row,
        prefix="ms_parent_sample_container",
        container_row=_read_mapping(ms_row, "parent_sample_container"),
    )

    row["aliquoting_data_id"] = _to_nested_scalar(aliquot_row, "id")
    _flatten_mapping(
        row,
        prefix="aliquot",
        mapping=aliquot_row,
        exclude={
            "id",
            "sample_container",
            "parent_sample_container",
            "parent_container",
            "aliquot_volume_unit",
        },
    )
    _flatten_mapping(
        row,
        prefix="aliquot_volume_unit",
        mapping=_as_mapping(_read_mapping(aliquot_row, "aliquot_volume_unit")),
    )
    _flatten_container(
        row,
        prefix="aliquot_sample_container",
        container_row=_read_mapping(aliquot_row, "sample_container"),
    )
    _flatten_container(
        row,
        prefix="aliquot_parent_sample_container",
        container_row=_read_mapping(aliquot_row, "parent_sample_container"),
    )
    _flatten_container(
        row,
        prefix="aliquot_parent_container",
        container_row=_read_mapping(aliquot_row, "parent_container"),
    )

    row["extraction_data_id"] = _to_nested_scalar(extraction_row, "id")
    _flatten_mapping(
        row,
        prefix="extraction",
        mapping=extraction_row,
        exclude={
            "id",
            "dried_weight_unit",
            "solvent_volume_unit",
            "extraction_method",
            "batch",
            "sample_container",
            "parent_sample_container",
            "parent_container",
            "extraction_container",
        },
    )
    _flatten_mapping(
        row,
        prefix="extraction_dried_weight_unit",
        mapping=_as_mapping(_read_mapping(extraction_row, "dried_weight_unit")),
    )
    _flatten_mapping(
        row,
        prefix="extraction_solvent_volume_unit",
        mapping=_as_mapping(_read_mapping(extraction_row, "solvent_volume_unit")),
    )
    _flatten_mapping(
        row,
        prefix="extraction_method",
        mapping=_as_mapping(_read_mapping(extraction_row, "extraction_method")),
    )
    _flatten_batch(
        row,
        prefix="extraction_batch",
        batch_row=_read_mapping(extraction_row, "batch"),
    )
    _flatten_container(
        row,
        prefix="extraction_sample_container",
        container_row=_read_mapping(extraction_row, "sample_container"),
    )
    _flatten_container(
        row,
        prefix="extraction_parent_sample_container",
        container_row=_read_mapping(extraction_row, "parent_sample_container"),
    )
    _flatten_container(
        row,
        prefix="extraction_parent_container",
        container_row=_read_mapping(extraction_row, "parent_container"),
    )
    _flatten_mapping(
        row,
        prefix="extraction_container_model",
        mapping=_as_mapping(_read_mapping(extraction_row, "extraction_container")),
    )

    row["dried_samples_data_id"] = _to_nested_scalar(dried_row, "id")
    _flatten_mapping(
        row,
        prefix="dried",
        mapping=dried_row,
        exclude={"id", "sample_container", "parent_container", "batch", "field_data"},
    )
    _flatten_container(
        row,
        prefix="dried_sample_container",
        container_row=_read_mapping(dried_row, "sample_container"),
    )
    _flatten_container(
        row,
        prefix="dried_parent_container",
        container_row=_read_mapping(dried_row, "parent_container"),
    )
    _flatten_batch(row, prefix="dried_batch", batch_row=_read_mapping(dried_row, "batch"))
    _flatten_mapping(
        row,
        prefix="field",
        mapping=_as_mapping(_read_mapping(dried_row, "field_data")),
    )

    field_data = field_row if field_row is not None else _read_mapping(dried_row, "field_data")
    _flatten_mapping(row, prefix="field", mapping=field_data)

    row["original_sample_container_id"] = _read_nested_string(
        dried_row,
        "sample_container",
        "container_id",
    )
    row["ms_container_id"] = _read_nested_string(
        ms_row,
        "parent_sample_container",
        "container_id",
    )
    row["ms_container_type"] = _read_container_type(
        _read_nested_mapping(ms_row, "parent_sample_container", "container_model")
    )
    row["extraction_container_id"] = _read_nested_string(
        extraction_row,
        "sample_container",
        "container_id",
    )
    row["extraction_container_type"] = _read_container_type(
        _read_nested_mapping(extraction_row, "sample_container", "container_model")
    )
    row["original_sample_container_type"] = _read_container_type(
        _read_nested_mapping(dried_row, "sample_container", "container_model")
    )
    row["original_sample_id"] = _read_nested_string(field_data, "sample_id")
    row["qfield_project"] = _read_nested_string(field_data, "qfield_project")

    return row


def write_ms_metadata_csv(
    table: MsMetadataTable,
    output_path: str | Path,
    *,
    delimiter: str = ",",
) -> None:
    """Write a metadata table to a delimited text file."""

    path = Path(output_path)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(table.fieldnames), delimiter=delimiter)
        writer.writeheader()
        for row in table.rows:
            writer.writerow(row)


def flatten_row_fieldnames(rows: list[CsvRow]) -> tuple[str, ...]:
    """Collect fieldnames from rows while preserving first-seen order."""

    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for fieldname in row:
            if fieldname in seen:
                continue
            seen.add(fieldname)
            fieldnames.append(fieldname)
    return tuple(fieldnames)


def compact_table(table: MsMetadataTable) -> MsMetadataTable:
    """Drop columns that are empty across the whole table and reorder them."""

    populated_fieldnames = [
        fieldname
        for fieldname in table.fieldnames
        if any(row.get(fieldname) not in (None, "") for row in table.rows)
    ]
    ordered_fieldnames = order_fieldnames(tuple(populated_fieldnames))
    compact_rows = tuple(
        {fieldname: row.get(fieldname) for fieldname in ordered_fieldnames}
        for row in table.rows
    )
    return MsMetadataTable(fieldnames=ordered_fieldnames, rows=compact_rows)




def select_table_view(table: MsMetadataTable, *, view: str) -> MsMetadataTable:
    """Return a cleaned table in the requested export view."""

    cleaned = compact_table(table)
    if view == "full":
        return cleaned
    if view != "compact":
        raise ValueError(f"Unsupported metadata export view: {view}")

    fieldnames = tuple(
        fieldname
        for fieldname in cleaned.fieldnames
        if fieldname in COMPACT_FIELDNAMES
        or any(fieldname.startswith(prefix) for prefix in COMPACT_FIELD_PREFIXES)
    )
    rows = tuple({fieldname: row.get(fieldname) for fieldname in fieldnames} for row in cleaned.rows)
    return MsMetadataTable(fieldnames=fieldnames, rows=rows)


def order_fieldnames(fieldnames: tuple[str, ...]) -> tuple[str, ...]:
    """Return fieldnames in a stable, human-oriented export order."""

    seen = set(fieldnames)
    ordered: list[str] = []
    for prefix in COLUMN_PREFIX_ORDER:
        for fieldname in fieldnames:
            if fieldname not in seen or fieldname in ordered:
                continue
            if fieldname == prefix or fieldname.startswith(prefix):
                ordered.append(fieldname)

    for fieldname in fieldnames:
        if fieldname not in ordered:
            ordered.append(fieldname)

    return tuple(ordered)


def _flatten_batch(row: CsvRow, *, prefix: str, batch_row: Any) -> None:
    """Flatten batch metadata and its batch type relation."""

    batch_mapping = _as_mapping(batch_row)
    _flatten_mapping(
        row,
        prefix=prefix,
        mapping=batch_mapping,
        exclude={"batch_type"},
    )
    _flatten_mapping(
        row,
        prefix=f"{prefix}_type",
        mapping=_read_mapping(batch_mapping, "batch_type"),
    )


def _flatten_container(row: CsvRow, *, prefix: str, container_row: Any) -> None:
    """Flatten a container and its model relation."""

    container_mapping = _as_mapping(container_row)
    _flatten_mapping(
        row,
        prefix=prefix,
        mapping=container_mapping,
        exclude={"container_model"},
    )
    _flatten_mapping(
        row,
        prefix=f"{prefix}_model",
        mapping=_read_mapping(container_mapping, "container_model"),
    )


def _flatten_mapping(
    row: CsvRow,
    *,
    prefix: str,
    mapping: Any,
    exclude: set[str] | frozenset[str] = frozenset(),
) -> None:
    """Flatten a nested Directus object into CSV-safe scalar values."""

    data = _as_mapping(mapping)
    for key, value in data.items():
        if key in exclude:
            continue

        column_name = sanitize_column_name(f"{prefix}_{key}")
        if value is None:
            row[column_name] = None
        elif isinstance(value, str | int | float | bool):
            row[column_name] = value
        elif isinstance(value, dict):
            _flatten_mapping(row, prefix=column_name, mapping=value)
        elif isinstance(value, list):
            row[column_name] = json.dumps(value, ensure_ascii=False, sort_keys=True)
        else:
            row[column_name] = json.dumps(value, ensure_ascii=False, sort_keys=True)


def sanitize_column_name(name: str) -> str:
    """Convert arbitrary Directus field paths into stable ASCII CSV headers."""

    normalized = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    normalized = normalized.replace("%", "percent")
    normalized = re.sub(r"[^0-9A-Za-z]+", "_", normalized)
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return normalized.lower()


def _to_scalar(value: Any) -> CsvScalar:
    """Return the value when it is CSV-safe, otherwise JSON-serialize it."""

    if value is None or isinstance(value, str | int | float | bool):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _to_nested_scalar(mapping: dict[str, Any] | None, key: str) -> CsvScalar:
    """Read a single scalar value from an optional mapping."""

    if mapping is None:
        return None
    return _to_scalar(mapping.get(key))


def _as_mapping(value: Any) -> dict[str, Any]:
    """Return the value when it is a dict, otherwise an empty mapping."""

    if isinstance(value, dict):
        return value
    return {}


def _read_mapping(mapping: dict[str, Any] | None, key: str) -> dict[str, Any] | None:
    """Read a nested mapping from a possibly-null dict."""

    if mapping is None:
        return None
    value = mapping.get(key)
    if isinstance(value, dict):
        return value
    return None


def _read_nested_mapping(
    mapping: dict[str, Any] | None,
    first_key: str,
    second_key: str,
) -> dict[str, Any] | None:
    """Read a nested mapping two levels deep."""

    return _read_mapping(_read_mapping(mapping, first_key), second_key)


def _read_nested_string(mapping: dict[str, Any] | None, *path: str) -> str | None:
    """Read a nested string from a mapping."""

    current: Any = mapping
    for part in path:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    if isinstance(current, str):
        return current
    return None


def _read_container_type(container_model: dict[str, Any] | None) -> str | None:
    """Read a container type from either a scalar or expanded relation payload."""

    if not isinstance(container_model, dict):
        return None
    value = container_model.get("container_type")
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        nested = value.get("container_type")
        if isinstance(nested, str):
            return nested
    return None
