"""Behavioral checks for guarded project container curation."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


def _script() -> ModuleType:
    path = Path(__file__).parents[1] / "scripts/curate_project_containers.py"
    spec = importlib.util.spec_from_file_location("curate_project_containers", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeClient:
    writes: list[tuple[int, dict[str, Any]]] = []
    live_changed = False

    def __init__(self, settings: Any) -> None:
        self.containers = {
            101: {"id": 101, "container_id": "bg_001", "used": False},
            102: {"id": 102, "container_id": "bg_002", "used": False},
        }

    def _authenticate(self) -> None:
        pass

    def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        if path == "/fields/Containers":
            return {"data": [{"field": "used", "meta": {"readonly": False}}]}
        if path == "/items/Field_Data":
            assert params["filter[qfield_project][_eq]"] == "bg"
            return {"data": [{"id": 1, "sample_id": "bg_001"}, {"id": 2, "sample_id": "bg_002"}]}
        if path == "/items/Dried_Samples_Data":
            assert params["filter[field_data][qfield_project][_eq]"] == "bg"
            return {
                "data": [
                    {
                        "field_data": {"id": 1},
                        "sample_container": {"id": 101, "container_id": "bg_001"},
                    },
                ]
            }
        if path == "/items/Containers":
            if "filter[container_id][_in]" in params:
                assert set(params["filter[container_id][_in]"].split(",")) == {"bg_001", "bg_002"}
            else:
                assert set(params["filter[id][_in]"].split(",")) == {"101", "102"}
            return {"data": list(self.containers.values())}
        if path.startswith("/items/Containers/"):
            container = self.containers[int(path.rsplit("/", 1)[1])].copy()
            if self.live_changed:
                container["used"] = True
            return {"data": container}
        raise AssertionError(path)

    def _patch_item(self, *, collection: str, item_id: int, payload: dict[str, Any]) -> None:
        assert collection == "Containers"
        self.writes.append((item_id, payload.copy()))
        self.containers[item_id].update(payload)


def test_project_container_preview_and_commit(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    script = _script()
    FakeClient.writes = []
    FakeClient.live_changed = False
    monkeypatch.setattr(script, "load_settings", lambda env_file=None: object())
    monkeypatch.setattr(script, "DirectusClient", FakeClient)
    argv = ["--project", "bg", "--expect-count", "2", "--set", "used=true"]

    script.main(argv)
    preview_output = capsys.readouterr().out
    preview = json.loads(preview_output.split("\nDry run:")[0])
    assert [row["container_id"] for row in preview] == ["bg_001", "bg_002"]
    assert [row["selection_source"] for row in preview] == [
        "dried_sample_link",
        "sample_id_exact_container_id",
    ]
    assert [row["before"] for row in preview] == [{"used": False}, {"used": False}]
    assert [row["after"] for row in preview] == [{"used": True}, {"used": True}]
    assert "Dry run: 2 containers; no changes made." in preview_output
    assert FakeClient.writes == []

    backup = tmp_path / "original.json"
    script.main([*argv, "--commit", "--backup", str(backup)])
    assert FakeClient.writes == [(101, {"used": True}), (102, {"used": True})]
    saved = json.loads(backup.read_text(encoding="utf-8"))
    assert saved["project"] == "bg"
    assert [row["container_id"] for row in saved["rows"]] == ["bg_001", "bg_002"]
    assert [row["before"]["used"] for row in saved["rows"]] == [False, False]
    assert "Verified bg_002 (102)" in capsys.readouterr().out


def test_project_container_wrong_key_stops_before_any_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _script()
    FakeClient.writes = []

    class NoProjectClient(FakeClient):
        def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
            if path == "/items/Field_Data":
                return {"data": []}
            return super()._get(path, params)

    monkeypatch.setattr(script, "load_settings", lambda env_file=None: object())
    monkeypatch.setattr(script, "DirectusClient", NoProjectClient)
    with pytest.raises(ValueError, match="check the exact qfield_project key"):
        script.main(["--project", "bg", "--expect-count", "26", "--set", "used=true"])
    assert FakeClient.writes == []


def test_project_container_selection_requires_complete_one_to_one_links() -> None:
    script = _script()
    fields = [{"id": 1, "sample_id": "a"}, {"id": 2, "sample_id": "b"}]
    linked = [{"field_data": {"id": 1}, "sample_container": {"id": 10, "container_id": "a"}}]
    with pytest.raises(ValueError, match="Sample b: no linked or exact-code container"):
        script.select_containers(fields, linked, [], 2)
    with pytest.raises(ValueError, match="Expected 3 Field_Data rows, found 2"):
        script.select_containers(fields, linked, [], 3)
    with pytest.raises(ValueError, match="share a container"):
        script.select_containers(
            fields,
            [
                *linked,
                {"field_data": {"id": 2}, "sample_container": {"id": 10, "container_id": "a"}},
            ],
            [],
            2,
        )


def test_project_container_fallback_requires_unambiguous_exact_code() -> None:
    script = _script()
    fields = [{"id": 1, "sample_id": "bg_001"}]
    code_rows = [{"id": 101, "container_id": "bg_001"}]
    assert script.select_containers(fields, [], code_rows, 1) == [
        ("bg_001", 101, "bg_001", "sample_id_exact_container_id")
    ]
    with pytest.raises(ValueError, match="ambiguous original sample containers"):
        script.select_containers(fields, [], [*code_rows, {"id": 102, "container_id": "bg_001"}], 1)
    linked = [{"field_data": {"id": 1}, "sample_container": {"id": 200, "container_id": "x"}}]
    with pytest.raises(ValueError, match="dried link conflicts"):
        script.select_containers(fields, linked, code_rows, 1)


def test_project_container_commit_stops_on_changed_live_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    script = _script()
    FakeClient.writes = []
    FakeClient.live_changed = True
    monkeypatch.setattr(script, "load_settings", lambda env_file=None: object())
    monkeypatch.setattr(script, "DirectusClient", FakeClient)
    backup = tmp_path / "backup.json"
    with pytest.raises(ValueError, match="changed after preview"):
        script.main(
            [
                "--project",
                "bg",
                "--expect-count",
                "2",
                "--set",
                "used=true",
                "--commit",
                "--backup",
                str(backup),
            ]
        )
    assert FakeClient.writes == []
    assert backup.exists()
    FakeClient.live_changed = False


def test_project_container_rejects_identity_updates_before_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _script()
    FakeClient.writes = []
    monkeypatch.setattr(script, "load_settings", lambda env_file=None: object())
    monkeypatch.setattr(script, "DirectusClient", FakeClient)
    with pytest.raises(ValueError, match="identity Containers fields"):
        script.main(["--project", "bg", "--expect-count", "2", "--set", 'container_id="same"'])
    assert FakeClient.writes == []
