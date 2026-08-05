"""Prepare the approved extended orthographic fuzzy CoL match round."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Any

from directus_explorer.taxon_backbone import write_tsv
from directus_explorer.taxon_curation import (
    COL_DATASET_DOI,
    COL_DATASET_KEY,
    COL_DATASET_VERSION,
)

DEFAULT_ROOT = Path("data/working/taxonomy_backbone/col_2026_07_17_xr")
DEFAULT_OUTPUT = DEFAULT_ROOT / "review_rounds/fuzzy_extended_round_001"
APPROVED_NAMES = {
    "Cotoeaster adpressa",
    "Cottoneaster adpressa",
    "Ramondia myconii",
    "Saxifraga ferdinand-coburgii",
    "Sempervivum x rupicolum (montanum x wulfeni)",
    "Vaccinium vitis idaea",
}


def main() -> None:
    args = _parse_args()
    review_rows = _read_tsv(args.review)
    mapping_by_id = {row["field_data_id"]: row for row in _read_tsv(args.mapping)}
    selected = [row for row in review_rows if row["taxon_name_unified"] in APPROVED_NAMES]
    if {row["taxon_name_unified"] for row in selected} != APPROVED_NAMES:
        found = {row["taxon_name_unified"] for row in selected}
        missing = sorted(APPROVED_NAMES - found)
        raise SystemExit(f"Approved fuzzy names missing from review: {missing}")

    decisions: list[dict[str, Any]] = []
    taxa_by_id: dict[str, dict[str, str]] = {}
    backfill: list[dict[str, str]] = []
    for row in selected:
        if (
            row["gnverifier_match_type"] not in {"Fuzzy", "PartialFuzzy"}
            or row["gnverifier_edit_distance"] not in {"2", "3"}
            or row["verified_col_accepted_rank"] != "species"
        ):
            raise SystemExit(
                f"Approved name {row['taxon_name_unified']!r} no longer has its "
                "verified extended fuzzy species match"
            )
        col_id = row["verified_col_id"].strip()
        accepted_name = row["verified_col_accepted_name"].strip()
        if not col_id or not accepted_name:
            raise SystemExit(f"Approved name {row['taxon_name_unified']!r} lacks CoL data")
        decisions.append(
            {
                "review_id": row["review_id"],
                "taxon_name_unified": row["taxon_name_unified"],
                "field_data_count": row["field_data_count"],
                "projects": row["projects"],
                "field_data_ids": row["field_data_ids"],
                "sample_ids": row["sample_ids"],
                "gnverifier_matched_name": row["gnverifier_canonical_name"],
                "decision": "accept_fuzzy_supported",
                "chosen_col_id": col_id,
                "chosen_col_accepted_name": accepted_name,
                "chosen_col_accepted_authorship": row[
                    "verified_col_accepted_authorship"
                ],
                "chosen_col_accepted_rank": row["verified_col_accepted_rank"],
                "chosen_resolution_status": "fuzzy_supported",
                "reviewer": "user",
                "reviewed_at": "2026-08-05",
                "review_note": (
                    "User approved the verified extended orthographic match; preserve "
                    "the original taxon_name_unified string."
                ),
            }
        )
        taxon = {
            "col_id": col_id,
            "scientific_name": accepted_name,
            "authorship": row["verified_col_accepted_authorship"],
            "rank": row["verified_col_accepted_rank"],
            "backbone_version": COL_DATASET_VERSION,
            "backbone_dataset_key": str(COL_DATASET_KEY),
            "backbone_doi": COL_DATASET_DOI,
        }
        existing_taxon = taxa_by_id.get(col_id)
        if existing_taxon and existing_taxon != taxon:
            raise SystemExit(f"Conflicting accepted metadata for CoL ID {col_id}")
        taxa_by_id[col_id] = taxon
        for field_id in row["field_data_ids"].split(";"):
            mapping = mapping_by_id.get(field_id)
            if mapping is None:
                raise SystemExit(f"Field_Data {field_id} is missing from the global mapping")
            if mapping["taxon_name_unified"] != row["taxon_name_unified"]:
                raise SystemExit(f"Field_Data {field_id} unified name changed locally")
            backfill.append(
                {
                    "project": mapping["project"],
                    "field_data_id": field_id,
                    "sample_id": mapping["sample_id"],
                    "current_taxon_name_unified": mapping["taxon_name_unified"],
                    "taxon_col_id": col_id,
                    "taxon_resolution_status": "fuzzy_supported",
                    "taxon_backbone_version": COL_DATASET_VERSION,
                }
            )

    expected_count = sum(int(row["field_data_count"]) for row in selected)
    if len(selected) != 6 or expected_count != 9 or len(backfill) != expected_count:
        raise SystemExit(
            f"Approved extended fuzzy scope changed: {len(selected)} names, "
            f"{expected_count} expected records, {len(backfill)} expanded records"
        )
    args.output.mkdir(parents=True, exist_ok=True)
    write_tsv(args.output / "fuzzy_extended_round_001_review.tsv", decisions)
    write_tsv(
        args.output / "fuzzy_extended_round_001_taxa_seed.tsv",
        [taxa_by_id[col_id] for col_id in sorted(taxa_by_id)],
    )
    write_tsv(
        args.output / "fuzzy_extended_round_001_field_data_backfill.tsv", backfill
    )
    print(f"Recorded {len(decisions)} approved extended-fuzzy decisions")
    print(f"Prepared {len(taxa_by_id)} distinct accepted CoL taxa")
    print(f"Prepared {len(backfill)} Field_Data fuzzy-supported mappings")
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
