"""Exercise the full curator upload against an isolated HTTP Directus."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import directus_explorer.interactive_taxon_curation as curator
from directus_explorer.interactive_taxon_curation import (
    RELEASE,
    finish_upload,
    latest_upload,
    prepare_upload,
    read_tsv,
    render,
    render_batch,
    upload_source,
    write_rows,
)
from directus_explorer.taxon_curation import COL_DATASET_VERSION
from directus_explorer.taxon_curation_worklist import build_worklist


class FakeDirectus(BaseHTTPRequestHandler):
    rows: list[dict]
    taxa: list[dict]
    patches: list[dict]
    deny_taxa: bool = False

    def log_message(self, *_args: object) -> None:
        pass

    def send_data(self, data: object, status: int = 200) -> None:
        body = json.dumps({"data": data}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path.startswith("/fields/Taxa"):
            self.send_data([{"field": name} for name in (
                "id", "col_id", "scientific_name", "authorship", "rank",
                "backbone_version", "backbone_dataset_key", "backbone_doi",
            )])
        elif self.path.startswith("/items/Field_Data"):
            self.send_data(self.rows)
        elif self.path.startswith("/items/Taxa"):
            if self.deny_taxa:
                self.send_error(403)
            else:
                self.send_data(self.taxa)
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        data = json.loads(self.rfile.read(length))
        if self.path == "/auth/login":
            self.send_data({"access_token": "local-only"})
        elif self.path == "/items/Taxa":
            row = {"id": len(self.taxa) + 1, **data}
            self.taxa.append(row)
            self.send_data(row, 201)
        else:
            self.send_error(404)

    def do_PATCH(self) -> None:
        if self.path != "/items/Field_Data":
            self.send_error(404)
            return
        length = int(self.headers["Content-Length"])
        payload = json.loads(self.rfile.read(length))
        changed = []
        for item in payload:
            row = next(row for row in self.rows if row["id"] == item["id"])
            row.update(item)
            self.patches.append(item)
            changed.append(row)
        self.send_data(changed)


def _backbone(
    name: str, field_id: int, *, col_id: str = "", review: bool = False
) -> dict[str, str]:
    return {
        "taxon_name_unified": name, "projects": "bg-unifr",
        "field_data_ids": str(field_id), "sample_ids": f"sample-{field_id}",
        "requires_review": str(review).lower(),
        "taxon_col_id": col_id,
        "taxon_resolution_status": "unmatched" if review else "accepted_variant",
        "taxon_backbone_version": "2026-07-17 XR",
        "taxon_backbone_dataset_key": "315834", "taxon_backbone_doi": "10.48580/dgykv",
        "col_accepted_name": name if not review else "",
        "col_accepted_authorship": "L." if not review else "",
        "col_accepted_rank": "genus" if name == "Passiflora" else "species",
        "col_matched_status": "accepted" if not review else "",
        "col_fuzzy_candidates": "", "parsed_name": name,
        "parsed_rank": "species", "parse_status": "parsed",
        "parsed_cultivar": "", "col_match_type": "variant" if not review else "none",
        "col_matched_name": "", "col_matched_rank": "", "col_matched_usage_id": "",
    }


def _fixture_files(root: Path) -> Path:
    out = root / "bg-unifr_backbone" / RELEASE
    out.mkdir(parents=True)
    lepechinia = _backbone("Lepechinia hastata", 10, col_id="3T8YK")
    passiflora = _backbone("Passiflora", 11, col_id="6JGB")
    typo = _backbone("Lepechiria hastata", 12, review=True)
    typo["col_fuzzy_candidates"] = json.dumps([{
        "id": "3T8YK", "name": "Lepechinia hastata", "authorship": "L.",
        "rank": "species", "status": "accepted",
    }])
    cultivar = _backbone("X Passiflora 'Anastasia'", 13, review=True)
    cultivar.update({
        "taxon_resolution_status": "cultivar_requires_review", "parsed_cultivar": "Anastasia",
        "parsed_name": "× Passiflora 'Anastasia'", "col_match_type": "higherrank",
        "col_matched_usage_id": "6JGB", "col_matched_name": "Passiflora",
        "col_matched_rank": "genus", "col_matched_status": "accepted",
    })
    backbone = [lepechinia, passiflora, typo, cultivar]
    mapping = [{
        "project": "bg-unifr", "field_data_id": row["field_data_ids"],
        "sample_id": row["sample_ids"], "taxon_name_unified": row["taxon_name_unified"],
        "taxon_col_id": row["taxon_col_id"],
        "taxon_resolution_status": row["taxon_resolution_status"],
        "taxon_backbone_version": row["taxon_backbone_version"],
        "taxon_backbone_dataset_key": row["taxon_backbone_dataset_key"],
        "taxon_backbone_doi": row["taxon_backbone_doi"],
        "requires_review": row["requires_review"],
    } for row in backbone]
    write_rows(out / "taxon_name_unified_col_backbone.tsv", backbone)
    write_rows(out / "field_data_col_mapping.tsv", mapping)
    return out


@pytest.mark.parametrize(
    ("cultivar_choice", "expected_col_id", "expected_status", "linked_count"),
    [
        ("6JGB", "6JGB", "reviewed_cultivar_genus", 4),
        ("unresolved", None, "reviewed_unresolved", 3),
    ],
)
def test_full_project_preview_and_confirmed_upload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cultivar_choice: str,
    expected_col_id: str | None,
    expected_status: str,
    linked_count: int,
) -> None:
    output = _fixture_files(tmp_path)
    FakeDirectus.rows = [{
        "id": field_id, "qfield_project": "bg-unifr",
        "sample_id": f"sample-{field_id}", "taxon_name_unified": name,
        "taxon_col_id": None, "taxon_resolution_status": None,
        "taxon_backbone_version": None, "taxon_ref": None,
    } for field_id, name in ((10, "Lepechinia hastata"), (11, "Passiflora"),
                             (12, "Lepechiria hastata"), (13, "X Passiflora 'Anastasia'"))]
    FakeDirectus.deny_taxa = False
    FakeDirectus.taxa = []
    FakeDirectus.patches = []
    with ThreadingHTTPServer(("127.0.0.1", 0), FakeDirectus) as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            env_file = tmp_path / "env"
            env_file.write_text(
                f"DIRECTUS_INSTANCE=http://127.0.0.1:{server.server_port}\n"
                "DIRECTUS_USERNAME=test@example.invalid\nDIRECTUS_PASSWORD=fake\n"
            )
            for key in ("DIRECTUS_INSTANCE", "DIRECTUS_USERNAME", "DIRECTUS_PASSWORD"):
                monkeypatch.delenv(key, raising=False)
            markup = render_batch("bg-unifr", tmp_path, "local-token", {
                "records": [{"state": "pending_link"}] * 4, "project_count": 4,
            })
            assert "Lepechiria hastata" in markup
            assert "X Passiflora" in markup
            assert "CoL 3T8YK" in markup and "CoL 6JGB" in markup
            form = {
                "source_digest": upload_source(output),
                "choice_0": "3T8YK",
                "choice_1": cultivar_choice,
            }
            with pytest.raises(ValueError, match="Choose a resolution"):
                prepare_upload("bg-unifr", tmp_path, env_file, {**form, "choice_1": ""})
            assert "4 records" in prepare_upload("bg-unifr", tmp_path, env_file, form)
            assert FakeDirectus.taxa == [] and FakeDirectus.patches == []
            latest = latest_upload(tmp_path, "bg-unifr")
            assert latest is not None
            preview = latest / "preview.json"
            digest = hashlib.sha256(preview.read_bytes()).hexdigest()
            with pytest.raises(ValueError, match="Type bg-unifr"):
                finish_upload(
                    "bg-unifr", tmp_path, env_file,
                    {"confirm": "wrong", "preview_digest": digest},
                )
            FakeDirectus.rows[0]["sample_id"] = "changed-sample"
            with pytest.raises(ValueError, match="Sample changed"):
                finish_upload(
                    "bg-unifr", tmp_path, env_file,
                    {"confirm": "bg-unifr", "preview_digest": digest},
                )
            assert FakeDirectus.taxa == [] and FakeDirectus.patches == []
            FakeDirectus.rows[0]["sample_id"] = "sample-10"
            assert FakeDirectus.taxa == [] and FakeDirectus.patches == []
            message = finish_upload("bg-unifr", tmp_path, env_file,
                                    {"confirm": "bg-unifr", "preview_digest": digest})
            assert "4 project records verified" in message
            assert len(FakeDirectus.taxa) == 2
            assert len(FakeDirectus.patches) == 4
            typo = next(row for row in FakeDirectus.rows if row["id"] == 12)
            cultivar = next(row for row in FakeDirectus.rows if row["id"] == 13)
            assert typo["taxon_name_unified"] == "Lepechiria hastata"
            assert typo["taxon_col_id"] == "3T8YK"
            assert typo["taxon_resolution_status"] == "reviewed_spelling"
            assert cultivar["taxon_name_unified"] == "X Passiflora 'Anastasia'"
            assert cultivar["taxon_col_id"] == expected_col_id
            assert cultivar["taxon_resolution_status"] == expected_status
            complete = json.loads((latest / "complete.json").read_text())
            assert complete["linked_records"] == linked_count
            audits = [{
                "project": "bg-unifr", "field_data_id": str(row["id"]),
                "sample_id": row["sample_id"],
                "stored_taxon_name_unified": row["taxon_name_unified"],
            } for row in FakeDirectus.rows]
            worklist = build_worklist(
                audits, FakeDirectus.rows, FakeDirectus.taxa,
                "bg-unifr", COL_DATASET_VERSION,
            )
            assert worklist["pending_count"] == 0
            page = render(
                "bg-unifr", None, "local-token", "", tmp_path / "audit.tsv", tmp_path,
                state={"project": "bg-unifr", "audit_rows": audits,
                       "worklist": worklist, "warning": ""},
            )
            assert "No entries require taxonomic resolution" in page
            assert "Preview finalized batch" not in page
            assert "already verified" in finish_upload(
                "bg-unifr", tmp_path, env_file, {"confirm": "bg-unifr", "preview_digest": digest}
            )
            assert len(FakeDirectus.patches) == 4
            assert len(read_tsv(latest / "backfill.tsv")) == 4
        finally:
            server.shutdown()
            worker.join(timeout=5)


@pytest.mark.parametrize("taxa_readable", [True, False])
def test_live_refresh_filter_evidence_preview_push_and_empty_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, taxa_readable: bool
) -> None:
    root = tmp_path / "curation"
    output = _fixture_files(root)
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    source = Path(__file__).resolve().parents[1] / "scripts"
    for name in (
        "audit_field_data_taxon_name_unified.py",
        "seed_taxa_directus.py",
        "backfill_taxon_backbone_field_data.py",
    ):
        (scripts / name).symlink_to(source / name)
    monkeypatch.setattr(curator, "ROOT", tmp_path)
    FakeDirectus.rows = [{
        "id": field_id, "qfield_project": "bg-unifr",
        "sample_id": f"sample-{field_id}", "taxon_name_unified": name,
        "taxon_name_final": name + " L." if field_id == 10 else name,
        "taxon_name": name,
        "observation_notes": {"habitat": "wet rock"},
        "taxon_col_id": None, "taxon_resolution_status": None,
        "taxon_backbone_version": None, "taxon_ref": None,
    } for field_id, name in (
        (10, "Lepechinia hastata"), (11, "Passiflora"),
        (12, "Lepechiria hastata"), (13, "X Passiflora 'Anastasia'"),
    )]
    FakeDirectus.taxa = []
    FakeDirectus.patches = []
    FakeDirectus.deny_taxa = not taxa_readable
    FakeDirectus.rows.append({
        "id": 50, "qfield_project": "fung", "sample_id": "fung-50",
        "taxon_name_unified": "", "taxon_name_final": "Amanita muscaria",
        "taxon_name": "Amanita muscaria", "taxon_col_id": None,
        "taxon_resolution_status": None, "taxon_backbone_version": None,
        "taxon_ref": None,
    })
    audit = (tmp_path / "data/output/directus_integrity/taxon_name_unified_audit"
             / "taxon_name_unified_audit_all.tsv")
    audit.parent.mkdir(parents=True)
    write_rows(audit, [
        {"project": "bg-unifr", "field_data_id": "10"},
        {"project": "fung", "field_data_id": "50"},
    ])
    with ThreadingHTTPServer(("127.0.0.1", 0), FakeDirectus) as fake:
        fake_thread = threading.Thread(target=fake.serve_forever, daemon=True)
        fake_thread.start()
        env_file = tmp_path / "env"
        env_file.write_text(
            f"DIRECTUS_INSTANCE=http://127.0.0.1:{fake.server_port}\n"
            "DIRECTUS_USERNAME=test@example.invalid\nDIRECTUS_PASSWORD=fake\n"
        )
        for key in ("DIRECTUS_INSTANCE", "DIRECTUS_USERNAME", "DIRECTUS_PASSWORD"):
            monkeypatch.delenv(key, raising=False)
        with ThreadingHTTPServer(
            ("127.0.0.1", 0), curator.handler(
                "bg-unifr", audit, root, env_file, env_file,
            ),
        ) as dashboard:
            thread = threading.Thread(target=dashboard.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{dashboard.server_port}/"

                def post(values: dict[str, str]) -> str:
                    request = urllib.request.Request(
                        url, data=urllib.parse.urlencode({"token": token, **values}).encode(),
                    )
                    return urllib.request.urlopen(request, timeout=20).read().decode()

                first = urllib.request.urlopen(url, timeout=5).read().decode()
                token_match = re.search(r'name="token" value="([^"]+)"', first)
                assert token_match is not None
                token = token_match.group(1)
                assert "Press Refresh" in first
                page = post({"action": "refresh", "project": "bg-unifr"})
                assert ("4 records need resolution" if taxa_readable
                        else "4 records awaiting Taxa verification") in page
                assert "Lepechiria hastata" in page
                assert FakeDirectus.taxa == [] and FakeDirectus.patches == []
                taxon = urllib.request.urlopen(
                    url + "?project=bg-unifr&taxon=Lepechiria", timeout=5
                ).read().decode()
                assert taxon.count('<details class="group"') == 1
                detail = urllib.request.urlopen(
                    url + "?project=bg-unifr&view=all&row=12", timeout=5
                ).read().decode()
                assert "All Field_Data fields for sample-12" in detail
                assert "observation_notes" in detail and "wet rock" in detail
                assert detail.count('<details class="group"') == 1
                switched = post({"action": "filter", "project": "fung", "taxon": "Amanita"})
                assert "fung · current status" in switched
                assert "Amanita muscaria" in switched
                assert "fung-50" in switched
                assert "Lepechiria hastata" not in switched
                assert "1 records " in switched
                refreshed = post({"action": "refresh", "project": "fung", "taxon": "Amanita"})
                assert "fung · current status" in refreshed
                assert 'value="Amanita" list="taxon-names"' in refreshed
                assert "fung-50" in refreshed
                assert "Lepechiria hastata" not in refreshed
                restored = post({
                    "action": "filter", "project": "bg-unifr", "taxon": "Lepechiria",
                })
                assert restored.count('<details class="group"') == 1
                assert "fung-50" not in restored
                if taxa_readable:
                    preview = post({
                        "action": "upload_preview", "project": "bg-unifr",
                        "source_digest": upload_source(output),
                        "choice_0": "3T8YK", "choice_1": "6JGB",
                    })
                    assert "Finalized batch to push" in preview
                    assert "Lepechinia hastata · CoL 3T8YK" in preview
                    assert "Passiflora · CoL 6JGB" in preview
                    assert FakeDirectus.taxa == [] and FakeDirectus.patches == []
                    digest = re.search(r'name="preview_digest" value="([^"]+)"', preview)
                    assert digest is not None
                    complete = post({
                        "action": "upload_apply", "project": "bg-unifr",
                        "preview_digest": digest.group(1), "confirm": "bg-unifr",
                    })
                    assert "No entries require taxonomic resolution" in complete
                    assert '<details class="group"' not in complete
                    assert "Preview finalized batch" not in complete
                    assert len(FakeDirectus.patches) == 4
                    assert "No entries require taxonomic resolution" in post({
                        "action": "refresh", "project": "bg-unifr",
                    })
                else:
                    assert "denied Taxa read access" in page
                    assert "Preview finalized batch" not in page
                    blocked = post({
                        "action": "upload_preview", "project": "bg-unifr",
                        "source_digest": upload_source(output),
                        "choice_0": "3T8YK", "choice_1": "6JGB",
                    })
                    assert "cannot read Taxa" in blocked
                    assert FakeDirectus.taxa == [] and FakeDirectus.patches == []
            finally:
                dashboard.shutdown()
                thread.join(timeout=5)
        fake.shutdown()
        fake_thread.join(timeout=5)
