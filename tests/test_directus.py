"""Tests for the Directus client."""

from __future__ import annotations

import csv
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import pytest

from directus_explorer.config import Settings
from directus_explorer.directus import DirectusAuthError, DirectusClient, DirectusResponseError
from directus_explorer.ms_metadata import MsMetadataTable
from directus_explorer.samples import ProfiledSample, ProjectSampleSummary
from directus_explorer.taxonomy import TaxonResolution


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
    """Metadata export should flatten lineage rows and retain field-rooted metadata."""

    settings = Settings(
        directus_instance="https://example.test/directus",
        directus_username="user@example.test",
        directus_password="secret",
    )
    client = DirectusClient(settings)

    def fake_post(*args: Any, **kwargs: Any) -> FakeResponse:
        return FakeResponse(200, {"data": {"access_token": "token"}})

    responses = {
        "Containers": {
            "data": [
                {
                    "id": 6001,
                    "container_id": "box_001",
                    "label": "Box 001",
                    "container_model": {"container_type": "vial box"},
                    "parent_container": {"id": 7001, "container_id": "shelf_01"},
                },
                {
                    "id": 7001,
                    "container_id": "shelf_01",
                    "label": "Shelf 01",
                    "container_model": {"container_type": "shelf"},
                    "parent_container": {"id": 8001, "container_id": "fridge_a"},
                },
                {
                    "id": 8001,
                    "container_id": "fridge_a",
                    "label": "Fridge A",
                    "container_model": {"container_type": "fridge"},
                },
                {
                    "id": 1,
                    "container_id": "rack_001",
                    "label": "Rack 001",
                    "container_model": {"container_type": "rack"},
                    "parent_container": {"id": 7001, "container_id": "shelf_01"},
                },
                {
                    "id": 7,
                    "container_id": "room_101",
                    "label": "Room 101",
                    "container_model": {"container_type": "room"},
                },
            ]
        },
        "Field_Data": {
            "data": [
                {
                    "id": 40,
                    "sample_id": "dbgi_001195",
                    "qfield_project": "jbuf",
                    "sample_name": "Sample Name",
                    "temperature_°C": 20.5,
                    "geometry": {"type": "Point", "coordinates": [7.1, 46.7]},
                }
            ]
        },
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
                        "container_model": {
                            "volume": 1.5,
                            "is_sample_container": True,
                            "container_type": "Vial",
                        },
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
                            "container_model": {"container_type": "Falcon"},
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
                    "sample_container": {
                        "id": 6365,
                        "container_id": "dbgi_001195_01",
                        "container_model": {"container_type": "Vial"},
                    },
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
                    "sample_container": {
                        "id": 1225,
                        "container_id": "dbgi_001195",
                        "container_model": {"container_type": "Falcon"},
                    },
                    "parent_container": {"id": 7, "container_id": "container_000007"},
                    "batch": {"batch_id": "batch_dried_001"},
                    "field_data": {
                        "id": 40,
                        "sample_id": "dbgi_001195",
                        "qfield_project": "jbuf",
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
    assert table.fieldnames[:3] == (
        "field_data_id",
        "original_sample_id",
        "ms_container_id",
    )
    assert "extraction_container_id" in table.fieldnames
    assert "original_sample_container_id" in table.fieldnames
    assert "qfield_project" in table.fieldnames
    assert "profile_mode" in table.fieldnames
    assert row["ms_data_id"] == 1
    assert row["profile_mode"] == "positive"
    assert row["ms_filename"] == "20240307_EB_dbgi_001195_01_01"
    assert row["ms_injection_method_method_name"] == "100mm_C18_15min_DDA_ELON_pos"
    assert row["ms_instrument_instrument_id"] == "inst_000001"
    assert row["ms_instrument_model_instrument_model"] == "LC-MS QExactive Plus"
    assert row["aliquoting_data_id"] == 10
    assert row["extraction_data_id"] == 20
    assert row["dried_samples_data_id"] == 30
    assert row["ms_container_id"] == "dbgi_001195_01_01"
    assert row["ms_container_type"] == "Vial"
    assert row["extraction_container_id"] == "dbgi_001195_01"
    assert row["extraction_container_type"] == "Vial"
    assert row["original_sample_container_id"] == "dbgi_001195"
    assert row["original_sample_container_type"] == "Falcon"
    assert row["original_sample_id"] == "dbgi_001195"
    assert row["qfield_project"] == "jbuf"
    assert row["field_sample_name"] == "Sample Name"
    assert row["field_temperature_c"] == 20.5
    assert row["field_geometry_coordinates"] == "[7.1, 46.7]"
    assert "field_geometry" not in table.fieldnames


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
        "Containers": {"data": []},
        "Field_Data": {
            "data": [
                {"id": 10, "sample_id": "dbgi_001195", "qfield_project": "jbuf"},
                {"id": 11, "sample_id": "fibl_000001", "qfield_project": "fibl"},
            ]
        },
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


def test_build_ms_metadata_table_includes_non_profiled_samples_and_watcher_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Export should include collected samples without MS data and enrich exact watcher matches."""

    settings = Settings(
        directus_instance="https://example.test/directus",
        directus_username="user@example.test",
        directus_password="secret",
    )
    client = DirectusClient(settings)

    def fake_post(*args: Any, **kwargs: Any) -> FakeResponse:
        return FakeResponse(200, {"data": {"access_token": "token"}})

    responses = {
        "Containers": {
            "data": [
                {
                    "id": 6001,
                    "container_id": "box_001",
                    "label": "Box 001",
                    "container_model": {"container_type": "vial box"},
                }
            ]
        },
        "Field_Data": {
            "data": [
                {"id": 10, "sample_id": "dbgi_001195", "qfield_project": "jbuf"},
                {"id": 11, "sample_id": "dbgi_009999", "qfield_project": "jbuf"},
            ]
        },
        "MS_Data": {
            "data": [
                {
                    "id": 1,
                    "filename": "exact_hit",
                    "injection_method": {"method_name": "method_pos"},
                    "parent_sample_container": {
                        "container_id": "dbgi_001195_01_01",
                        "container_model": {"container_type": "Vial"},
                    },
                }
            ]
        },
            "Aliquoting_Data": {
                "data": [
                    {
                    "sample_container": {"container_id": "dbgi_001195_01_01"},
                    "parent_sample_container": {
                        "container_id": "dbgi_001195_01",
                        "container_model": {"container_type": "Falcon"},
                    },
                    "parent_container": {"id": 6001, "container_id": "box_001"},
                    }
                ]
            },
        "Extraction_Data": {
            "data": [
                {
                    "sample_container": {
                        "container_id": "dbgi_001195_01",
                        "container_model": {"container_type": "Vial"},
                    },
                    "parent_sample_container": {"container_id": "dbgi_001195"},
                }
            ]
        },
        "Dried_Samples_Data": {
            "data": [
                {
                    "sample_container": {
                        "container_id": "dbgi_001195",
                        "container_model": {"container_type": "Falcon"},
                    },
                    "field_data": {"sample_id": "dbgi_001195", "qfield_project": "jbuf"},
                }
            ]
        },
    }

    watcher_tsv = tmp_path / "watcher.tsv"
    watcher_tsv.write_text(
        "file_path\tfile_name\tpolarity\tinstrument\n"
        "/converted/exact_hit.mzML\texact_hit.mzML\tMS:1000130|positive scan\tQE-HFX\n",
        encoding="utf-8",
    )

    def fake_get(url: str, *args: Any, **kwargs: Any) -> FakeResponse:
        for collection, payload in responses.items():
            if f"/items/{collection}" in url:
                return FakeResponse(200, payload)
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(client._session, "post", fake_post)
    monkeypatch.setattr(client._session, "get", fake_get)

    table = client.build_ms_metadata_table_with_watcher(
        project="jbuf",
        watcher_tsv_paths=(watcher_tsv,),
    )

    assert len(table.rows) == 2
    profiled_row, unprofiled_row = table.rows
    assert profiled_row["original_sample_id"] == "dbgi_001195"
    assert profiled_row["ms_container_id"] == "dbgi_001195_01_01"
    assert profiled_row["ms_container_type"] == "Vial"
    assert profiled_row["extraction_container_id"] == "dbgi_001195_01"
    assert profiled_row["extraction_container_type"] == "Vial"
    assert profiled_row["original_sample_container_type"] == "Falcon"
    assert profiled_row["watcher_exact_filename_match"] is True
    assert profiled_row["watcher_file_name"] == "exact_hit.mzML"
    assert profiled_row["watcher_instrument"] == "QE-HFX"

    assert unprofiled_row["original_sample_id"] == "dbgi_009999"
    assert unprofiled_row["ms_data_id"] is None
    assert unprofiled_row["ms_container_id"] is None
    assert unprofiled_row["extraction_container_id"] is None
    assert unprofiled_row["watcher_exact_filename_match"] is None


def test_export_ms_metadata_csv_writes_flat_csv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The export should write headers and rows to the requested TSV path."""

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
        rows = list(csv.DictReader(handle, delimiter="\t"))
    assert rows == [{"ms_data_id": "1", "qfield_project": "jbuf"}]


def test_export_ms_metadata_csv_compact_view_writes_curated_columns(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Compact view should keep only the curated metadata-focused columns."""

    settings = Settings(
        directus_instance="https://example.test/directus",
        directus_username="user@example.test",
        directus_password="secret",
    )
    client = DirectusClient(settings)

    monkeypatch.setattr(
        client,
        "build_ms_metadata_table_with_watcher",
        lambda project=None, watcher_tsv_paths=(): MsMetadataTable(
            fieldnames=(
                "original_sample_id",
                "ms_container_id",
                "extraction_container_id",
                "original_sample_container_id",
                "qfield_project",
                "field_sample_name",
                "ms_data_id",
                "ms_filename",
                "watcher_converted_file_sha1",
                "watcher_polarity",
                "watcher_acquisition_date",
                "watcher_raw_file_sha1",
                "field_user_created",
            ),
            rows=(
                {
                    "original_sample_id": "dbgi_001",
                    "ms_container_id": "dbgi_001_01_01",
                    "extraction_container_id": "dbgi_001_01",
                    "original_sample_container_id": "dbgi_001",
                    "qfield_project": "jbuf",
                    "field_sample_name": "Sample A",
                    "ms_data_id": "123",
                    "ms_filename": "file_a",
                    "watcher_converted_file_sha1": "sha1-mzml",
                    "watcher_polarity": "positive",
                    "watcher_acquisition_date": "2025-01-02T03:04:05Z",
                    "watcher_raw_file_sha1": "sha1-raw",
                    "field_user_created": "internal-user",
                },
            ),
        ),
    )

    output_path = tmp_path / "compact.tsv"
    row_count = client.export_ms_metadata_csv(
        output_path=output_path,
        project="jbuf",
        view="compact",
    )

    assert row_count == 1
    with output_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
        fieldnames = tuple(rows[0].keys())
    assert fieldnames == (
        "original_sample_id",
        "ms_container_id",
        "extraction_container_id",
        "original_sample_container_id",
        "qfield_project",
        "field_sample_name",
        "ms_filename",
        "watcher_converted_file_sha1",
        "watcher_polarity",
        "watcher_acquisition_date",
        "watcher_raw_file_sha1",
    )
    assert rows == [
        {
            "original_sample_id": "dbgi_001",
            "ms_container_id": "dbgi_001_01_01",
            "extraction_container_id": "dbgi_001_01",
            "original_sample_container_id": "dbgi_001",
            "qfield_project": "jbuf",
            "field_sample_name": "Sample A",
            "ms_filename": "file_a",
            "watcher_converted_file_sha1": "sha1-mzml",
            "watcher_polarity": "positive",
            "watcher_acquisition_date": "2025-01-02T03:04:05Z",
            "watcher_raw_file_sha1": "sha1-raw",
        }
    ]


def test_build_sample_locations_table_resolves_storage_hierarchy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Location table should resolve sample containers and storage ancestry separately."""

    settings = Settings(
        directus_instance="https://example.test/directus",
        directus_username="user@example.test",
        directus_password="secret",
    )
    client = DirectusClient(settings)

    def fake_post(*args: Any, **kwargs: Any) -> FakeResponse:
        return FakeResponse(200, {"data": {"access_token": "token"}})

    responses = {
        "Containers": {
            "data": [
                {
                    "id": 6001,
                    "container_id": "box_001",
                    "container_model": {"container_type": "vial box"},
                    "parent_container": {"id": 7001, "container_id": "shelf_01"},
                },
                {
                    "id": 7001,
                    "container_id": "shelf_01",
                    "container_model": {"container_type": "shelf"},
                    "parent_container": {"id": 8001, "container_id": "fridge_a"},
                },
                {
                    "id": 8001,
                    "container_id": "fridge_a",
                    "container_model": {"container_type": "fridge"},
                },
                {
                    "id": 1,
                    "container_id": "rack_001",
                    "container_model": {"container_type": "rack"},
                },
            ]
        },
        "Field_Data": {"data": [{"id": 40, "sample_id": "dbgi_001195", "qfield_project": "jbuf"}]},
        "MS_Data": {
            "data": [
                {
                    "parent_sample_container": {
                        "container_id": "dbgi_001195_01_01",
                        "container_model": {"container_type": "Vial"},
                    }
                }
            ]
        },
        "Aliquoting_Data": {
            "data": [
                {
                    "sample_container": {"container_id": "dbgi_001195_01_01"},
                    "parent_sample_container": {"container_id": "dbgi_001195_01"},
                    "parent_container": {"id": 6001, "container_id": "box_001"},
                }
            ]
        },
        "Extraction_Data": {
            "data": [
                {
                    "sample_container": {
                        "container_id": "dbgi_001195_01",
                        "container_model": {"container_type": "Vial"},
                    },
                    "parent_sample_container": {"container_id": "dbgi_001195"},
                    "parent_container": {"id": 1, "container_id": "rack_001"},
                }
            ]
        },
        "Dried_Samples_Data": {
            "data": [
                {
                    "sample_container": {
                        "container_id": "dbgi_001195",
                        "container_model": {"container_type": "Falcon"},
                    },
                    "parent_container": {"id": 1, "container_id": "rack_001"},
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

    table = client.build_sample_locations_table(sample_id="dbgi_001195_01_01")

    assert len(table.rows) == 1
    row = table.rows[0]
    assert row["original_sample_id"] == "dbgi_001195"
    assert row["ms_container_id"] == "dbgi_001195_01_01"
    assert row["ms_container_type"] == "Vial"
    assert row["ms_storage_level_1_container_id"] == "box_001"
    assert row["ms_storage_level_1_type"] == "vial box"
    assert row["ms_storage_level_2_container_id"] == "shelf_01"
    assert row["ms_storage_level_2_type"] == "shelf"
    assert row["extraction_container_id"] == "dbgi_001195_01"
    assert row["original_sample_container_id"] == "dbgi_001195"


def test_build_sample_locations_table_requires_exactly_one_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Location table builder should require either one sample or one project."""

    settings = Settings(
        directus_instance="https://example.test/directus",
        directus_username="user@example.test",
        directus_password="secret",
    )
    client = DirectusClient(settings)

    with pytest.raises(ValueError, match="Exactly one of sample_id or project"):
        client.build_sample_locations_table()


def test_build_sample_locations_table_handles_samples_without_ms_or_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Location table should not crash when only the original sample branch exists."""

    settings = Settings(
        directus_instance="https://example.test/directus",
        directus_username="user@example.test",
        directus_password="secret",
    )
    client = DirectusClient(settings)

    def fake_post(*args: Any, **kwargs: Any) -> FakeResponse:
        return FakeResponse(200, {"data": {"access_token": "token"}})

    responses = {
        "Containers": {"data": []},
        "Field_Data": {"data": [{"id": 40, "sample_id": "dbgi_003187", "qfield_project": "jbuf"}]},
        "MS_Data": {"data": []},
        "Aliquoting_Data": {"data": []},
        "Extraction_Data": {"data": []},
        "Dried_Samples_Data": {
            "data": [
                {
                    "sample_container": {
                        "container_id": "dbgi_003187",
                        "container_model": {"container_type": "Falcon"},
                    },
                    "field_data": {"sample_id": "dbgi_003187", "qfield_project": "jbuf"},
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

    table = client.build_sample_locations_table(sample_id="dbgi_003187")

    assert len(table.rows) == 1
    row = table.rows[0]
    assert row["original_sample_id"] == "dbgi_003187"
    assert "ms_container_id" not in table.fieldnames
    assert "ms_container_type" not in table.fieldnames
    assert "extraction_container_id" not in table.fieldnames
    assert "extraction_container_type" not in table.fieldnames
    assert row["original_sample_container_id"] == "dbgi_003187"
    assert row["original_sample_container_type"] == "Falcon"


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
        "Containers": {"data": []},
        "Field_Data": {"data": []},
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
        "Field_Data": {"data": []},
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


def test_list_profiled_samples_resolves_extraction_only_lineage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Profiled samples can resolve through extraction parent when dried rows are absent."""

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
                    "parent_sample_container": {"container_id": "drog_000469_01"},
                    "injection_method": {"method_name": "HSST3_ddMS2_pos"},
                },
            ]
        },
        "Field_Data": {
            "data": [
                {
                    "sample_id": "drog_000469",
                    "qfield_project": "droguier_jbn",
                    "taxon_name_unified": "Vaccinium myrtillus",
                }
            ]
        },
        "Aliquoting_Data": {"data": []},
        "Extraction_Data": {
            "data": [
                {
                    "sample_container": {"container_id": "drog_000469_01"},
                    "parent_sample_container": {"container_id": "drog_000469"},
                }
            ]
        },
        "Dried_Samples_Data": {"data": []},
    }

    def fake_get(url: str, *args: Any, **kwargs: Any) -> FakeResponse:
        for collection, payload in responses.items():
            if f"/items/{collection}" in url:
                return FakeResponse(200, payload)
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(client._session, "post", fake_post)
    monkeypatch.setattr(client._session, "get", fake_get)

    profiled = client.list_profiled_samples(mode="positive")

    assert [(sample.sample_id, sample.qfield_project, sample.mode) for sample in profiled] == [
        ("drog_000469", "droguier_jbn", "positive")
    ]
    assert profiled[0].species == "Vaccinium myrtillus"


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
        if url.endswith("/items/Field_Data") and params == {
            "limit": "-1",
            "fields": "sample_id,qfield_project,taxon_name,sample_name,taxon_name_unified",
        }:
            return FakeResponse(
                200,
                {
                    "data": [
                        {"sample_id": "dbgi_001195", "qfield_project": "jbuf"},
                        {"sample_id": "fibl_000001", "qfield_project": "fibl"},
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


def test_summarize_samples_by_project_group_adds_member_project_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sample project groups should add counts from configured member projects."""

    settings = Settings(
        directus_instance="https://example.test/directus",
        directus_username="user@example.test",
        directus_password="secret",
    )
    client = DirectusClient(settings)

    monkeypatch.setattr(
        client,
        "summarize_samples_by_project",
        lambda: [
            ProjectSampleSummary("jbc", 10, 0, 0, 0, 0),
            ProjectSampleSummary("jbn", 20, 2, 1, 0, 1),
            ProjectSampleSummary("jbp", 30, 3, 0, 1, 2),
            ProjectSampleSummary("jbuf", 40, 4, 1, 1, 2),
            ProjectSampleSummary("kew-botanical-gardens", 50, 5, 0, 0, 5),
            ProjectSampleSummary("sandbox", 999, 999, 999, 999, 999),
        ],
    )

    summary = client.summarize_samples_by_project_group("dbgi")

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
    ] == [("dbgi", 150, 14, 2, 2, 10)]


def test_sample_compact_metadata_table_collapses_rows_and_adds_taxonomy() -> None:
    """Sample compact metadata should produce one row per collected sample."""

    settings = Settings(
        directus_instance="https://example.test/directus",
        directus_username="user@example.test",
        directus_password="secret",
    )

    class FakeTaxonResolver:
        def resolve_names(self, names: Iterable[str]) -> Mapping[str, TaxonResolution]:
            assert set(names) == {"Achillea millefolium"}
            return {
                "Achillea millefolium": TaxonResolution(
                    input_name="Achillea millefolium",
                    canonical_name="Achillea millefolium",
                    taxon_id="97H4",
                    scientific_name="Achillea millefolium",
                    matched_name="Achillea millefolium L.",
                    current_name="Achillea millefolium L.",
                    synonym="false",
                    data_source_id="1",
                    data_source_title="Catalogue of Life",
                    kind="BestMatch",
                    sort_score="9.41215",
                    match_type="Exact",
                    edit_distance="0",
                    classification_path=(
                        "Eukaryota|Plantae|Pteridobiotina|Tracheophyta|"
                        "Magnoliopsida|Asterales|Asteraceae|Achillea|"
                        "Achillea millefolium"
                    ),
                    error="",
                )
            }

    client = DirectusClient(settings, taxon_resolver=FakeTaxonResolver())
    table = MsMetadataTable(
        fieldnames=(
            "original_sample_id",
            "qfield_project",
            "profile_mode",
            "field_taxon_name",
            "field_sample_name",
            "ms_filename",
        ),
        rows=(
            {
                "original_sample_id": "dbgi_001",
                "qfield_project": "jbuf",
                "profile_mode": "positive",
                "field_taxon_name": "Achillea millefolium",
                "field_sample_name": "",
                "ms_filename": "pos.raw",
            },
            {
                "original_sample_id": "dbgi_001",
                "qfield_project": "jbuf",
                "profile_mode": "negative",
                "field_taxon_name": "Achillea millefolium",
                "field_sample_name": "",
                "ms_filename": "neg.raw",
            },
        ),
    )

    compact = client._build_sample_compact_metadata_table(table)

    assert len(compact.rows) == 1
    row = compact.rows[0]
    assert row["original_sample_id"] == "dbgi_001"
    assert row["profile_mode"] == "both"
    assert row["profiled_positive"] == "true"
    assert row["profiled_negative"] == "true"
    assert row["ms_filename"] == "pos.raw; neg.raw"
    assert row["resolved_taxon_canonical"] == "Achillea millefolium"
    assert row["resolved_taxon_id"] == "97H4"
    assert row["resolved_taxon_family"] == "Asteraceae"
    assert row["resolved_taxon_order"] == "Asterales"


def test_summarize_species_by_project_group_unions_resolved_taxa(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Species project groups should count each resolved COL taxon once."""

    settings = Settings(
        directus_instance="https://example.test/directus",
        directus_username="user@example.test",
        directus_password="secret",
    )

    class FakeTaxonResolver:
        def resolve_names(self, names: Iterable[str]) -> Mapping[str, TaxonResolution]:
            taxon_ids = {
                "Achillea millefolium": "97H4",
                "Achillea millefolium L.": "97H4",
                "Bellis perennis": "69W5",
                "Taraxacum officinale": "54P3",
            }
            return {
                name: TaxonResolution(
                    input_name=name,
                    canonical_name=name,
                    taxon_id=taxon_ids[name],
                    scientific_name=name,
                    matched_name=f"{name} L.",
                    current_name=f"{name} L.",
                    synonym="false",
                    data_source_id="1",
                    data_source_title="Catalogue of Life",
                    kind="BestMatch",
                    sort_score="9.4",
                    match_type="Exact",
                    edit_distance="0",
                    classification_path=f"Eukaryota|Plantae|Tracheophyta|Magnoliopsida|Asterales|Asteraceae|{name.split()[0]}|{name}",
                    error="",
                )
                for name in names
                if name in taxon_ids
            }

    client = DirectusClient(settings, taxon_resolver=FakeTaxonResolver())
    client._authenticated = True

    monkeypatch.setattr(
        client,
        "_get_field_species_by_project",
        lambda: {
            "jbc": {"Achillea millefolium"},
            "jbn": {"Achillea millefolium L.", "Bellis perennis"},
            "sandbox": {"Taraxacum officinale"},
        },
    )
    monkeypatch.setattr(
        client,
        "list_profiled_samples",
        lambda mode="any": [
            ProfiledSample(
                sample_id="dbgi_1",
                qfield_project="jbc",
                mode="both",
                species_names=("Achillea millefolium",),
            ),
            ProfiledSample(
                sample_id="dbgi_2",
                qfield_project="jbn",
                mode="positive",
                species_names=("Bellis perennis",),
            ),
            ProfiledSample(
                sample_id="sandbox_1",
                qfield_project="sandbox",
                mode="both",
                species_names=("Taraxacum officinale",),
            ),
        ],
    )

    summary = client.summarize_species_by_project_group("dbgi")

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
    ] == [("dbgi", 2, 2, 1, 0, 1)]


def test_summarize_species_by_project_counts_distinct_species(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Species summary should count resolved Catalogue of Life taxa."""

    settings = Settings(
        directus_instance="https://example.test/directus",
        directus_username="user@example.test",
        directus_password="secret",
    )

    class FakeTaxonResolver:
        def resolve_names(self, names: Iterable[str]) -> Mapping[str, TaxonResolution]:
            taxon_ids = {
                "Achillea millefolium": "97H4",
                "Achillea millefolium L.": "97H4",
                "Artemisia vulgaris": "63CDH",
                "Bellis perennis": "69W5",
                "Taraxacum officinale": "54P3",
            }
            return {
                name: TaxonResolution(
                    input_name=name,
                    canonical_name=name,
                    taxon_id=taxon_ids.get(name, ""),
                    scientific_name=name,
                    matched_name=f"{name} L.",
                    current_name=f"{name} L.",
                    synonym="false",
                    data_source_id="1" if name in taxon_ids else "",
                    data_source_title="Catalogue of Life" if name in taxon_ids else "",
                    kind="BestMatch" if name in taxon_ids else "",
                    sort_score="9.4" if name in taxon_ids else "",
                    match_type="Exact" if name in taxon_ids else "NoMatch",
                    edit_distance="0" if name in taxon_ids else "",
                    classification_path=f"Eukaryota|Plantae|Tracheophyta|Magnoliopsida|Asterales|Asteraceae|{name.split()[0]}|{name}",
                    error="",
                )
                for name in names
            }

    client = DirectusClient(settings, taxon_resolver=FakeTaxonResolver())

    def fake_post(*args: Any, **kwargs: Any) -> FakeResponse:
        return FakeResponse(200, {"data": {"access_token": "token"}})

    def fake_get(url: str, params: Any = None, *args: Any, **kwargs: Any) -> FakeResponse:
        if url.endswith("/items/Field_Data") and params == {
            "limit": "-1",
            "fields": "qfield_project,taxon_name_unified,taxon_name,sample_name",
        }:
            return FakeResponse(
                200,
                {
                    "data": [
                        {
                            "qfield_project": "jbuf",
                            "taxon_name": "Achillea millefolium",
                            "sample_name": "Achillea millefolium L.",
                        },
                        {
                            "qfield_project": "jbuf",
                            "taxon_name": "Achillea millefolium",
                            "sample_name": "Artemisia vulgaris",
                        },
                        {
                            "qfield_project": "jbuf",
                            "taxon_name": None,
                            "sample_name": "Unresolved garden label",
                        },
                        {
                            "qfield_project": "fibl",
                            "taxon_name": "Taraxacum officinale",
                            "sample_name": "",
                        },
                    ]
                },
            )
        if url.endswith("/items/Field_Data") and params == {
            "limit": "-1",
            "fields": "sample_id,qfield_project,taxon_name,sample_name,taxon_name_unified",
        }:
            return FakeResponse(200, {"data": []})
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
                            "parent_sample_container": {"container_id": "dbgi_001196_01_01"},
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
                            "sample_container": {"container_id": "dbgi_001196_01_01"},
                            "parent_sample_container": {"container_id": "dbgi_001196_01"},
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
                            "sample_container": {"container_id": "dbgi_001196_01"},
                            "parent_sample_container": {"container_id": "dbgi_001196"},
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
                            "field_data": {
                                "sample_id": "dbgi_001195",
                                "qfield_project": "jbuf",
                                "taxon_name": "Achillea millefolium",
                                "sample_name": "Achillea millefolium L.",
                            },
                        },
                        {
                            "sample_container": {"container_id": "dbgi_001196"},
                            "field_data": {
                                "sample_id": "dbgi_001196",
                                "qfield_project": "jbuf",
                                "taxon_name": "",
                                "sample_name": "Bellis perennis",
                            },
                        },
                    ]
                },
            )
        raise AssertionError(f"Unexpected URL or params: {url!r} {params!r}")

    monkeypatch.setattr(client._session, "post", fake_post)
    monkeypatch.setattr(client._session, "get", fake_get)

    summary = client.summarize_species_by_project()

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
        ("fibl", 1, 0, 0, 0, 0),
        ("jbuf", 2, 2, 1, 0, 1),
    ]
