"""Audit Directus sample integrity across projects.

This is a read-only report generator. It focuses on Field_Data identifiers,
project-level duplication, taxonomic completeness, coordinate/date sanity, and
basic sample-lineage coverage through dried/extraction/aliquot/MS collections.
"""

from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from directus_explorer.config import load_settings
from directus_explorer.directus import DirectusClient

OUTPUT_DIR = Path("data/output/directus_integrity")
DEFAULT_ENV_FILE = Path(".env")

CANONICAL_SAMPLE_RE = re.compile(r"^[a-z][a-z0-9]*_\d{6}$")
SHORT_PREFIX_RE = re.compile(r"^[a-z][a-z0-9]*_\d{1,5}$")
DERIVED_CONTAINER_RE = re.compile(r"^[a-z][a-z0-9]*_\d{6}(?:_\d{2})+$")
DBGI_ID_RE = re.compile(r"^dbgi_(\d+)$")


@dataclass(frozen=True, slots=True)
class FieldRecord:
    """Normalized subset of one Field_Data row."""

    id: int
    project: str
    sample_id: str
    sample_id_class: str
    canonical_candidate: str
    taxon_name: str
    sample_name: str
    taxon_name_final: str
    date: str
    latitude: str
    longitude: str
    coordinate_status: str
    duplicate_fingerprint: str

    @property
    def has_taxon_name(self) -> bool:
        return bool(self.taxon_name or self.sample_name or self.taxon_name_final)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    client = DirectusClient(load_settings(DEFAULT_ENV_FILE))
    client._authenticate()

    field_rows = client._get_items(collection="Field_Data", fields="*")
    dried_rows = client._get_items(
        collection="Dried_Samples_Data",
        fields="id,sample_container.container_id,field_data.id,field_data.sample_id,field_data.qfield_project",
    )
    extraction_rows = client._get_items(
        collection="Extraction_Data",
        fields="id,sample_container.container_id,parent_sample_container.container_id",
    )
    aliquot_rows = client._get_items(
        collection="Aliquoting_Data",
        fields="id,sample_container.container_id,parent_sample_container.container_id",
    )
    ms_rows = client._get_items(
        collection="MS_Data",
        fields=(
            "id,filename,parent_sample_container.container_id,"
            "injection_method.method_name,instrument.instrument_name"
        ),
    )

    records = [_normalize_field_record(row) for row in field_rows]
    _write_raw_snapshot(field_rows)
    _write_project_summary(records)
    _write_field_issues(records)
    _write_duplicate_sample_ids(records)
    _write_duplicate_observations(records)
    _write_lineage_reports(
        records=records,
        dried_rows=dried_rows,
        extraction_rows=extraction_rows,
        aliquot_rows=aliquot_rows,
        ms_rows=ms_rows,
    )
    _write_per_project_splits()
    _write_index(records, dried_rows, extraction_rows, aliquot_rows, ms_rows)


def _normalize_field_record(row: dict[str, Any]) -> FieldRecord:
    sample_id = _clean(row.get("sample_id"))
    project = _clean(row.get("qfield_project")) or "(missing)"
    taxon_name = _clean(row.get("taxon_name"))
    sample_name = _clean(row.get("sample_name"))
    taxon_name_final = _clean(row.get("taxon_name_final"))
    date = _clean(row.get("date"))
    latitude = _clean(row.get("latitude"))
    longitude = _clean(row.get("longitude"))
    coordinate_status = _coordinate_status(latitude, longitude)
    return FieldRecord(
        id=_int_id(row),
        project=project,
        sample_id=sample_id,
        sample_id_class=_sample_id_class(sample_id),
        canonical_candidate=_canonical_candidate(sample_id),
        taxon_name=taxon_name,
        sample_name=sample_name,
        taxon_name_final=taxon_name_final,
        date=date,
        latitude=latitude,
        longitude=longitude,
        coordinate_status=coordinate_status,
        duplicate_fingerprint=_duplicate_fingerprint(
            project=project,
            taxon_name=taxon_name or sample_name or taxon_name_final,
            date=date,
            latitude=latitude,
            longitude=longitude,
        ),
    )


def _write_raw_snapshot(rows: list[dict[str, Any]]) -> None:
    path = OUTPUT_DIR / "field_data_raw_snapshot.json"
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "row_count": len(rows),
        "rows": rows,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_project_summary(records: list[FieldRecord]) -> None:
    by_project: dict[str, list[FieldRecord]] = defaultdict(list)
    for record in records:
        by_project[record.project].append(record)

    rows: list[dict[str, Any]] = []
    for project, project_records in sorted(by_project.items()):
        sample_counts = Counter(record.sample_id for record in project_records if record.sample_id)
        duplicate_sample_row_count = sum(count for count in sample_counts.values() if count > 1)
        duplicate_sample_id_count = sum(1 for count in sample_counts.values() if count > 1)
        fingerprint_counts = Counter(
            record.duplicate_fingerprint
            for record in project_records
            if record.duplicate_fingerprint
        )
        duplicate_fingerprint_row_count = sum(
            count for count in fingerprint_counts.values() if count > 1
        )
        duplicate_fingerprint_count = sum(1 for count in fingerprint_counts.values() if count > 1)
        class_counts = Counter(record.sample_id_class for record in project_records)
        coordinate_counts = Counter(record.coordinate_status for record in project_records)
        rows.append(
            {
                "project": project,
                "field_data_rows": len(project_records),
                "distinct_sample_ids": len(sample_counts),
                "missing_sample_id_rows": class_counts["missing"],
                "canonical_sample_id_rows": class_counts["canonical"],
                "nonstandard_sample_id_rows": sum(
                    count
                    for sample_class, count in class_counts.items()
                    if sample_class not in {"canonical", "missing"}
                ),
                "duplicate_sample_id_count": duplicate_sample_id_count,
                "duplicate_sample_id_row_count": duplicate_sample_row_count,
                "missing_taxon_rows": sum(not record.has_taxon_name for record in project_records),
                "missing_date_rows": sum(not record.date for record in project_records),
                "missing_coordinate_rows": coordinate_counts["missing"],
                "coordinates_look_swapped_rows": coordinate_counts["looks_swapped_ch_lat_lon"],
                "coordinates_out_of_ch_range_rows": coordinate_counts["out_of_ch_range"],
                "duplicate_observation_fingerprint_count": duplicate_fingerprint_count,
                "duplicate_observation_fingerprint_row_count": duplicate_fingerprint_row_count,
            }
        )
    _write_tsv(OUTPUT_DIR / "project_summary.tsv", rows)
    (OUTPUT_DIR / "project_summary.json").write_text(
        json.dumps(rows, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _write_field_issues(records: list[FieldRecord]) -> None:
    rows: list[dict[str, Any]] = []
    for record in records:
        issues: list[str] = []
        if record.sample_id_class != "canonical":
            issues.append(f"sample_id_{record.sample_id_class}")
        if not record.has_taxon_name:
            issues.append("missing_taxon_name")
        if not record.date:
            issues.append("missing_date")
        if record.coordinate_status not in {"ok_ch_range", "out_of_ch_range"}:
            issues.append(f"coordinate_{record.coordinate_status}")
        if not issues:
            continue
        rows.append(_record_row(record) | {"issues": ";".join(issues)})
    _write_tsv(OUTPUT_DIR / "field_data_issues.tsv", rows)


def _write_duplicate_sample_ids(records: list[FieldRecord]) -> None:
    groups: dict[tuple[str, str], list[FieldRecord]] = defaultdict(list)
    for record in records:
        if record.sample_id:
            groups[(record.project, record.sample_id)].append(record)

    within_rows: list[dict[str, Any]] = []
    for (project, sample_id), group in sorted(groups.items()):
        if len(group) <= 1:
            continue
        within_rows.append(
            {
                "project": project,
                "sample_id": sample_id,
                "row_count": len(group),
                "field_data_ids": _join(record.id for record in group),
                "taxon_names": _join_unique(record.taxon_name for record in group),
                "dates": _join_unique(record.date for record in group),
                "coordinates": _join_unique(
                    f"{record.latitude},{record.longitude}" for record in group
                ),
            }
        )
    _write_tsv(OUTPUT_DIR / "duplicate_sample_ids_within_project.tsv", within_rows)

    cross_groups: dict[str, list[FieldRecord]] = defaultdict(list)
    for record in records:
        if record.sample_id:
            cross_groups[record.sample_id].append(record)
    cross_rows: list[dict[str, Any]] = []
    for sample_id, group in sorted(cross_groups.items()):
        projects = sorted({record.project for record in group})
        if len(projects) <= 1:
            continue
        cross_rows.append(
            {
                "sample_id": sample_id,
                "project_count": len(projects),
                "projects": _join(projects),
                "row_count": len(group),
                "field_data_ids": _join(record.id for record in group),
                "taxon_names": _join_unique(record.taxon_name for record in group),
            }
        )
    _write_tsv(OUTPUT_DIR / "duplicate_sample_ids_across_projects.tsv", cross_rows)


def _write_duplicate_observations(records: list[FieldRecord]) -> None:
    groups: dict[str, list[FieldRecord]] = defaultdict(list)
    for record in records:
        if record.duplicate_fingerprint:
            groups[record.duplicate_fingerprint].append(record)

    rows: list[dict[str, Any]] = []
    group_id = 0
    for fingerprint, group in sorted(groups.items()):
        if len(group) <= 1:
            continue
        group_id += 1
        for record in sorted(group, key=lambda value: value.id):
            rows.append(
                {
                    "group_id": group_id,
                    "group_size": len(group),
                    "fingerprint": fingerprint,
                    **_record_row(record),
                }
            )
    _write_tsv(OUTPUT_DIR / "duplicate_observation_fingerprints.tsv", rows)


def _write_lineage_reports(
    *,
    records: list[FieldRecord],
    dried_rows: list[dict[str, Any]],
    extraction_rows: list[dict[str, Any]],
    aliquot_rows: list[dict[str, Any]],
    ms_rows: list[dict[str, Any]],
) -> None:
    records_by_sample_id: dict[str, list[FieldRecord]] = defaultdict(list)
    for record in records:
        if record.sample_id:
            records_by_sample_id[record.sample_id].append(record)

    dried_by_sample_id = _dried_by_sample_id(dried_rows)
    extraction_by_parent = _group_by_nested(
        extraction_rows,
        ("parent_sample_container", "container_id"),
    )
    extraction_by_child = _group_by_nested(extraction_rows, ("sample_container", "container_id"))
    aliquot_by_parent = _group_by_nested(aliquot_rows, ("parent_sample_container", "container_id"))
    aliquot_by_child = _group_by_nested(aliquot_rows, ("sample_container", "container_id"))
    ms_by_parent = _group_by_nested(ms_rows, ("parent_sample_container", "container_id"))

    lineage_rows: list[dict[str, Any]] = []
    project_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for record in records:
        dried = dried_by_sample_id.get(record.sample_id, [])
        dried_container_ids = [
            _nested_string(row, ("sample_container", "container_id"))
            for row in dried
            if _nested_string(row, ("sample_container", "container_id"))
        ]
        extraction_rows_for_record = [
            row
            for container_id in dried_container_ids
            for row in extraction_by_parent[container_id]
        ]
        if not extraction_rows_for_record:
            extraction_rows_for_record = extraction_by_child.get(f"{record.sample_id}_01", [])
        extraction_container_ids = [
            _nested_string(row, ("sample_container", "container_id"))
            for row in extraction_rows_for_record
            if _nested_string(row, ("sample_container", "container_id"))
        ]
        aliquot_rows_for_record = [
            row
            for container_id in extraction_container_ids
            for row in aliquot_by_parent[container_id]
        ]
        ms_rows_for_record = [
            row for container_id in extraction_container_ids for row in ms_by_parent[container_id]
        ]
        for aliquot_row in aliquot_rows_for_record:
            aliquot_container_id = _nested_string(aliquot_row, ("sample_container", "container_id"))
            if aliquot_container_id:
                ms_rows_for_record.extend(ms_by_parent[aliquot_container_id])

        has_dried = bool(dried)
        has_extraction = bool(extraction_rows_for_record)
        has_aliquot = bool(aliquot_rows_for_record)
        has_ms = bool(ms_rows_for_record)
        project_counts[record.project]["field_rows"] += 1
        project_counts[record.project]["has_dried"] += int(has_dried)
        project_counts[record.project]["missing_dried"] += int(not has_dried)
        project_counts[record.project]["has_extraction"] += int(has_extraction)
        project_counts[record.project]["missing_extraction"] += int(not has_extraction)
        project_counts[record.project]["has_aliquot"] += int(has_aliquot)
        project_counts[record.project]["has_ms"] += int(has_ms)

        if not (has_dried and has_extraction):
            lineage_rows.append(
                {
                    **_record_row(record),
                    "has_dried": str(has_dried).lower(),
                    "has_extraction": str(has_extraction).lower(),
                    "has_aliquot": str(has_aliquot).lower(),
                    "has_ms": str(has_ms).lower(),
                    "dried_sample_container_ids": _join(dried_container_ids),
                    "extraction_sample_container_ids": _join(extraction_container_ids),
                    "issue": ";".join(
                        issue
                        for issue, present in (
                            ("missing_dried", has_dried),
                            ("missing_extraction", has_extraction),
                        )
                        if not present
                    ),
                }
            )

    project_rows = [
        {"project": project, **dict(counts)}
        for project, counts in sorted(project_counts.items())
    ]
    _write_tsv(OUTPUT_DIR / "lineage_summary_by_project.tsv", project_rows)
    _write_tsv(OUTPUT_DIR / "lineage_issues.tsv", lineage_rows)
    _write_orphan_lineage_reports(
        records_by_sample_id=records_by_sample_id,
        dried_rows=dried_rows,
        extraction_rows=extraction_rows,
        aliquot_rows=aliquot_rows,
        ms_rows=ms_rows,
        extraction_by_child=extraction_by_child,
        aliquot_by_child=aliquot_by_child,
    )


def _write_orphan_lineage_reports(
    *,
    records_by_sample_id: dict[str, list[FieldRecord]],
    dried_rows: list[dict[str, Any]],
    extraction_rows: list[dict[str, Any]],
    aliquot_rows: list[dict[str, Any]],
    ms_rows: list[dict[str, Any]],
    extraction_by_child: dict[str, list[dict[str, Any]]],
    aliquot_by_child: dict[str, list[dict[str, Any]]],
) -> None:
    orphan_rows: list[dict[str, Any]] = []
    for row in dried_rows:
        sample_id = _nested_string(row, ("field_data", "sample_id"))
        if not sample_id or sample_id not in records_by_sample_id:
            orphan_rows.append(
                {
                    "collection": "Dried_Samples_Data",
                    "id": row.get("id"),
                    "container_id": _nested_string(row, ("sample_container", "container_id")),
                    "sample_id": sample_id,
                    "issue": "missing_field_data_for_dried_sample",
                }
            )
    for row in extraction_rows:
        child = _nested_string(row, ("sample_container", "container_id"))
        parent = _nested_string(row, ("parent_sample_container", "container_id"))
        if parent and parent not in records_by_sample_id and parent not in extraction_by_child:
            orphan_rows.append(
                {
                    "collection": "Extraction_Data",
                    "id": row.get("id"),
                    "container_id": child,
                    "sample_id": parent,
                    "issue": "parent_not_field_sample_or_extraction_container",
                }
            )
    for row in aliquot_rows:
        parent = _nested_string(row, ("parent_sample_container", "container_id"))
        if parent and parent not in extraction_by_child:
            orphan_rows.append(
                {
                    "collection": "Aliquoting_Data",
                    "id": row.get("id"),
                    "container_id": _nested_string(row, ("sample_container", "container_id")),
                    "sample_id": parent,
                    "issue": "parent_not_extraction_container",
                }
            )
    for row in ms_rows:
        parent = _nested_string(row, ("parent_sample_container", "container_id"))
        if parent and parent not in extraction_by_child and parent not in aliquot_by_child:
            orphan_rows.append(
                {
                    "collection": "MS_Data",
                    "id": row.get("id"),
                    "container_id": parent,
                    "sample_id": "",
                    "issue": "parent_not_extraction_or_aliquot_container",
                }
            )
    _write_tsv(OUTPUT_DIR / "lineage_orphans.tsv", orphan_rows)


def _write_index(
    records: list[FieldRecord],
    dried_rows: list[dict[str, Any]],
    extraction_rows: list[dict[str, Any]],
    aliquot_rows: list[dict[str, Any]],
    ms_rows: list[dict[str, Any]],
) -> None:
    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "collection_counts": {
            "Field_Data": len(records),
            "Dried_Samples_Data": len(dried_rows),
            "Extraction_Data": len(extraction_rows),
            "Aliquoting_Data": len(aliquot_rows),
            "MS_Data": len(ms_rows),
        },
        "reports": sorted(path.name for path in OUTPUT_DIR.iterdir() if path.is_file()),
        "per_project_report_dir": str(OUTPUT_DIR / "by_project"),
    }
    (OUTPUT_DIR / "integrity_report_index.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _write_per_project_splits() -> None:
    split_dir = OUTPUT_DIR / "by_project"
    split_dir.mkdir(parents=True, exist_ok=True)
    for path in (
        OUTPUT_DIR / "field_data_issues.tsv",
        OUTPUT_DIR / "duplicate_observation_fingerprints.tsv",
        OUTPUT_DIR / "lineage_issues.tsv",
    ):
        rows = _read_tsv(path)
        rows_by_project: dict[str, list[dict[str, str]]] = defaultdict(list)
        for row in rows:
            project = row.get("project", "")
            if project:
                rows_by_project[project].append(row)
        for project, project_rows in rows_by_project.items():
            target = split_dir / f"{_safe_filename(project)}__{path.name}"
            _write_tsv(target, project_rows)


def _sample_id_class(sample_id: str) -> str:
    if not sample_id:
        return "missing"
    sample_id_lower = sample_id.lower()
    if CANONICAL_SAMPLE_RE.fullmatch(sample_id_lower):
        return "canonical"
    if DERIVED_CONTAINER_RE.fullmatch(sample_id_lower):
        return "derived_container_id"
    if SHORT_PREFIX_RE.fullmatch(sample_id_lower):
        return "short_numeric_suffix"
    return "other_noncanonical"


def _canonical_candidate(sample_id: str) -> str:
    match = DBGI_ID_RE.fullmatch(sample_id.lower())
    if match is None:
        return ""
    return f"dbgi_{int(match.group(1)):06d}"


def _coordinate_status(latitude: str, longitude: str) -> str:
    if not latitude or not longitude:
        return "missing"
    try:
        lat = float(latitude)
        lon = float(longitude)
    except ValueError:
        return "invalid_number"
    if 45 <= lat <= 48 and 5 <= lon <= 11:
        return "ok_ch_range"
    if 5 <= lat <= 11 and 45 <= lon <= 48:
        return "looks_swapped_ch_lat_lon"
    return "out_of_ch_range"


def _duplicate_fingerprint(
    *,
    project: str,
    taxon_name: str,
    date: str,
    latitude: str,
    longitude: str,
) -> str:
    if not date or not latitude or not longitude:
        return ""
    try:
        lat = round(float(latitude), 6)
        lon = round(float(longitude), 6)
    except ValueError:
        return ""
    return "|".join([project, taxon_name.strip().lower(), date.strip(), str(lat), str(lon)])


def _dried_by_sample_id(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        sample_id = _nested_string(row, ("field_data", "sample_id"))
        if sample_id:
            grouped[sample_id].append(row)
    return grouped


def _group_by_nested(
    rows: list[dict[str, Any]],
    path: tuple[str, ...],
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        value = _nested_string(row, path)
        if value:
            grouped[value].append(row)
    return grouped


def _nested_string(row: dict[str, Any], path: tuple[str, ...]) -> str:
    current: Any = row
    for key in path:
        if not isinstance(current, dict):
            return ""
        current = current.get(key)
    return _clean(current)


def _record_row(record: FieldRecord) -> dict[str, Any]:
    return {
        "project": record.project,
        "field_data_id": record.id,
        "sample_id": record.sample_id,
        "sample_id_class": record.sample_id_class,
        "canonical_candidate": record.canonical_candidate,
        "taxon_name": record.taxon_name,
        "sample_name": record.sample_name,
        "taxon_name_final": record.taxon_name_final,
        "date": record.date,
        "latitude": record.latitude,
        "longitude": record.longitude,
        "coordinate_status": record.coordinate_status,
    }


def _write_tsv(path: Path, rows: list[dict[str, Any]]) -> None:
    if rows:
        fieldnames = list(rows[0])
    else:
        fieldnames = ("message",)
        rows = [{"message": "no_rows"}]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _safe_filename(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z_.-]+", "_", value).strip("_") or "missing_project"


def _int_id(row: dict[str, Any]) -> int:
    return int(str(row["id"]))


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _join(values: Any) -> str:
    return ";".join(str(value) for value in values if value is not None and str(value) != "")


def _join_unique(values: Any) -> str:
    return _join(sorted({str(value) for value in values if value}))


if __name__ == "__main__":
    main()
