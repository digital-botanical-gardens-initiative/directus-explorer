"""Tests for taxonomy curation dashboard aggregation and rendering."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from directus_explorer.taxon_curation_dashboard import load_dashboard_data, render_dashboard


def _write_tsv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def test_dashboard_aggregates_review_and_execution_progress(tmp_path: Path) -> None:
    audit_path = tmp_path / "audit.json"
    audit_path.write_text(
        json.dumps(
            {
                "generated_at": "2026-07-28T00:00:00Z",
                "row_count": 100,
                "rows_with_any_issue": 4,
                "issue_counts": {"source_taxon_field_conflict": 2},
                "project_summary": [
                    {
                        "project": "jbp",
                        "field_data_rows": 10,
                        "rows_with_any_issue": 2,
                        "source_taxon_field_conflict": 2,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    round_dir = tmp_path / "curation/col_release/jbp_round_001"
    review = round_dir / "jbp_round_001_review.tsv"
    _write_tsv(
        review,
        [
            {
                "project": "jbp",
                "round": "1",
                "review_id": "abc",
                "affected_row_count": "2",
                "field_data_ids": "10;11",
                "taxon_name": "Erica arborea",
                "name_proposition": "Phoenix doctylifera",
                "col_accepted_name": "Erica arborea",
                "col_accepted_usage_id": "6GL83",
                "col_matched_status": "accepted",
                "decision": "accept_proposal",
                "confidence": "high",
                "review_note": "Image confirmed <Erica>.",
            }
        ],
    )
    _write_tsv(
        round_dir / "jbp_round_001_write_preview_x_execution_y.tsv",
        [
            {
                "field_data_id": "10",
                "status": "updated",
            },
            {
                "field_data_id": "11",
                "status": "updated",
            },
        ],
    )

    data = load_dashboard_data(
        curation_root=tmp_path / "curation",
        audit_summary=audit_path,
    )
    output = render_dashboard(data)

    assert data.reviewed_records == 2
    assert data.applied_records == 2
    assert data.project_rows[0]["coverage"] == 100
    assert data.project_rows[0]["estimated_open_issues"] == 0
    assert data.estimated_open_issues == 2
    assert data.decisions[0]["status"] == "applied"
    assert "Image confirmed &lt;Erica&gt;." in output
    assert "100.0% of baseline conflicts" in output
