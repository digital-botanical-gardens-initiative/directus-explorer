"""Behavioral tests for sparse, reviewed CoL parent links."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from directus_explorer.taxon_curation import (
    COL_DATASET_DOI,
    COL_DATASET_KEY,
    COL_DATASET_VERSION,
    ColCurationError,
)
from directus_explorer.taxon_lineage import build_lineage, observed_col_ids, plan_lineage


def _load_script() -> ModuleType:
    path = Path(__file__).parents[1] / "scripts/enrich_taxa_lineage.py"
    spec = importlib.util.spec_from_file_location("enrich_taxa_lineage", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lineage_script = _load_script()


class Col:
    dataset_key = COL_DATASET_KEY

    def __init__(self, usages: dict[str, dict[str, Any]]) -> None:
        self.usages = usages
        self.calls: list[str] = []

    def validate_pinned_dataset(self) -> None:
        pass

    def _get_json(self, path: str) -> dict[str, Any]:
        col_id = path.rsplit("/", 1)[-1]
        self.calls.append(col_id)
        return self.usages[col_id]


def usage(col_id: str, name: str, rank: str, parent: str | None) -> dict[str, Any]:
    return {"id": col_id, "status": "accepted", "parentId": parent,
            "name": {"scientificName": name, "rank": rank}}


def taxa_row(
    col_id: str, name: str, rank: str, row_id: int, parent: int | None = None
) -> dict[str, Any]:
    return {"id": row_id, "col_id": col_id, "scientific_name": name,
            "authorship": "", "rank": rank, "backbone_version": COL_DATASET_VERSION,
            "backbone_dataset_key": COL_DATASET_KEY, "backbone_doi": COL_DATASET_DOI,
            "parent_ref": parent}


class Directus:
    def __init__(self, taxa: list[dict[str, Any]], fields: list[dict[str, Any]]) -> None:
        self.taxa = copy.deepcopy(taxa)
        self.fields = copy.deepcopy(fields)
        self.writes: list[tuple[str, str]] = []

    def _get_items(self, collection: str, fields: str) -> list[dict[str, Any]]:
        return copy.deepcopy(self.taxa if collection == "Taxa" else self.fields)

    def _post_item(self, collection: str, payload: dict[str, Any]) -> dict[str, Any]:
        assert collection == "Taxa"
        assert payload["parent_ref"] is None or any(
            row["id"] == payload["parent_ref"] for row in self.taxa
        )
        row = {"id": max(row["id"] for row in self.taxa) + 1, **payload}
        self.taxa.append(row)
        self.writes.append(("create", row["col_id"]))
        return copy.deepcopy(row)

    def _patch_item(
        self, *, collection: str, item_id: int, payload: dict[str, Any]
    ) -> dict[str, Any]:
        assert collection == "Taxa" and set(payload) == {"parent_ref"}
        row = next(row for row in self.taxa if row["id"] == item_id)
        row.update(payload)
        self.writes.append(("link", row["col_id"]))
        return copy.deepcopy(row)


def test_observed_lineage_shared_ancestors_and_parent_first_commit(tmp_path: Path) -> None:
    col = Col({
        "S1": usage("S1", "Bellis perennis", "species", "G"),
        "S2": usage("S2", "Bellis annua", "species", "G"),
        "G": usage("G", "Bellis", "genus", "F"),
        "F": usage("F", "Asteraceae", "family", "K"),
        "K": usage("K", "Plantae", "kingdom", None),
    })
    taxa = [taxa_row("S1", "Bellis perennis", "species", 1),
            taxa_row("S2", "Bellis annua", "species", 2)]
    fields = [
        {"id": 11, "taxon_ref": 1, "taxon_col_id": "S1",
         "taxon_backbone_version": COL_DATASET_VERSION},
        {"id": 12, "taxon_ref": 2, "taxon_col_id": "S2",
         "taxon_backbone_version": COL_DATASET_VERSION},
        {"id": 13, "taxon_ref": None, "taxon_col_id": None},
    ]
    directus = Directus(taxa, fields)
    proposed = lineage_script.snapshot(directus, col)  # type: ignore[arg-type]
    assert proposed["observed_col_ids"] == ["S1", "S2"]
    assert [row["col_id"] for row in proposed["lineage"]] == ["K", "F", "G", "S1", "S2"]
    assert sorted(col.calls) == ["F", "G", "K", "S1", "S2"]
    assert proposed["missing_col_ids"] == ["K", "F", "G"]
    assert len(proposed["new_parent_links"]) == 2

    journal = tmp_path / "executed.jsonl"
    lineage_script.apply(directus, proposed, journal)  # type: ignore[arg-type]
    assert directus.writes == [
        ("create", "K"), ("create", "F"), ("create", "G"),
        ("link", "S1"), ("link", "S2"),
    ]
    by_id = {row["id"]: row for row in directus.taxa}
    assert [by_id[by_id[by_id[1]["parent_ref"]]["parent_ref"]]["col_id"],
            by_id[by_id[2]["parent_ref"]]["col_id"]] == ["F", "G"]
    assert next(row for row in directus.taxa if row["col_id"] == "K")["parent_ref"] is None
    assert directus.fields == fields
    assert len(journal.read_text().splitlines()) == 5
    assert all(json.loads(line)["action"] in {"create", "link"}
               for line in journal.read_text().splitlines())
    assert plan_lineage(proposed["lineage"], directus.taxa) == ([], [], [])


def test_rejects_cycle_missing_usage_and_nonaccepted_usage() -> None:
    col = Col({"A": usage("A", "A", "species", "B"),
               "B": usage("B", "B", "genus", "A")})
    with pytest.raises(ValueError, match="Cycle"):
        build_lineage(col, ["A"])  # type: ignore[arg-type]
    col.usages["B"]["status"] = "synonym"
    with pytest.raises(ColCurationError, match="not an accepted taxon concept"):
        build_lineage(col, ["A"])  # type: ignore[arg-type]
    del col.usages["B"]["name"]
    with pytest.raises(ColCurationError, match="no scientific name"):
        build_lineage(col, ["A"])  # type: ignore[arg-type]


def test_provisionally_accepted_usage_keeps_status_for_review() -> None:
    col = Col({"P": usage("P", "Erigeron glabratus", "species", None)})
    col.usages["P"]["status"] = "provisionally accepted"
    rows = build_lineage(col, ["P"])  # type: ignore[arg-type]
    assert rows[0]["col_status"] == "provisionally accepted"


def test_existing_parent_and_release_conflicts_block_writes() -> None:
    col = Col({"S": usage("S", "Bellis perennis", "species", "G"),
               "G": usage("G", "Bellis", "genus", None)})
    lineage = build_lineage(col, ["S"])  # type: ignore[arg-type]
    existing = [taxa_row("S", "Bellis perennis", "species", 1, parent=3),
                taxa_row("G", "Bellis", "genus", 2),
                taxa_row("X", "Wrong", "genus", 3)]
    _, _, conflicts = plan_lineage(lineage, existing)
    assert conflicts == [{"col_id": "S", "reason": "parent_mismatch:X!=G"}]
    existing[0]["parent_ref"] = None
    existing[0]["backbone_version"] = "older"
    _, _, conflicts = plan_lineage(lineage, existing)
    assert conflicts[0]["reason"] == "metadata_mismatch"
    with pytest.raises(ValueError, match="another CoL release"):
        observed_col_ids([{"id": 11, "taxon_ref": 1, "taxon_col_id": "S",
                           "taxon_backbone_version": "older"}], existing)
    with pytest.raises(ValueError, match="conflicting CoL ID"):
        observed_col_ids([{"id": 11, "taxon_ref": 1, "taxon_col_id": "X"}], existing)


def test_commit_rejects_changed_preview_before_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    col = Col({
        "S": usage("S", "Bellis perennis", "species", "G"),
        "G": usage("G", "Bellis", "genus", None),
    })
    directus = Directus(
        [taxa_row("S", "Bellis perennis", "species", 1)],
        [{"id": 11, "taxon_ref": 1, "taxon_col_id": "S",
          "taxon_backbone_version": COL_DATASET_VERSION}],
    )
    directus._authenticate = lambda: None  # type: ignore[method-assign]
    directus._get = lambda path, params: {"data": (  # type: ignore[method-assign]
        [{"field": "parent_ref"}] if path.startswith("/fields/")
        else [{"field": "parent_ref", "related_collection": "Taxa"}]
    )}
    monkeypatch.setattr(lineage_script, "DirectusClient", lambda _: directus)
    monkeypatch.setattr(lineage_script, "ChecklistBankColClient", lambda: col)
    monkeypatch.setattr(lineage_script, "load_settings", lambda **_: None)
    monkeypatch.setattr(
        "sys.argv", ["enrich_taxa_lineage", "--output-dir", str(tmp_path)]
    )
    lineage_script.main()
    preview = next(tmp_path.glob("lineage_preview_*.json"))
    assert not directus.writes

    directus.fields[0]["taxon_ref"] = None
    monkeypatch.setattr(
        "sys.argv", ["enrich_taxa_lineage", "--output-dir", str(tmp_path),
                     "--preview", str(preview), "--commit"]
    )
    with pytest.raises(ValueError, match="differs from current"):
        lineage_script.main()
    assert not directus.writes


def test_validated_usage_cache_resumes_without_refetching(tmp_path: Path) -> None:
    col = Col({
        "S": usage("S", "Bellis perennis", "species", "G"),
        "G": usage("G", "Bellis", "genus", None),
    })
    cache = tmp_path / "pinned.sqlite"
    first = build_lineage(col, ["S"], cache_path=cache)  # type: ignore[arg-type]
    assert col.calls == ["S", "G"]
    col.calls.clear()
    second = build_lineage(col, ["S"], cache_path=cache)  # type: ignore[arg-type]
    assert first == second and col.calls == []

    col.usages["T"] = usage("T", "Bellis annua", "species", "G")
    build_lineage(col, ["S", "T"], cache_path=cache)  # type: ignore[arg-type]
    assert col.calls == ["T"]
