"""Directus API client used by the CLI and Python API."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import Settings
from .ms_converted_check import flatten_watcher_metadata, load_watcher_entries, strip_mzml_suffix
from .ms_metadata import (
    ALIQUOTING_DATA_FIELDS,
    DRIED_SAMPLES_DATA_FIELDS,
    EXTRACTION_DATA_FIELDS,
    MS_DATA_FIELDS,
    MsMetadataTable,
    build_sample_metadata_row,
    compact_table,
    flatten_row_fieldnames,
    select_table_view,
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
        """Return a flattened metadata table with one row per collected sample lineage."""

        return self.build_ms_metadata_table_with_watcher(project=project, watcher_tsv_paths=())

    def build_ms_metadata_table_with_watcher(
        self,
        *,
        project: str | None = None,
        watcher_tsv_paths: tuple[Path, ...] = (),
    ) -> MsMetadataTable:
        """Return a flattened metadata table enriched from optional watcher TSV exports."""

        self._authenticate()

        field_rows = self._get_items(collection="Field_Data", fields="*")
        container_rows = self._get_items(
            collection="Containers",
            fields=(
                "*,container_model.*,container_model.container_type.*,"
                "parent_container.*,parent_container.container_model.*,"
                "parent_container.container_model.container_type.*"
            ),
        )
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

        self._index_rows_by_nested_string(
            collection="Field_Data",
            rows=field_rows,
            path=("sample_id",),
        )
        self._index_rows_by_nested_string(
            collection="Aliquoting_Data",
            rows=aliquot_rows,
            path=("sample_container", "container_id"),
        )
        self._index_rows_by_nested_string(
            collection="Extraction_Data",
            rows=extraction_rows,
            path=("sample_container", "container_id"),
        )
        self._index_rows_by_nested_string(
            collection="Dried_Samples_Data",
            rows=dried_rows,
            path=("sample_container", "container_id"),
        )

        dried_by_sample_id = self._group_rows_by_nested_string(
            rows=dried_rows,
            path=("field_data", "sample_id"),
        )
        extraction_by_parent_container_id = self._group_rows_by_nested_string(
            rows=extraction_rows,
            path=("parent_sample_container", "container_id"),
        )
        aliquot_by_parent_container_id = self._group_rows_by_nested_string(
            rows=aliquot_rows,
            path=("parent_sample_container", "container_id"),
        )
        ms_by_parent_container_id = self._group_rows_by_nested_string(
            rows=ms_rows,
            path=("parent_sample_container", "container_id"),
        )
        containers_by_id = self._index_rows_by_scalar_key(
            collection="Containers",
            rows=container_rows,
            key_field="id",
        )
        watcher_by_stem = self._build_watcher_index(watcher_tsv_paths)

        rows: list[dict[str, Any]] = []
        for field_row in field_rows:
            qfield_project = field_row.get("qfield_project")
            if project is not None and qfield_project != project:
                continue

            sample_id = field_row.get("sample_id")
            if not isinstance(sample_id, str):
                continue

            sample_dried_rows = dried_by_sample_id.get(sample_id, [None])
            for dried_row in sample_dried_rows:
                dried_container_id = self._read_nested_string(
                    dried_row,
                    "sample_container",
                    "container_id",
                )
                sample_extraction_rows = (
                    extraction_by_parent_container_id.get(dried_container_id, [None])
                    if dried_container_id is not None
                    else [None]
                )
                for extraction_row in sample_extraction_rows:
                    extraction_container_id = self._read_nested_string(
                        extraction_row,
                        "sample_container",
                        "container_id",
                    )
                    sample_aliquot_rows = (
                        aliquot_by_parent_container_id.get(extraction_container_id, [None])
                        if extraction_container_id is not None
                        else [None]
                    )
                    for aliquot_row in sample_aliquot_rows:
                        aliquot_container_id = self._read_nested_string(
                            aliquot_row,
                            "sample_container",
                            "container_id",
                        )
                        sample_ms_rows = (
                            ms_by_parent_container_id.get(aliquot_container_id, [None])
                            if aliquot_container_id is not None
                            else [None]
                        )
                        for ms_row in sample_ms_rows:
                            row = build_sample_metadata_row(
                                field_row=field_row,
                                ms_row=ms_row,
                                aliquot_row=aliquot_row,
                                extraction_row=extraction_row,
                                dried_row=dried_row,
                            )
                            self._enrich_row_with_watcher_metadata(
                                row=row,
                                watcher_by_stem=watcher_by_stem,
                            )
                            rows.append(row)

        return compact_table(
            MsMetadataTable(
            fieldnames=flatten_row_fieldnames(rows),
            rows=tuple(rows),
            )
        )

    def export_ms_metadata_csv(
        self,
        output_path: str | Path,
        project: str | None = None,
        watcher_tsv_paths: tuple[Path, ...] = (),
        view: str = "full",
    ) -> int:
        """Write a flattened sample/MS metadata TSV and return the number of rows written."""

        table = self.build_ms_metadata_table_with_watcher(
            project=project,
            watcher_tsv_paths=watcher_tsv_paths,
        )
        table = select_table_view(table, view=view)
        write_ms_metadata_csv(table, output_path, delimiter="\t")
        return len(table.rows)

    def build_sample_locations_table(
        self,
        *,
        sample_id: str | None = None,
        project: str | None = None,
    ) -> MsMetadataTable:
        """Return container and physical storage rows for one sample or one project."""

        if (sample_id is None) == (project is None):
            raise ValueError("Exactly one of sample_id or project must be provided")

        self._authenticate()

        field_rows = self._get_items(collection="Field_Data", fields="*")
        container_rows = self._get_items(
            collection="Containers",
            fields=(
                "*,container_model.*,container_model.container_type.*,"
                "parent_container.*,parent_container.container_model.*,"
                "parent_container.container_model.container_type.*"
            ),
        )
        ms_rows = self._get_items(collection="MS_Data", fields=MS_DATA_FIELDS)
        aliquot_rows = self._get_items(collection="Aliquoting_Data", fields=ALIQUOTING_DATA_FIELDS)
        extraction_rows = self._get_items(collection="Extraction_Data", fields=EXTRACTION_DATA_FIELDS)
        dried_rows = self._get_items(collection="Dried_Samples_Data", fields=DRIED_SAMPLES_DATA_FIELDS)

        dried_by_sample_id = self._group_rows_by_nested_string(
            rows=dried_rows,
            path=("field_data", "sample_id"),
        )
        extraction_by_parent_container_id = self._group_rows_by_nested_string(
            rows=extraction_rows,
            path=("parent_sample_container", "container_id"),
        )
        aliquot_by_parent_container_id = self._group_rows_by_nested_string(
            rows=aliquot_rows,
            path=("parent_sample_container", "container_id"),
        )
        ms_by_parent_container_id = self._group_rows_by_nested_string(
            rows=ms_rows,
            path=("parent_sample_container", "container_id"),
        )
        containers_by_id = self._index_rows_by_scalar_key(
            collection="Containers",
            rows=container_rows,
            key_field="id",
        )

        rows: list[dict[str, Any]] = []
        seen: set[tuple[tuple[str, Any], ...]] = set()
        for field_row in field_rows:
            qfield_project = field_row.get("qfield_project")
            if project is not None and qfield_project != project:
                continue

            original_sample_id = field_row.get("sample_id")
            if not isinstance(original_sample_id, str):
                continue

            sample_dried_rows = dried_by_sample_id.get(original_sample_id, [None])
            for dried_row in sample_dried_rows:
                dried_container_id = self._read_nested_string(
                    dried_row,
                    "sample_container",
                    "container_id",
                )
                sample_extraction_rows = (
                    extraction_by_parent_container_id.get(dried_container_id, [None])
                    if dried_container_id is not None
                    else [None]
                )
                for extraction_row in sample_extraction_rows:
                    extraction_container_id = self._read_nested_string(
                        extraction_row,
                        "sample_container",
                        "container_id",
                    )
                    sample_aliquot_rows = (
                        aliquot_by_parent_container_id.get(extraction_container_id, [None])
                        if extraction_container_id is not None
                        else [None]
                    )
                    for aliquot_row in sample_aliquot_rows:
                        aliquot_container_id = self._read_nested_string(
                            aliquot_row,
                            "sample_container",
                            "container_id",
                        )
                        sample_ms_rows = (
                            ms_by_parent_container_id.get(aliquot_container_id, [None])
                            if aliquot_container_id is not None
                            else [None]
                        )
                        for ms_row in sample_ms_rows:
                            row = self._build_sample_location_row(
                                field_row=field_row,
                                ms_row=ms_row,
                                aliquot_row=aliquot_row,
                                extraction_row=extraction_row,
                                dried_row=dried_row,
                                containers_by_id=containers_by_id,
                            )
                            if sample_id is not None and not self._row_matches_sample_identifier(
                                row=row,
                                sample_id=sample_id,
                            ):
                                continue

                            normalized = tuple(sorted(row.items()))
                            if normalized in seen:
                                continue
                            seen.add(normalized)
                            rows.append(row)

        return compact_table(
            MsMetadataTable(
                fieldnames=flatten_row_fieldnames(rows),
                rows=tuple(rows),
            )
        )

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
    def _index_rows_by_scalar_key(
        *,
        collection: str,
        rows: list[dict[str, Any]],
        key_field: str,
    ) -> dict[str, dict[str, Any]]:
        """Index rows by a top-level scalar field and reject duplicates."""

        mapping: dict[str, dict[str, Any]] = {}
        for row in rows:
            key = row.get(key_field)
            if key is None:
                continue
            normalized = str(key)
            if normalized in mapping:
                raise DirectusResponseError(
                    f"Directus {collection} returned duplicate rows for {key_field}={normalized!r}"
                )
            mapping[normalized] = row
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
    def _group_rows_by_nested_string(
        *,
        rows: list[dict[str, Any]],
        path: tuple[str, ...],
    ) -> dict[str, list[dict[str, Any]]]:
        """Group rows by a nested string field."""

        mapping: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            key = DirectusClient._read_nested_string(row, *path)
            if key is None:
                continue
            mapping.setdefault(key, []).append(row)
        return mapping

    @staticmethod
    def _build_watcher_index(
        watcher_tsv_paths: tuple[Path, ...],
    ) -> dict[str, Any]:
        """Index watcher TSV rows by stemmed file name."""

        if not watcher_tsv_paths:
            return {}

        return {
            strip_mzml_suffix(entry.file_name): entry
            for entry in load_watcher_entries(watcher_tsv_paths)
        }

    @staticmethod
    def _enrich_row_with_watcher_metadata(
        *,
        row: dict[str, Any],
        watcher_by_stem: dict[str, Any],
    ) -> None:
        """Attach watcher metadata to a row when an exact mzML stem is available."""

        ms_filename = row.get("ms_filename")
        if not isinstance(ms_filename, str) or not ms_filename:
            row["watcher_exact_filename_match"] = None
            return

        watcher_entry = watcher_by_stem.get(ms_filename)
        row["watcher_exact_filename_match"] = watcher_entry is not None
        row.update(flatten_watcher_metadata(watcher_entry))

    @staticmethod
    def _enrich_row_with_storage_chain(
        *,
        row: dict[str, Any],
        prefix: str,
        start_container: dict[str, Any] | None,
        containers_by_id: dict[str, dict[str, Any]],
    ) -> None:
        """Attach a physical storage ancestry chain for one sample container."""

        current = start_container
        seen: set[str] = set()
        level = 1
        while isinstance(current, dict):
            current_id = current.get("id")
            normalized_id = str(current_id) if current_id is not None else None
            if normalized_id is not None and normalized_id in seen:
                break
            if normalized_id is not None:
                seen.add(normalized_id)

            container_row = (
                containers_by_id.get(normalized_id, current) if normalized_id is not None else current
            )
            row[f"{prefix}_level_{level}_container_id"] = container_row.get("container_id")
            row[f"{prefix}_level_{level}_type"] = DirectusClient._resolve_container_type(container_row)

            parent_container = container_row.get("parent_container")
            if not isinstance(parent_container, dict):
                break
            current = (
                containers_by_id.get(str(parent_container.get("id")), parent_container)
                if parent_container.get("id") is not None
                else parent_container
            )
            level += 1

    @staticmethod
    def _resolve_container_type(container_row: dict[str, Any] | None) -> str | None:
        """Return a human-readable container type when present."""

        if not isinstance(container_row, dict):
            return None
        container_model = container_row.get("container_model")
        if isinstance(container_model, dict):
            value = container_model.get("container_type")
            if isinstance(value, dict):
                nested = value.get("container_type")
                if isinstance(nested, str) and nested:
                    return nested
            if isinstance(value, str) and value:
                return value
        value = container_row.get("container_type")
        if isinstance(value, dict):
            nested = value.get("container_type")
            if isinstance(nested, str) and nested:
                return nested
        if isinstance(value, str) and value:
            return value
        return None

    def _build_sample_location_row(
        self,
        *,
        field_row: dict[str, Any],
        ms_row: dict[str, Any] | None,
        aliquot_row: dict[str, Any] | None,
        extraction_row: dict[str, Any] | None,
        dried_row: dict[str, Any] | None,
        containers_by_id: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """Build one physical location row for a sample lineage."""

        row: dict[str, Any] = {
            "original_sample_id": field_row.get("sample_id"),
            "qfield_project": field_row.get("qfield_project"),
            "ms_container_id": self._read_nested_string(ms_row, "parent_sample_container", "container_id"),
            "ms_container_type": self._resolve_container_type(
                self._read_nested_mapping(ms_row, "parent_sample_container", "container_model")
            ),
            "extraction_container_id": self._read_nested_string(
                extraction_row,
                "sample_container",
                "container_id",
            ),
            "extraction_container_type": self._resolve_container_type(
                self._read_nested_mapping(extraction_row, "sample_container", "container_model")
            ),
            "original_sample_container_id": self._read_nested_string(
                dried_row,
                "sample_container",
                "container_id",
            ),
            "original_sample_container_type": self._resolve_container_type(
                self._read_nested_mapping(dried_row, "sample_container", "container_model")
            ),
        }
        self._enrich_row_with_storage_chain(
            row=row,
            prefix="ms_storage",
            start_container=self._read_mapping(aliquot_row, "parent_container"),
            containers_by_id=containers_by_id,
        )
        self._enrich_row_with_storage_chain(
            row=row,
            prefix="extraction_storage",
            start_container=self._read_mapping(extraction_row, "parent_container"),
            containers_by_id=containers_by_id,
        )
        self._enrich_row_with_storage_chain(
            row=row,
            prefix="original_storage",
            start_container=self._read_mapping(dried_row, "parent_container"),
            containers_by_id=containers_by_id,
        )
        return row

    @staticmethod
    def _row_matches_sample_identifier(*, row: dict[str, Any], sample_id: str) -> bool:
        """Return whether the queried sample/container identifier matches a location row."""

        return sample_id in {
            row.get("original_sample_id"),
            row.get("original_sample_container_id"),
            row.get("extraction_container_id"),
            row.get("ms_container_id"),
        }

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

    @staticmethod
    def _read_mapping(row: dict[str, Any] | None, key: str) -> dict[str, Any] | None:
        """Read one nested mapping from an optional row."""

        if not isinstance(row, dict):
            return None
        value = row.get(key)
        if isinstance(value, dict):
            return value
        return None

    @staticmethod
    def _read_nested_mapping(
        row: dict[str, Any] | None,
        first_key: str,
        second_key: str,
    ) -> dict[str, Any] | None:
        """Read a nested mapping two levels deep from an optional row."""

        return DirectusClient._read_mapping(DirectusClient._read_mapping(row, first_key), second_key)

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
