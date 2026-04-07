"""Tests for the Directus client."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import pytest

from directus_explorer.config import Settings
from directus_explorer.directus import DirectusAuthError, DirectusClient, DirectusResponseError
from directus_explorer.ms_metadata import MsMetadataTable


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


def test_build_ms_metadata_table_flattens_linked_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MS metadata export should flatten the resolved lineage into one row."""

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
                    "id": 1,
                    "status": "OK",
                    "filename": "20240307_EB_dbgi_001195_01_01",
                    "injection_volume": 2,
                    "converted": True,
                    "processed": False,
                    "injection_volume_unit": {"symbol": "uL"},
                    "injection_method": {
                        "id": 1,
                        "method_name": "100mm_C18_15min_DDA_ELON_pos",
                        "method_description": None,
                    },
                    "instrument_used": {
                        "id": 1,
                        "instrument_id": "inst_000001",
                        "instrument_model": {"instrument_model": "LC-MS QExactive Plus"},
                        "instrument_location": {"room_name": "0.308"},
                    },
                    "batch": {"batch_id": "batch_ms_001", "batch_type": {"batch_type": "ms"}},
                    "parent_sample_container": {
                        "id": 7310,
                        "container_id": "dbgi_001195_01_01",
                        "container_model": {"volume": 1.5, "is_sample_container": True},
                    },
                }
            ]
        },
        "Aliquoting_Data": {
            "data": [
                {
                    "id": 10,
                    "status": "OK",
                    "uuid_aliquot": "aliquot-uuid",
                    "aliquot_volume": 120,
                    "aliquot_volume_unit": {"symbol": "uL"},
                    "sample_container": {"id": 7310, "container_id": "dbgi_001195_01_01"},
                    "parent_sample_container": {
                        "id": 6365,
                        "container_id": "dbgi_001195_01",
                    },
                    "parent_container": {"id": 6001, "container_id": "container_000184"},
                }
            ]
        },
        "Extraction_Data": {
            "data": [
                {
                    "id": 20,
                    "status": "OK",
                    "uuid_extraction": "extraction-uuid",
                    "dried_weight": 51,
                    "dried_weight_unit": {"symbol": "mg"},
                    "solvent_volume": 1700,
                    "solvent_volume_unit": {"symbol": "uL"},
                    "sample_container": {"id": 6365, "container_id": "dbgi_001195_01"},
                    "parent_sample_container": {"id": 1225, "container_id": "dbgi_001195"},
                    "parent_container": {"id": 1, "container_id": "container_000001"},
                    "extraction_method": {"method_name": "MeOH/H2O"},
                    "batch": {
                        "batch_id": "batch_ext_001",
                        "batch_type": {"batch_type": "extraction"},
                    },
                    "extraction_container": {"id": 5, "volume": 1.5},
                }
            ]
        },
        "Dried_Samples_Data": {
            "data": [
                {
                    "id": 30,
                    "status": "OK",
                    "uuid_dried_sample": "dried-uuid",
                    "sample_container": {"id": 1225, "container_id": "dbgi_001195"},
                    "parent_container": {"id": 7, "container_id": "container_000007"},
                    "batch": {"batch_id": "batch_dried_001"},
                    "field_data": {
                        "id": 40,
                        "sample_id": "dbgi_001195",
                        "qfield_project": "jbuf",
                        "sample_name": "Sample Name",
                        "temperature_°C": 20.5,
                        "geometry": {"type": "Point", "coordinates": [7.1, 46.7]},
                    },
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

    table = client.build_ms_metadata_table()

    assert len(table.rows) == 1
    row = table.rows[0]
    assert row["ms_data_id"] == 1
    assert row["profile_mode"] == "positive"
    assert row["ms_filename"] == "20240307_EB_dbgi_001195_01_01"
    assert row["ms_injection_method_method_name"] == "100mm_C18_15min_DDA_ELON_pos"
    assert row["ms_instrument_instrument_id"] == "inst_000001"
    assert row["ms_instrument_model_instrument_model"] == "LC-MS QExactive Plus"
    assert row["aliquoting_data_id"] == 10
    assert row["extraction_data_id"] == 20
    assert row["dried_samples_data_id"] == 30
    assert row["original_sample_container_id"] == "dbgi_001195"
    assert row["original_sample_id"] == "dbgi_001195"
    assert row["qfield_project"] == "jbuf"
    assert row["field_sample_name"] == "Sample Name"
    assert row["field_temperature_c"] == 20.5
    assert row["field_geometry_coordinates"] == "[7.1, 46.7]"


def test_build_ms_metadata_table_filters_by_project(monkeypatch: pytest.MonkeyPatch) -> None:
    """Project filters should keep only matching MS rows in the export table."""

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
                    "id": 1,
                    "injection_method": {"method_name": "method_pos"},
                    "parent_sample_container": {"container_id": "dbgi_001195_01_01"},
                },
                {
                    "id": 2,
                    "injection_method": {"method_name": "method_neg"},
                    "parent_sample_container": {"container_id": "fibl_000001_01_01"},
                },
            ]
        },
        "Aliquoting_Data": {
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
        "Extraction_Data": {
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
        "Dried_Samples_Data": {
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
    }

    def fake_get(url: str, *args: Any, **kwargs: Any) -> FakeResponse:
        for collection, payload in responses.items():
            if f"/items/{collection}" in url:
                return FakeResponse(200, payload)
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(client._session, "post", fake_post)
    monkeypatch.setattr(client._session, "get", fake_get)

    table = client.build_ms_metadata_table(project="jbuf")

    assert [row["ms_data_id"] for row in table.rows] == [1]


def test_export_ms_metadata_csv_writes_flat_csv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The CSV export should write headers and rows to the requested path."""

    settings = Settings(
        directus_instance="https://example.test/directus",
        directus_username="user@example.test",
        directus_password="secret",
    )
    client = DirectusClient(settings)

    monkeypatch.setattr(
        client,
        "build_ms_metadata_table",
        lambda project=None: MsMetadataTable(
            fieldnames=("ms_data_id", "qfield_project"),
            rows=({"ms_data_id": 1, "qfield_project": "jbuf"},),
        ),
    )

    output_path = tmp_path / "metadata.csv"
    row_count = client.export_ms_metadata_csv(output_path=output_path, project="jbuf")

    assert row_count == 1
    with output_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows == [{"ms_data_id": "1", "qfield_project": "jbuf"}]


def test_build_ms_metadata_table_rejects_duplicate_lineage_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Duplicate child container ids should fail loudly instead of silently overwriting."""

    settings = Settings(
        directus_instance="https://example.test/directus",
        directus_username="user@example.test",
        directus_password="secret",
    )
    client = DirectusClient(settings)

    def fake_post(*args: Any, **kwargs: Any) -> FakeResponse:
        return FakeResponse(200, {"data": {"access_token": "token"}})

    responses = {
        "MS_Data": {"data": []},
        "Aliquoting_Data": {
            "data": [
                {"sample_container": {"container_id": "dup"}},
                {"sample_container": {"container_id": "dup"}},
            ]
        },
        "Extraction_Data": {"data": []},
        "Dried_Samples_Data": {"data": []},
    }

    def fake_get(url: str, *args: Any, **kwargs: Any) -> FakeResponse:
        for collection, payload in responses.items():
            if f"/items/{collection}" in url:
                return FakeResponse(200, payload)
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(client._session, "post", fake_post)
    monkeypatch.setattr(client._session, "get", fake_get)

    with pytest.raises(DirectusResponseError, match="duplicate rows"):
        client.build_ms_metadata_table()


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
