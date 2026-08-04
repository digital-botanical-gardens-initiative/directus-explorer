"""Generate a standalone HTML dashboard for taxonomy curation progress."""

from __future__ import annotations

import argparse
from pathlib import Path

from directus_explorer.taxon_curation_dashboard import load_dashboard_data, write_dashboard

DEFAULT_ROOT = Path("data/working/taxonomy_curation")
DEFAULT_AUDIT = Path(
    "data/output/directus_integrity/taxon_name_unified_audit/"
    "taxon_name_unified_summary.json"
)


def main() -> None:
    args = _parse_args()
    data = load_dashboard_data(
        curation_root=args.curation_root,
        audit_summary=args.audit_summary,
    )
    write_dashboard(args.output, data)
    print(f"Wrote taxonomy curation dashboard to {args.output}")
    print(
        f"Projects: {len(data.project_rows)}; rounds: {len(data.rounds)}; "
        f"reviewed records: {data.reviewed_records}; applied records: {data.applied_records}"
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--curation-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--audit-summary", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_ROOT / "taxonomy_curation_dashboard.html",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
