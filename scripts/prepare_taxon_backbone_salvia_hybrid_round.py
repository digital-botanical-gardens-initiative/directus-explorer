"""Prepare the approved Salvia hybrid formula with genus and parent CoL IDs."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from directus_explorer.taxon_backbone import write_tsv
from directus_explorer.taxon_curation import (
    COL_DATASET_DOI,
    COL_DATASET_KEY,
    COL_DATASET_VERSION,
)

DEFAULT_ROOT = Path("data/working/taxonomy_backbone/col_2026_07_17_xr")
DEFAULT_OUTPUT = DEFAULT_ROOT / "review_rounds/salvia_hybrid_round_001"
UNIFIED_NAME = "Salvia greggii x microphylla"
PARENT_COL_IDS = ["6XGYF", "79H2B"]
TAXA = {
    "8W39F": ("Salvia", "L.", "genus"),
    "6XGYF": ("Salvia greggii", "A.Gray", "species"),
    "79H2B": ("Salvia microphylla", "Kunth", "species"),
}


def main() -> None:
    args = _parse_args()
    review_rows = _read_tsv(args.review)
    mapping_by_id = {row["field_data_id"]: row for row in _read_tsv(args.mapping)}
    selected = [row for row in review_rows if row["taxon_name_unified"] == UNIFIED_NAME]
    if len(selected) != 1 or selected[0]["field_data_count"] != "2":
        raise SystemExit("Approved Salvia hybrid scope is no longer exactly two records")

    row = selected[0]
    decisions: list[dict[str, Any]] = [
        {
            "review_id": row["review_id"],
            "taxon_name_unified": UNIFIED_NAME,
            "field_data_count": row["field_data_count"],
            "projects": row["projects"],
            "field_data_ids": row["field_data_ids"],
            "sample_ids": row["sample_ids"],
            "decision": "accept_hybrid_formula_genus_fallback",
            "chosen_col_id": "8W39F",
            "chosen_col_accepted_name": "Salvia",
            "chosen_col_accepted_authorship": "L.",
            "chosen_col_accepted_rank": "genus",
            "parent_col_ids": json.dumps(PARENT_COL_IDS, separators=(",", ":")),
            "chosen_resolution_status": "hybrid_formula_genus_fallback",
            "reviewer": "user",
            "reviewed_at": "2026-08-05",
            "review_note": (
                "Preserve the explicit hybrid formula, use the accepted Salvia genus as "
                "the single taxon_ref, and record both accepted CoL parent species IDs."
            ),
        }
    ]
    taxa = [
        {
            "col_id": col_id,
            "scientific_name": metadata[0],
            "authorship": metadata[1],
            "rank": metadata[2],
            "backbone_version": COL_DATASET_VERSION,
            "backbone_dataset_key": str(COL_DATASET_KEY),
            "backbone_doi": COL_DATASET_DOI,
        }
        for col_id, metadata in sorted(TAXA.items())
    ]
    backfill: list[dict[str, str]] = []
    for field_id in row["field_data_ids"].split(";"):
        mapping = mapping_by_id.get(field_id)
        if mapping is None:
            raise SystemExit(f"Field_Data {field_id} is missing from the global mapping")
        if mapping["taxon_name_unified"] != UNIFIED_NAME:
            raise SystemExit(f"Field_Data {field_id} unified name changed locally")
        backfill.append(
            {
                "project": mapping["project"],
                "field_data_id": field_id,
                "sample_id": mapping["sample_id"],
                "current_taxon_name_unified": mapping["taxon_name_unified"],
                "taxon_col_id": "8W39F",
                "taxon_resolution_status": "hybrid_formula_genus_fallback",
                "taxon_backbone_version": COL_DATASET_VERSION,
                "taxon_parent_col_ids": json.dumps(
                    PARENT_COL_IDS, separators=(",", ":")
                ),
            }
        )
    if len(backfill) != 2:
        raise SystemExit(f"Expected two Salvia hybrid records, got {len(backfill)}")

    args.output.mkdir(parents=True, exist_ok=True)
    write_tsv(args.output / "salvia_hybrid_round_001_review.tsv", decisions)
    write_tsv(args.output / "salvia_hybrid_round_001_taxa_seed.tsv", taxa)
    write_tsv(
        args.output / "salvia_hybrid_round_001_field_data_backfill.tsv", backfill
    )
    print("Recorded one approved Salvia hybrid-formula decision")
    print("Prepared three CoL taxa: genus and two accepted parent species")
    print("Prepared two Field_Data hybrid-formula mappings")
    print("No taxon_name_unified values will change.")
    print("No Directus data was changed.")


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return [
            {key: value or "" for key, value in row.items()}
            for row in csv.DictReader(handle, delimiter="\t")
        ]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--review",
        type=Path,
        default=DEFAULT_ROOT / "taxon_name_unified_col_review_verified.tsv",
    )
    parser.add_argument(
        "--mapping", type=Path, default=DEFAULT_ROOT / "field_data_col_mapping.tsv"
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


if __name__ == "__main__":
    main()
