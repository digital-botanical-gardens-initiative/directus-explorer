"""Build a database-wide Catalogue of Life backbone mapping."""

from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from directus_explorer.taxon_curation import (
    COL_DATASET_DOI,
    COL_DATASET_KEY,
    COL_DATASET_VERSION,
    ColNameResolution,
)

SAFE_RESOLUTION_STATUSES = {
    "accepted_exact",
    "accepted_variant",
    "synonym",
}


def read_unified_taxon_rows(path: Path) -> list[dict[str, str]]:
    """Read populated unified names from a Field_Data audit export."""

    with path.open(encoding="utf-8", newline="") as handle:
        return [
            {key: value or "" for key, value in row.items()}
            for row in csv.DictReader(handle, delimiter="\t")
            if (row.get("stored_taxon_name_unified") or "").strip()
        ]


def inventory_unified_names(rows: Iterable[dict[str, str]]) -> list[dict[str, Any]]:
    """Aggregate Field_Data rows by their verbatim unified taxon name."""

    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        name = row.get("stored_taxon_name_unified", "").strip()
        if name:
            grouped[name].append(row)

    inventory: list[dict[str, Any]] = []
    for name, members in sorted(grouped.items(), key=lambda item: item[0].casefold()):
        inventory.append(
            {
                "taxon_name_unified": name,
                "field_data_count": len(members),
                "project_count": len({row.get("project", "") for row in members}),
                "projects": ";".join(sorted({row.get("project", "") for row in members})),
                "field_data_ids": ";".join(row.get("field_data_id", "") for row in members),
                "sample_ids": ";".join(row.get("sample_id", "") for row in members),
            }
        )
    return inventory


def resolution_status(resolution: ColNameResolution) -> str:
    """Classify a pinned-CoL resolution for downstream review and storage."""

    if resolution.parsed_cultivar:
        return "cultivar_requires_review"
    if not resolution.is_usable_match:
        if resolution.match_type.casefold() == "higherrank":
            return "higher_rank_match"
        if resolution.parse_status == "unparsed":
            return "unparsed"
        return "unmatched"
    if resolution.match_type.casefold() == "ambiguous":
        return "ambiguous_match"
    if resolution.matched_status.casefold() == "synonym":
        return "synonym"
    if resolution.match_type.casefold() == "exact":
        return "accepted_exact"
    if resolution.match_type.casefold() == "variant":
        return "accepted_variant"
    return "matched_requires_review"


def build_backbone_rows(
    inventory: Iterable[dict[str, Any]],
    resolutions: dict[str, ColNameResolution],
) -> list[dict[str, Any]]:
    """Combine name occurrence counts with pinned-CoL resolution metadata."""

    output: list[dict[str, Any]] = []
    for item in inventory:
        name = str(item["taxon_name_unified"])
        resolution = resolutions[name]
        status = resolution_status(resolution)
        output.append(
            {
                **item,
                "taxon_col_id": resolution.accepted_usage_id,
                "taxon_resolution_status": status,
                "taxon_backbone_version": COL_DATASET_VERSION,
                "taxon_backbone_dataset_key": COL_DATASET_KEY,
                "taxon_backbone_doi": COL_DATASET_DOI,
                "requires_review": str(status not in SAFE_RESOLUTION_STATUSES).lower(),
                "col_lookup_name": resolution.lookup_name or resolution.verbatim_name,
                "parsed_name": resolution.parsed_name,
                "parsed_authorship": resolution.parsed_authorship,
                "parsed_rank": resolution.parsed_rank,
                "parsed_cultivar": resolution.parsed_cultivar,
                "parse_status": resolution.parse_status,
                "col_match_type": resolution.match_type,
                "col_matched_usage_id": resolution.matched_usage_id,
                "col_matched_name": resolution.matched_name,
                "col_matched_authorship": resolution.matched_authorship,
                "col_matched_rank": resolution.matched_rank,
                "col_matched_status": resolution.matched_status,
                "col_accepted_name": resolution.accepted_name,
                "col_accepted_authorship": resolution.accepted_authorship,
                "col_accepted_rank": resolution.accepted_rank,
                "col_issues": resolution.issues,
                "col_fuzzy_candidates": resolution.fuzzy_candidates,
            }
        )
    return output


def build_field_data_mapping(
    rows: Iterable[dict[str, str]],
    resolutions: dict[str, ColNameResolution],
) -> list[dict[str, str]]:
    """Create a row-level mapping suitable for a later Directus preflight."""

    output: list[dict[str, str]] = []
    for row in rows:
        name = row["stored_taxon_name_unified"].strip()
        resolution = resolutions[name]
        status = resolution_status(resolution)
        output.append(
            {
                "project": row.get("project", ""),
                "field_data_id": row.get("field_data_id", ""),
                "sample_id": row.get("sample_id", ""),
                "taxon_name_unified": name,
                "taxon_col_id": resolution.accepted_usage_id,
                "taxon_resolution_status": status,
                "taxon_backbone_version": COL_DATASET_VERSION,
                "taxon_backbone_dataset_key": str(COL_DATASET_KEY),
                "taxon_backbone_doi": COL_DATASET_DOI,
                "requires_review": str(status not in SAFE_RESOLUTION_STATUSES).lower(),
            }
        )
    return output


def load_resolution_cache(path: Path) -> dict[str, ColNameResolution]:
    """Load the latest valid resolution for each name from an append-only cache."""

    if not path.exists():
        return {}
    resolutions: dict[str, ColNameResolution] = {}
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                resolution = ColNameResolution(**payload)
            except (json.JSONDecodeError, TypeError) as exc:
                raise ValueError(f"Invalid resolution cache line {line_number}: {exc}") from exc
            resolutions[resolution.verbatim_name] = resolution
    return resolutions


def append_resolution_cache(path: Path, resolution: ColNameResolution) -> None:
    """Append and flush one completed resolution for interruption-safe resume."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(asdict(resolution), ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()


def normalize_global_lookup_name(name: str) -> str:
    """Normalize common legacy separators without changing taxonomic content."""

    return re.sub(r"\s+", " ", name.replace("_", " ")).strip()


def write_tsv(path: Path, rows: list[dict[str, Any]]) -> None:
    """Write a list of mappings as UTF-8 TSV."""

    if not rows:
        raise ValueError(f"Refusing to write an empty TSV: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
