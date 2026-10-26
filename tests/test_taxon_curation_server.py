"""Behavioral checks for the local browser curation workflow."""

from __future__ import annotations

import csv
import hashlib
import re
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from directus_explorer.interactive_taxon_curation import handler, main, read_tsv, save_review
from directus_explorer.taxon_curation_manifest import build_taxon_update_manifest


def _tsv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def test_browser_decisions_prepare_one_group_and_skip_another(tmp_path: Path) -> None:
    review = tmp_path / "review.tsv"
    audit = tmp_path / "audit.tsv"
    _tsv(audit, [
        {"field_data_id": "10", "stored_taxon_name_unified": ""},
        {"field_data_id": "11", "stored_taxon_name_unified": ""},
    ])
    _tsv(review, [
        {
            "review_id": review_id, "project": "bg-unifr", "round": "1",
            "field_data_ids": field_id, "sample_ids": f"sample-{field_id}",
            "proposal_source": "taxon_name_final", "proposal_verbatim_name": name,
            "col_accepted_name": name, "col_accepted_usage_id": col_id,
            "col_accepted_rank": "species", "decision": "", "confidence": "",
            "chosen_col_accepted_name": "Unrelated override",
            "chosen_col_accepted_usage_id": "OTHER",
            "chosen_col_accepted_rank": "genus", "reviewed_at": "", "review_note": "",
        }
        for review_id, field_id, name, col_id in (
            ("alpha", "10", "Drosera capensis", "37QPF"),
            ("beta", "11", "Salvia leucantha", "79GNS"),
        )
    ])
    digest = hashlib.sha256(review.read_bytes()).hexdigest()
    form = {
        "digest": digest,
        "row_alpha_accept": "1",
        "row_beta_decision": "defer",
    }
    assert save_review(review, form, audit) == 2
    rows = read_tsv(review)
    manifest, skipped = build_taxon_update_manifest(
        rows, {row["field_data_id"]: row for row in read_tsv(audit)}
    )
    assert [(row["field_data_id"], row["new_taxon_name_unified"]) for row in manifest] == [
        ("10", "Drosera capensis")
    ]
    assert [(row["field_data_id"], row["decision"]) for row in skipped] == [
        ("11", "defer")
    ]
    with pytest.raises(ValueError, match="Review changed"):
        save_review(review, form, audit)


def test_http_filters_and_preview_require_token_and_refresh(tmp_path: Path) -> None:
    audit = tmp_path / "audit.tsv"
    _tsv(audit, [
        {"project": "bg-unifr", "field_data_id": "10",
         "stored_taxon_name_unified": "Drosera capensis"},
        {"project": "other-project", "field_data_id": "11",
         "stored_taxon_name_unified": "Salvia"},
    ])
    with ThreadingHTTPServer(
        ("127.0.0.1", 0),
        handler("bg-unifr", audit, tmp_path, tmp_path / "env", tmp_path / "admin-env"),
    ) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/"
            page = urllib.request.urlopen(url, timeout=5).read().decode()
            token = re.search(r'name="token" value="([^"]+)"', page)
            assert token is not None
            assert "Refresh from Directus" in page
            assert "other-project" in page
            assert "generate_taxon_curation_review" not in page
            values = {"action": "upload_preview", "project": "bg-unifr"}
            with pytest.raises(urllib.error.HTTPError) as unauthorized:
                urllib.request.urlopen(urllib.request.Request(
                    url, data=urllib.parse.urlencode(values).encode()
                ), timeout=5)
            assert unauthorized.value.code == 403
            values["token"] = token.group(1)
            response = urllib.request.urlopen(urllib.request.Request(
                url, data=urllib.parse.urlencode(values).encode()
            ), timeout=5).read().decode()
            assert "Press Refresh before previewing" in response
            other = urllib.request.urlopen(
                url + "?project=other-project", timeout=5
            ).read().decode()
            assert 'value="other-project" selected' in other
            with pytest.raises(urllib.error.HTTPError) as unknown:
                urllib.request.urlopen(url + "?project=unknown-project", timeout=5)
            assert unknown.value.code == 400
        finally:
            server.shutdown()
            thread.join(timeout=5)


def test_dashboard_reports_missing_audit_and_admin_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    for key in ("DIRECTUS_INSTANCE", "DIRECTUS_USERNAME", "DIRECTUS_PASSWORD"):
        monkeypatch.delenv(key, raising=False)
    audit_env = tmp_path / "missing-audit.env"
    admin_env = tmp_path / "missing-admin.env"
    monkeypatch.setattr(sys, "argv", [
        "serve_taxon_curation.py", "--project", "fung",
        "--env-file", str(audit_env), "--admin-env-file", str(admin_env),
    ])
    with pytest.raises(SystemExit) as stopped:
        main()
    assert stopped.value.code == 2
    message = capsys.readouterr().err
    assert f"--env-file {audit_env} (audit; file does not exist)" in message
    admin_error = (
        f"--admin-env-file {admin_env} "
        "(Taxa verification and writes; file does not exist)"
    )
    assert admin_error in message
    assert "do not use a literal placeholder path" in message

    with ThreadingHTTPServer(
        ("127.0.0.1", 0),
        handler("fung", tmp_path / "audit.tsv", tmp_path, audit_env, admin_env),
    ) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/"
            page = urllib.request.urlopen(url, timeout=5).read().decode()
            token = re.search(r'name="token" value="([^"]+)"', page)
            assert token is not None
            request = urllib.request.Request(url, data=urllib.parse.urlencode({
                "token": token.group(1), "project": "fung", "action": "refresh",
            }).encode())
            result = urllib.request.urlopen(request, timeout=5).read().decode()
            assert f"--env-file {audit_env} (audit; file does not exist)" in result
            assert admin_error in result
            assert "Press Refresh to load" in result
        finally:
            server.shutdown()
            thread.join(timeout=5)
