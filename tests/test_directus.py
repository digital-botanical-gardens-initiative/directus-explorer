"""Tests for the Directus client."""

from __future__ import annotations

from typing import Any

import pytest

from directus_explorer.config import Settings
from directus_explorer.directus import DirectusAuthError, DirectusClient, DirectusResponseError


class FakeResponse:
    """Small response stub for requests-based tests."""

    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        """Return the configured payload."""

        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def test_count_field_samples_returns_zero_when_no_rows_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A zero-count aggregate response should return zero."""

    settings = Settings(
        directus_instance="https://example.test/directus",
        directus_username="user@example.test",
        directus_password="secret",
    )
    client = DirectusClient(settings)

    def fake_post(*args: Any, **kwargs: Any) -> FakeResponse:
        return FakeResponse(200, {"data": {"access_token": "token"}})

    def fake_get(*args: Any, **kwargs: Any) -> FakeResponse:
        return FakeResponse(200, {"data": [{"count": {"id": "0"}}]})

    monkeypatch.setattr(client._session, "post", fake_post)
    monkeypatch.setattr(client._session, "get", fake_get)

    assert client.count_field_samples("jbuf") == 0


def test_count_field_samples_raises_on_login_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Authentication errors should be surfaced clearly."""

    settings = Settings(
        directus_instance="https://example.test/directus",
        directus_username="user@example.test",
        directus_password="secret",
    )
    client = DirectusClient(settings)

    monkeypatch.setattr(client._session, "post", lambda *args, **kwargs: FakeResponse(401, {}))

    with pytest.raises(DirectusAuthError, match="status 401"):
        client.count_field_samples("jbuf")


def test_count_field_samples_raises_on_malformed_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    """Malformed aggregate payloads should raise a response error."""

    settings = Settings(
        directus_instance="https://example.test/directus",
        directus_username="user@example.test",
        directus_password="secret",
    )
    client = DirectusClient(settings)

    def fake_post(*args: Any, **kwargs: Any) -> FakeResponse:
        return FakeResponse(200, {"data": {"access_token": "token"}})

    def fake_get(*args: Any, **kwargs: Any) -> FakeResponse:
        return FakeResponse(200, {"data": [{"count": {}}]})

    monkeypatch.setattr(client._session, "post", fake_post)
    monkeypatch.setattr(client._session, "get", fake_get)

    with pytest.raises(DirectusResponseError, match="count.id"):
        client.count_field_samples("jbuf")


def test_list_projects_returns_sorted_project_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Project listing should return sorted distinct qfield project values."""

    settings = Settings(
        directus_instance="https://example.test/directus",
        directus_username="user@example.test",
        directus_password="secret",
    )
    client = DirectusClient(settings)

    def fake_post(*args: Any, **kwargs: Any) -> FakeResponse:
        return FakeResponse(200, {"data": {"access_token": "token"}})

    def fake_get(url: str, params: Any = None, *args: Any, **kwargs: Any) -> FakeResponse:
        if url.endswith("/items/Field_Data") and params == {
            "limit": "-1",
            "groupBy[]": "qfield_project",
            "aggregate[count]": "id",
        }:
            return FakeResponse(
                200,
                {
                    "data": [
                        {"qfield_project": "jbuf", "count": {"id": "10"}},
                        {"qfield_project": "artemisia", "count": {"id": "5"}},
                    ]
                },
            )
        raise AssertionError(f"Unexpected URL or params: {url!r} {params!r}")

    monkeypatch.setattr(client._session, "post", fake_post)
    monkeypatch.setattr(client._session, "get", fake_get)

    assert client.list_projects() == ["artemisia", "jbuf"]


def test_list_profiled_samples_resolves_both_modes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Profiled samples should resolve through aliquot and extraction lineage."""

    settings = Settings(
        directus_instance="https://example.test/directus",
        directus_username="user@example.test",
        directus_password="secret",
    )
    client = DirectusClient(settings)

    def fake_post(*args: Any, **kwargs: Any) -> FakeResponse:
        return FakeResponse(200, {"data": {"access_token": "token"}})

    responses = {
        "MS_Data": {
            "data": [
                {
                    "parent_sample_container": {"container_id": "dbgi_001195_01_01"},
                    "injection_method": {"method_name": "method_pos"},
                },
                {
                    "parent_sample_container": {"container_id": "dbgi_001195_01_01"},
                    "injection_method": {"method_name": "method_neg"},
                },
            ]
        },
        "Aliquoting_Data": {
            "data": [
                {
                    "sample_container": {"container_id": "dbgi_001195_01_01"},
                    "parent_sample_container": {"container_id": "dbgi_001195_01"},
                }
            ]
        },
        "Extraction_Data": {
            "data": [
                {
                    "sample_container": {"container_id": "dbgi_001195_01"},
                    "parent_sample_container": {"container_id": "dbgi_001195"},
                }
            ]
        },
        "Dried_Samples_Data": {
            "data": [
                {
                    "sample_container": {"container_id": "dbgi_001195"},
                    "field_data": {"sample_id": "dbgi_001195", "qfield_project": "jbuf"},
                }
            ]
        },
    }

    def fake_get(url: str, *args: Any, **kwargs: Any) -> FakeResponse:
        for collection, payload in responses.items():
            if f"/items/{collection}" in url:
                return FakeResponse(200, payload)
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(client._session, "post", fake_post)
    monkeypatch.setattr(client._session, "get", fake_get)

    profiled = client.list_profiled_samples(mode="both")

    assert [(sample.sample_id, sample.qfield_project, sample.mode) for sample in profiled] == [
        ("dbgi_001195", "jbuf", "both")
    ]


def test_summarize_samples_by_project_aggregates_collected_and_profiled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Project summary should combine collected totals with resolved profiled counts."""

    settings = Settings(
        directus_instance="https://example.test/directus",
        directus_username="user@example.test",
        directus_password="secret",
    )
    client = DirectusClient(settings)

    def fake_post(*args: Any, **kwargs: Any) -> FakeResponse:
        return FakeResponse(200, {"data": {"access_token": "token"}})

    def fake_get(url: str, params: Any = None, *args: Any, **kwargs: Any) -> FakeResponse:
        if url.endswith("/items/Field_Data") and params == {
            "limit": "-1",
            "groupBy[]": "qfield_project",
            "aggregate[count]": "id",
        }:
            return FakeResponse(
                200,
                {
                    "data": [
                        {"qfield_project": "jbuf", "count": {"id": "10"}},
                        {"qfield_project": "fibl", "count": {"id": "5"}},
                    ]
                },
            )
        if "/items/MS_Data" in url:
            return FakeResponse(
                200,
                {
                    "data": [
                        {
                            "parent_sample_container": {"container_id": "dbgi_001195_01_01"},
                            "injection_method": {"method_name": "method_pos"},
                        },
                        {
                            "parent_sample_container": {"container_id": "dbgi_001195_01_01"},
                            "injection_method": {"method_name": "method_neg"},
                        },
                        {
                            "parent_sample_container": {"container_id": "fibl_000001_01_01"},
                            "injection_method": {"method_name": "method_pos"},
                        },
                    ]
                },
            )
        if "/items/Aliquoting_Data" in url:
            return FakeResponse(
                200,
                {
                    "data": [
                        {
                            "sample_container": {"container_id": "dbgi_001195_01_01"},
                            "parent_sample_container": {"container_id": "dbgi_001195_01"},
                        },
                        {
                            "sample_container": {"container_id": "fibl_000001_01_01"},
                            "parent_sample_container": {"container_id": "fibl_000001_01"},
                        },
                    ]
                },
            )
        if "/items/Extraction_Data" in url:
            return FakeResponse(
                200,
                {
                    "data": [
                        {
                            "sample_container": {"container_id": "dbgi_001195_01"},
                            "parent_sample_container": {"container_id": "dbgi_001195"},
                        },
                        {
                            "sample_container": {"container_id": "fibl_000001_01"},
                            "parent_sample_container": {"container_id": "fibl_000001"},
                        },
                    ]
                },
            )
        if "/items/Dried_Samples_Data" in url:
            return FakeResponse(
                200,
                {
                    "data": [
                        {
                            "sample_container": {"container_id": "dbgi_001195"},
                            "field_data": {"sample_id": "dbgi_001195", "qfield_project": "jbuf"},
                        },
                        {
                            "sample_container": {"container_id": "fibl_000001"},
                            "field_data": {"sample_id": "fibl_000001", "qfield_project": "fibl"},
                        },
                    ]
                },
            )
        raise AssertionError(f"Unexpected URL or params: {url!r} {params!r}")

    monkeypatch.setattr(client._session, "post", fake_post)
    monkeypatch.setattr(client._session, "get", fake_get)

    summary = client.summarize_samples_by_project()

    assert [
        (
            item.qfield_project,
            item.collected_count,
            item.profiled_count,
            item.positive_count,
            item.negative_count,
            item.both_count,
        )
        for item in summary
    ] == [
        ("fibl", 5, 1, 1, 0, 0),
        ("jbuf", 10, 1, 0, 0, 1),
    ]
