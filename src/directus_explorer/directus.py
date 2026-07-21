"""Directus API client used by the CLI and Python API."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import Settings
from .injection_audit import (
    build_injection_audit_table,
    load_injection_list,
    summarize_injection_audit,
    write_injection_audit_tsv,
)
from .ms_converted_check import (
    flatten_watcher_metadata,
    load_watcher_entries,
    strip_mzml_suffix,
)
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
    PROJECT_GROUPS,
    ProfiledSample,
    ProjectSampleSummary,
    ProjectSpeciesSummary,
    build_sample_count_params,
    classify_profile_mode,
    collapse_profile_modes,
    filter_profiled_samples,
    parse_sample_count_response,
    resolve_original_sample_container_id,
)
from .taxonomy import (
    CatalogueOfLifeTaxonResolver,
    TaxonResolution,
    TaxonResolutionError,
    TaxonResolver,
)


class DirectusError(RuntimeError):
    """Base class for Directus-related failures."""


class DirectusAuthError(DirectusError):
    """Raised when Directus authentication fails."""


class DirectusResponseError(DirectusError):
    """Raised when Directus returns an error or malformed payload."""


TAXONOMY_METADATA_FIELDNAMES = (
    "resolved_taxon_input_name",
    "resolved_taxon_canonical",
    "resolved_taxon_id",
    "resolved_taxon_scientific_name",
    "resolved_taxon_matched_name",
    "resolved_taxon_current_name",
    "resolved_taxon_synonym",
    "resolved_taxon_source_id",
    "resolved_taxon_source_title",
    "resolved_taxon_kind",
    "resolved_taxon_sort_score",
    "resolved_taxon_match_type",
    "resolved_taxon_edit_distance",
    "resolved_taxon_classification_path",
    "resolved_taxon_kingdom",
    "resolved_taxon_phylum",
    "resolved_taxon_class",
    "resolved_taxon_order",
    "resolved_taxon_family",
    "resolved_taxon_genus",
)


class DirectusClient:
    """Small authenticated Directus client for read-only explorer queries."""

    def __init__(
        self,
        settings: Settings,
        taxon_resolver: TaxonResolver | None = None,
    ) -> None:
        """Create a client bound to the provided Directus settings."""

        self._settings = settings
        self._session = self._build_session()
        self._authenticated = False
        self._taxon_resolver = taxon_resolver or CatalogueOfLifeTaxonResolver()

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
        project_group: str | None = None,
        watcher_tsv_paths: tuple[Path, ...] = (),
    ) -> MsMetadataTable:
        """Return a flattened metadata table enriched from optional watcher TSV exports."""

        project_keys = self._resolve_project_filter(project=project, project_group=project_group)
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
            if project_keys is not None and qfield_project not in project_keys:
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
        project_group: str | None = None,
        watcher_tsv_paths: tuple[Path, ...] = (),
        view: str = "full",
    ) -> int:
        """Write a flattened sample/MS metadata TSV and return the number of rows written."""

        table = self.build_ms_metadata_table_with_watcher(
            project=project,
            project_group=project_group,
            watcher_tsv_paths=watcher_tsv_paths,
        )
        if view == "sample-compact":
            table = self._build_sample_compact_metadata_table(table)
        else:
            table = select_table_view(table, view=view)
        write_ms_metadata_csv(table, output_path, delimiter="\t")
        return len(table.rows)

    def export_injection_audit_tsv(
        self,
        *,
        input_path: str | Path,
        output_path: str | Path,
        required_file_type: str = "sample",
        ms_parent_level: str = "aliquot",
    ) -> int:
        """Write an injection-list lineage audit TSV and return the row count."""

        table = self.build_injection_audit_table(
            input_path=input_path,
            required_file_type=required_file_type,
            ms_parent_level=ms_parent_level,
        )
        write_injection_audit_tsv(table, Path(output_path))
        return len(table.rows)

    def build_injection_audit_table(
        self,
        *,
        input_path: str | Path,
        required_file_type: str = "sample",
        ms_parent_level: str = "aliquot",
    ) -> MsMetadataTable:
        """Audit an acquisition injection list against Directus lineage records."""

        injection_rows = load_injection_list(Path(input_path))
        self._authenticate()

        dried_rows = self._get_items(
            collection="Dried_Samples_Data",
            fields="id,sample_container.container_id,field_data.sample_id",
        )
        extraction_rows = self._get_items(
            collection="Extraction_Data",
            fields=(
                "id,sample_container.container_id,"
                "parent_sample_container.container_id"
            ),
        )
        aliquot_rows = self._get_items(
            collection="Aliquoting_Data",
            fields=(
                "id,sample_container.container_id,"
                "parent_sample_container.container_id"
            ),
        )
        ms_rows = self._get_items(
            collection="MS_Data",
            fields="id,filename,parent_sample_container.container_id",
        )
        return build_injection_audit_table(
            injection_rows=injection_rows,
            dried_rows=dried_rows,
            extraction_rows=extraction_rows,
            aliquot_rows=aliquot_rows,
            ms_rows=ms_rows,
            required_file_type=required_file_type,
            ms_parent_level=ms_parent_level,
        )

    def import_ready_injection_runs(
        self,
        *,
        input_path: str | Path,
        required_file_type: str = "sample",
        ms_parent_level: str = "aliquot",
        injection_volume: int,
        injection_volume_unit_id: int,
        injection_method_id: int,
        instrument_id: int,
        status: str = "published",
        batch_id: int | None = None,
        limit: int | None = None,
        commit: bool = False,
    ) -> dict[str, Any]:
        """Create MS_Data rows for audit-ready injection-list rows."""

        if limit is not None and limit < 1:
            raise ValueError("--limit must be greater than zero")

        injection_rows = load_injection_list(Path(input_path))
        self._authenticate()

        dried_rows = self._get_items(
            collection="Dried_Samples_Data",
            fields="id,sample_container.container_id,field_data.sample_id",
        )
        extraction_rows = self._get_items(
            collection="Extraction_Data",
            fields=(
                "id,sample_container.id,sample_container.container_id,"
                "parent_sample_container.container_id"
            ),
        )
        aliquot_rows = self._get_items(
            collection="Aliquoting_Data",
            fields=(
                "id,sample_container.id,sample_container.container_id,"
                "parent_sample_container.container_id"
            ),
        )
        ms_rows = self._get_items(
            collection="MS_Data",
            fields="id,filename,parent_sample_container.container_id",
        )
        audit_table = build_injection_audit_table(
            injection_rows=injection_rows,
            dried_rows=dried_rows,
            extraction_rows=extraction_rows,
            aliquot_rows=aliquot_rows,
            ms_rows=ms_rows,
            required_file_type=required_file_type,
            ms_parent_level=ms_parent_level,
        )
        ready_rows = [row for row in audit_table.rows if row.get("status") == "ready"]
        selected_rows = ready_rows[:limit] if limit is not None else ready_rows
        parent_container_ids = self._ms_parent_container_ids_by_code(
            extraction_rows=extraction_rows,
            aliquot_rows=aliquot_rows,
            ms_parent_level=ms_parent_level,
        )

        created_rows: list[dict[str, Any]] = []
        skipped_rows: list[dict[str, Any]] = []
        for row in selected_rows:
            parent_container_code = row.get("target_ms_parent_container_id")
            if not isinstance(parent_container_code, str) or not parent_container_code:
                skipped_rows.append(
                    {
                        "filename": row.get("normalized_filename"),
                        "reason": "missing_target_ms_parent_container_id",
                    }
                )
                continue
            parent_container_id = parent_container_ids.get(parent_container_code)
            if parent_container_id is None:
                skipped_rows.append(
                    {
                        "filename": row.get("normalized_filename"),
                        "reason": "target_ms_parent_container_not_resolved",
                        "target_ms_parent_container_id": parent_container_code,
                    }
                )
                continue

            filename = row.get("normalized_filename")
            if not isinstance(filename, str) or not filename:
                skipped_rows.append(
                    {
                        "filename": filename,
                        "reason": "missing_normalized_filename",
                    }
                )
                continue

            payload: dict[str, Any] = {
                "filename": filename,
                "parent_sample_container": parent_container_id,
                "injection_volume": injection_volume,
                "injection_volume_unit": injection_volume_unit_id,
                "injection_method": injection_method_id,
                "instrument_used": instrument_id,
                "status": status,
            }
            if batch_id is not None:
                payload["batch"] = batch_id
            if commit:
                created_rows.append(self._post_item("MS_Data", payload))

        summary = summarize_injection_audit(audit_table)
        return {
            "dry_run": not commit,
            "ready_count": summary["ready_count"],
            "selected_count": len(selected_rows),
            "created_count": len(created_rows),
            "skipped_selected_count": len(skipped_rows),
            "blocked_count": summary["blocked_count"],
            "already_imported_count": summary["already_imported_count"],
            "created_rows": created_rows,
            "skipped_rows": skipped_rows,
        }

    def _build_sample_compact_metadata_table(self, table: MsMetadataTable) -> MsMetadataTable:
        """Return one compact metadata row per original collected sample."""

        compact = select_table_view(table, view="compact")
        rows_by_sample_id: dict[str, list[dict[str, Any]]] = {}
        for row in compact.rows:
            sample_id = row.get("original_sample_id")
            if not isinstance(sample_id, str) or not sample_id:
                continue
            rows_by_sample_id.setdefault(sample_id, []).append(row)

        names_by_sample_id: dict[str, tuple[str, ...]] = {}
        for sample_id, rows in rows_by_sample_id.items():
            names: list[str] = []
            seen: set[str] = set()
            for row in rows:
                for fieldname in ("field_taxon_name", "field_sample_name"):
                    value = row.get(fieldname)
                    if value is None:
                        continue
                    name = str(value).strip()
                    if not name or name in seen:
                        continue
                    seen.add(name)
                    names.append(name)
            names_by_sample_id[sample_id] = tuple(names)

        resolution_by_name = self._resolve_taxon_names(
            {
                name
                for names in names_by_sample_id.values()
                for name in names
            }
        )

        output_rows: list[dict[str, Any]] = []
        for sample_id in sorted(rows_by_sample_id):
            sample_rows = rows_by_sample_id[sample_id]
            profile_modes = {
                str(row.get("profile_mode"))
                for row in sample_rows
                if row.get("profile_mode") not in (None, "")
            }
            collapsed_row = {
                fieldname: self._join_distinct_values(
                    row.get(fieldname) for row in sample_rows
                )
                for fieldname in compact.fieldnames
                if fieldname != "profile_mode"
            }
            has_positive = "positive" in profile_modes
            has_negative = "negative" in profile_modes
            collapsed_row["profile_mode"] = self._collapse_profile_mode_flags(
                has_positive=has_positive,
                has_negative=has_negative,
            )
            collapsed_row["profiled_positive"] = str(has_positive).lower()
            collapsed_row["profiled_negative"] = str(has_negative).lower()
            collapsed_row["profiled_any"] = str(has_positive or has_negative).lower()
            collapsed_row["metadata_lineage_row_count"] = str(len(sample_rows))
            collapsed_row.update(
                self._taxonomy_metadata_for_sample(
                    names_by_sample_id[sample_id],
                    resolution_by_name,
                )
            )
            output_rows.append(collapsed_row)

        fieldnames = self._sample_compact_fieldnames(compact.fieldnames)
        return MsMetadataTable(fieldnames=fieldnames, rows=tuple(output_rows))

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
            fields=(
                "sample_container.container_id,field_data.sample_id,"
                "field_data.qfield_project,field_data.taxon_name,field_data.sample_name"
            ),
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

        metadata_by_sample_id: dict[str, tuple[str, tuple[str, ...]]] = {}
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
            sample_id, qfield_project, species_names = sample_metadata
            metadata_by_sample_id[sample_id] = (qfield_project, species_names)
            modes_by_sample_id.setdefault(sample_id, set()).add(profile_mode)

        profiled_samples = [
            ProfiledSample(
                sample_id=sample_id,
                qfield_project=metadata_by_sample_id[sample_id][0],
                mode=collapse_profile_modes(modes),
                species=(
                    metadata_by_sample_id[sample_id][1][0]
                    if metadata_by_sample_id[sample_id][1]
                    else None
                ),
                species_names=metadata_by_sample_id[sample_id][1],
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

    def summarize_samples_by_project_group(self, group_name: str) -> list[ProjectSampleSummary]:
        """Return collected and profiled sample counts for one configured project group."""

        group_name = group_name.lower()
        project_keys = set(PROJECT_GROUPS[group_name])
        summary_by_project = {
            summary.qfield_project: summary
            for summary in self.summarize_samples_by_project()
            if summary.qfield_project in project_keys
        }
        return [
            ProjectSampleSummary(
                qfield_project=group_name,
                collected_count=sum(
                    summary.collected_count for summary in summary_by_project.values()
                ),
                profiled_count=sum(
                    summary.profiled_count for summary in summary_by_project.values()
                ),
                positive_count=sum(
                    summary.positive_count for summary in summary_by_project.values()
                ),
                negative_count=sum(
                    summary.negative_count for summary in summary_by_project.values()
                ),
                both_count=sum(summary.both_count for summary in summary_by_project.values()),
            )
        ]

    def summarize_species_by_project(self) -> list[ProjectSpeciesSummary]:
        """Return distinct collected and profiled species counts grouped by qfield project."""

        return self._summarize_species_by_project_groups(project_groups=None)

    def summarize_species_by_project_group(self, group_name: str) -> list[ProjectSpeciesSummary]:
        """Return resolved species counts for one configured project group."""

        group_name = group_name.lower()
        return self._summarize_species_by_project_groups(
            project_groups={group_name: set(PROJECT_GROUPS[group_name])}
        )

    def _summarize_species_by_project_groups(
        self,
        *,
        project_groups: dict[str, set[str]] | None,
    ) -> list[ProjectSpeciesSummary]:
        """Return distinct resolved species counts grouped by project or project group."""

        self._authenticate()

        collected_species = self._get_field_species_by_project()
        profiled_samples = self.list_profiled_samples(mode="any")
        resolution_by_name = self._resolve_taxon_names(
            {
                name
                for names in collected_species.values()
                for name in names
            }
            | {
                name
                for sample in profiled_samples
                for name in sample.species_names
            }
        )

        species_by_project: dict[str, dict[str, set[str]]] = {}
        for qfield_project, species in collected_species.items():
            group_key = self._project_summary_group_key(qfield_project, project_groups)
            if group_key is None:
                continue
            project_species = species_by_project.setdefault(
                group_key,
                {
                    "collected": set(),
                    "profiled": set(),
                    "positive": set(),
                    "negative": set(),
                    "both": set(),
                },
            )
            project_species["collected"].update(
                self._resolved_taxon_keys(species, resolution_by_name)
            )

        for sample in profiled_samples:
            group_key = self._project_summary_group_key(sample.qfield_project, project_groups)
            if group_key is None:
                continue
            resolved_sample_species = self._resolved_taxon_keys(
                sample.species_names,
                resolution_by_name,
            )
            if not resolved_sample_species:
                continue

            project_species = species_by_project.setdefault(
                group_key,
                {
                    "collected": set(),
                    "profiled": set(),
                    "positive": set(),
                    "negative": set(),
                    "both": set(),
                },
            )
            project_species["profiled"].update(resolved_sample_species)
            if sample.mode == "positive":
                project_species["positive"].update(resolved_sample_species)
            elif sample.mode == "negative":
                project_species["negative"].update(resolved_sample_species)
            elif sample.mode == "both":
                project_species["both"].update(resolved_sample_species)
            else:
                raise DirectusResponseError(f"Unexpected profiled sample mode: {sample.mode}")

        return [
            ProjectSpeciesSummary(
                qfield_project=qfield_project,
                collected_count=len(project_species["collected"]),
                profiled_count=len(project_species["profiled"]),
                positive_count=len(project_species["positive"]),
                negative_count=len(project_species["negative"]),
                both_count=len(project_species["both"]),
            )
            for qfield_project, project_species in sorted(species_by_project.items())
        ]

    @staticmethod
    def _project_summary_group_key(
        qfield_project: str,
        project_groups: dict[str, set[str]] | None,
    ) -> str | None:
        """Return the output grouping key for a qfield project."""

        if project_groups is None:
            return qfield_project
        for group_name, project_keys in project_groups.items():
            if qfield_project in project_keys:
                return group_name
        return None

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

    def _post_item(self, collection: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Create one Directus item and return the created row payload."""

        response = self._session.post(
            self._url(f"/items/{collection}"),
            json=payload,
            timeout=(10, 60),
        )
        if response.status_code not in {200, 201}:
            raise DirectusError(
                f"Directus POST /items/{collection} failed with status {response.status_code}: "
                f"{response.text}"
            )

        try:
            response_payload = response.json()
        except ValueError as exc:
            raise DirectusResponseError("Directus response was not valid JSON") from exc

        if not isinstance(response_payload, dict):
            raise DirectusResponseError("Directus response payload was not an object")

        data = response_payload.get("data")
        if not isinstance(data, dict):
            raise DirectusResponseError(
                f"Directus {collection} create payload did not contain an object data row"
            )
        return data

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

    def _get_field_species_by_project(self) -> dict[str, set[str]]:
        """Return distinct collected species grouped by qfield project."""

        field_rows = self._get_items(
            collection="Field_Data",
            fields="qfield_project,taxon_name,sample_name",
        )

        species_by_project: dict[str, set[str]] = {}
        for row in field_rows:
            qfield_project = self._read_nested_string(row, "qfield_project")
            if qfield_project is None:
                continue

            species_by_project.setdefault(qfield_project, set())
            species_by_project[qfield_project].update(
                self._read_species_names(row, field_data_prefix=False)
            )

        return species_by_project

    @staticmethod
    def _resolve_project_filter(
        *,
        project: str | None,
        project_group: str | None,
    ) -> set[str] | None:
        """Return the accepted qfield project keys for an optional export filter."""

        if project is not None and project_group is not None:
            raise DirectusResponseError("Use either --project or --project-group, not both")
        if project is not None:
            return {project}
        if project_group is None:
            return None
        group_key = project_group.lower()
        try:
            return set(PROJECT_GROUPS[group_key])
        except KeyError as exc:
            raise DirectusResponseError(f"Unknown project group: {project_group}") from exc

    def _resolve_taxon_names(
        self,
        names: set[str],
    ) -> Mapping[str, TaxonResolution]:
        """Resolve raw taxon names against Catalogue of Life."""

        try:
            return self._taxon_resolver.resolve_names(names)
        except TaxonResolutionError as exc:
            raise DirectusResponseError(str(exc)) from exc

    @staticmethod
    def _resolved_taxon_keys(
        names: Iterable[str],
        resolution_by_name: Mapping[str, TaxonResolution],
    ) -> set[str]:
        """Return Catalogue of Life taxon keys for resolved raw names."""

        keys: set[str] = set()
        for name in names:
            resolution = resolution_by_name.get(name)
            if resolution is None:
                continue
            key = resolution.aggregation_key
            if key is not None:
                keys.add(key)
        return keys

    @staticmethod
    def _join_distinct_values(values: Iterable[Any]) -> str:
        """Join distinct non-empty row values while preserving row order."""

        joined: list[str] = []
        seen: set[str] = set()
        for value in values:
            if value in (None, ""):
                continue
            normalized = str(value).strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            joined.append(normalized)
        return "; ".join(joined)

    @staticmethod
    def _collapse_profile_mode_flags(*, has_positive: bool, has_negative: bool) -> str:
        """Return the compact profile-mode label for positive/negative flags."""

        if has_positive and has_negative:
            return "both"
        if has_positive:
            return "positive"
        if has_negative:
            return "negative"
        return ""

    @staticmethod
    def _taxonomy_metadata_for_sample(
        names: tuple[str, ...],
        resolution_by_name: Mapping[str, TaxonResolution],
    ) -> dict[str, str]:
        """Return taxonomy metadata for the first resolved sample name."""

        resolution = next(
            (
                resolution_by_name[name]
                for name in names
                if name in resolution_by_name
                and resolution_by_name[name].is_catalogue_of_life_match
            ),
            None,
        )
        if resolution is None:
            return {fieldname: "" for fieldname in TAXONOMY_METADATA_FIELDNAMES}

        lineage = DirectusClient._parse_col_lineage(resolution)
        return {
            "resolved_taxon_input_name": resolution.input_name,
            "resolved_taxon_canonical": resolution.canonical_name,
            "resolved_taxon_id": resolution.taxon_id,
            "resolved_taxon_scientific_name": resolution.scientific_name,
            "resolved_taxon_matched_name": resolution.matched_name,
            "resolved_taxon_current_name": resolution.current_name,
            "resolved_taxon_synonym": resolution.synonym,
            "resolved_taxon_source_id": resolution.data_source_id,
            "resolved_taxon_source_title": resolution.data_source_title,
            "resolved_taxon_kind": resolution.kind,
            "resolved_taxon_sort_score": resolution.sort_score,
            "resolved_taxon_match_type": resolution.match_type,
            "resolved_taxon_edit_distance": resolution.edit_distance,
            "resolved_taxon_classification_path": resolution.classification_path,
            "resolved_taxon_kingdom": lineage.get("kingdom", ""),
            "resolved_taxon_phylum": lineage.get("phylum", ""),
            "resolved_taxon_class": lineage.get("class", ""),
            "resolved_taxon_order": lineage.get("order", ""),
            "resolved_taxon_family": lineage.get("family", ""),
            "resolved_taxon_genus": lineage.get("genus", ""),
        }

    @staticmethod
    def _parse_col_lineage(resolution: TaxonResolution) -> dict[str, str]:
        """Extract common upper-taxonomy columns from a COL classification path."""

        parts = [
            part.strip()
            for part in resolution.classification_path.split("|")
            if part.strip()
        ]
        genus = resolution.canonical_name.split(" ", 1)[0] if resolution.canonical_name else ""
        lineage = {
            "kingdom": "Plantae" if "Plantae" in parts else "",
            "phylum": "Tracheophyta" if "Tracheophyta" in parts else "",
            "class": DirectusClient._first_lineage_part_with_suffix(parts, "opsida"),
            "order": DirectusClient._first_lineage_part_with_suffix(parts, "ales"),
            "family": DirectusClient._first_lineage_part_with_suffix(parts, "aceae"),
            "genus": genus,
        }
        return lineage

    @staticmethod
    def _first_lineage_part_with_suffix(parts: list[str], suffix: str) -> str:
        """Return the first COL lineage part with the requested taxonomic suffix."""

        return next((part for part in parts if part.endswith(suffix)), "")

    @staticmethod
    def _sample_compact_fieldnames(compact_fieldnames: tuple[str, ...]) -> tuple[str, ...]:
        """Return ordered fieldnames for the one-row-per-sample compact export."""

        profile_fieldnames = (
            "profile_mode",
            "profiled_positive",
            "profiled_negative",
            "profiled_any",
            "metadata_lineage_row_count",
        )
        base_fieldnames = tuple(
            fieldname
            for fieldname in compact_fieldnames
            if fieldname != "profile_mode"
        )
        return base_fieldnames + profile_fieldnames + TAXONOMY_METADATA_FIELDNAMES

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
    def _ms_parent_container_ids_by_code(
        *,
        extraction_rows: list[dict[str, Any]],
        aliquot_rows: list[dict[str, Any]],
        ms_parent_level: str,
    ) -> dict[str, int]:
        """Return sample container integer ids keyed by container code."""

        if ms_parent_level == "extraction":
            rows = extraction_rows
        elif ms_parent_level == "aliquot":
            rows = aliquot_rows
        else:
            raise ValueError("ms_parent_level must be either 'aliquot' or 'extraction'")

        mapping: dict[str, int] = {}
        for row in rows:
            container = DirectusClient._read_mapping(row, "sample_container")
            container_code = DirectusClient._read_nested_string(
                row,
                "sample_container",
                "container_id",
            )
            if container is None or container_code is None:
                continue
            container_id = container.get("id")
            if isinstance(container_id, int):
                mapping.setdefault(container_code, container_id)
        return mapping

    @staticmethod
    def _build_sample_metadata_map(
        *,
        rows: list[dict[str, Any]],
    ) -> dict[str, tuple[str, str, tuple[str, ...]]]:
        """Build a sample-container to sample metadata map from dried sample rows."""

        mapping: dict[str, tuple[str, str, tuple[str, ...]]] = {}
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
            species_names = DirectusClient._read_species_names(row, field_data_prefix=True)
            mapping[container_id] = (sample_id, qfield_project, species_names)
        return mapping

    @staticmethod
    def _read_species_names(
        row: dict[str, Any],
        *,
        field_data_prefix: bool,
    ) -> tuple[str, ...]:
        """Return distinct species/name labels from taxon_name and sample_name."""

        path_prefix = ("field_data",) if field_data_prefix else ()
        names: list[str] = []
        seen: set[str] = set()
        for field_name in ("taxon_name", "sample_name"):
            value = DirectusClient._read_nested_string(row, *path_prefix, field_name)
            if value is None:
                continue
            normalized = value.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            names.append(normalized)
        return tuple(names)

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
