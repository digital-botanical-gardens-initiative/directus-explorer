"""Prepare local Taxa seed and safe Field_Data backfill TSVs."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from directus_explorer.taxon_backbone import write_tsv
from directus_explorer.taxon_backbone_directus import (
    build_safe_field_data_backfill,
    build_taxa_seed,
)

DEFAULT_ROOT = Path("data/working/taxonomy_backbone/col_2026_07_17_xr")


def main() -> None:
    args = _parse_args()
    backbone = _read_tsv(args.backbone)
    mapping = _read_tsv(args.mapping)
    taxa = build_taxa_seed(backbone)
    backfill = build_safe_field_data_backfill(mapping)
    write_tsv(args.taxa_seed, taxa)
    write_tsv(args.safe_backfill, backfill)
    args.summary.write_text(
        json.dumps(
            {
                "directus_writes": False,
                "taxa_seed_count": len(taxa),
                "safe_field_data_backfill_count": len(backfill),
                "taxa_seed": str(args.taxa_seed),
                "safe_field_data_backfill": str(args.safe_backfill),
                "taxon_name_unified_changes": 0,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Prepared {len(taxa)} unique accepted taxa in {args.taxa_seed}")
    print(f"Prepared {len(backfill)} safe Field_Data mappings in {args.safe_backfill}")
    print("No taxon_name_unified values will be changed by this batch.")
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
        "--backbone", type=Path, default=DEFAULT_ROOT / "taxon_name_unified_col_backbone.tsv"
    )
    parser.add_argument(
        "--mapping", type=Path, default=DEFAULT_ROOT / "field_data_col_mapping.tsv"
    )
    parser.add_argument("--taxa-seed", type=Path, default=DEFAULT_ROOT / "taxa_seed.tsv")
    parser.add_argument(
        "--safe-backfill", type=Path, default=DEFAULT_ROOT / "field_data_safe_backfill.tsv"
    )
    parser.add_argument(
        "--summary", type=Path, default=DEFAULT_ROOT / "directus_backfill_summary.json"
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
