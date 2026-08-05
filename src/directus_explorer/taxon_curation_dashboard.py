"""Build a standalone dashboard from local taxonomy curation artifacts."""

# ruff: noqa: E501

from __future__ import annotations

import csv
import html
import json
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote


@dataclass(frozen=True, slots=True)
class DashboardData:
    """Aggregated audit, round, decision, and resolution data."""

    audit_generated_at: str
    audited_records: int
    baseline_issues: int
    baseline_conflicts: int
    project_rows: tuple[dict[str, Any], ...]
    rounds: tuple[dict[str, Any], ...]
    decisions: tuple[dict[str, Any], ...]
    resolution_flags: tuple[dict[str, Any], ...]

    @property
    def reviewed_records(self) -> int:
        return len(
            {
                field_id
                for row in self.decisions
                if row["decision"]
                for field_id in row["field_data_ids"]
            }
        )

    @property
    def applied_records(self) -> int:
        return len(
            {
                field_id
                for row in self.decisions
                for field_id in row["applied_field_data_ids"]
            }
        )

    @property
    def unresolved_records(self) -> int:
        return len(
            {
                field_id
                for row in self.decisions
                if row["decision"] in {"clear_placeholder", "unresolved"}
                for field_id in row["field_data_ids"]
            }
        )

    @property
    def settled_records(self) -> int:
        return len(
            {
                field_id
                for row in self.decisions
                for field_id in row["settled_field_data_ids"]
            }
        )

    @property
    def estimated_open_issues(self) -> int:
        """Estimate open audit rows by subtracting journaled curation updates."""

        return max(self.baseline_issues - self.settled_records, 0)


def load_dashboard_data(
    *,
    curation_root: Path,
    audit_summary: Path,
    directus_instance: str = "",
) -> DashboardData:
    """Discover curation rounds and combine them with the baseline audit summary."""

    audit = _read_json(audit_summary)
    audit_projects = {
        str(row["project"]): dict(row) for row in audit.get("project_summary", [])
    }
    decisions: list[dict[str, Any]] = []
    rounds: list[dict[str, Any]] = []
    resolution_flags: list[dict[str, Any]] = []
    reviewed_by_project: defaultdict[str, set[str]] = defaultdict(set)
    applied_by_project: defaultdict[str, set[str]] = defaultdict(set)
    unresolved_by_project: defaultdict[str, set[str]] = defaultdict(set)
    settled_by_project: defaultdict[str, set[str]] = defaultdict(set)

    for review_path in sorted(curation_root.glob("**/*_review.tsv")):
        stem = review_path.name.removesuffix("_review.tsv")
        review_rows = _read_tsv(review_path)
        if not review_rows:
            continue
        project = review_rows[0].get("project", "(unknown)")
        round_number = review_rows[0].get("round", "")
        metadata_path = review_path.with_name(f"{stem}_metadata.json")
        resolution_path = review_path.with_name(f"{stem}_name_resolution.tsv")
        metadata = _read_json(metadata_path) if metadata_path.exists() else {}
        resolutions = _read_tsv(resolution_path) if resolution_path.exists() else []
        execution_paths = sorted(review_path.parent.glob(f"{stem}_write_preview_*_execution_*.tsv"))
        applied_ids = {
            row.get("field_data_id", "")
            for path in execution_paths
            for row in _read_tsv(path)
            if row.get("status") == "updated"
        }
        preflight_paths = sorted(review_path.parent.glob(f"{stem}_preflight_*.json"))
        latest_preflight = _read_json(preflight_paths[-1]) if preflight_paths else {}

        group_count = len(review_rows)
        record_count = sum(_integer(row.get("affected_row_count")) for row in review_rows)
        reviewed_groups = sum(bool(row.get("decision", "").strip()) for row in review_rows)
        applied_count = len(applied_ids)
        unresolved_no_write_ids = {
            field_id
            for row in review_rows
            if row.get("decision", "").strip() == "unresolved"
            for field_id in row.get("field_data_ids", "").split(";")
            if field_id
        }
        unresolved_ids = {
            field_id
            for row in review_rows
            if row.get("decision", "").strip()
            in {"clear_placeholder", "unresolved"}
            for field_id in row.get("field_data_ids", "").split(";")
            if field_id
        }
        settled_ids = applied_ids | unresolved_no_write_ids
        reviewed_by_project[project].update(
            field_id
            for row in review_rows
            if row.get("decision", "").strip()
            for field_id in row.get("field_data_ids", "").split(";")
            if field_id
        )
        applied_by_project[project].update(applied_ids)
        unresolved_by_project[project].update(unresolved_ids)
        settled_by_project[project].update(settled_ids)

        artifact_links = [
            _artifact_link(curation_root, review_path, "Review"),
            _artifact_link(curation_root, metadata_path, "Metadata"),
            _artifact_link(curation_root, resolution_path, "Resolutions"),
        ]
        if preflight_paths:
            artifact_links.append(
                _artifact_link(curation_root, preflight_paths[-1], "Latest preflight")
            )
        if execution_paths:
            artifact_links.append(
                _artifact_link(curation_root, execution_paths[-1], "Execution journal")
            )
        rounds.append(
            {
                "project": project,
                "round": round_number,
                "groups": group_count,
                "records": record_count,
                "reviewed_groups": reviewed_groups,
                "applied_records": applied_count,
                "settled_records": len(settled_ids),
                "col_version": metadata.get("col_dataset", {}).get("version", ""),
                "col_doi": metadata.get("col_dataset", {}).get("doi", ""),
                "preflight_conflicts": _integer(latest_preflight.get("conflict_count")),
                "artifacts": artifact_links,
            }
        )

        for row in review_rows:
            ids = [value for value in row.get("field_data_ids", "").split(";") if value]
            sample_ids = [
                value for value in row.get("sample_ids", "").split(";") if value
            ]
            if len(sample_ids) != len(ids):
                sample_ids = [""] * len(ids)
            applied = sum(field_id in applied_ids for field_id in ids)
            decision = row.get("decision", "").strip()
            status = _decision_status(decision, applied=applied, total=len(ids))
            chosen_name = row.get("chosen_col_accepted_name", "")
            chosen_id = row.get("chosen_col_accepted_usage_id", "")
            settled_decision_ids = tuple(
                field_id
                for field_id in ids
                if field_id in applied_ids or decision == "unresolved"
            )
            decisions.append(
                {
                    "project": project,
                    "round": round_number,
                    "review_id": row.get("review_id", ""),
                    "affected_records": len(ids),
                    "applied_records": applied,
                    "field_data_ids": tuple(ids),
                    "sample_ids": tuple(sample_ids),
                    "directus_items": tuple(
                        {
                            "field_data_id": field_id,
                            "sample_id": sample_id,
                            "url": _directus_item_url(directus_instance, field_id),
                        }
                        for field_id, sample_id in zip(ids, sample_ids, strict=True)
                    ),
                    "applied_field_data_ids": tuple(
                        field_id for field_id in ids if field_id in applied_ids
                    ),
                    "settled_field_data_ids": settled_decision_ids,
                    "taxon_name": row.get("taxon_name", ""),
                    "name_proposition": row.get("name_proposition", ""),
                    "accepted_name": (
                        chosen_name or row.get("col_accepted_name", "")
                    ),
                    "col_id": (
                        chosen_id or row.get("col_accepted_usage_id", "")
                    ),
                    "match_status": row.get("col_matched_status", ""),
                    "decision": decision,
                    "confidence": row.get("confidence", ""),
                    "status": status,
                    "note": row.get("review_note", ""),
                }
            )

        for row in resolutions:
            if not _resolution_needs_attention(row):
                continue
            resolution_flags.append(
                {
                    "project": project,
                    "round": round_number,
                    "verbatim_name": row.get("verbatim_name", ""),
                    "reason": _resolution_reason(row),
                    "accepted_name": row.get("col_accepted_name", "")
                    or row.get("fuzzy_suggestion_col_accepted_name", ""),
                    "col_id": row.get("col_accepted_usage_id", "")
                    or row.get("fuzzy_suggestion_col_accepted_usage_id", ""),
                    "detail": _resolution_detail(row),
                }
            )

    all_projects = sorted(
        set(audit_projects)
        | set(reviewed_by_project)
        | set(applied_by_project)
        | set(unresolved_by_project)
    )
    project_rows: list[dict[str, Any]] = []
    for project in all_projects:
        audit_row = audit_projects.get(project, {})
        conflicts = _integer(audit_row.get("source_taxon_field_conflict"))
        applied = len(applied_by_project[project])
        reviewed = len(reviewed_by_project[project])
        unresolved = len(unresolved_by_project[project])
        settled = len(settled_by_project[project])
        project_rows.append(
            {
                "project": project,
                "field_data_rows": _integer(audit_row.get("field_data_rows")),
                "baseline_issues": _integer(audit_row.get("rows_with_any_issue")),
                "estimated_open_issues": max(
                    _integer(audit_row.get("rows_with_any_issue")) - settled,
                    0,
                ),
                "baseline_conflicts": conflicts,
                "reviewed_records": reviewed,
                "applied_records": applied,
                "unresolved_records": unresolved,
                "settled_records": settled,
                "remaining_conflicts": max(conflicts - applied, 0),
                "coverage": min(applied / conflicts * 100, 100) if conflicts else None,
                "has_round": any(item["project"] == project for item in rounds),
            }
        )
    project_rows.sort(
        key=lambda row: (
            not bool(row["has_round"]),
            -int(row["remaining_conflicts"]),
            str(row["project"]),
        )
    )

    return DashboardData(
        audit_generated_at=str(audit.get("generated_at", "")),
        audited_records=_integer(audit.get("row_count")),
        baseline_issues=_integer(audit.get("rows_with_any_issue")),
        baseline_conflicts=_integer((audit.get("issue_counts") or {}).get("source_taxon_field_conflict")),
        project_rows=tuple(project_rows),
        rounds=tuple(rounds),
        decisions=tuple(decisions),
        resolution_flags=tuple(resolution_flags),
    )


def render_dashboard(data: DashboardData, *, title: str = "Taxonomy curation") -> str:
    """Render a self-contained, filterable HTML dashboard."""

    generated = datetime.now(UTC).isoformat(timespec="seconds")
    conflict_coverage = (
        data.applied_records / data.baseline_conflicts * 100 if data.baseline_conflicts else 0
    )
    project_options = "".join(
        f'<option value="{_e(str(row["project"]))}">{_e(str(row["project"]))}</option>'
        for row in data.project_rows
        if row["has_round"]
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(title)}</title>
<style>
:root{{--ink:#17352b;--muted:#61716b;--paper:#f4f1e9;--panel:#fffdf8;--line:#d9d8cd;--moss:#2e6b51;--lime:#b9d77a;--amber:#e6a84a;--coral:#c95f4b;--blue:#4e7f91;--shadow:0 14px 38px rgba(28,52,43,.09)}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--paper);color:var(--ink);font:15px/1.5 Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}} .shell{{max-width:1440px;margin:auto;padding:30px}} header{{display:flex;justify-content:space-between;gap:24px;align-items:end;padding:28px 32px;border-radius:22px;background:var(--ink);color:#fff;box-shadow:var(--shadow)}} .eyebrow{{text-transform:uppercase;letter-spacing:.16em;font-size:11px;font-weight:800;color:var(--lime)}} h1{{font-family:Georgia,serif;font-size:clamp(34px,5vw,62px);line-height:1;margin:8px 0}} header p{{max-width:650px;margin:0;color:#d5dfda}} .stamp{{min-width:250px;text-align:right;color:#c7d3ce;font-size:12px}} .cards{{display:grid;grid-template-columns:repeat(6,1fr);gap:14px;margin:20px 0}} .card,.panel{{background:var(--panel);border:1px solid var(--line);border-radius:16px;box-shadow:var(--shadow)}} .card{{padding:18px}} .card .value{{font:700 29px/1 Georgia,serif;margin:7px 0}} .label{{font-size:11px;text-transform:uppercase;letter-spacing:.1em;color:var(--muted);font-weight:800}} .sub{{font-size:12px;color:var(--muted)}} .grid{{display:grid;grid-template-columns:minmax(0,1.6fr) minmax(310px,.7fr);gap:18px;margin:18px 0}} .panel{{padding:22px;overflow:hidden}} h2{{font:700 24px/1.2 Georgia,serif;margin:0 0 5px}} .panel-head{{display:flex;align-items:end;justify-content:space-between;gap:12px;margin-bottom:18px}} .panel-head p{{margin:0;color:var(--muted);font-size:13px}} table{{width:100%;border-collapse:collapse}} th{{text-align:left;font-size:10px;text-transform:uppercase;letter-spacing:.09em;color:var(--muted);padding:10px 9px;border-bottom:1px solid var(--line)}} td{{padding:11px 9px;border-bottom:1px solid #ebe9df;vertical-align:top}} tr:last-child td{{border-bottom:0}} tbody tr:hover{{background:#f7f5ed}} .num{{font-variant-numeric:tabular-nums;text-align:right}} .progress{{height:8px;background:#e6e6dc;border-radius:99px;overflow:hidden;min-width:90px;margin-top:5px}} .progress>i{{display:block;height:100%;background:linear-gradient(90deg,var(--moss),var(--lime));border-radius:inherit}} .round{{border-left:4px solid var(--moss);padding:14px 0 14px 16px;border-bottom:1px solid var(--line)}} .round:last-child{{border-bottom:0}} .round-title{{display:flex;justify-content:space-between;font-weight:800}} .round-stats{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:12px 0}} .round-stats span{{background:#f1f3eb;padding:8px;border-radius:8px;text-align:center;font-size:12px}} a{{color:var(--moss);text-decoration:none;font-weight:700}} a:hover{{text-decoration:underline}} .links{{display:flex;gap:10px;flex-wrap:wrap;font-size:12px}} .toolbar{{display:flex;gap:10px;flex-wrap:wrap;margin:15px 0}} input,select{{border:1px solid var(--line);border-radius:10px;padding:10px 12px;background:white;color:var(--ink);min-width:180px}} .wide{{overflow:auto}} .badge{{display:inline-block;padding:4px 8px;border-radius:999px;font-size:10px;text-transform:uppercase;letter-spacing:.06em;font-weight:900;white-space:nowrap}} .applied{{background:#dcebdc;color:#25583d}} .reviewed{{background:#dfeaf0;color:#315f73}} .unresolved{{background:#ece7dc;color:#695f49}} .pending{{background:#f7e8cc;color:#855c18}} .partial{{background:#f5d9d1;color:#8b4133}} .scientific{{font-family:Georgia,serif;font-style:italic;font-size:16px}} .arrow{{color:#9aa39f;padding:0 5px}} .note{{max-width:330px;color:var(--muted);font-size:12px}} .flag-list{{display:grid;gap:9px}} .flag{{padding:12px;border:1px solid var(--line);border-radius:12px;background:#faf9f3}} .flag b{{font-family:Georgia,serif;font-style:italic}} .flag small{{display:block;color:var(--muted);margin-top:4px}} .empty{{padding:30px;text-align:center;color:var(--muted)}} footer{{padding:20px 4px;color:var(--muted);font-size:12px}} @media(max-width:1000px){{.cards{{grid-template-columns:repeat(2,1fr)}}.grid{{grid-template-columns:1fr}}}} @media(max-width:620px){{.shell{{padding:14px}}header{{padding:22px;display:block}}.stamp{{text-align:left;margin-top:18px}}.cards{{grid-template-columns:1fr 1fr}}.card:last-child{{grid-column:1/-1}}}}
</style>
</head>
<body><main class="shell">
<header><div><div class="eyebrow">DBGI · evidence-led taxonomy</div><h1>{_e(title)}</h1><p>Track the path from baseline audit to reviewed identification, pinned Catalogue of Life resolution, preflight, and applied Directus value.</p></div><div class="stamp">Dashboard generated<br><strong>{_e(generated)}</strong><br>Baseline audit<br><strong>{_e(data.audit_generated_at)}</strong></div></header>
<section class="cards">
{_metric_card("Audited records", data.audited_records, "Baseline Field_Data snapshot")}
{_metric_card("Estimated open", data.estimated_open_issues, f"From {data.baseline_issues:,} baseline issues")}
{_metric_card("Conflict backlog", data.baseline_conflicts, "Rows needing source-field decisions")}
{_metric_card("Reviewed", data.reviewed_records, f"{len(data.decisions)} grouped decisions")}
{_metric_card("Applied", data.applied_records, f"{conflict_coverage:.1f}% of baseline conflicts")}
{_metric_card("Unresolved", data.unresolved_records, "Reviewed without an inferred taxon")}
</section>
<section class="grid"><div class="panel"><div class="panel-head"><div><h2>Project progress</h2><p>Actionable open excludes applied updates and reviewed no-evidence outcomes. Unresolved records remain explicit.</p></div></div>{_project_table(data.project_rows)}</div><aside class="panel"><div class="panel-head"><div><h2>Curation rounds</h2><p>Backbone, review, and execution status.</p></div></div>{_round_cards(data.rounds)}</aside></section>
<section class="panel"><div class="panel-head"><div><h2>Decision register</h2><p>Search the evidence trail and inspect accepted CoL and explicit unresolved outcomes.</p></div><span id="visible-count" class="sub"></span></div><div class="toolbar"><input id="search" type="search" placeholder="Search taxon, note, CoL ID…"><select id="project-filter"><option value="">All projects</option>{project_options}</select><select id="status-filter"><option value="">All statuses</option><option value="applied">Applied</option><option value="unresolved">Unresolved</option><option value="reviewed">Reviewed</option><option value="partial">Partial</option><option value="pending">Pending</option></select></div><div class="wide">{_decision_table(data.decisions)}</div></section>
<section class="panel" style="margin-top:18px"><div class="panel-head"><div><h2>Names requiring interpretation</h2><p>Synonyms, cultivar annotations, and fuzzy corrections retained for provenance.</p></div></div>{_resolution_flags(data.resolution_flags)}</section>
<footer>Standalone report · no network requests · source artifacts remain the authority.</footer>
</main>
<script>
const rows=[...document.querySelectorAll('#decisions tbody tr')];
const search=document.querySelector('#search'), project=document.querySelector('#project-filter'), status=document.querySelector('#status-filter'), count=document.querySelector('#visible-count');
function filterRows(){{const q=search.value.trim().toLowerCase();let visible=0;rows.forEach(row=>{{const show=(!q||row.textContent.toLowerCase().includes(q))&&(!project.value||row.dataset.project===project.value)&&(!status.value||row.dataset.status===status.value);row.hidden=!show;if(show)visible++;}});count.textContent=`${{visible}} of ${{rows.length}} groups`;}}
[search,project,status].forEach(el=>el.addEventListener('input',filterRows));filterRows();
</script></body></html>"""


def write_dashboard(path: Path, data: DashboardData) -> None:
    """Write the rendered dashboard to disk."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_dashboard(data), encoding="utf-8")


def _project_table(rows: tuple[dict[str, Any], ...]) -> str:
    body = []
    for row in rows:
        coverage = row["coverage"]
        coverage_html = "—"
        if coverage is not None:
            coverage_html = (
                f'<strong>{float(coverage):.0f}%</strong><div class="progress">'
                f'<i style="width:{float(coverage):.1f}%"></i></div>'
            )
        body.append(
            "<tr>"
            f'<td><strong>{_e(row["project"])}</strong></td>'
            f'<td class="num">{_number(row["field_data_rows"])}</td>'
            f'<td class="num">{_number(row["baseline_issues"])}</td>'
            f'<td class="num"><strong>{_number(row["estimated_open_issues"])}</strong></td>'
            f'<td class="num">{_number(row["baseline_conflicts"])}</td>'
            f'<td class="num">{_number(row["reviewed_records"])}</td>'
            f'<td class="num">{_number(row["applied_records"])}</td>'
            f'<td class="num">{_number(row["unresolved_records"])}</td>'
            f"<td>{coverage_html}</td></tr>"
        )
    return '<div class="wide"><table><thead><tr><th>Project</th><th class="num">Rows</th><th class="num">Baseline issues</th><th class="num">Actionable open</th><th class="num">Conflicts</th><th class="num">Reviewed</th><th class="num">Applied</th><th class="num">Unresolved</th><th>Conflict coverage</th></tr></thead><tbody>' + "".join(body) + "</tbody></table></div>"


def _round_cards(rounds: tuple[dict[str, Any], ...]) -> str:
    if not rounds:
        return '<div class="empty">No curation rounds found.</div>'
    output = []
    for row in rounds:
        links = "".join(
            f'<a href="{_e(link["href"])}">{_e(link["label"])}</a>'
            for link in row["artifacts"]
            if link["exists"]
        )
        status = "Complete" if row["settled_records"] == row["records"] else "In progress"
        badge = "applied" if status == "Complete" else "pending"
        output.append(
            '<article class="round">'
            f'<div class="round-title"><span>{_e(row["project"])} · round {_e(row["round"])}</span><span class="badge {badge}">{status}</span></div>'
            f'<div class="sub">{_e(row["col_version"] or "Backbone not recorded")} · DOI {_e(row["col_doi"] or "—")}</div>'
            f'<div class="round-stats"><span><b>{row["groups"]}</b><br>groups</span><span><b>{row["reviewed_groups"]}</b><br>reviewed</span><span><b>{row["settled_records"]}/{row["records"]}</b><br>settled</span></div>'
            f'<div class="links">{links}</div></article>'
        )
    return "".join(output)


def _decision_table(rows: tuple[dict[str, Any], ...]) -> str:
    body = []
    for row in rows:
        body.append(
            f'<tr data-project="{_e(row["project"])}" data-status="{_e(row["status"])}">'
            f'<td><span class="badge {_e(row["status"])}">{_e(row["status"])}</span><div class="sub">{row["applied_records"]}/{row["affected_records"]} records</div></td>'
            f'<td><strong>{_e(row["project"])}</strong><div class="sub">Round {_e(row["round"])} · {_e(row["review_id"])}</div></td>'
            f'<td>{_directus_items(row["directus_items"])}</td>'
            f'<td><span class="scientific">{_e(row["taxon_name"] or "—")}</span></td>'
            f'<td><span class="scientific">{_e(row["name_proposition"] or "—")}</span></td>'
            f'<td><span class="scientific">{_e(row["accepted_name"] or "—")}</span><div class="sub">CoL {_e(row["col_id"] or "—")} · {_e(row["match_status"] or "—")}</div></td>'
            f'<td>{_e(row["decision"] or "Pending")}<div class="sub">{_e(row["confidence"] or "—")} confidence</div></td>'
            f'<td class="note">{_e(row["note"] or "—")}</td></tr>'
        )
    return '<table id="decisions"><thead><tr><th>Status</th><th>Round</th><th>Samples / Directus</th><th>Authoritative taxon_name</th><th>Historical name_proposition</th><th>Accepted CoL taxon</th><th>Decision</th><th>Review note</th></tr></thead><tbody>' + "".join(body) + "</tbody></table>"


def _directus_items(items: tuple[dict[str, str], ...]) -> str:
    links = []
    for item in items:
        label = item["sample_id"] or f"Field_Data {item['field_data_id']}"
        suffix = f" <span class=\"sub\">#{_e(item['field_data_id'])}</span>"
        if item["url"]:
            links.append(
                f'<a href="{_e(item["url"])}" target="_blank" rel="noopener">'
                f"{_e(label)}</a>{suffix}"
            )
        else:
            links.append(f"{_e(label)}{suffix}")
    if len(links) <= 3:
        return "<br>".join(links) or "—"
    return (
        f"<details><summary>{len(links)} samples</summary>"
        + "<div>"
        + "<br>".join(links)
        + "</div></details>"
    )


def _directus_item_url(instance: str, field_id: str) -> str:
    if not instance.strip():
        return ""
    return (
        instance.rstrip("/")
        + "/admin/content/Field_Data/"
        + quote(field_id, safe="")
    )


def _resolution_flags(rows: tuple[dict[str, Any], ...]) -> str:
    if not rows:
        return '<div class="empty">No special name-resolution cases.</div>'
    items = []
    for row in rows:
        items.append(
            '<article class="flag">'
            f'<span class="badge reviewed">{_e(row["reason"])}</span> '
            f'<b>{_e(row["verbatim_name"])}</b><span class="arrow">→</span>'
            f'<b>{_e(row["accepted_name"] or "Unresolved")}</b> '
            f'<span class="sub">CoL {_e(row["col_id"] or "—")}</span>'
            f'<small>{_e(row["project"])} · round {_e(row["round"])} · {_e(row["detail"])}</small></article>'
        )
    return '<div class="flag-list">' + "".join(items) + "</div>"


def _metric_card(label: str, value: int, sub: str) -> str:
    return f'<article class="card"><div class="label">{_e(label)}</div><div class="value">{_number(value)}</div><div class="sub">{_e(sub)}</div></article>'


def _decision_status(decision: str, *, applied: int, total: int) -> str:
    if decision == "unresolved":
        return "unresolved"
    if decision == "clear_placeholder" and total and applied == total:
        return "unresolved"
    if total and applied == total:
        return "applied"
    if applied:
        return "partial"
    if decision:
        return "reviewed"
    return "pending"


def _resolution_needs_attention(row: dict[str, str]) -> bool:
    return bool(
        row.get("parsed_cultivar")
        or row.get("fuzzy_suggestion_name")
        or row.get("col_matched_status") not in {"", "accepted"}
    )


def _resolution_reason(row: dict[str, str]) -> str:
    reasons = []
    if row.get("fuzzy_suggestion_name"):
        reasons.append("fuzzy")
    if row.get("parsed_cultivar"):
        reasons.append("cultivar")
    if row.get("col_matched_status") not in {"", "accepted"}:
        reasons.append("synonym")
    return " + ".join(reasons)


def _resolution_detail(row: dict[str, str]) -> str:
    details = []
    if row.get("fuzzy_suggestion_name"):
        details.append(
            f"gnverifier suggested {row['fuzzy_suggestion_name']} "
            f"(edit distance {row.get('gnverifier_edit_distance', '—')})"
        )
    if row.get("parsed_cultivar"):
        details.append(f"cultivar epithet: {row['parsed_cultivar']}")
    if row.get("col_matched_status") not in {"", "accepted"}:
        details.append(f"matched usage status: {row['col_matched_status']}")
    return "; ".join(details)


def _artifact_link(root: Path, path: Path, label: str) -> dict[str, Any]:
    return {
        "label": label,
        "href": os.path.relpath(path, root),
        "exists": path.exists(),
    }


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return [
            {key: value or "" for key, value in row.items()}
            for row in csv.DictReader(handle, delimiter="\t")
        ]


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return payload


def _integer(value: object) -> int:
    try:
        return int(str(value or 0))
    except ValueError:
        return 0


def _number(value: object) -> str:
    return f"{_integer(value):,}"


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)
