"""Prepare the final linked and explicitly unresolved taxonomy residuals."""

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
DEFAULT_OUTPUT = DEFAULT_ROOT / "review_rounds/final_residual_round_001"

# audited unified name: (corrected unified name, lookup, expected accepted ID, status)
LINKED = {
    "Allium murale": (
        "Alyssum murale",
        "Odontarrhena muralis",
        "87C5N",
        "label_corrected_nomenclatural_synonym",
    ),
    "Muscari szouitsanum": (
        "Muscari szovitsianum",
        "Muscari armeniacum",
        "44PWL",
        "label_corrected_nomenclatural_synonym",
    ),
    "Laserpitum silero": (
        "Laserpitium siler",
        "Siler montanum subsp. montanum",
        "5L73M",
        "label_corrected_nomenclatural_synonym",
    ),
    "Jaribarba lutea": (
        "Jovibarba hirta",
        "Sempervivum globiferum subsp. hirtum",
        "",
        "label_corrected_nomenclatural_synonym",
    ),
    "Rhamnus alpinus": (
        "Rhamnus alpinus",
        "Atadinus alpinus",
        "J7DS",
        "ambiguous_current_supported",
    ),
}
UNRESOLVED = {
    "'Direktor Benschop x' Dorothy Perkins'",
    "Josephin x Medaorhold",
    "Leucopsa dirapta",
}


def main() -> None:
    args = _parse_args()
    rows = _read_tsv(args.review)
    mapping_by_id = {row["field_data_id"]: row for row in _read_tsv(args.mapping)}
    selected = [
        row
        for row in rows
        if row["taxon_name_unified"] in LINKED
        or row["taxon_name_unified"] in UNRESOLVED
    ]
    expected_names = set(LINKED) | UNRESOLVED
    found = {row["taxon_name_unified"] for row in selected}
    if found != expected_names:
        raise SystemExit(f"Residual names missing: {sorted(expected_names - found)}")

    client = ChecklistBankColClient(timeout=args.timeout)
    client.validate_pinned_dataset()
    decisions: list[dict[str, Any]] = []
    taxa_by_id: dict[str, dict[str, str]] = {}
    backfill: list[dict[str, str]] = []
    for index, row in enumerate(selected, start=1):
        unified_name = row["taxon_name_unified"]
        if unified_name in UNRESOLVED:
            corrected_unified_name = unified_name
            col_id = ""
            accepted_name = ""
            accepted_authorship = ""
            accepted_rank = ""
            status = "non_taxonomic_or_uninterpretable_unresolved"
            lookup_name = ""
        else:
            corrected_unified_name, lookup_name, expected_id, status = LINKED[unified_name]
            resolution = client.resolve_name(lookup_name, kingdom="Plantae")
            if not resolution.is_usable_match:
                raise SystemExit(f"Final lookup {lookup_name!r} has no pinned CoL match")
            if expected_id and resolution.accepted_usage_id != expected_id:
                raise SystemExit(
                    f"CoL ID changed for {lookup_name!r}: expected {expected_id}, "
                    f"received {resolution.accepted_usage_id}"
                )
            if "genus_fallback" in status and resolution.accepted_rank != "genus":
                raise SystemExit(
                    f"Fallback {lookup_name!r} resolved at {resolution.accepted_rank!r}"
                )
            col_id = resolution.accepted_usage_id
            accepted_name = resolution.accepted_name
            accepted_authorship = resolution.accepted_authorship
            accepted_rank = resolution.accepted_rank
            taxon = {
                "col_id": col_id,
                "scientific_name": accepted_name,
                "authorship": accepted_authorship,
                "rank": accepted_rank,
                "backbone_version": COL_DATASET_VERSION,
                "backbone_dataset_key": str(COL_DATASET_KEY),
                "backbone_doi": COL_DATASET_DOI,
            }
            existing = taxa_by_id.get(col_id)
            if existing and existing != taxon:
                raise SystemExit(f"Conflicting accepted metadata for CoL ID {col_id}")
            taxa_by_id[col_id] = taxon

        decisions.append(
            {
                "review_id": row["review_id"],
                "taxon_name_unified": unified_name,
                "corrected_taxon_name_unified": corrected_unified_name,
                "approved_lookup_name": lookup_name,
                "field_data_count": row["field_data_count"],
                "projects": row["projects"],
                "field_data_ids": row["field_data_ids"],
                "sample_ids": row["sample_ids"],
                "decision": f"accept_{status}",
                "chosen_col_id": col_id,
                "chosen_col_accepted_name": accepted_name,
                "chosen_col_accepted_authorship": accepted_authorship,
                "chosen_col_accepted_rank": accepted_rank,
                "chosen_resolution_status": status,
                "reviewer": "assistant_research",
                "reviewed_at": "2026-08-05",
                "review_note": (
                    "No defensible CoL taxon can be inferred; retain the unified value "
                    "as evidence and record an explicit unresolved outcome."
                    if not col_id
                    else "Final conservative or supported resolution against pinned CoL."
                ),
            }
        )
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
                    "current_taxon_name_unified": corrected_unified_name,
                    "taxon_col_id": col_id,
                    "taxon_resolution_status": status,
                    "taxon_backbone_version": COL_DATASET_VERSION,
                }
            )
        outcome = f"{accepted_name} [{col_id}]" if col_id else "explicit unresolved"
        print(f"Verified {index}/{len(selected)}: {unified_name} -> {outcome}", flush=True)

    if len(selected) != 8 or len(backfill) != 8:
        raise SystemExit(
            f"Final residual scope changed: {len(selected)} names, {len(backfill)} mappings"
        )
    args.output.mkdir(parents=True, exist_ok=True)
    write_tsv(args.output / "final_residual_round_001_review.tsv", decisions)
    write_tsv(
        args.output / "final_residual_round_001_taxa_seed.tsv",
        [taxa_by_id[col_id] for col_id in sorted(taxa_by_id)],
    )
    write_tsv(
        args.output / "final_residual_round_001_field_data_backfill.tsv", backfill
    )
    print(f"Recorded {len(decisions)} final decisions")
    print(f"Linked: {len(LINKED)}; explicit unresolved: {len(UNRESOLVED)}")
    print("Four taxon_name_unified corrections must be applied first.")
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
