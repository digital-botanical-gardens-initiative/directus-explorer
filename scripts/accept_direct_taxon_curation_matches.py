"""Accept pending review rows that have a direct pinned-CoL target."""

from __future__ import annotations

import argparse
import csv
from datetime import UTC, datetime
from pathlib import Path

from directus_explorer.taxon_curation_review import accept_direct_col_matches


def main() -> None:
    args = _parse_args()
    rows = _read_tsv(args.review)
    reviewed_at = datetime.now(UTC).date().isoformat()
    updated_rows, accepted = accept_direct_col_matches(rows, reviewed_at=reviewed_at)
    if not args.commit:
        print(f"Dry run: would accept {accepted} direct CoL matches in {args.review}")
        return
    _write_tsv(args.review, updated_rows)
    print(f"Accepted {accepted} direct CoL matches in {args.review}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--commit", action="store_true")
    return parser.parse_args()


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return [
            {key: value or "" for key, value in row.items()}
            for row in csv.DictReader(handle, delimiter="\t")
        ]


def _write_tsv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
