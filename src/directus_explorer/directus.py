"""Directus API client used by the CLI and Python API."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import Settings
from .ms_metadata import (
    ALIQUOTING_DATA_FIELDS,
    DRIED_SAMPLES_DATA_FIELDS,
    EXTRACTION_DATA_FIELDS,
    MS_DATA_FIELDS,
    MsMetadataTable,
    build_ms_metadata_row,
    flatten_row_fieldnames,
    write_ms_metadata_csv,
)
from .samples import (
    ProfiledSample,
    ProjectSampleSummary,
    build_sample_count_params,
    classify_profile_mode,
    collapse_profile_modes,
    filter_profiled_samples,
    parse_sample_count_response,
    resolve_original_sample_container_id,
)


class DirectusError(RuntimeError):
    """Base class for Directus-related failures."""


class DirectusAuthError(DirectusError):
    """Raised when Directus authentication fails."""


class DirectusResponseError(DirectusError):
    """Raised when Directus returns an error or malformed payload."""


class DirectusClient:
    """Small authenticated Directus client for read-only explorer queries."""

    def __init__(self, settings: Settings) -> None:
        """Create a client bound to the provided Directus settings."""

        self._settings = settings
        self._session = self._build_session()
        self._authenticated = False

    def count_field_samples(self, qfield_project: str) -> int:
        """Return the number of Field_Data rows for the given qfield project."""

        self._authenticate()
        payload = self._get(
            "/items/Field_Data",
            params=build_sample_count_params(qfield_project=qfield_project),
        )
        try:
            return parse_sample_count_response(payload)
        except ValueError as exc:
            raise DirectusResponseError(str(exc)) from exc

    def list_projects(self) -> list[str]:
        """Return all distinct qfield project keys known to Field_Data."""

        self._authenticate()
        return sorted(self._get_field_sample_counts_by_project())

    def build_ms_metadata_table(self, project: str | None = None) -> MsMetadataTable:
        """Return a flattened metadata table with one row per MS_Data record."""

        self._authenticate()

        ms_rows = self._get_items(collection="MS_Data", fields=MS_DATA_FIELDS)
        aliquot_rows = self._get_items(
            collection="Aliquoting_Data",
            fields=ALIQUOTING_DATA_FIELDS,
        )
        extraction_rows = self._get_items(
            collection="Extraction_Data",
            fields=EXTRACTION_DATA_FIELDS,
        )
        dried_rows = self._get_items(
            collection="Dried_Samples_Data",
            fields=DRIED_SAMPLES_DATA_FIELDS,
        )

        aliquot_by_container_id = self._index_rows_by_nested_string(
            collection="Aliquoting_Data",
            rows=aliquot_rows,
            path=("sample_container", "container_id"),
        )
        extraction_by_container_id = self._index_rows_by_nested_string(
            collection="Extraction_Data",
            rows=extraction_rows,
            path=("sample_container", "container_id"),
        )
        dried_by_container_id = self._index_rows_by_nested_string(
            collection="Dried_Samples_Data",
            rows=dried_rows,
            path=("sample_container", "container_id"),
        )

        rows: list[dict[str, Any]] = []
        for ms_row in ms_rows:
            aliquot_container_id = self._read_nested_string(
                ms_row,
                "parent_sample_container",
                "container_id",
            )
            aliquot_row = (
                aliquot_by_container_id.get(aliquot_container_id)
                if aliquot_container_id is not None
                else None
            )
            extraction_container_id = self._read_nested_string(
                aliquot_row,
                "parent_sample_container",
                "container_id",
            )
            extraction_row = (
                extraction_by_container_id.get(extraction_container_id)
                if extraction_container_id is not None
                else None
            )
            dried_container_id = self._read_nested_string(
                extraction_row,
                "parent_sample_container",
                "container_id",
            )
            dried_row = (
                dried_by_container_id.get(dried_container_id)
                if dried_container_id is not None
                else None
            )

            row = build_ms_metadata_row(
                ms_row,
                aliquot_row=aliquot_row,
                extraction_row=extraction_row,
                dried_row=dried_row,
            )
            if project is not None and row.get("qfield_project") != project:
                continue
            rows.append(row)

        return MsMetadataTable(
            fieldnames=flatten_row_fieldnames(rows),
            rows=tuple(rows),
        )

    def export_ms_metadata_csv(
        self,
        output_path: str | Path,
        project: str | None = None,
    ) -> int:
        """Write a flattened MS metadata CSV and return the number of rows written."""

        table = self.build_ms_metadata_table(project=project)
        write_ms_metadata_csv(table, output_path)
        return len(table.rows)

    def list_profiled_samples(
        self,
        mode: str = "any",
        project: str | None = None,
    ) -> list[ProfiledSample]:
        """Return original sample ids that have mass-spec profiles for the requested mode."""

        self._authenticate()

        ms_rows = self._get_items(
            collection="MS_Data",
            fields="parent_sample_container.container_id,injection_method.method_name",
        )
        aliquot_rows = self._get_items(
            collection="Aliquoting_Data",
            fields="sample_container.container_id,parent_sample_container.container_id",
        )
        extraction_rows = self._get_items(
            collection="Extraction_Data",
            fields="sample_container.container_id,parent_sample_container.container_id",
        )
        dried_rows = self._get_items(
            collection="Dried_Samples_Data",
            fields="sample_container.container_id,field_data.sample_id,field_data.qfield_project",
        )

        aliquot_parent_by_child = self._build_relation_map(
            rows=aliquot_rows,
            child_path=("sample_container", "container_id"),
            parent_path=("parent_sample_container", "container_id"),
        )
        extraction_parent_by_child = self._build_relation_map(
            rows=extraction_rows,
            child_path=("sample_container", "container_id"),
            parent_path=("parent_sample_container", "container_id"),
        )
        dried_sample_metadata_by_container = self._build_sample_metadata_map(
            rows=dried_rows,
        )
        original_sample_container_ids = set(dried_sample_metadata_by_container)

        metadata_by_sample_id: dict[str, str] = {}
        modes_by_sample_id: dict[str, set[str]] = {}
        for row in ms_rows:
            profiled_container_id = self._read_nested_string(
                row,
                "parent_sample_container",
                "container_id",
            )
            method_name = self._read_nested_string(row, "injection_method", "method_name")
            if profiled_container_id is None or method_name is None:
                continue

            profile_mode = classify_profile_mode(method_name)
            if profile_mode is None:
                continue

            original_sample_container_id = resolve_original_sample_container_id(
                profiled_container_id,
                aliquot_parent_by_child=aliquot_parent_by_child,
                extraction_parent_by_child=extraction_parent_by_child,
                original_sample_container_ids=original_sample_container_ids,
            )
            if original_sample_container_id is None:
                continue

            sample_metadata = dried_sample_metadata_by_container.get(original_sample_container_id)
            if sample_metadata is None:
                continue
            sample_id, qfield_project = sample_metadata
            metadata_by_sample_id[sample_id] = qfield_project
            modes_by_sample_id.setdefault(sample_id, set()).add(profile_mode)

        profiled_samples = [
            ProfiledSample(
                sample_id=sample_id,
                qfield_project=metadata_by_sample_id[sample_id],
                mode=collapse_profile_modes(modes),
            )
            for sample_id, modes in sorted(modes_by_sample_id.items())
        ]
        return filter_profiled_samples(profiled_samples, mode=mode, project=project)

    def summarize_samples_by_project(self) -> list[ProjectSampleSummary]:
        """Return collected and profiled sample counts grouped by qfield project."""

        self._authenticate()

        collected_counts = self._get_field_sample_counts_by_project()
        profiled_samples = self.list_profiled_samples(mode="any")

        counts_by_project: dict[str, dict[str, int]] = {
            qfield_project: {
                "collected_count": collected_count,
                "profiled_count": 0,
                "positive_count": 0,
                "negative_count": 0,
                "both_count": 0,
            }
            for qfield_project, collected_count in collected_counts.items()
        }

        for sample in profiled_samples:
            project_counts = counts_by_project.setdefault(
                sample.qfield_project,
                {
                    "collected_count": 0,
                    "profiled_count": 0,
                    "positive_count": 0,
                    "negative_count": 0,
                    "both_count": 0,
                },
            )
            project_counts["profiled_count"] += 1
            if sample.mode == "positive":
                project_counts["positive_count"] += 1
            elif sample.mode == "negative":
                project_counts["negative_count"] += 1
            elif sample.mode == "both":
                project_counts["both_count"] += 1
            else:
                raise DirectusResponseError(f"Unexpected profiled sample mode: {sample.mode}")

        return [
            ProjectSampleSummary(
                qfield_project=qfield_project,
                collected_count=project_counts["collected_count"],
                profiled_count=project_counts["profiled_count"],
                positive_count=project_counts["positive_count"],
                negative_count=project_counts["negative_count"],
                both_count=project_counts["both_count"],
            )
            for qfield_project, project_counts in sorted(counts_by_project.items())
        ]

    def _authenticate(self) -> None:
        """Authenticate once and cache the bearer token on the session."""

        if self._authenticated:
            return

        response = self._session.post(
            self._url("/auth/login"),
            json={
                "email": self._settings.directus_username,
                "password": self._settings.directus_password,
            },
            timeout=(10, 30),
        )
        if response.status_code != 200:
            raise DirectusAuthError(f"Directus login failed with status {response.status_code}")

        try:
            payload = response.json()
            token = payload["data"]["access_token"]
        except (KeyError, TypeError, ValueError) as exc:
            raise DirectusResponseError(
                "Directus login response did not contain an access token"
            ) from exc

        self._session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }
        )
        self._authenticated = True

    def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        """Perform a GET request and return the parsed JSON payload."""

        response = self._session.get(self._url(path), params=params, timeout=(10, 60))
        if response.status_code != 200:
            raise DirectusError(f"Directus GET {path} failed with status {response.status_code}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise DirectusResponseError("Directus response was not valid JSON") from exc

        if not isinstance(payload, dict):
            raise DirectusResponseError("Directus response payload was not an object")

        return payload

    def _get_items(self, collection: str, fields: str) -> list[dict[str, Any]]:
        """Fetch all rows for a collection using a minimal field projection."""

        payload = self._get(
            f"/items/{collection}",
            params={
                "limit": "-1",
                "fields": fields,
            },
        )
        data = payload.get("data")
        if not isinstance(data, list):
            raise DirectusResponseError(
                f"Directus {collection} payload did not contain a data list"
            )
        if not all(isinstance(row, dict) for row in data):
            raise DirectusResponseError(f"Directus {collection} payload contained a non-object row")
        return data

    def _get_field_sample_counts_by_project(self) -> dict[str, int]:
        """Return collected sample counts grouped by qfield project."""

        payload = self._get(
            "/items/Field_Data",
            params={
                "limit": "-1",
                "groupBy[]": "qfield_project",
                "aggregate[count]": "id",
            },
        )
        data = payload.get("data")
        if not isinstance(data, list):
            raise DirectusResponseError(
                "Directus Field_Data aggregate payload did not contain a data list"
            )

        counts_by_project: dict[str, int] = {}
        for row in data:
            if not isinstance(row, dict):
                raise DirectusResponseError("Directus Field_Data aggregate row is not an object")

            qfield_project = row.get("qfield_project")
            count_data = row.get("count")
            if not isinstance(qfield_project, str) or not isinstance(count_data, dict):
                continue

            count_value = count_data.get("id")
            if count_value is None:
                raise DirectusResponseError(
                    "Directus Field_Data aggregate row did not contain count.id"
                )

            try:
                counts_by_project[qfield_project] = int(str(count_value))
            except ValueError as exc:
                raise DirectusResponseError(
                    f"Directus Field_Data aggregate count is not an integer: {count_value!r}"
                ) from exc

        return counts_by_project

    def _index_rows_by_nested_string(
        self,
        *,
        collection: str,
        rows: list[dict[str, Any]],
        path: tuple[str, ...],
    ) -> dict[str, dict[str, Any]]:
        """Index rows by a nested string field and reject duplicates."""

        mapping: dict[str, dict[str, Any]] = {}
        for row in rows:
            key = self._read_nested_string(row, *path)
            if key is None:
                continue
            if key in mapping:
                dotted_path = ".".join(path)
                raise DirectusResponseError(
                    f"Directus {collection} returned duplicate rows for {dotted_path}={key!r}"
                )
            mapping[key] = row
        return mapping

    @staticmethod
    def _build_relation_map(
        *,
        rows: list[dict[str, Any]],
        child_path: tuple[str, str],
        parent_path: tuple[str, str],
    ) -> dict[str, str]:
        """Build a child-to-parent identifier map from nested relation rows."""

        mapping: dict[str, str] = {}
        for row in rows:
            child_value = DirectusClient._read_nested_string(row, *child_path)
            parent_value = DirectusClient._read_nested_string(row, *parent_path)
            if child_value is None or parent_value is None:
                continue
            mapping[child_value] = parent_value
        return mapping

    @staticmethod
    def _build_sample_metadata_map(
        *,
        rows: list[dict[str, Any]],
    ) -> dict[str, tuple[str, str]]:
        """Build a sample-container to sample metadata map from dried sample rows."""

        mapping: dict[str, tuple[str, str]] = {}
        for row in rows:
            container_id = DirectusClient._read_nested_string(
                row,
                "sample_container",
                "container_id",
            )
            sample_id = DirectusClient._read_nested_string(row, "field_data", "sample_id")
            qfield_project = DirectusClient._read_nested_string(row, "field_data", "qfield_project")
            if container_id is None or sample_id is None or qfield_project is None:
                continue
            mapping[container_id] = (sample_id, qfield_project)
        return mapping

    @staticmethod
    def _read_nested_string(row: dict[str, Any] | None, *path: str) -> str | None:
        """Read a nested string from a Directus row payload."""

        current: Any = row
        for part in path:
            if not isinstance(current, dict):
                return None
            current = current.get(part)

        if not isinstance(current, str):
            return None
        return current

    def _url(self, path: str) -> str:
        """Build an absolute Directus URL for the given API path."""

        return f"{self._settings.directus_instance}{path}"

    @staticmethod
    def _build_session() -> requests.Session:
        """Create a requests session with conservative retry behavior."""

        session = requests.Session()
        retry = Retry(
            total=5,
            connect=5,
            read=5,
            backoff_factor=1.0,
            status_forcelist=(429, 502, 503, 504),
            allowed_methods=frozenset({"GET", "POST"}),
            raise_on_status=False,
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session
