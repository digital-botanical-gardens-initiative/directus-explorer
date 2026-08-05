"""Tests for guarded taxon update input validation."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


def _load_script() -> ModuleType:
    path = Path(__file__).parents[1] / "scripts/apply_taxon_curation_directus_update.py"
    spec = importlib.util.spec_from_file_location("taxon_apply_script", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _preview() -> dict[str, str]:
    return {
        "field_data_id": "42",
        "project": "jbp",
        "decision": "accept_proposal",
        "preflight_status": "ready",
        "current_taxon_name_unified": "",
        "new_taxon_name_unified": "Erica arborea",
    }


def _backup() -> dict[str, str]:
    return {
        "field_data_id": "42",
        "target_field": "taxon_name_unified",
        "old_value": "",
        "proposed_new_value": "Erica arborea",
    }


def test_validate_inputs_accepts_matching_preview_and_backup() -> None:
    script = _load_script()

    script._validate_inputs([_preview()], [_backup()], expected_project="jbp")


def test_validate_inputs_rejects_mismatched_backup() -> None:
    script = _load_script()
    backup = _backup()
    backup["proposed_new_value"] = "Phoenix dactylifera"

    with pytest.raises(ValueError, match="Backup proposed value differs"):
        script._validate_inputs([_preview()], [backup], expected_project="jbp")


def test_validate_inputs_accepts_reviewed_override() -> None:
    script = _load_script()
    preview = _preview()
    preview["decision"] = "override"

    script._validate_inputs([preview], [_backup()], expected_project="jbp")


def test_validate_inputs_accepts_placeholder_clear() -> None:
    script = _load_script()
    preview = _preview()
    preview.update(
        {
            "decision": "clear_placeholder",
            "current_taxon_name_unified": "aaunknown",
            "new_taxon_name_unified": "",
            "update_action": "clear_value",
        }
    )
    backup = _backup()
    backup.update({"old_value": "aaunknown", "proposed_new_value": ""})

    script._validate_inputs([preview], [backup], expected_project="jbp")


def test_resolve_input_paths_discovers_matching_latest_pair(tmp_path: Path) -> None:
    script = _load_script()
    round_dir = tmp_path / "col_release/jbuf_round_001"
    round_dir.mkdir(parents=True)
    preview = round_dir / "jbuf_round_001_write_preview_20260803T131322Z.tsv"
    backup = round_dir / "jbuf_round_001_directus_backup_20260803T131322Z.tsv"
    preview.touch()
    backup.touch()

    selected = script._resolve_input_paths(
        preview=None,
        backup=None,
        latest=True,
        project="jbuf",
        curation_root=tmp_path,
    )

    assert selected == (preview, backup)
