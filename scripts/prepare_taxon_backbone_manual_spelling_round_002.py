"""Verify and prepare the second approved manual spelling recovery round."""

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
    ChecklistBankColClient,
)

DEFAULT_ROOT = Path("data/working/taxonomy_backbone/col_2026_07_17_xr")
DEFAULT_OUTPUT = DEFAULT_ROOT / "review_rounds/manual_spelling_round_002"
CORRECTIONS = {
    "A Araucaria bidwillii": "Araucaria bidwillii",
    "A Auraucaria bidwilii": "Araucaria bidwillii",
    "Aillium hymenorrhizum,": "Allium hymenorrhizum",
    "Alchemia alpina": "Alchemilla alpina",
    "Allysum spinosum": "Alyssum spinosum",
    "Androsaceae sarmentosa": "Androsace sarmentosa",
    "Cunningham lanceolata": "Cunninghamia lanceolata",
    "Epidemium grandiflorum": "Epimedium grandiflorum",
    "Erysium ochroleucum.": "Erysimum ochroleucum",
    "Glechauma hederaceae": "Glechoma hederacea",
    "Hemerocellis lilioasphodolus": "Hemerocallis lilioasphodelus",
    "Ile aquifolium": "Ilex aquifolium",
    "Mateucia struthilpteris": "Matteuccia struthiopteris",
    "Mordana  citriodora": "Monarda citriodora",
    "Onoethera fruticosa": "Oenothera fruticosa",
    "Phlodouglasii Hook.": "Phlox douglasii",
    "Placytodon grandiflora": "Platycodon grandiflorus",
    "Sali serpyillifolia": "Salix serpyllifolia",
    "Schrophuleria canina": "Scrophularia canina",
    "Sempervirens montanum": "Sempervivum montanum",
    "Sibbeldia duneata": "Sibbaldia cuneata",
    "Sparaginum angustifolium": "Sparganium angustifolium",
    "Triteteia taxa": "Triteleia laxa",
}


def main() -> None:
    args = _parse_args()
    review_rows = _read_tsv(args.review)
    mapping_by_id = {row["field_data_id"]: row for row in _read_tsv(args.mapping)}
    selected = [row for row in review_rows if row["taxon_name_unified"] in CORRECTIONS]
    found = {row["taxon_name_unified"] for row in selected}
    if found != set(CORRECTIONS):
        missing = sorted(set(CORRECTIONS) - found)
        raise SystemExit(f"Approved names missing from review: {missing}")

    client = ChecklistBankColClient(timeout=args.timeout)
    client.validate_pinned_dataset()
    decisions: list[dict[str, Any]] = []
    taxa_by_id: dict[str, dict[str, str]] = {}
    backfill: list[dict[str, str]] = []
    for index, row in enumerate(selected, start=1):
        unified_name = row["taxon_name_unified"]
        corrected_name = CORRECTIONS[unified_name]
        resolution = client.resolve_name(corrected_name, kingdom="Plantae")
        if not resolution.is_usable_match or resolution.accepted_rank not in {
            "species",
            "subspecies",
            "variety",
            "form",
        }:
            raise SystemExit(
                f"Corrected name {corrected_name!r} did not resolve to a usable "
                f"taxon: match_type={resolution.match_type!r}, "
                f"accepted_rank={resolution.accepted_rank!r}"
            )
        col_id = resolution.accepted_usage_id
        decisions.append(
            {
                "review_id": row["review_id"],
                "taxon_name_unified": unified_name,
                "corrected_lookup_name": corrected_name,
                "field_data_count": row["field_data_count"],
                "projects": row["projects"],
                "field_data_ids": row["field_data_ids"],
                "sample_ids": row["sample_ids"],
                "pinned_match_type": resolution.match_type,
                "pinned_matched_name": resolution.matched_name,
                "decision": "accept_manual_spelling_supported",
                "chosen_col_id": col_id,
                "chosen_col_accepted_name": resolution.accepted_name,
                "chosen_col_accepted_authorship": resolution.accepted_authorship,
                "chosen_col_accepted_rank": resolution.accepted_rank,
                "chosen_resolution_status": "manual_spelling_supported",
                "reviewer": "user",
                "reviewed_at": "2026-08-05",
                "review_note": (
                    "User approved the corrected lookup spelling, which was independently "
                    "resolved against pinned CoL XR; preserve taxon_name_unified verbatim."
                ),
            }
        )
        taxon = {
            "col_id": col_id,
            "scientific_name": resolution.accepted_name,
            "authorship": resolution.accepted_authorship,
            "rank": resolution.accepted_rank,
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
                    "taxon_resolution_status": "manual_spelling_supported",
                    "taxon_backbone_version": COL_DATASET_VERSION,
                }
            )
        print(
            f"Verified {index}/{len(selected)}: {unified_name} -> "
            f"{resolution.accepted_name} [{col_id}]",
            flush=True,
        )

    expected_count = sum(int(row["field_data_count"]) for row in selected)
    if len(selected) != 23 or expected_count != 23 or len(backfill) != expected_count:
        raise SystemExit(
            f"Approved manual spelling scope changed: {len(selected)} names, "
            f"{expected_count} expected records, {len(backfill)} expanded records"
        )
    args.output.mkdir(parents=True, exist_ok=True)
    write_tsv(args.output / "manual_spelling_round_002_review.tsv", decisions)
    write_tsv(
        args.output / "manual_spelling_round_002_taxa_seed.tsv",
        [taxa_by_id[col_id] for col_id in sorted(taxa_by_id)],
    )
    write_tsv(
        args.output / "manual_spelling_round_002_field_data_backfill.tsv", backfill
    )
    print(f"Recorded {len(decisions)} approved manual spelling decisions")
    print(f"Prepared {len(taxa_by_id)} distinct accepted CoL taxa")
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
    parser.add_argument("--timeout", type=float, default=30)
    return parser.parse_args()


if __name__ == "__main__":
    main()
