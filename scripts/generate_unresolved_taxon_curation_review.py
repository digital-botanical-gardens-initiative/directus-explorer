"""Generate reviewed unresolved outcomes without making Directus changes."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from directus_explorer.taxon_curation import (
    COL_DATASET_DOI,
    COL_DATASET_KEY,
    COL_DATASET_VERSION,
)
from directus_explorer.taxon_curation_unresolved import build_unresolved_review_rows

DEFAULT_AUDIT = Path(
    "data/output/directus_integrity/taxon_name_unified_audit/"
    "taxon_name_unified_audit_all.tsv"
)
DEFAULT_OUTPUT_ROOT = Path("data/working/taxonomy_curation")


def main() -> None:
    args = _parse_args()
    audit_rows = _read_tsv(args.audit)
    excluded_ids = _reviewed_ids(args.output_root, project=args.project)
    review_rows = build_unresolved_review_rows(
        audit_rows,
        project=args.project,
        round_number=args.round,
        excluded_field_ids=excluded_ids,
        reviewed_at=args.reviewed_at,
        reviewer=args.reviewer,
    )
    if not review_rows:
        raise SystemExit(f"No unreviewed unresolved rows found for {args.project!r}")

    stem = f"{args.project}_round_{args.round:03d}"
    output_dir = args.output_root / _release_slug() / stem
    review_path = output_dir / f"{stem}_review.tsv"
    metadata_path = output_dir / f"{stem}_metadata.json"
    if review_path.exists() or metadata_path.exists():
        raise SystemExit(f"Refusing to overwrite existing curation round: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_tsv(review_path, review_rows)
    metadata_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "mode": "reviewed_unresolved_cleanup",
                "directus_writes": False,
                "project": args.project,
                "round": args.round,
                "input": str(args.audit),
                "input_sha256": hashlib.sha256(args.audit.read_bytes()).hexdigest(),
                "review_group_count": len(review_rows),
                "affected_field_data_count": sum(
                    int(row["affected_row_count"]) for row in review_rows
                ),
                "col_dataset": {
                    "key": COL_DATASET_KEY,
                    "version": COL_DATASET_VERSION,
                    "doi": COL_DATASET_DOI,
                    "lookup_performed": False,
                    "reason": "No usable taxonomic names were present.",
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(review_rows)} reviewed outcome groups to {review_path}")
    print(
        "Affected records: "
        + str(sum(int(row["affected_row_count"]) for row in review_rows))
    )
    print("No Directus data was changed.")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--round", type=int, required=True)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--reviewed-at", default=date.today().isoformat())
    parser.add_argument(
        "--reviewer",
        default="",
        help="Reviewer identity to record for the generated outcomes.",
    )
    return parser.parse_args()


def _reviewed_ids(root: Path, *, project: str) -> set[str]:
    reviewed: set[str] = set()
    for path in root.glob(f"**/{project}_round_*_review.tsv"):
        for row in _read_tsv(path):
            if not row.get("decision", "").strip():
                continue
            reviewed.update(
                value for value in row.get("field_data_ids", "").split(";") if value
            )
    return reviewed


def _release_slug() -> str:
    return "col_" + COL_DATASET_VERSION.lower().replace("-", "_").replace(" ", "_")


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


if __name__ == "__main__":
    main()
