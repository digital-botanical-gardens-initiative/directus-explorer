"""Generate a standalone dashboard for database-wide CoL backbone coverage."""

# ruff: noqa: E501

from __future__ import annotations

import argparse
import csv
import html
import json
from collections import Counter
from pathlib import Path

DEFAULT_ROOT = Path("data/working/taxonomy_backbone/col_2026_07_17_xr")
DIRECTUS_BASE = "https://emi-collection.unifr.ch/directus"


def main() -> None:
    args = _parse_args()
    summary = json.loads(args.summary.read_text(encoding="utf-8"))
    review = _read_tsv(args.review)
    completed_mappings = _completed_field_data_mappings(args.review_rounds)
    completed_ids = set(completed_mappings)
    review_ids = set().union(*(_field_data_ids(row) for row in review))
    completed_review = [
        row for row in review if _field_data_ids(row) <= completed_ids
    ]
    remaining_review = [
        row for row in review if not _field_data_ids(row) <= completed_ids
    ]
    cohorts = Counter(
        (row["current_resolution_status"], row["suggestion_verification_status"])
        for row in remaining_review
    )
    completed_records = sum(int(row["field_data_count"]) for row in completed_review)
    completed_linked_records = sum(
        bool(col_id)
        for field_id, col_id in completed_mappings.items()
        if field_id in review_ids
    )
    completed_unresolved_records = completed_records - completed_linked_records
    remaining_records = sum(int(row["field_data_count"]) for row in remaining_review)
    verified_remaining_names = sum(
        bool(row["verified_col_id"]) for row in remaining_review
    )
    safe_records = (
        summary["field_data_count"]
        - summary["resolution_status_by_field_data"]["ambiguous_match"]
        - summary["resolution_status_by_field_data"]["cultivar_requires_review"]
        - summary["resolution_status_by_field_data"]["higher_rank_match"]
        - summary["resolution_status_by_field_data"]["unmatched"]
        - summary["resolution_status_by_field_data"]["unparsed"]
    )
    cards = [
        ("Populated records", summary["field_data_count"]),
        ("Automatic links", safe_records),
        ("Reviewed CoL links", completed_linked_records),
        ("Explicit unresolved", completed_unresolved_records),
        ("Remaining names", len(remaining_review)),
        ("Remaining records", remaining_records),
    ]
    cohort_rows = "".join(
        "<tr>"
        f"<td>{html.escape(status)}</td>"
        f"<td>{html.escape(verification)}</td>"
        f"<td class='num'>{count}</td>"
            f"<td class='num'>{sum(int(row['field_data_count']) for row in remaining_review if (row['current_resolution_status'], row['suggestion_verification_status']) == (status, verification))}</td>"
        "</tr>"
        for (status, verification), count in sorted(cohorts.items())
    )
    detail_rows = "".join(
        _detail_row(row)
        for row in sorted(
            remaining_review,
            key=lambda row: (
                row["current_resolution_status"],
                -int(row["field_data_count"]),
                row["taxon_name_unified"].casefold(),
            ),
        )
    )
    card_html = "".join(
        f"<article class='card'><span>{html.escape(label)}</span><b>{value:,}</b></article>"
        for label, value in cards
    )
    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>CoL Taxonomy Backbone</title>
<style>
body{{font:14px system-ui;margin:0;background:#f5f6f8;color:#20242a}}main{{max-width:1500px;margin:auto;padding:24px}}
h1{{margin:0 0 6px}}.sub{{color:#667085}}.cards{{display:grid;grid-template-columns:repeat(6,1fr);gap:10px;margin:20px 0}}
.card,.panel{{background:white;border:1px solid #dde1e7;border-radius:10px}}.card{{padding:14px}}.card span{{display:block;color:#667085}}.card b{{font-size:24px}}
.panel{{padding:16px;margin:16px 0;overflow:auto}}table{{border-collapse:collapse;width:100%}}th,td{{padding:8px;border-bottom:1px solid #eaecf0;text-align:left;vertical-align:top}}th{{position:sticky;top:0;background:white}}.num{{text-align:right}}
input{{padding:9px;width:360px;border:1px solid #ccd1d8;border-radius:7px}}a{{color:#175cd3;text-decoration:none}}code{{font-size:12px}}details{{max-width:420px}}
@media(max-width:900px){{.cards{{grid-template-columns:repeat(2,1fr)}}}}
</style></head><body><main>
<h1>Catalogue of Life taxonomy backbone</h1><p class="sub">Pinned release {html.escape(summary['col_dataset']['version'])} · dataset {summary['col_dataset']['key']} · DOI {html.escape(summary['col_dataset']['doi'])}</p>
<section class="cards">{card_html}</section>
<section class="panel"><h2>Remaining review cohorts</h2><p class="sub">{len(completed_review):,} names / {completed_records:,} records have been completed through reviewed rounds. {verified_remaining_names:,} remaining names have a pinned candidate.</p><table><thead><tr><th>Current status</th><th>Pinned suggestion</th><th class="num">Names</th><th class="num">Records</th></tr></thead><tbody>{cohort_rows}</tbody></table></section>
<section class="panel"><h2>Names still requiring decisions</h2><p><input id="filter" placeholder="Filter names, projects, status…"></p>
<table id="review"><thead><tr><th>Status</th><th>Unified name</th><th class="num">Records</th><th>Projects / samples</th><th>Current pinned result</th><th>Verified candidate</th><th>gnverifier</th></tr></thead><tbody>{detail_rows}</tbody></table></section>
</main><script>const q=document.querySelector('#filter');q.addEventListener('input',()=>{{const v=q.value.toLowerCase();document.querySelectorAll('#review tbody tr').forEach(r=>r.hidden=!r.textContent.toLowerCase().includes(v));}});</script></body></html>"""
    args.output.write_text(document, encoding="utf-8")
    print(f"Wrote CoL backbone dashboard to {args.output}")
    print(
        f"Automatic links: {safe_records}; reviewed links: {completed_linked_records}; "
        f"explicit unresolved: {completed_unresolved_records}; "
        f"remaining names: {len(remaining_review)}; remaining records: {remaining_records}"
    )


def _detail_row(row: dict[str, str]) -> str:
    field_ids = row["field_data_ids"].split(";")
    sample_ids = row["sample_ids"].split(";")
    links = [
        f'<a href="{DIRECTUS_BASE}/admin/content/Field_Data/{html.escape(field_id)}" target="_blank">{html.escape(sample_id or field_id)}</a>'
        for field_id, sample_id in zip(field_ids, sample_ids, strict=False)
    ]
    visible = ", ".join(links[:5])
    if len(links) > 5:
        visible += f"<details><summary>+{len(links)-5} more</summary>{'<br>'.join(links[5:])}</details>"
    current = (
        f"{html.escape(row['current_col_accepted_name'] or '—')}<br><code>{html.escape(row['current_col_id'] or '—')}</code>"
    )
    candidate = (
        f"{html.escape(row['verified_col_accepted_name'] or '—')}<br><code>{html.escape(row['verified_col_id'] or '—')}</code><br><span class='sub'>{html.escape(row['suggestion_verification_status'])}</span>"
    )
    gn = (
        f"{html.escape(row['gnverifier_match_type'] or '—')} · d={html.escape(row['gnverifier_edit_distance'] or '—')}"
    )
    return (
        "<tr>"
        f"<td>{html.escape(row['current_resolution_status'])}</td>"
        f"<td><i>{html.escape(row['taxon_name_unified'])}</i></td>"
        f"<td class='num'>{int(row['field_data_count']):,}</td>"
        f"<td>{html.escape(row['projects'])}<br>{visible}</td>"
        f"<td>{current}</td><td>{candidate}</td><td>{gn}</td>"
        "</tr>"
    )


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return [
            {key: value or "" for key, value in row.items()}
            for row in csv.DictReader(handle, delimiter="\t")
        ]


def _field_data_ids(row: dict[str, str]) -> set[str]:
    return {value for value in row["field_data_ids"].split(";") if value}


def _completed_field_data_mappings(review_rounds: Path) -> dict[str, str]:
    completed: dict[str, str] = {}
    for journal in review_rounds.glob("*/field_data_backfill_execution_*.tsv"):
        for row in _read_tsv(journal):
            if row.get("status") in {"updated", "already_exact"}:
                completed[row["field_data_id"]] = row.get("taxon_col_id", "")
    return completed


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--summary", type=Path, default=DEFAULT_ROOT / "taxon_name_unified_col_summary.json"
    )
    parser.add_argument(
        "--review",
        type=Path,
        default=DEFAULT_ROOT / "taxon_name_unified_col_review_verified.tsv",
    )
    parser.add_argument(
        "--output", type=Path, default=DEFAULT_ROOT / "taxon_backbone_dashboard.html"
    )
    parser.add_argument(
        "--review-rounds", type=Path, default=DEFAULT_ROOT / "review_rounds"
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
