"""Prepare the final exact-name conflict round using researched or conservative links."""

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
DEFAULT_OUTPUT = DEFAULT_ROOT / "review_rounds/exact_conflicts_final_round_001"

# unified name: (pinned lookup, expected accepted ID, resolution status)
DECISIONS = {
    "Alnus viridis": (
        "Alnus alnobetula subsp. alnobetula",
        "7J97N",
        "nomenclatural_synonym_supported",
    ),
    "Alyssum murale": (
        "Odontarrhena muralis",
        "87C5N",
        "nomenclatural_synonym_supported",
    ),
    "Androsace primuloides": (
        "Androsace sarmentosa subsp. primuloides",
        "5FM29",
        "nomenclatural_synonym_supported",
    ),
    "Arenaria juniperina (L.) Maire & Petitm.": (
        "Sabulina juniperina",
        "4TVSL",
        "authorship_supported",
    ),
    "Aster sibiricus": (
        "Eurybia sibirica subsp. sibirica",
        "7JRKM",
        "nomenclatural_synonym_supported",
    ),
    "Dieffenbachia amoena Gentil": (
        "Dieffenbachia seguine var. seguine",
        "VP6CR",
        "authorship_supported",
    ),
    "Geranium fremontii": (
        "Geranium caespitosum",
        "6KCK5",
        "nomenclatural_synonym_supported",
    ),
    "Holodiscus dumosus (Hook.) A. Heller": (
        "Holodiscus discolor",
        "6LXYK",
        "authorship_supported",
    ),
    "Leontopodium linearifolium": (
        "Leontopodium junpeianum",
        "6P8L9",
        "nomenclatural_synonym_supported",
    ),
    "Lithospermum graminifolium": (
        "Moltkia suffruticosa subsp. suffruticosa",
        "5JQSL",
        "nomenclatural_synonym_supported",
    ),
    "Passiflora macrocarpa": (
        "Passiflora quadrangularis",
        "764F8",
        "nomenclatural_synonym_supported",
    ),
    "Scutellaria peregrina": (
        "Scutellaria columnae subsp. columnae",
        "5L4GQ",
        "nomenclatural_synonym_supported",
    ),
    "Senecio abrotanifolius": (
        "Jacobaea abrotanifolia subsp. abrotanifolia",
        "5J5Y4",
        "nomenclatural_synonym_supported",
    ),
    "Alyssum alpestre": ("Odontarrhena", "", "ambiguous_homonym_genus_fallback"),
    "Anthemis montana": ("Anthemis", "", "ambiguous_homonym_genus_fallback"),
    "Centaurea dealbata": ("Asteraceae", "", "ambiguous_homonym_family_fallback"),
    "Citrus nobilis": ("Citrus", "", "ambiguous_homonym_genus_fallback"),
    "Festuca gigantea": ("Poaceae", "", "ambiguous_homonym_family_fallback"),
    "Gentiana campestris": ("Gentianella", "", "ambiguous_homonym_genus_fallback"),
    "Inula salicina": ("Asteraceae", "", "ambiguous_homonym_family_fallback"),
    "Ligusticum ferulaceum": (
        "Coristospermum",
        "",
        "ambiguous_homonym_genus_fallback",
    ),
    "Maranta bicolor": ("Marantaceae", "", "ambiguous_homonym_family_fallback"),
    "Mentha gracilis": ("Mentha", "", "ambiguous_homonym_genus_fallback"),
    "Potentilla parviflora": ("Potentilla", "", "ambiguous_homonym_genus_fallback"),
    "Sedum reflexum": ("Petrosedum", "", "ambiguous_homonym_genus_fallback"),
    "Senecio jacobaea": ("Jacobaea", "", "ambiguous_homonym_genus_fallback"),
    "Yucca angustifolia": ("Yucca", "", "ambiguous_homonym_genus_fallback"),
}


def main() -> None:
    args = _parse_args()
    review_rows = _read_tsv(args.review)
    mapping_by_id = {row["field_data_id"]: row for row in _read_tsv(args.mapping)}
    selected = [row for row in review_rows if row["taxon_name_unified"] in DECISIONS]
    found = {row["taxon_name_unified"] for row in selected}
    if found != set(DECISIONS):
        raise SystemExit(f"Names missing from review: {sorted(set(DECISIONS) - found)}")

    client = ChecklistBankColClient(timeout=args.timeout)
    client.validate_pinned_dataset()
    review_output: list[dict[str, Any]] = []
    taxa_by_id: dict[str, dict[str, str]] = {}
    backfill: list[dict[str, str]] = []
    for index, row in enumerate(selected, start=1):
        unified_name = row["taxon_name_unified"]
        lookup_name, expected_id, status = DECISIONS[unified_name]
        resolution = client.resolve_name(lookup_name, kingdom="Plantae")
        if not resolution.is_usable_match:
            raise SystemExit(f"Approved lookup {lookup_name!r} has no pinned CoL match")
        if expected_id and resolution.accepted_usage_id != expected_id:
            raise SystemExit(
                f"CoL ID changed for {lookup_name!r}: expected {expected_id}, "
                f"received {resolution.accepted_usage_id}"
            )
        expected_rank = "family" if "family_fallback" in status else "genus"
        if "fallback" in status and resolution.accepted_rank != expected_rank:
            raise SystemExit(
                f"Fallback {lookup_name!r} resolved at {resolution.accepted_rank!r}, "
                f"expected {expected_rank!r}"
            )
        col_id = resolution.accepted_usage_id
        review_output.append(
            {
                "review_id": row["review_id"],
                "taxon_name_unified": unified_name,
                "approved_lookup_name": lookup_name,
                "field_data_count": row["field_data_count"],
                "projects": row["projects"],
                "field_data_ids": row["field_data_ids"],
                "sample_ids": row["sample_ids"],
                "decision": f"accept_{status}",
                "chosen_col_id": col_id,
                "chosen_col_accepted_name": resolution.accepted_name,
                "chosen_col_accepted_authorship": resolution.accepted_authorship,
                "chosen_col_accepted_rank": resolution.accepted_rank,
                "chosen_resolution_status": status,
                "reviewer": "assistant_research",
                "reviewed_at": "2026-08-05",
                "review_note": (
                    "Exact synonym supported by pinned CoL and Kew nomenclatural evidence."
                    if "fallback" not in status
                    else "Authorship is absent and competing homonyms resolve differently; "
                    "retain taxon_name_unified and use the lowest unambiguous CoL rank."
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
                raise SystemExit(f"Field_Data {field_id} missing from global mapping")
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
        print(
            f"Verified {index}/{len(selected)}: {unified_name} -> "
            f"{resolution.accepted_name} [{col_id}] ({resolution.accepted_rank})",
            flush=True,
        )

    expected_count = sum(int(row["field_data_count"]) for row in selected)
    if len(selected) != 27 or expected_count != 27 or len(backfill) != expected_count:
        raise SystemExit(
            f"Final exact-conflict scope changed: {len(selected)} names, "
            f"{expected_count} records, {len(backfill)} mappings"
        )
    args.output.mkdir(parents=True, exist_ok=True)
    write_tsv(args.output / "exact_conflicts_final_round_001_review.tsv", review_output)
    write_tsv(
        args.output / "exact_conflicts_final_round_001_taxa_seed.tsv",
        [taxa_by_id[col_id] for col_id in sorted(taxa_by_id)],
    )
    write_tsv(
        args.output / "exact_conflicts_final_round_001_field_data_backfill.tsv", backfill
    )
    precise = sum("fallback" not in row["chosen_resolution_status"] for row in review_output)
    print(f"Recorded {len(review_output)} researched exact-name decisions")
    print(f"Precise links: {precise}; conservative fallbacks: {len(review_output) - precise}")
    print(f"Prepared {len(taxa_by_id)} distinct CoL taxa and {len(backfill)} mappings")
    print("No taxon_name_unified values will change. No Directus data was changed.")


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
