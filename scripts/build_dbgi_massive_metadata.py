"""Build a taxonomically complete, file-level DBGI metadata table for MassIVE.

The exporter is read-only with respect to Directus. It writes one upload row per
physical mzML file only when the file maps to MS_Data, resolves through the
sample lineage to Field_Data, and has a complete, internally consistent Taxa
relation. Every excluded file is retained in a separate audit table.
"""

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
from directus_explorer.directus import MS_DATA_FIELDS, DirectusClient
from directus_explorer.samples import PROJECT_GROUPS, classify_profile_mode

TAXON_REQUIRED_FIELDS = (
    "col_id",
    "scientific_name",
    "rank",
    "backbone_version",
    "backbone_dataset_key",
    "backbone_doi",
)

OUTPUT_FIELDS = (
    "filename",
    "file_path",
    "file_size_bytes",
    "sample_id",
    "ms_data_id",
    "alternative_ms_data_ids",
    "qfield_project",
    "sample_name",
    "sample_type",
    "collector_full_name",
    "collector_people_id",
    "collector_orcid",
    "collector_inaturalist",
    "collection_date",
    "latitude",
    "longitude",
    "is_wild",
    "observation_subject",
    "catalogue_number",
    "ipen_accession",
    "weather",
    "soil_type",
    "temperature_celsius",
    "ecology_comment",
    "environment_comment",
    "taxon_col_id",
    "taxon_scientific_name",
    "taxon_authorship",
    "taxon_accepted_name",
    "taxon_rank",
    "taxon_resolution_status",
    "taxon_parent_col_ids",
    "taxon_backbone_version",
    "taxon_backbone_dataset_key",
    "taxon_backbone_doi",
    "taxon_directus_id",
    "ms_parent_container_id",
    "lineage_resolution_path",
    "extraction_data_id",
    "extraction_method",
    "dried_weight",
    "dried_weight_unit",
    "extraction_solvent_volume",
    "extraction_solvent_volume_unit",
    "aliquoting_data_id",
    "aliquot_volume",
    "aliquot_volume_unit",
    "injection_method",
    "ionization_mode",
    "injection_volume",
    "injection_volume_unit",
    "instrument_id",
    "instrument_model",
    "instrument_brand",
    "instrument_location",
    "ms_batch_id",
    "directus_filename",
    "file_match_method",
    "metadata_warnings",
)

EXCLUSION_FIELDS = (
    "filename",
    "file_path",
    "file_size_bytes",
    "exclusion_reasons",
    "matched_ms_data_ids",
    "candidate_sample_ids",
    "candidate_projects",
    "candidate_taxon_ref_ids",
    "candidate_taxon_col_ids",
    "file_match_method",
)


def main() -> None:
    args = _parse_args()
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_path = args.output_dir / f"dbgi_massive_metadata_{timestamp}.tsv"
    exclusions_path = args.output_dir / f"dbgi_massive_metadata_exclusions_{timestamp}.tsv"
    summary_path = args.output_dir / f"dbgi_massive_metadata_summary_{timestamp}.json"
    for path in (output_path, exclusions_path, summary_path):
        if path.exists():
            raise FileExistsError(f"Refusing to overwrite immutable output: {path}")

    client = DirectusClient(load_settings(env_file=args.env_file))
    client._authenticate()
    field_rows = client._get_items(collection="Field_Data", fields="*")
    people_rows = client._get_items(collection="People", fields="*")
    taxa_rows = client._get_items(collection="Taxa", fields="*")
    dried_rows = client._get_items(
        collection="Dried_Samples_Data",
        fields="id,sample_container.container_id,field_data.sample_id",
    )
    extraction_rows = client._get_items(
        collection="Extraction_Data",
        fields=(
            "id,sample_container.container_id,parent_sample_container.container_id,"
            "dried_weight,dried_weight_unit.symbol,solvent_volume,"
            "solvent_volume_unit.symbol,extraction_method.method_name,batch.batch_id"
        ),
    )
    aliquot_rows = client._get_items(
        collection="Aliquoting_Data",
        fields=(
            "id,sample_container.container_id,parent_sample_container.container_id,"
            "aliquot_volume,aliquot_volume_unit.symbol"
        ),
    )
    ms_rows = client._get_items(collection="MS_Data", fields=MS_DATA_FIELDS)

    context = _build_context(
        client=client,
        field_rows=field_rows,
        people_rows=people_rows,
        taxa_rows=taxa_rows,
        dried_rows=dried_rows,
        extraction_rows=extraction_rows,
        aliquot_rows=aliquot_rows,
        ms_rows=ms_rows,
    )
    output_rows, excluded_rows = _build_rows(client=client, args=args, context=context)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    _write_tsv(output_path, output_rows, OUTPUT_FIELDS)
    _write_tsv(exclusions_path, excluded_rows, EXCLUSION_FIELDS)
    summary = _build_summary(
        timestamp=timestamp,
        args=args,
        output_path=output_path,
        exclusions_path=exclusions_path,
        output_rows=output_rows,
        excluded_rows=excluded_rows,
        context=context,
    )
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mzml-dir",
        type=Path,
        default=Path("/media/nvme8tb/dbgi/dbgi_msdata"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("/media/nvme8tb/dbgi/metadata"),
    )
    parser.add_argument("--env-file", type=Path, default=Path(".env.admin"))
    return parser.parse_args()


def _build_context(
    *,
    client: DirectusClient,
    field_rows: list[dict[str, Any]],
    people_rows: list[dict[str, Any]],
    taxa_rows: list[dict[str, Any]],
    dried_rows: list[dict[str, Any]],
    extraction_rows: list[dict[str, Any]],
    aliquot_rows: list[dict[str, Any]],
    ms_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    field_by_sample: dict[str, dict[str, Any]] = {}
    duplicate_field_sample_ids: set[str] = set()
    for row in field_rows:
        sample_id = _text(row.get("sample_id"))
        if not sample_id:
            continue
        if sample_id in field_by_sample:
            duplicate_field_sample_ids.add(sample_id)
        else:
            field_by_sample[sample_id] = row

    people_by_id = {_relation_id(row.get("id")): row for row in people_rows}
    taxa_by_id = {_relation_id(row.get("id")): row for row in taxa_rows}
    dried_sample_by_container = {
        client._read_nested_string(
            row, "sample_container", "container_id"
        ): client._read_nested_string(row, "field_data", "sample_id")
        for row in dried_rows
        if client._read_nested_string(row, "sample_container", "container_id")
        and client._read_nested_string(row, "field_data", "sample_id")
    }
    extraction_by_child = _rows_by_child(client, extraction_rows)
    aliquot_by_child = _rows_by_child(client, aliquot_rows)

    ms_by_filename: dict[str, list[dict[str, Any]]] = defaultdict(list)
    ms_by_parent: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in ms_rows:
        filename = _stem(_text(row.get("filename"))).casefold()
        if filename:
            ms_by_filename[filename].append(row)
        parent_id = client._read_nested_string(row, "parent_sample_container", "container_id")
        if parent_id:
            ms_by_parent[parent_id.casefold()].append(row)

    return {
        "field_by_sample": field_by_sample,
        "duplicate_field_sample_ids": duplicate_field_sample_ids,
        "people_by_id": people_by_id,
        "taxa_by_id": taxa_by_id,
        "dried_sample_by_container": dried_sample_by_container,
        "extraction_by_child": extraction_by_child,
        "aliquot_by_child": aliquot_by_child,
        "ms_by_filename": ms_by_filename,
        "ms_by_parent": ms_by_parent,
        "directus_counts": {
            "Field_Data": len(field_rows),
            "People": len(people_rows),
            "Taxa": len(taxa_rows),
            "Dried_Samples_Data": len(dried_rows),
            "Extraction_Data": len(extraction_rows),
            "Aliquoting_Data": len(aliquot_rows),
            "MS_Data": len(ms_rows),
        },
    }


def _build_rows(
    *, client: DirectusClient, args: argparse.Namespace, context: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    output_rows: list[dict[str, Any]] = []
    excluded_rows: list[dict[str, Any]] = []
    dbgi_projects = set(PROJECT_GROUPS["dbgi"])

    for path in sorted(args.mzml_dir.glob("*.mzML"), key=lambda value: value.name.casefold()):
        matches, match_method = _match_ms_rows(path.name, context)
        candidates: list[dict[str, Any]] = []
        for ms_row in matches:
            resolved = _resolve_ms_row(client=client, ms_row=ms_row, context=context)
            if resolved["project"] in dbgi_projects:
                candidates.append(resolved)

        reasons: list[str] = []
        if not matches:
            reasons.append("no_ms_data_match")
        elif not candidates:
            reasons.append("no_resolved_dbgi_lineage")

        chosen = _choose_candidate(candidates)
        if chosen is not None:
            reasons.extend(_taxon_exclusion_reasons(chosen, context))

        if reasons:
            excluded_rows.append(
                _exclusion_row(
                    path=path,
                    reasons=reasons,
                    matches=matches,
                    candidates=candidates,
                    match_method=match_method,
                )
            )
            continue

        assert chosen is not None
        output_rows.append(
            _output_row(
                client=client,
                path=path,
                chosen=chosen,
                candidates=candidates,
                match_method=match_method,
                context=context,
                mzml_dir=args.mzml_dir,
            )
        )

    return output_rows, excluded_rows


def _match_ms_rows(filename: str, context: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    exact = context["ms_by_filename"].get(_stem(filename).casefold(), [])
    if exact:
        return _dedupe_rows(exact), "exact_filename"

    matched: list[dict[str, Any]] = []
    identifiers = re.findall(
        r"(?<![A-Za-z0-9])dbgi_\d{6}(?:_\d{2}){0,2}(?![A-Za-z0-9])",
        _stem(filename),
        flags=re.IGNORECASE,
    )
    for identifier in identifiers:
        keys = [identifier.casefold()]
        head, separator, tail = identifier.rpartition("_")
        if separator and re.fullmatch(r"\d{2}", tail):
            keys.append(head.casefold())
        for key in keys:
            matched.extend(context["ms_by_parent"].get(key, []))
    return _dedupe_rows(matched), "parent_container_id" if matched else ""


def _resolve_ms_row(
    *, client: DirectusClient, ms_row: dict[str, Any], context: dict[str, Any]
) -> dict[str, Any]:
    parent_id = client._read_nested_string(ms_row, "parent_sample_container", "container_id") or ""
    aliquot_row = context["aliquot_by_child"].get(parent_id)
    extraction_child_id = parent_id
    resolution_path = "extraction_to_original_sample"
    if aliquot_row is not None:
        extraction_child_id = (
            client._read_nested_string(aliquot_row, "parent_sample_container", "container_id") or ""
        )
        resolution_path = "aliquot_to_extraction_to_original_sample"
    extraction_row = context["extraction_by_child"].get(extraction_child_id)

    sample_id = ""
    if parent_id in context["dried_sample_by_container"]:
        sample_id = context["dried_sample_by_container"][parent_id]
        resolution_path = "ms_parent_is_dried_sample_container"
    elif extraction_row is not None:
        original_id = (
            client._read_nested_string(extraction_row, "parent_sample_container", "container_id")
            or ""
        )
        sample_id = context["dried_sample_by_container"].get(original_id, original_id)
    elif parent_id in context["field_by_sample"]:
        sample_id = parent_id
        resolution_path = "ms_parent_is_field_sample_id"

    field_row = context["field_by_sample"].get(sample_id)
    return {
        "ms_row": ms_row,
        "parent_id": parent_id,
        "sample_id": sample_id,
        "field_row": field_row,
        "project": _text(field_row.get("qfield_project")) if field_row else "",
        "aliquot_row": aliquot_row,
        "extraction_row": extraction_row,
        "resolution_path": resolution_path if field_row else "unresolved",
    }


def _choose_candidate(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not candidates:
        return None
    return min(candidates, key=lambda row: int(row["ms_row"]["id"]))


def _taxon_exclusion_reasons(chosen: dict[str, Any], context: dict[str, Any]) -> list[str]:
    field_row = chosen["field_row"]
    if field_row is None:
        return ["missing_field_data"]
    sample_id = chosen["sample_id"]
    reasons: list[str] = []
    if sample_id in context["duplicate_field_sample_ids"]:
        reasons.append("duplicate_field_data_sample_id")

    taxon_ref_id = _relation_id(field_row.get("taxon_ref"))
    if not taxon_ref_id:
        return reasons + ["missing_taxon_ref"]
    taxon = context["taxa_by_id"].get(taxon_ref_id)
    if taxon is None:
        return reasons + ["missing_taxa_record"]

    field_col_id = _text(field_row.get("taxon_col_id"))
    taxon_col_id = _text(taxon.get("col_id"))
    if field_col_id != taxon_col_id:
        reasons.append("taxon_col_id_mismatch")
    resolution_status = _text(field_row.get("taxon_resolution_status"))
    if not resolution_status or "unresolved" in resolution_status.casefold():
        reasons.append("incomplete_taxon_resolution_status")
    for fieldname in TAXON_REQUIRED_FIELDS:
        if not _text(taxon.get(fieldname)):
            reasons.append(f"missing_taxa_{fieldname}")
    return reasons


def _output_row(
    *,
    client: DirectusClient,
    path: Path,
    chosen: dict[str, Any],
    candidates: list[dict[str, Any]],
    match_method: str,
    context: dict[str, Any],
    mzml_dir: Path,
) -> dict[str, Any]:
    field = chosen["field_row"]
    ms = chosen["ms_row"]
    extraction = chosen["extraction_row"] or {}
    aliquot = chosen["aliquot_row"] or {}
    person_id = _relation_id(field.get("collector_ref"))
    person = context["people_by_id"].get(person_id, {})
    taxon_id = _relation_id(field.get("taxon_ref"))
    taxon = context["taxa_by_id"][taxon_id]
    warnings: list[str] = []
    if not person_id:
        warnings.append("missing_collector_ref")
    elif not person:
        warnings.append("missing_people_record")
    elif not _text(person.get("full_name")):
        warnings.append("missing_people_full_name")
    if len(candidates) > 1:
        warnings.append("multiple_ms_rows_same_file_match")

    method = client._read_nested_string(ms, "injection_method", "method_name") or ""
    instrument_location = (
        client._read_nested_string(ms, "instrument_used", "instrument_location", "room_name") or ""
    )
    accepted_name = " ".join(
        value
        for value in (_text(taxon.get("scientific_name")), _text(taxon.get("authorship")))
        if value
    )
    alternative_ids = sorted(
        {str(row["ms_row"]["id"]) for row in candidates if row is not chosen},
        key=int,
    )
    return {
        "filename": path.name,
        "file_path": str(Path(mzml_dir.name) / path.name),
        "file_size_bytes": path.stat().st_size,
        "sample_id": chosen["sample_id"],
        "ms_data_id": ms["id"],
        "alternative_ms_data_ids": "|".join(alternative_ids),
        "qfield_project": chosen["project"],
        "sample_name": _text(field.get("sample_name")),
        "sample_type": "plant specimen",
        "collector_full_name": _text(person.get("full_name")),
        "collector_people_id": person_id,
        "collector_orcid": _text(person.get("orcid")),
        "collector_inaturalist": _text(person.get("inat")),
        "collection_date": _text(field.get("date")),
        "latitude": _text(field.get("latitude")),
        "longitude": _text(field.get("longitude")),
        "is_wild": _text(field.get("is_wild")),
        "observation_subject": _text(field.get("observation_subject")),
        "catalogue_number": _text(field.get("catalogue_number")),
        "ipen_accession": _text(field.get("ipen")),
        "weather": _text(field.get("weather")),
        "soil_type": _text(field.get("soil_type")),
        "temperature_celsius": _text(field.get("temperature_°C")),
        "ecology_comment": _text(field.get("comment_eco")),
        "environment_comment": _text(field.get("comment_env")),
        "taxon_col_id": _text(taxon.get("col_id")),
        "taxon_scientific_name": _text(taxon.get("scientific_name")),
        "taxon_authorship": _text(taxon.get("authorship")),
        "taxon_accepted_name": accepted_name,
        "taxon_rank": _text(taxon.get("rank")),
        "taxon_resolution_status": _text(field.get("taxon_resolution_status")),
        "taxon_parent_col_ids": _json_cell(field.get("taxon_parent_col_ids")),
        "taxon_backbone_version": _text(taxon.get("backbone_version")),
        "taxon_backbone_dataset_key": _text(taxon.get("backbone_dataset_key")),
        "taxon_backbone_doi": _text(taxon.get("backbone_doi")),
        "taxon_directus_id": taxon_id,
        "ms_parent_container_id": chosen["parent_id"],
        "lineage_resolution_path": chosen["resolution_path"],
        "extraction_data_id": _text(extraction.get("id")),
        "extraction_method": client._read_nested_string(
            extraction, "extraction_method", "method_name"
        )
        or "",
        "dried_weight": _text(extraction.get("dried_weight")),
        "dried_weight_unit": client._read_nested_string(extraction, "dried_weight_unit", "symbol")
        or "",
        "extraction_solvent_volume": _text(extraction.get("solvent_volume")),
        "extraction_solvent_volume_unit": client._read_nested_string(
            extraction, "solvent_volume_unit", "symbol"
        )
        or "",
        "aliquoting_data_id": _text(aliquot.get("id")),
        "aliquot_volume": _text(aliquot.get("aliquot_volume")),
        "aliquot_volume_unit": client._read_nested_string(aliquot, "aliquot_volume_unit", "symbol")
        or "",
        "injection_method": method,
        "ionization_mode": classify_profile_mode(method) or "",
        "injection_volume": _text(ms.get("injection_volume")),
        "injection_volume_unit": client._read_nested_string(ms, "injection_volume_unit", "symbol")
        or "",
        "instrument_id": client._read_nested_string(ms, "instrument_used", "instrument_id") or "",
        "instrument_model": client._read_nested_string(
            ms, "instrument_used", "instrument_model", "instrument_model"
        )
        or "",
        "instrument_brand": client._read_nested_string(
            ms, "instrument_used", "instrument_model", "brand"
        )
        or "",
        "instrument_location": instrument_location,
        "ms_batch_id": client._read_nested_string(ms, "batch", "batch_id") or "",
        "directus_filename": _text(ms.get("filename")),
        "file_match_method": match_method,
        "metadata_warnings": "|".join(warnings),
    }


def _exclusion_row(
    *,
    path: Path,
    reasons: list[str],
    matches: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    match_method: str,
) -> dict[str, Any]:
    return {
        "filename": path.name,
        "file_path": str(path),
        "file_size_bytes": path.stat().st_size,
        "exclusion_reasons": "|".join(sorted(set(reasons))),
        "matched_ms_data_ids": "|".join(sorted({str(row["id"]) for row in matches}, key=int)),
        "candidate_sample_ids": "|".join(
            sorted({row["sample_id"] for row in candidates if row["sample_id"]})
        ),
        "candidate_projects": "|".join(
            sorted({row["project"] for row in candidates if row["project"]})
        ),
        "candidate_taxon_ref_ids": "|".join(
            sorted(
                {
                    _relation_id(row["field_row"].get("taxon_ref"))
                    for row in candidates
                    if row["field_row"] and _relation_id(row["field_row"].get("taxon_ref"))
                },
                key=int,
            )
        ),
        "candidate_taxon_col_ids": "|".join(
            sorted(
                {
                    _text(row["field_row"].get("taxon_col_id"))
                    for row in candidates
                    if row["field_row"] and _text(row["field_row"].get("taxon_col_id"))
                }
            )
        ),
        "file_match_method": match_method,
    }


def _build_summary(
    *,
    timestamp: str,
    args: argparse.Namespace,
    output_path: Path,
    exclusions_path: Path,
    output_rows: list[dict[str, Any]],
    excluded_rows: list[dict[str, Any]],
    context: dict[str, Any],
) -> dict[str, Any]:
    exclusion_counts = Counter(
        reason
        for row in excluded_rows
        for reason in str(row["exclusion_reasons"]).split("|")
        if reason
    )
    warning_counts = Counter(
        warning
        for row in output_rows
        for warning in str(row["metadata_warnings"]).split("|")
        if warning
    )
    return {
        "generated_at_utc": timestamp,
        "mode": "directus_get_only",
        "mzml_directory": str(args.mzml_dir),
        "mzml_file_count": len(output_rows) + len(excluded_rows),
        "massive_eligible_row_count": len(output_rows),
        "excluded_file_count": len(excluded_rows),
        "eligible_by_project": dict(
            sorted(Counter(row["qfield_project"] for row in output_rows).items())
        ),
        "eligible_by_taxon_rank": dict(
            sorted(Counter(row["taxon_rank"] for row in output_rows).items())
        ),
        "eligible_by_match_method": dict(
            sorted(Counter(row["file_match_method"] for row in output_rows).items())
        ),
        "exclusion_reason_counts": dict(sorted(exclusion_counts.items())),
        "eligible_warning_counts": dict(sorted(warning_counts.items())),
        "directus_collection_counts": context["directus_counts"],
        "taxon_required_fields": list(TAXON_REQUIRED_FIELDS),
        "metadata_table": str(output_path),
        "exclusion_audit": str(exclusions_path),
    }


def _rows_by_child(client: DirectusClient, rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for row in rows:
        child_id = client._read_nested_string(row, "sample_container", "container_id")
        if child_id:
            output.setdefault(child_id, row)
    return output


def _dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return list({str(row["id"]): row for row in rows}.values())


def _relation_id(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("id")
    return _text(value)


def _stem(value: str) -> str:
    name = Path(value).name
    return name[:-5] if name.casefold().endswith(".mzml") else name


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, dict | list):
        return _json_cell(value)
    return str(value).strip()


def _json_cell(value: Any) -> str:
    if value in (None, "", [], {}):
        return ""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _write_tsv(path: Path, rows: list[dict[str, Any]], fieldnames: tuple[str, ...]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=fieldnames, delimiter="\t", extrasaction="ignore"
        )
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
