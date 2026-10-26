"""Serve a project-scoped, local-only review and Directus curation workflow.

The browser never talks to Directus. Existing scripts provide the audit, review,
preflight, and verified write operations; writes require an explicit confirmation.
"""

# ruff: noqa: E501

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import secrets
import subprocess
import sys
from datetime import UTC, date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from urllib.parse import parse_qs, urlencode, urlsplit

import requests

from directus_explorer.config import Settings, SettingsError, load_settings
from directus_explorer.directus import DirectusClient, DirectusError
from directus_explorer.taxon_backbone_directus import plan_field_data_backfill
from directus_explorer.taxon_curation import COL_DATASET_VERSION
from directus_explorer.taxon_curation_exceptions import build_upload, exception_options
from directus_explorer.taxon_curation_manifest import build_taxon_update_manifest
from directus_explorer.taxon_curation_review import preserve_review_decisions
from directus_explorer.taxon_curation_worklist import build_worklist

ROOT = Path(__file__).resolve().parents[2]
AUDIT = Path("data/output/directus_integrity/taxon_name_unified_audit/taxon_name_unified_audit_all.tsv")
CURATION = Path("data/working/taxonomy_curation")
INTEGRITY = Path("data/output/directus_integrity")
RELEASE = "col_" + COL_DATASET_VERSION.lower().replace("-", "_").replace(" ", "_")
FIELDS = (
    "decision", "chosen_source_field", "chosen_verbatim_name", "chosen_col_accepted_name",
    "chosen_col_accepted_usage_id", "chosen_col_accepted_rank", "confidence", "reviewer",
    "reviewed_at", "review_note",
)
DECISIONS = ("", "accept_proposal", "override", "unresolved", "defer")


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def review_path(project: str, root: Path) -> Path | None:
    paths = sorted(root.glob(f"col_*/{project}_round_*/{project}_round_*_review.tsv"))
    return paths[-1] if paths else None


def save_review(path: Path, form: dict[str, str], audit: Path) -> int:
    original = path.read_bytes()
    if form.get("digest") != hashlib.sha256(original).hexdigest():
        raise ValueError("Review changed since this page loaded. Reload before saving.")
    rows = read_tsv(path)
    ids = {row["review_id"] for row in rows}
    for key in form:
        if key.startswith("row_") and key.split("_", 2)[1] not in ids:
            raise ValueError("Unknown review row; reload the page.")
    for row in rows:
        row_id = row["review_id"]
        checked = form.get(f"row_{row_id}_accept") == "1"
        decision = "accept_proposal" if checked else form.get(f"row_{row_id}_decision", "")
        if checked and form.get(f"row_{row_id}_decision", "") not in {"", "accept_proposal"}:
            raise ValueError(f"Uncheck {row['proposal_verbatim_name']} before choosing another outcome.")
        if decision not in DECISIONS:
            raise ValueError(f"Invalid decision for {row_id}")
        for field in FIELDS:
            if field == "decision":
                row[field] = decision
                continue
            value = form.get(f"row_{row_id}_{field}", row.get(field, "")).strip()
            if len(value) > 1000:
                raise ValueError(f"{field} is too long for {row_id}")
            row[field] = value
        if decision == "accept_proposal":
            for field in FIELDS:
                if field.startswith("chosen_"):
                    row[field] = ""
        if decision and not row["reviewed_at"]:
            row["reviewed_at"] = date.today().isoformat()
        if decision == "accept_proposal" and not all(
            row.get(field, "").strip()
            for field in ("col_accepted_name", "col_accepted_usage_id", "col_accepted_rank")
        ):
            raise ValueError(f"No pinned CoL target for {row_id}; choose override or defer.")
    if audit.exists() and all(row["decision"] for row in rows):
        audit_by_id = {row["field_data_id"]: row for row in read_tsv(audit)}
        build_taxon_update_manifest(rows, audit_by_id)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    return sum(bool(row["decision"]) for row in rows)


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def control(row: dict[str, str], field: str, *, size: int = 22) -> str:
    name = f"row_{row['review_id']}_{field}"
    return (f'<input name="{esc(name)}" value="{esc(row.get(field, ""))}" '
            f'size="{size}" aria-label="{esc(field)} for {esc(row["review_id"])}">')


def render_batch(project: str, root: Path, token: str, worklist: dict,
                 *, taxa_access: bool = True) -> str:
    pending = [row for row in worklist["records"] if row["state"] != "complete"]
    if not pending or any(row["state"] == "missing_name" for row in pending):
        return ""
    output = root / f"{project}_backbone" / RELEASE
    backbone_path = output / "taxon_name_unified_col_backbone.tsv"
    mapping_path = output / "field_data_col_mapping.tsv"
    if not backbone_path.exists() or not mapping_path.exists():
        return '<section><h2>Final batch</h2><p>CoL lookups are not ready. Press Refresh to retry.</p></section>'
    options = exception_options(read_tsv(backbone_path))
    if not taxa_access:
        candidates = "".join(
            f'<li>{esc(name)}: '
            f'{esc(", ".join(item["scientific_name"] + " · CoL " + item["col_id"]
                           for item in matches) or "No pinned candidate; leave unlinked")}</li>'
            for name, matches in options.items()
        )
        return f'''<section><h2>Taxonomic outcomes awaiting verification</h2>
<p>The remaining proposals can be inspected, but a finalized batch cannot be
previewed or pushed until the configured Directus admin account can read Taxa.
Correct its permissions, then press Refresh.</p><ul>{candidates}</ul></section>'''
    latest = latest_upload(root, project)
    preview = None
    if latest and (latest / "preview.json").exists():
        preview = json.loads((latest / "preview.json").read_text(encoding="utf-8"))
        if (preview["source_digest"] != upload_source(output)
            or preview["total"] != worklist["project_count"]
            or (latest / "complete.json").exists()):
            preview = None
    selected = preview["choices"] if preview else {}
    decisions = []
    for index, (name, candidates) in enumerate(options.items()):
        choices = "".join(
            f'<option value="{esc(candidate["col_id"])}" '
            f'{"selected" if selected.get(name) == candidate["col_id"] else ""}>'
            f'{esc(candidate["scientific_name"])} · CoL {esc(candidate["col_id"])} '
            f'({esc(candidate["rank"])})</option>'
            for candidate in candidates
        )
        rationale = "".join(
            f'<li>{esc(candidate["scientific_name"])}: {esc(candidate["rationale"])}</li>'
            for candidate in candidates
        )
        decisions.append(f'''<div class="decision"><label><strong>{esc(name)}</strong>
<select name="choice_{index}" required aria-label="CoL choice for {esc(name)}">
<option value="">Select a taxonomic outcome</option>{choices}
<option value="unresolved" {"selected" if selected.get(name) == "unresolved" else ""}>
Keep unlinked (reviewed unresolved)</option></select></label>
<details><summary>Evidence and meaning</summary><ul>{rationale}</ul>
<p>The recorded name is retained. A genus match does not claim to identify a cultivar.</p>
</details></div>''')
    preview_html = ""
    if preview:
        seed = {row["col_id"]: row for row in read_tsv(latest / "seed.tsv")}
        grouped: dict[tuple[str, str], list[str]] = {}
        for row in read_tsv(latest / "backfill.tsv"):
            grouped.setdefault(
                (row["current_taxon_name_unified"], row["taxon_col_id"]), []
            ).append(row["sample_id"])
        batch = "".join(
            f'<tr><td>{esc(name)}</td><td>'
            f'{esc(seed[col_id]["scientific_name"] + " · CoL " + col_id if col_id
                   else "Reviewed; no CoL match")}</td>'
            f'<td>{len(samples)}</td><td>{esc(", ".join(samples))}</td></tr>'
            for (name, col_id), samples in sorted(grouped.items())
        )
        digest = hashlib.sha256((latest / "preview.json").read_bytes()).hexdigest()
        preview_html = f'''<div class="batch"><h3>Finalized batch to push · {preview["total"]} records</h3>
<p>{preview["linked_count"]} CoL links, {preview["total"] - preview["linked_count"]}
explicitly unlinked; {preview["taxa_missing"]} missing Taxa will be created first.
{preview["ready_count"]} records will change, {preview["exact_count"]} are already current.
Nothing has been written by this preview. This batch includes the <strong>entire
{esc(project)} project</strong>, regardless of the display filters above.</p>
<div class="scroll"><table><thead><tr><th>Recorded name</th><th>Approved CoL target</th>
<th>Records</th><th>Sample IDs</th></tr></thead><tbody>{batch}</tbody></table></div>
<form method="post"><input type="hidden" name="token" value="{token}">
<input type="hidden" name="project" value="{esc(project)}">
<input type="hidden" name="preview_digest" value="{digest}">
<label>Type <strong>{esc(project)}</strong> to authorize these Directus changes
<input name="confirm" autocomplete="off" required></label>
<button class="primary" name="action" value="upload_apply">Push finalized batch</button>
</form><details><summary>Local backup and audit files</summary><code>{esc(latest)}</code></details>
</div>'''
    return f'''<section><h2>Resolve pending taxon names</h2>
<p>Accepted CoL matches are included automatically. Select only the exceptions below;
unmatched names can be explicitly left unlinked.</p>
<form method="post"><input type="hidden" name="token" value="{token}">
<input type="hidden" name="project" value="{esc(project)}">
<input type="hidden" name="source_digest" value="{upload_source(output)}">
{''.join(decisions) if decisions else '<p>No exceptional names need a decision.</p>'}
<button name="action" value="upload_preview">Preview finalized batch</button></form>
{preview_html}</section>'''


def record_fields(values: dict) -> str:
    output = []
    for key, value in sorted(values.items()):
        text = json.dumps(value, ensure_ascii=False) if isinstance(value, dict | list) else value
        output.append(f'<dt>{esc(key)}</dt><dd>{esc("" if text is None else text)}</dd>')
    return "".join(output)

def render_missing(project: str, path: Path | None, token: str,
                   root: Path, records: list[dict]) -> str:
    missing = [row for row in records if row["state"] == "missing_name"]
    if not missing:
        return ""
    agreed = sum(bool(row["audit"].get("expected_unified_taxon_name"))
                 and not row["audit"].get("source_taxon_field_conflict") for row in missing)
    safe = ""
    if agreed:
        safe = f'''<p>{agreed} agreed recorded names can be copied into empty unified fields.</p>
<form method="post"><input type="hidden" name="token" value="{token}">
<input type="hidden" name="project" value="{esc(project)}">
<button name="action" value="safe">Preview {agreed} agreed name updates</button></form>'''
        runs = sorted((root / f"{project}_safe_names").glob("run_*"))
        destination = runs[-1] if runs else None
        if destination and (destination / "source.json").exists():
            summary = json.loads((destination / "summary.json").read_text(encoding="utf-8"))
            if summary["update_count"]:
                safe += f'''<form method="post"><input type="hidden" name="token" value="{token}">
<input type="hidden" name="project" value="{esc(project)}">
<label>Type {esc(project)} to confirm <input name="confirm" required autocomplete="off"></label>
<button class="primary" name="action" value="safe_apply">Push {summary["update_count"]} agreed names</button>
</form>'''
    conflict_ids = {row["audit"]["field_data_id"] for row in missing
                    if row["audit"].get("source_taxon_field_conflict")}
    review = ""
    if conflict_ids:
        if path is None:
            review = "<p>Conflicting recorded names need proposals. Press Refresh to fetch them.</p>"
        else:
            rows = [row for row in read_tsv(path) if
                    conflict_ids.intersection(row["field_data_ids"].split(";"))]
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            decisions = "".join(
                f'''<tr><td>{esc(row["proposal_verbatim_name"])}<br>
<small>{esc(row["sample_ids"])}</small></td>
<td>{esc(row["col_accepted_name"])} · CoL {esc(row["col_accepted_usage_id"])}</td>
<td><label><input type="checkbox" name="row_{esc(row["review_id"])}_accept"
value="1" {"checked" if row.get("decision") == "accept_proposal" else ""}> Accept</label>
<select name="row_{esc(row["review_id"])}_decision">
{''.join(f'<option value="{choice}" {"selected" if row.get("decision") == choice else ""}>'
         f'{label}</option>' for choice, label in (
            ("", "Choose if not accepting"), ("defer", "Review later"),
            ("unresolved", "Leave unresolved"), ("override", "Use another CoL name")))}
</select>
<details><summary>Alternative details</summary>
<label>Source {control(row, "chosen_source_field")}</label>
<label>Verbatim {control(row, "chosen_verbatim_name")}</label>
<label>CoL name {control(row, "chosen_col_accepted_name")}</label>
<label>CoL ID {control(row, "chosen_col_accepted_usage_id")}</label>
<label>Rank {control(row, "chosen_col_accepted_rank")}</label></details></td></tr>'''
                for row in rows
            )
            review = f'''<h3>Approve conflicting recorded names</h3>
<form method="post"><input type="hidden" name="token" value="{token}">
<input type="hidden" name="project" value="{esc(project)}">
<input type="hidden" name="digest" value="{digest}">
<div class="scroll"><table><thead><tr><th>Recorded</th><th>CoL proposal</th>
<th>Decision</th></tr></thead><tbody>{decisions}</tbody></table></div>
<button name="action" value="preview">Preview reviewed names</button></form>'''
            summaries = sorted(path.parent.glob(path.stem.removesuffix("_review") + "_preflight_*.json"))
            if summaries:
                summary = json.loads(summaries[-1].read_text(encoding="utf-8"))
                manifest = path.with_name(path.name.replace("_review.tsv", "_update_manifest.tsv"))
                if (manifest.exists() and
                    summary["manifest_sha256"] == hashlib.sha256(manifest.read_bytes()).hexdigest() and
                    manifest.stat().st_mtime_ns >= path.stat().st_mtime_ns and
                    not summary["conflict_count"] and summary["ready_count"]):
                    review += f'''<p>{summary["ready_count"]} reviewed name updates ready to push.</p>
<form method="post"><input type="hidden" name="token" value="{token}">
<input type="hidden" name="project" value="{esc(project)}">
<label>Type {esc(project)} to confirm <input name="confirm" required autocomplete="off"></label>
<button class="primary" name="action" value="apply">Push reviewed names</button></form>'''
    return f'''<section><h2>Names to complete first · {len(missing)} records</h2>
<p>Complete these recorded names before creating CoL links. Refresh afterward
to prepare the taxonomic batch.</p>{safe}{review}</section>'''


def render(project: str, path: Path | None, token: str, notice: str,
           audit: Path, root: Path, *, state: dict | None = None,
           taxon: str = "", row_id: str = "", show_all: bool = False,
           project_options: set[str] | None = None, error: bool = False) -> str:
    audits = state["audit_rows"] if state else (
        read_tsv(audit) if project_options is None and audit.exists() else []
    )
    projects = sorted(project_options or
                      {row["project"] for row in audits if row.get("project")} | {project})
    options = "".join(
        f'<option value="{esc(name)}" {"selected" if name == project else ""}>'
        f'{esc(name)}</option>' for name in projects
    )
    active = state is not None and state["project"] == project
    worklist = state["worklist"] if active else None
    all_rows = worklist["records"] if worklist else []
    shown = [
        record for record in all_rows
        if (show_all or record["state"] != "complete")
        and (not taxon or taxon.casefold() in record["name"].casefold()
             or taxon.casefold() in str(record["audit"].get("expected_unified_taxon_name", "")).casefold()
             or taxon.casefold() in str(record["raw"].get("sample_id", "")).casefold())
        and (not row_id or str(record["raw"].get("id")) == row_id)
    ]
    names = sorted({record["name"] for record in all_rows if record["name"]}, key=str.casefold)
    datalist = "".join(f'<option value="{esc(name)}">' for name in names)
    grouped: dict[str, list[dict]] = {}
    for record in shown:
        name = record["name"] or record["audit"].get("expected_unified_taxon_name") or "(no recorded name)"
        grouped.setdefault(name, []).append(record)
    backbone = root / f"{project}_backbone" / RELEASE / "taxon_name_unified_col_backbone.tsv"
    proposals = {row["taxon_name_unified"]: row
                 for row in read_tsv(backbone)} if backbone.exists() else {}
    groups = []
    for name, members in sorted(grouped.items(), key=lambda pair: pair[0].casefold()):
        candidate = proposals.get(name, {})
        target = (f'{candidate["col_accepted_name"]} · CoL {candidate["taxon_col_id"]}'
                  if candidate.get("requires_review") == "false"
                  and candidate.get("taxon_col_id") else
                  "Curator decision required" if candidate.get("requires_review") == "true" else "")
        entries = []
        for record in members:
            raw = record["raw"]
            sample = str(raw.get("sample_id") or raw.get("id"))
            link = "/?" + urlencode({
                "project": project, "view": "all", "row": str(raw["id"]),
            })
            entries.append(f'''<div class="record">
<a href="{esc(link)}">{esc(sample)}</a> · {esc(record["state"].replace("_", " "))}
<details {"open" if row_id else ""}><summary>All Field_Data fields for {esc(sample)}</summary>
<dl>{record_fields(raw)}</dl>
<details><summary>Audit interpretation</summary><dl>
{record_fields(record["audit"])}</dl></details></details></div>''')
        groups.append(f'''<details class="group" {"open" if taxon or row_id else ""}>
<summary><strong>{esc(name)}</strong> · {len(members)} record{"s" if len(members) != 1 else ""}
{f"→ {esc(target)}" if target else ""}</summary>
<div class="members">{''.join(entries)}</div></details>''')
    if worklist:
        pending = worklist["pending_count"]
        if not worklist["project_count"]:
            status = f"No observations found for {esc(project)}."
        elif pending:
            label = ("awaiting Taxa verification" if active and not state.get("taxa_access", True)
                     else "need resolution")
            status = (f'<strong>{pending} records {label}</strong> · '
                      f'{worklist["complete_count"]} complete in {esc(project)}.'
                      f'<p>Showing {len(shown)} {"all" if show_all else "pending"} records'
                      f'{" matching the filter" if taxon or row_id else ""}.</p>')
        else:
            status = (f'<strong>No entries require taxonomic resolution.</strong> '
                      f'All {worklist["project_count"]} records in {esc(project)} are complete.')
    else:
        status = "Press Refresh to load the current Directus records. Nothing is written by Refresh."
    all_link = "/?" + urlencode({"project": project, "view": "all" if not show_all else "pending"})
    work_html = (
        f'<div class="worklist">{"".join(groups) if groups else "<p>No entries to show.</p>"}</div>'
        if worklist else ""
    )
    missing_html = render_missing(project, path, token, root, all_rows) if active else ""
    batch_html = (render_batch(project, root, token, worklist,
                               taxa_access=state.get("taxa_access", True))
                  if active else "")
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Taxon curation · {esc(project)}</title><style>
body{{font:16px system-ui;max-width:1100px;margin:1.5rem auto;padding:0 1rem;
color:#202938;background:#f5f7fa}}h1{{margin-bottom:.25rem}}p{{line-height:1.45}}
section{{background:white;border:1px solid #d5dce5;border-radius:8px;
padding:1rem 1.2rem;margin:1rem 0}}h2{{margin:.2rem 0 .7rem}}
small,.muted{{color:#526075}}.toolbar{{display:flex;align-items:center;gap:1rem}}
.scroll{{overflow-x:auto}}table{{width:100%;border-collapse:collapse}}
td,th{{padding:.55rem;text-align:left;border-bottom:1px solid #ddd;vertical-align:top}}
input,select,button{{font:inherit;padding:.4rem;border:1px solid #abb7c9;border-radius:5px}}
input[type=checkbox]{{width:1.1rem;height:1.1rem;vertical-align:middle}}
button{{cursor:pointer;background:#e9f0fd}}.primary{{background:#d7e6ff;font-weight:bold;
padding:.6rem 1rem}}button:hover{{background:#c6dbfa}}
.warning,.notice{{padding:.75rem;background:#fff3df;border-left:4px solid #ae6f00;
white-space:pre-wrap;overflow-wrap:anywhere}}.notice{{background:#e9f4ea;border-color:#347a46}}
.group{{border-top:1px solid #ddd;padding:.65rem 0}}summary{{cursor:pointer}}
.members{{padding:.5rem 1rem}}.record{{padding:.3rem 0}}.record details{{margin:.4rem 0}}
dl{{display:grid;grid-template-columns:minmax(150px,1fr) 3fr;gap:.3rem .7rem;
font-size:.9rem;overflow-wrap:anywhere}}dt{{font-weight:bold}}dd{{margin:0}}
.decision{{padding:.6rem 0;border-top:1px solid #ddd}}.decision select{{display:block;
max-width:100%;width:30rem;margin:.4rem 0}}.batch{{border-top:2px solid #d5dce5;margin-top:1rem}}
code{{overflow-wrap:anywhere}}
</style></head><body><h1>Taxon curation</h1>
<p class="muted">Find what remains, inspect the evidence, preview the entire project,
then push the verified batch.</p>
<section><form method="post"><input type="hidden" name="token" value="{token}">
<label>QField project <select name="project">{options}</select></label>
<label> Taxon or sample <input name="taxon" value="{esc(taxon)}" list="taxon-names"
placeholder="Search taxon or sample"></label><datalist id="taxon-names">{datalist}</datalist>
<button class="primary" name="action" value="refresh">Refresh from Directus</button>
<button name="action" value="filter">Filter</button></form>
<p class="muted">Refresh is read-only. Selecting a new project and filtering loads its records.</p>
<p><a href="{esc(all_link)}">{"Show all records" if not show_all else "Show only pending"}</a>
 · <a href="/?{urlencode({"project": project})}">Clear filters</a></p></section>
{f'<p class="{"warning" if error else "notice"}">{esc(notice)}</p>' if notice else ""}
{f'<p class="warning">{esc(state["warning"])}</p>' if active and state["warning"] else ""}
<section><h2>{esc(project)} · current status</h2><p>{status}</p>{work_html}</section>
{missing_html}{batch_html}
</body></html>'''


def handler(project: str, audit: Path, root: Path, env_file: Path, admin_env: Path):
    token = secrets.token_urlsafe(32)
    lock = Lock()
    snapshots: dict[str, dict] = {}
    known_projects = {project}
    if audit.exists():
        known_projects.update(row["project"] for row in read_tsv(audit) if row.get("project"))

    class Handler(BaseHTTPRequestHandler):
        def allowed_project(self, selected: str) -> bool:
            if (not selected or not all(
                c.isascii() and (c.isalnum() or c in "_-") for c in selected
            )):
                return False
            return selected in known_projects

        def respond(self, selected: str, notice: str = "", *,
                    filters: dict[str, str] | None = None, error: bool = False) -> None:
            filters = filters or {}
            content = render(
                selected, review_path(selected, root), token, notice, audit, root,
                state=snapshots.get(selected), taxon=filters.get("taxon", ""),
                row_id=filters.get("row", ""), show_all=filters.get("view") == "all",
                project_options=known_projects, error=error,
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'",
            )
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self) -> None:
            url = urlsplit(self.path)
            if url.path != "/":
                self.send_error(404)
                return
            parsed = parse_qs(url.query, keep_blank_values=True)
            if any(len(values) != 1 for values in parsed.values()):
                self.send_error(400)
                return
            filters = {key: values[0] for key, values in parsed.items()}
            selected = filters.get("project", project)
            if (not self.allowed_project(selected) or
                any(len(filters.get(key, "")) > 150 for key in ("taxon", "row")) or
                filters.get("view", "") not in {"", "all", "pending"}):
                self.send_error(400)
                return
            self.respond(selected, filters=filters)

        def do_POST(self) -> None:
            if self.path != "/":
                self.send_error(404)
                return
            origin = self.headers.get("Origin")
            if origin and origin != f"http://127.0.0.1:{self.server.server_port}":
                self.send_error(403)
                return
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 100_000:
                self.send_error(413)
                return
            raw = self.rfile.read(length).decode("utf-8")
            parsed = parse_qs(raw, keep_blank_values=True)
            if any(len(values) != 1 for values in parsed.values()):
                self.send_error(400)
                return
            form = {key: values[0] for key, values in parsed.items()}
            if not secrets.compare_digest(form.get("token", ""), token):
                self.send_error(403)
                return
            selected = form.get("project", project)
            taxon = form.get("taxon", "")
            if not self.allowed_project(selected) or len(taxon) > 150:
                self.send_error(400)
                return
            try:
                with lock:
                    if form.get("action") == "refresh" or (
                        form.get("action") == "filter" and selected not in snapshots
                    ):
                        snapshots[selected] = refresh_workspace(
                            selected, audit, root, env_file, admin_env
                        )
                        known_projects.update(snapshots[selected]["worklist"]["projects"])
                        worklist = snapshots[selected]["worklist"]
                        notice = (
                            f"Live refresh: {worklist['pending_count']} records pending; "
                            f"{worklist['complete_count']} complete. No Directus changes."
                        )
                    elif form.get("action") == "filter":
                        notice = ""
                    else:
                        if form.get("action") == "upload_preview" and selected not in snapshots:
                            raise ValueError("Press Refresh before previewing the batch.")
                        if (form.get("action") in {"upload_preview", "upload_apply"} and
                            not snapshots.get(selected, {}).get("taxa_access", True)):
                            raise ValueError(
                                "The admin account cannot read Taxa. Grant Taxa read/create "
                                "permission, then press Refresh before previewing or pushing."
                            )
                        notice = perform(form, selected, audit, root, env_file, admin_env)
                        if form.get("action") in {"upload_apply", "safe_apply", "apply"}:
                            try:
                                snapshots[selected] = refresh_workspace(
                                    selected, audit, root, env_file, admin_env
                                )
                            except (ValueError, OSError, DirectusError, requests.RequestException) as exc:
                                snapshots.pop(selected, None)
                                notice += f" Refresh status could not be loaded: {exc}"
            except (ValueError, OSError, KeyError, DirectusError,
                    requests.RequestException, subprocess.CalledProcessError) as exc:
                self.respond(selected, str(exc), filters={"taxon": taxon}, error=True)
                return
            self.respond(selected, notice, filters={"taxon": taxon})

    return Handler


def run_script(script: str, *args: str) -> str:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script), *args],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    message = (result.stdout + "\n" + result.stderr).strip()
    if result.returncode:
        raise ValueError(f"{script} failed:\n{message}")
    return message


def dashboard_settings(env_file: Path, admin_env: Path) -> tuple[Settings, Settings]:
    """Explain which Directus credential source is unavailable before fetching data."""
    settings: list[Settings] = []
    errors = []
    for flag, role, path in (
        ("--env-file", "audit", env_file),
        ("--admin-env-file", "Taxa verification and writes", admin_env),
    ):
        try:
            settings.append(load_settings(env_file=path))
        except SettingsError as exc:
            location = "file does not exist" if not path.is_file() else "credentials incomplete"
            errors.append(f"{flag} {path} ({role}; {location}): {exc}")
    if errors:
        raise SettingsError(
            "Directus credentials are not configured. " + " ".join(errors) +
            " Supply real credential paths relative to the repository directory "
            "or absolute paths; do not use a literal placeholder path."
        )
    return settings[0], settings[1]


def refresh_workspace(project: str, audit: Path, root: Path,
                      env_file: Path, admin_env: Path) -> dict:
    """Refresh authoritative project state; only local artifacts change."""
    _, admin_settings = dashboard_settings(env_file, admin_env)
    run_script("audit_field_data_taxon_name_unified.py", "--env-file", str(env_file))
    audit_rows = read_tsv(audit)
    client = DirectusClient(admin_settings)
    client._authenticate()
    field_rows = [
        row for row in client._get_items(collection="Field_Data", fields="*")
        if row.get("qfield_project") == project
    ]
    warning = ""
    taxa_access = True
    try:
        taxa_rows = client._get_items(collection="Taxa", fields="id,col_id")
    except DirectusError as exc:
        if "status 403" not in str(exc):
            raise
        taxa_access = False
        taxa_rows = []
        warning = ("Directus denied Taxa read access to the configured admin account (403). "
                   "Recorded names and row evidence are available, but the final batch "
                   "cannot be verified or pushed. Grant that account Taxa read/create "
                   "permission, then press Refresh.")
    worklist = build_worklist(audit_rows, field_rows, taxa_rows, project, COL_DATASET_VERSION)
    if worklist["project_count"] and worklist["pending_count"]:
        missing = [row for row in worklist["records"] if row["state"] == "missing_name"]
        if missing:
            conflicts = [
                row for row in read_tsv(
                    audit.parent / "taxon_name_unified_missing_expected_conflict.tsv"
                ) if row.get("project") == project
            ]
            existing = review_path(project, root)
            covered = (
                {field_id for row in read_tsv(existing)
                 for field_id in row["field_data_ids"].split(";")}
                if existing else set()
            )
            if conflicts and not {
                row["field_data_id"] for row in conflicts
            }.issubset(covered):
                round_number = int(existing.parent.name.rsplit("_", 1)[1]) + 1 if existing else 1
                try:
                    run_script("generate_taxon_curation_review.py", "--project", project,
                               "--round", str(round_number), "--no-gnverifier",
                               "--input", str(audit.parent / "taxon_name_unified_missing_expected_conflict.tsv"),
                               "--output-root", str(root))
                    if existing:
                        generated = review_path(project, root)
                        write_rows(generated, preserve_review_decisions(
                            read_tsv(generated), read_tsv(existing),
                        ))
                except ValueError as exc:
                    warning += f" Name suggestions unavailable: {exc}"
        else:
            output = root / f"{project}_backbone" / RELEASE
            mapping = output / "field_data_col_mapping.tsv"
            current = {
                row["field_data_id"]: row["stored_taxon_name_unified"]
                for row in audit_rows if row["project"] == project
            }
            previous = (
                {row["field_data_id"]: row["taxon_name_unified"] for row in read_tsv(mapping)}
                if mapping.exists() else {}
            )
            if previous != current:
                destination = root / f"{project}_backbone"
                destination.mkdir(parents=True, exist_ok=True)
                scoped = destination / "field_data_audit.tsv"
                write_rows(scoped, [row for row in audit_rows if row["project"] == project])
                try:
                    run_script("resolve_taxon_name_unified_col.py", "--input", str(scoped),
                               "--output-root", str(destination))
                except ValueError as exc:
                    warning += f" CoL lookup unavailable: {exc}"
    return {
        "project": project,
        "audit_rows": audit_rows,
        "field_rows": field_rows,
        "taxa_rows": taxa_rows,
        "worklist": worklist,
        "warning": warning,
        "taxa_access": taxa_access,
    }


def perform(form: dict[str, str], project: str, audit: Path, root: Path,
            env_file: Path, admin_env: Path) -> str:
    action = form.get("action")
    path = review_path(project, root)
    if action == "upload_preview":
        return prepare_upload(project, root, admin_env, form)
    if action == "upload_apply":
        return finish_upload(project, root, admin_env, form)
    if action == "safe":
        report = INTEGRITY / "by_project" / f"{project}__field_data_unified_taxon.tsv"
        run_script("audit_directus_integrity.py")
        if not report.exists():
            raise ValueError(f"No integrity report for {project}.")
        destination = (root / f"{project}_safe_names" /
                       datetime.now(UTC).strftime("run_%Y%m%dT%H%M%S%fZ"))
        destination.mkdir(parents=True)
        output = run_script("populate_field_data_unified_taxon.py", "--report", str(report),
                            "--env-file", str(env_file), *safe_paths(destination))
        if not json.loads((destination / "summary.json").read_text(encoding="utf-8"))["update_count"]:
            (destination / "manifest.tsv").write_text("", encoding="utf-8")
            (destination / "backup.tsv").write_text("", encoding="utf-8")
            return output + "\nNo non-conflicting names remain to apply."
        (destination / "source.json").write_text(json.dumps({
            "report_sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
            "backup_sha256": hashlib.sha256((destination / "backup.tsv").read_bytes()).hexdigest(),
            "manifest_sha256": hashlib.sha256((destination / "manifest.tsv").read_bytes()).hexdigest(),
        }), encoding="utf-8")
        return output
    if action == "safe_apply":
        if form.get("confirm") != project:
            raise ValueError(f"Type {project} exactly to confirm the Directus write.")
        destination = latest_safe_dir(root, project)
        report = INTEGRITY / "by_project" / f"{project}__field_data_unified_taxon.tsv"
        if not json.loads((destination / "summary.json").read_text(encoding="utf-8"))["update_count"]:
            raise ValueError("No non-conflicting names remain to apply.")
        source = json.loads((destination / "source.json").read_text(encoding="utf-8"))
        run_script("audit_directus_integrity.py")
        if (source["report_sha256"] != hashlib.sha256(report.read_bytes()).hexdigest() or
            source["backup_sha256"] != hashlib.sha256((destination / "backup.tsv").read_bytes()).hexdigest() or
            source["manifest_sha256"] != hashlib.sha256((destination / "manifest.tsv").read_bytes()).hexdigest()):
            raise ValueError("Source report or backup changed; prepare non-conflicting names again.")
        current = destination / "current"
        current.mkdir(exist_ok=True)
        run_script("populate_field_data_unified_taxon.py", "--report", str(report),
                   "--env-file", str(admin_env), *safe_paths(current))
        if (source["manifest_sha256"] != hashlib.sha256((current / "manifest.tsv").read_bytes()).hexdigest()
            or source["backup_sha256"] != hashlib.sha256((current / "backup.tsv").read_bytes()).hexdigest()):
            raise ValueError("Live target fields changed since preview; prepare again.")
        return run_script("populate_field_data_unified_taxon.py", "--report", str(report),
                          "--env-file", str(admin_env), *safe_paths(destination), "--commit")
    if path is None:
        raise ValueError("Generate a review first.")
    manifest = path.with_name(path.name.replace("_review.tsv", "_update_manifest.tsv"))
    if action == "preview":
        if not audit.exists():
            run_script("audit_field_data_taxon_name_unified.py", "--env-file", str(env_file))
        count = save_review(path, form, audit)
        if count < len(read_tsv(path)):
            return f"Saved {count} decisions. Choose an outcome for every remaining name to preview."
        run_script("prepare_taxon_curation_manifest.py", "--review", str(path),
                   "--audit", str(audit))
        summary_path = manifest.with_suffix(".summary.json")
        if not json.loads(summary_path.read_text(encoding="utf-8"))["update_count"]:
            return "Decisions saved. All names were deferred or left unresolved; no Directus write is needed."
        run_script("preflight_taxon_curation_directus_update.py", "--manifest",
                   str(manifest), "--env-file", str(env_file))
        summaries = sorted(path.parent.glob(path.stem.removesuffix("_review") + "_preflight_*.json"))
        summary = json.loads(summaries[-1].read_text(encoding="utf-8"))
        return (f"Read-only preview: {summary['ready_count']} records ready, "
                f"{summary['conflict_count']} blocking conflicts. No Directus changes.")
    if action != "apply":
        raise ValueError("Unknown action")
    summary_path = manifest.with_suffix(".summary.json")
    if not manifest.exists() or not summary_path.exists():
        raise ValueError("Choose decisions and preview them before applying.")
    if not json.loads(summary_path.read_text(encoding="utf-8"))["update_count"]:
        raise ValueError("No reviewed updates to apply.")
    if manifest.stat().st_mtime_ns < path.stat().st_mtime_ns:
        raise ValueError("Review changed since preview. Preview again.")
    if form.get("confirm") != project:
        raise ValueError(f"Type {project} exactly to confirm the Directus write.")
    summaries = sorted(path.parent.glob(path.stem.removesuffix("_review") + "_preflight_*.json"))
    if not summaries:
        raise ValueError("Run preflight first.")
    summary = json.loads(summaries[-1].read_text(encoding="utf-8"))
    if summary["manifest_sha256"] != hashlib.sha256(manifest.read_bytes()).hexdigest():
        raise ValueError("Manifest changed since preflight. Run preflight again.")
    if summary["conflict_count"]:
        raise ValueError("Preflight has blocking conflicts. Resolve them before applying.")
    if not summary["ready_count"]:
        raise ValueError("Preflight has no rows to apply.")
    if summary["backup_sha256"] != hashlib.sha256(Path(summary["backup"]).read_bytes()).hexdigest():
        raise ValueError("Backup changed since preflight. Run preflight again.")
    run_script("apply_taxon_curation_directus_update.py", "--preview",
               summary["write_preview"], "--backup", summary["backup"],
               "--project", project, "--env-file", str(admin_env), "--commit")
    try:
        run_script("audit_field_data_taxon_name_unified.py", "--env-file", str(env_file))
    except ValueError as exc:
        return (f"Applied and verified {summary['ready_count']} records, but the audit "
                f"could not be refreshed: {exc}")
    return f"Applied and verified {summary['ready_count']} reviewed updates."


def latest_safe_dir(root: Path, project: str) -> Path:
    runs = sorted((root / f"{project}_safe_names").glob("run_*"))
    if not runs:
        raise ValueError("Fill unambiguous names first.")
    return runs[-1]


def safe_paths(destination: Path) -> tuple[str, ...]:
    return (
        "--manifest", str(destination / "manifest.tsv"),
        "--backup", str(destination / "backup.tsv"),
        "--skipped", str(destination / "skipped.tsv"),
        "--summary", str(destination / "summary.json"),
    )


def upload_source(output: Path) -> str:
    digest = hashlib.sha256()
    for name in ("taxon_name_unified_col_backbone.tsv", "field_data_col_mapping.tsv"):
        digest.update((output / name).read_bytes())
    return digest.hexdigest()


def latest_upload(root: Path, project: str) -> Path | None:
    runs = sorted((root / f"{project}_upload" / RELEASE).glob("run_*"))
    return runs[-1] if runs else None


def write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    if not rows:
        raise ValueError(f"Refusing to write an empty upload: {path}")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def live_upload_plan(project: str, backfill: list[dict[str, str]],
                     seed: list[dict[str, str]], env_file: Path,
                     *, planned_taxa: bool) -> tuple[int, int, list[dict[str, str]]]:
    client = DirectusClient(load_settings(env_file=env_file))
    client._authenticate()
    current = client._get_items(
        collection="Field_Data",
        fields="id,sample_id,qfield_project,taxon_name_unified,taxon_col_id,"
               "taxon_resolution_status,taxon_backbone_version,taxon_ref,taxon_parent_col_ids",
    )
    taxa = client._get_items(collection="Taxa", fields="id,col_id")
    project_rows = {
        str(row["id"]): row for row in current
        if row.get("qfield_project") == project
    }
    proposed_ids = {row["field_data_id"] for row in backfill}
    if set(project_rows) != proposed_ids:
        raise ValueError(
            f"Project records changed: {len(project_rows)} live vs {len(proposed_ids)} "
            "in this review. Refresh the audit and resolve names before previewing."
        )
    by_id = {str(row["id"]): row for row in current}
    for proposal in backfill:
        observed = by_id[proposal["field_data_id"]]
        if str(observed.get("sample_id") or "") != proposal["sample_id"]:
            raise ValueError(f"Sample changed for {proposal['field_data_id']}; refresh and resolve.")
    if planned_taxa:
        existing = {str(row.get("col_id") or "") for row in taxa}
        taxa.extend(
            {"id": -(index + 1), "col_id": row["col_id"]}
            for index, row in enumerate(seed) if row["col_id"] not in existing
        )
    ready, exact, conflicts = plan_field_data_backfill(backfill, current, taxa)
    return len(ready), len(exact), conflicts


def prepare_upload(project: str, root: Path, admin_env: Path,
                   form: dict[str, str]) -> str:
    output = root / f"{project}_backbone" / RELEASE
    backbone_path = output / "taxon_name_unified_col_backbone.tsv"
    mapping_path = output / "field_data_col_mapping.tsv"
    if not backbone_path.exists() or not mapping_path.exists():
        raise ValueError("Resolve CoL names first, then review the exceptional names.")
    if form.get("source_digest") != upload_source(output):
        raise ValueError("CoL results changed. Reload the page and review the choices again.")
    backbone = read_tsv(backbone_path)
    options = exception_options(backbone)
    choices = {}
    for index, name in enumerate(options):
        value = form.get(f"choice_{index}", "")
        if not value:
            raise ValueError(f"Choose a resolution or 'Leave unlinked' for {name}.")
        choices[name] = value
    if any(key.startswith("choice_") and key not in {
        f"choice_{index}" for index in range(len(options))
    } for key in form):
        raise ValueError("Unknown name choice; reload the page.")
    seed, backfill = build_upload(backbone, read_tsv(mapping_path), choices, project)
    destination = (root / f"{project}_upload" / RELEASE /
                   datetime.now(UTC).strftime("run_%Y%m%dT%H%M%S%fZ"))
    destination.mkdir(parents=True)
    seed_path = destination / "seed.tsv"
    backfill_path = destination / "backfill.tsv"
    write_rows(seed_path, seed)
    write_rows(backfill_path, backfill)
    run_script("seed_taxa_directus.py", "--seed", str(seed_path),
               "--env-file", str(admin_env))
    summary = json.loads(sorted(destination.glob("taxa_seed_preflight_*.json"))[-1].read_text())
    ready, exact, conflicts = live_upload_plan(
        project, backfill, seed, admin_env, planned_taxa=True
    )
    if conflicts:
        reasons = ", ".join(
            f"{row['sample_id']}: {row['conflict_reason']}" for row in conflicts[:8]
        )
        raise ValueError(f"{len(conflicts)} live record conflicts. No data changed. {reasons}")
    (destination / "preview.json").write_text(json.dumps({
        "project": project,
        "source_digest": upload_source(output),
        "seed_sha256": hashlib.sha256(seed_path.read_bytes()).hexdigest(),
        "backfill_sha256": hashlib.sha256(backfill_path.read_bytes()).hexdigest(),
        "choices": choices,
        "taxa_missing": summary["missing_count"],
        "ready_count": ready,
        "exact_count": exact,
        "total": len(backfill),
        "linked_count": sum(bool(row["taxon_col_id"]) for row in backfill),
    }, indent=2) + "\n", encoding="utf-8")
    return (f"Read-only full-project preview: {len(backfill)} records, "
            f"{summary['missing_count']} missing Taxa to create, {ready} records to update; "
            "no conflicts. No Directus changes.")


def finish_upload(project: str, root: Path, admin_env: Path,
                  form: dict[str, str]) -> str:
    if form.get("confirm") != project:
        raise ValueError(f"Type {project} exactly to confirm the Directus write.")
    destination = latest_upload(root, project)
    if destination is None or not (destination / "preview.json").exists():
        raise ValueError("Preview the full upload before applying.")
    preview_path = destination / "preview.json"
    if form.get("preview_digest") != hashlib.sha256(preview_path.read_bytes()).hexdigest():
        raise ValueError("Upload preview changed. Reload and review before applying.")
    if (destination / "complete.json").exists():
        return "This upload was already verified. No new changes."
    preview = json.loads(preview_path.read_text(encoding="utf-8"))
    output = root / f"{project}_backbone" / RELEASE
    seed_path = destination / "seed.tsv"
    backfill_path = destination / "backfill.tsv"
    if (preview["project"] != project or
        preview["source_digest"] != upload_source(output) or
        preview["seed_sha256"] != hashlib.sha256(seed_path.read_bytes()).hexdigest() or
        preview["backfill_sha256"] != hashlib.sha256(backfill_path.read_bytes()).hexdigest()):
        raise ValueError("Review or upload files changed. Preview the full upload again.")
    seed = read_tsv(seed_path)
    backfill = read_tsv(backfill_path)
    # Rebuild from the current reviewed decisions: a modified preview never authorizes a write.
    expected_seed, expected_backfill = build_upload(
        read_tsv(output / "taxon_name_unified_col_backbone.tsv"),
        read_tsv(output / "field_data_col_mapping.tsv"), preview["choices"], project,
    )
    if seed != expected_seed or backfill != expected_backfill:
        raise ValueError("Upload no longer matches reviewed CoL names; preview again.")
    run_script("seed_taxa_directus.py", "--seed", str(seed_path), "--env-file", str(admin_env))
    ready, exact, conflicts = live_upload_plan(
        project, backfill, seed, admin_env, planned_taxa=True
    )
    if conflicts or ready + exact != len(backfill):
        raise ValueError("Live records changed since preview. No Taxa or links were written.")
    run_script("seed_taxa_directus.py", "--seed", str(seed_path),
               "--env-file", str(admin_env), "--commit")
    try:
        run_script("backfill_taxon_backbone_field_data.py", "--backfill",
                   str(backfill_path), "--env-file", str(admin_env))
        summary = json.loads(sorted(destination.glob("field_data_backfill_preflight_*.json"))[-1].read_text())
        if summary["conflict_count"]:
            raise ValueError(f"{summary['conflict_count']} link conflicts; no Field_Data rows written.")
        if summary["ready_count"]:
            run_script("backfill_taxon_backbone_field_data.py", "--backfill",
                       str(backfill_path), "--env-file", str(admin_env), "--commit")
        remaining, verified, conflicts = live_upload_plan(
            project, backfill, seed, admin_env, planned_taxa=False
        )
        if remaining or conflicts or verified != len(backfill):
            raise ValueError("Post-upload check found incomplete links.")
    except ValueError as exc:
        raise ValueError(
            f"Taxa were seeded, but record upload did not finish: {exc}. "
            "Preview the upload again to resume; existing Taxa are not duplicated."
        ) from exc
    (destination / "complete.json").write_text(json.dumps({
        "verified_records": verified,
        "linked_records": preview["linked_count"],
        "unlinked_reviewed_records": verified - preview["linked_count"],
    }, indent=2) + "\n", encoding="utf-8")
    return (f"Upload complete: {verified} project records verified; "
            f"{preview['linked_count']} linked to CoL Taxa, "
            f"{verified - preview['linked_count']} explicitly left unlinked.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--audit", type=Path, default=AUDIT)
    parser.add_argument("--curation-root", type=Path, default=CURATION)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--admin-env-file", type=Path, default=Path(".env.admin"))
    args = parser.parse_args()
    if not args.project or not all(
        character.isascii() and (character.isalnum() or character in "_-")
        for character in args.project
    ):
        parser.error("--project must contain only ASCII letters, digits, underscores, or hyphens")
    try:
        dashboard_settings(args.env_file, args.admin_env_file)
    except SettingsError as exc:
        parser.error(str(exc))
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler(
        args.project, args.audit, args.curation_root, args.env_file, args.admin_env_file))
    print(f"Open http://127.0.0.1:{server.server_port}/ for {args.project} (Ctrl-C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

