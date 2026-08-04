"""Tests for pinned Catalogue of Life taxon curation helpers."""

from __future__ import annotations

from typing import Any

from directus_explorer.taxon_curation import ChecklistBankColClient


class _Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict[str, Any]:
        return self.payload


class _Session:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.headers: dict[str, str] = {}
        self.responses = responses
        self.calls: list[str] = []

    def get(
        self, url: str, *, params: dict[str, str | int] | None, timeout: float
    ) -> _Response:
        self.calls.append(url)
        return _Response(self.responses.pop(0))


def test_resolve_synonym_returns_accepted_usage() -> None:
    session = _Session(
        [
            {
                "scientificName": "Consolida regalis",
                "rank": "species",
                "parsed": True,
            },
            {
                "match": True,
                "type": "variant",
                "id": "XV87",
                "usage": {"id": "XV87", "name": "Consolida regalis"},
            },
            {
                "id": "XV87",
                "name": {
                    "scientificName": "Consolida regalis",
                    "authorship": "Gray",
                    "rank": "species",
                },
                "status": "synonym",
                "accepted": {
                    "id": "5GTXB",
                    "name": {
                        "scientificName": "Delphinium consolida subsp. consolida",
                        "rank": "subspecies",
                    },
                    "status": "accepted",
                },
            },
        ]
    )
    client = ChecklistBankColClient(session=session)  # type: ignore[arg-type]

    result = client.resolve_name("Consolida regalis True Wild")

    assert result.matched_usage_id == "XV87"
    assert result.matched_status == "synonym"
    assert result.accepted_usage_id == "5GTXB"
    assert result.accepted_name == "Delphinium consolida subsp. consolida"
    assert result.is_usable_match


def test_higher_rank_fallback_is_rejected_and_gets_plant_fuzzy_candidates() -> None:
    session = _Session(
        [
            {"scientificName": "Artemissia allba", "rank": "species", "parsed": True},
            {
                "match": True,
                "type": "higherrank",
                "id": "P",
                "usage": {"id": "P", "name": "Plantae", "rank": "kingdom"},
            },
            {
                "result": [
                    {
                        "id": "animal",
                        "classification": [{"rank": "kingdom", "name": "Animalia"}],
                        "usage": {
                            "id": "animal",
                            "name": {"scientificName": "Artemia salina"},
                            "status": "accepted",
                        },
                    },
                    {
                        "id": "67SJZ",
                        "classification": [{"rank": "kingdom", "name": "Plantae"}],
                        "usage": {
                            "id": "67SJZ",
                            "name": {
                                "scientificName": "Artemisia alba",
                                "authorship": "Turra",
                                "rank": "species",
                            },
                            "status": "accepted",
                        },
                    },
                ]
            },
        ]
    )
    client = ChecklistBankColClient(session=session)  # type: ignore[arg-type]

    result = client.resolve_name("Artemissia allba")

    assert not result.match
    assert result.match_type == "higherrank"
    assert result.accepted_usage_id == ""
    assert '"id":"67SJZ"' in result.fuzzy_candidates
    assert "animal" not in result.fuzzy_candidates
