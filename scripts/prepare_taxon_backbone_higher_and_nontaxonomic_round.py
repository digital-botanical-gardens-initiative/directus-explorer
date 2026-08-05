"""Prepare approved higher-taxon fallbacks and non-taxonomic NULL decisions."""

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
DEFAULT_OUTPUT = DEFAULT_ROOT / "review_rounds/higher_and_nontaxonomic_round_001"
HIGHER_TAXA = {
    "Angiospermae": {
        "col_id": "P",
        "scientific_name": "Plantae",
        "rank": "kingdom",
        "status": "higher_taxon_fallback",
        "decision": "accept_broader_plantae_fallback",
    },
    "Monocotyledon": {
        "col_id": "L2L",
        "scientific_name": "Liliopsida",
        "rank": "class",
        "status": "higher_taxon_normalized",
        "decision": "accept_standardized_higher_taxon",
    },
    "Planta": {
        "col_id": "P",
        "scientific_name": "Plantae",
        "rank": "kingdom",
        "status": "higher_taxon_normalized",
        "decision": "accept_standardized_higher_taxon",
    },
}
NON_TAXONOMIC_NAMES = {
    "1/2 MS",
    "1/2 MS + 3-HDA",
    "Mixte",
    "NA et",
    "NA lo",
    "NAo",
    "NAo.o",
    "NAp",
    "PDA medium",
}
APPROVED_NAMES = set(HIGHER_TAXA) | NON_TAXONOMIC_NAMES


def main() -> None:
    args = _parse_args()
    review_rows = _read_tsv(args.review)
    mapping_by_id = {row["field_data_id"]: row for row in _read_tsv(args.mapping)}
    selected = [row for row in review_rows if row["taxon_name_unified"] in APPROVED_NAMES]
    found = {row["taxon_name_unified"] for row in selected}
    if found != APPROVED_NAMES:
        raise SystemExit(f"Approved names missing from review: {sorted(APPROVED_NAMES - found)}")

    decisions: list[dict[str, Any]] = []
    taxa_by_id: dict[str, dict[str, str]] = {}
    backfill: list[dict[str, str]] = []
    for row in selected:
        unified_name = row["taxon_name_unified"]
        higher = HIGHER_TAXA.get(unified_name)
        if higher:
            col_id = higher["col_id"]
            accepted_name = higher["scientific_name"]
            rank = higher["rank"]
            status = higher["status"]
            decision = higher["decision"]
            note = (
                "User approved a conservative or standardized higher-taxon CoL "
                "reference while preserving the original taxon_name_unified."
            )
        else:
            col_id = ""
            accepted_name = ""
            rank = ""
            status = "non_taxonomic_unresolved"
            decision = "leave_non_taxonomic_unresolved"
            note = (
                "The value is an experimental, mixed, or missing-value label rather "
                "than a taxon; no CoL reference is asserted."
            )
        decisions.append(
            {
                "review_id": row["review_id"],
                "taxon_name_unified": unified_name,
                "field_data_count": row["field_data_count"],
                "projects": row["projects"],
                "field_data_ids": row["field_data_ids"],
                "sample_ids": row["sample_ids"],
                "decision": decision,
                "chosen_col_id": col_id,
                "chosen_col_accepted_name": accepted_name,
                "chosen_col_accepted_authorship": "",
                "chosen_col_accepted_rank": rank,
                "chosen_resolution_status": status,
                "reviewer": "user",
                "reviewed_at": "2026-08-05",
                "review_note": note,
            }
        )
        if col_id:
            taxon = {
                "col_id": col_id,
                "scientific_name": accepted_name,
                "authorship": "",
                "rank": rank,
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
            if mapping["taxon_name_unified"] != unified_name:
                raise SystemExit(f"Field_Data {field_id} unified name changed locally")
            backfill.append(
                {
                    "project": mapping["project"],
                    "field_data_id": field_id,
                    "sample_id": mapping["sample_id"],
                    "current_taxon_name_unified": mapping["taxon_name_unified"],
                    "taxon_col_id": col_id,
                    "taxon_resolution_status": status,
                    "taxon_backbone_version": COL_DATASET_VERSION,
                }
            )

    expected_count = sum(int(row["field_data_count"]) for row in selected)
    if len(selected) != 12 or expected_count != 84 or len(backfill) != expected_count:
        raise SystemExit(
            f"Approved higher/non-taxonomic scope changed: {len(selected)} names, "
            f"{expected_count} expected records, {len(backfill)} expanded records"
        )
    args.output.mkdir(parents=True, exist_ok=True)
    write_tsv(args.output / "higher_and_nontaxonomic_round_001_review.tsv", decisions)
    write_tsv(
        args.output / "higher_and_nontaxonomic_round_001_taxa_seed.tsv",
        [taxa_by_id[col_id] for col_id in sorted(taxa_by_id)],
    )
    write_tsv(
        args.output / "higher_and_nontaxonomic_round_001_field_data_backfill.tsv",
        backfill,
    )
    print(f"Recorded {len(decisions)} approved decisions")
    print(f"Prepared {len(taxa_by_id)} distinct accepted CoL higher taxa")
    print(f"Prepared {len(backfill)} Field_Data mappings")
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
