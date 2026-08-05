"""Generate a human-review queue for unsafe global CoL backbone matches.

gnverifier results are diagnostic suggestions only. This command never writes to Directus.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
from pathlib import Path
from typing import Any

from directus_explorer.taxonomy import CatalogueOfLifeTaxonResolver

DEFAULT_INPUT = Path(
    "data/working/taxonomy_backbone/col_2026_07_17_xr/"
    "taxon_name_unified_col_backbone.tsv"
)
DEFAULT_OUTPUT = Path(
    "data/working/taxonomy_backbone/col_2026_07_17_xr/"
    "taxon_name_unified_col_review.tsv"
)


def main() -> None:
    args = _parse_args()
    backbone = _read_tsv(args.input)
    unsafe = [row for row in backbone if row.get("requires_review") == "true"]
    if not unsafe:
        raise SystemExit("No names require review.")

    gn = CatalogueOfLifeTaxonResolver().resolve_names(
        row["taxon_name_unified"] for row in unsafe
    )
    review_rows: list[dict[str, Any]] = []
    for row in unsafe:
        name = row["taxon_name_unified"]
        suggestion = gn.get(name)
        review_rows.append(
            {
                "review_id": hashlib.sha256(name.encode()).hexdigest()[:12],
                "taxon_name_unified": name,
                "field_data_count": row["field_data_count"],
                "project_count": row["project_count"],
                "projects": row["projects"],
                "field_data_ids": row["field_data_ids"],
                "sample_ids": row["sample_ids"],
                "current_resolution_status": row["taxon_resolution_status"],
                "current_col_lookup_name": row["col_lookup_name"],
                "current_col_id": row["taxon_col_id"],
                "current_col_accepted_name": row["col_accepted_name"],
                "current_col_accepted_authorship": row["col_accepted_authorship"],
                "current_col_accepted_rank": row["col_accepted_rank"],
                "current_col_match_type": row["col_match_type"],
                "current_col_matched_status": row["col_matched_status"],
                "parsed_name": row["parsed_name"],
                "parsed_cultivar": row["parsed_cultivar"],
                "col_fuzzy_candidates": row["col_fuzzy_candidates"],
                "gnverifier_taxon_id": suggestion.taxon_id if suggestion else "",
                "gnverifier_canonical_name": (
                    suggestion.canonical_name if suggestion else ""
                ),
                "gnverifier_current_name": suggestion.current_name if suggestion else "",
                "gnverifier_match_type": suggestion.match_type if suggestion else "",
                "gnverifier_sort_score": suggestion.sort_score if suggestion else "",
                "gnverifier_edit_distance": suggestion.edit_distance if suggestion else "",
                "gnverifier_error": suggestion.error if suggestion else "",
                "decision": "",
                "chosen_col_id": "",
                "chosen_col_accepted_name": "",
                "chosen_resolution_status": "",
                "reviewer": "",
                "reviewed_at": "",
                "review_note": "",
            }
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    _write_tsv(args.output, review_rows)
    print(f"Wrote {len(review_rows)} names requiring review to {args.output}")
    print(
        "Affected Field_Data records: "
        + str(sum(int(row["field_data_count"]) for row in review_rows))
    )
    print("gnverifier results are diagnostic and have not been accepted.")
    print("No Directus data was changed.")


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return [
            {key: value or "" for key, value in row.items()}
            for row in csv.DictReader(handle, delimiter="\t")
        ]


def _write_tsv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


if __name__ == "__main__":
    main()
