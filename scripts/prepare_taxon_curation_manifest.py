"""Expand completed taxon review decisions into a row-level update manifest.

This command is local and read-only with respect to Directus. It refuses to
prepare a manifest while review decisions are blank or invalid.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from directus_explorer.taxon_curation_manifest import build_taxon_update_manifest


def main() -> None:
    args = _parse_args()
    review_rows = _read_tsv(args.review)
    audit_by_id = {row["field_data_id"]: row for row in _read_tsv(args.audit)}
    manifest_rows, skipped_rows = build_taxon_update_manifest(review_rows, audit_by_id)

    output = args.output or args.review.with_name(
        args.review.name.removesuffix("_review.tsv") + "_update_manifest.tsv"
    )
    summary = output.with_suffix(".summary.json")
    _write_tsv(output, manifest_rows)
    summary.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "mode": "dry_run_manifest",
                "directus_writes": False,
                "review": str(args.review),
                "audit": str(args.audit),
                "update_count": len(manifest_rows),
                "skipped_count": len(skipped_rows),
                "skipped_by_decision": dict(
                    sorted(Counter(row["decision"] for row in skipped_rows).items())
                ),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Prepared {len(manifest_rows)} local updates in {output}")
    print(f"Skipped {len(skipped_rows)} rows by reviewed decision")
    print("No Directus data was changed.")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument(
        "--audit",
        type=Path,
        default=Path(
            "data/output/directus_integrity/taxon_name_unified_audit/"
            "taxon_name_unified_audit_all.tsv"
        ),
    )
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return [
            {key: value or "" for key, value in row.items()}
            for row in csv.DictReader(handle, delimiter="\t")
        ]


def _write_tsv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
