"""Export one TSV mapping each Directus MS filename to its original sample id."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from directus_explorer.config import load_settings
from directus_explorer.directus import DirectusClient, DirectusError, DirectusResponseError

DEFAULT_OUTPUT = Path("data/output/ms_file_handover/ms_filename_sample_id_by_project.tsv")
DEFAULT_SUMMARY = Path("data/output/ms_file_handover/ms_filename_sample_id_by_project_summary.json")
DEFAULT_UNRESOLVED = Path("data/output/ms_file_handover/ms_filename_sample_id_unresolved.tsv")
DEFAULT_PROJECT_DIR = Path("data/output/ms_file_handover/by_project")


def main() -> None:
    args = _parse_args()
    client = DirectusClient(load_settings(env_file=args.env_file))
    client._authenticate()

    field_rows = client._get_items(
        collection="Field_Data",
        fields="id,sample_id,qfield_project,taxon_name_unified,taxon_name,sample_name",
    )
    dried_rows = client._get_items(
        collection="Dried_Samples_Data",
        fields="id,sample_container.container_id,field_data.sample_id",
    )
    extraction_rows = client._get_items(
        collection="Extraction_Data",
        fields="id,sample_container.container_id,parent_sample_container.container_id",
    )
    aliquot_rows = client._get_items(
        collection="Aliquoting_Data",
        fields="id,sample_container.container_id,parent_sample_container.container_id",
    )
    ms_rows = client._get_items(
        collection="MS_Data",
        fields="id,filename,parent_sample_container.container_id,injection_method.method_name",
    )

    rows = _build_handover_rows(
        client=client,
        field_rows=field_rows,
        dried_rows=dried_rows,
        extraction_rows=extraction_rows,
        aliquot_rows=aliquot_rows,
        ms_rows=ms_rows,
    )
    if args.project:
        rows = [row for row in rows if row["qfield_project"] == args.project]
    unresolved_rows = [row for row in rows if row["resolution_status"] != "resolved"]
    output_rows = rows if args.include_unresolved else [
        row for row in rows if row["resolution_status"] == "resolved"
    ]

    _write_tsv(args.output, output_rows)
    _write_tsv(args.unresolved_output, unresolved_rows)
    project_paths = _write_project_tsvs(args.project_output_dir, output_rows)
    _write_summary(
        args.summary,
        all_rows=rows,
        output_rows=output_rows,
        unresolved_rows=unresolved_rows,
        output_path=args.output,
        unresolved_output_path=args.unresolved_output,
        project_paths=project_paths,
    )
    print(f"Wrote {len(output_rows)} MS filename handover rows to {args.output}")
    print(f"Wrote {len(project_paths)} project handover TSVs to {args.project_output_dir}")
    print(f"Wrote {len(unresolved_rows)} unresolved review rows to {args.unresolved_output}")
    print(f"Summary written to {args.summary}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--unresolved-output", type=Path, default=DEFAULT_UNRESOLVED)
    parser.add_argument("--project-output-dir", type=Path, default=DEFAULT_PROJECT_DIR)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--project", default=None, help="Optional qfield_project filter.")
    parser.add_argument(
        "--include-unresolved",
        action="store_true",
        help="Include unresolved MS rows in the main handover TSV.",
    )
    return parser.parse_args()


def _build_handover_rows(
    *,
    client: DirectusClient,
    field_rows: list[dict[str, Any]],
    dried_rows: list[dict[str, Any]],
    extraction_rows: list[dict[str, Any]],
    aliquot_rows: list[dict[str, Any]],
    ms_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    field_by_sample_id = _field_by_sample_id(client, field_rows)
    dried_sample_by_container = _dried_sample_by_container(client, dried_rows)
    extraction_parent_by_child = _relation_parent_by_child(client, extraction_rows)
    aliquot_parent_by_child = _relation_parent_by_child(client, aliquot_rows)

    output_rows: list[dict[str, Any]] = []
    for ms_row in ms_rows:
        parent_container_id = client._read_nested_string(
            ms_row,
            "parent_sample_container",
            "container_id",
        )
        resolution = _resolve_original_sample(
            parent_container_id=parent_container_id,
            field_by_sample_id=field_by_sample_id,
            dried_sample_by_container=dried_sample_by_container,
            extraction_parent_by_child=extraction_parent_by_child,
            aliquot_parent_by_child=aliquot_parent_by_child,
        )
        field_row = field_by_sample_id.get(resolution["sample_id"])
        output_rows.append(
            {
                "qfield_project": _clean(field_row.get("qfield_project")) if field_row else "",
                "sample_id": resolution["sample_id"],
                "ms_filename": _clean(ms_row.get("filename")),
                "ms_data_id": _clean(ms_row.get("id")),
                "ms_parent_container_id": parent_container_id or "",
                "resolution_path": resolution["resolution_path"],
                "resolution_status": resolution["resolution_status"],
                "injection_method": client._read_nested_string(
                    ms_row,
                    "injection_method",
                    "method_name",
                )
                or "",
                "taxon_name_unified": (
                    _clean(field_row.get("taxon_name_unified")) if field_row else ""
                ),
                "taxon_name": _clean(field_row.get("taxon_name")) if field_row else "",
                "sample_name": _clean(field_row.get("sample_name")) if field_row else "",
            }
        )

    return sorted(
        output_rows,
        key=lambda row: (
            row["qfield_project"],
            row["sample_id"],
            row["ms_filename"],
            row["ms_data_id"],
        ),
    )


def _resolve_original_sample(
    *,
    parent_container_id: str | None,
    field_by_sample_id: dict[str, dict[str, Any]],
    dried_sample_by_container: dict[str, str],
    extraction_parent_by_child: dict[str, str],
    aliquot_parent_by_child: dict[str, str],
) -> dict[str, str]:
    if parent_container_id is None:
        return {
            "sample_id": "",
            "resolution_path": "",
            "resolution_status": "missing_ms_parent_container",
        }

    if parent_container_id in dried_sample_by_container:
        return {
            "sample_id": dried_sample_by_container[parent_container_id],
            "resolution_path": "ms_parent_is_dried_sample_container",
            "resolution_status": "resolved",
        }

    extraction_container_id = aliquot_parent_by_child.get(parent_container_id)
    if extraction_container_id is not None:
        sample_id = _sample_id_from_extraction_parent(
            extraction_container_id,
            field_by_sample_id=field_by_sample_id,
            dried_sample_by_container=dried_sample_by_container,
            extraction_parent_by_child=extraction_parent_by_child,
        )
        return {
            "sample_id": sample_id,
            "resolution_path": "aliquot_to_extraction_to_original_sample",
            "resolution_status": "resolved" if sample_id else "unresolved",
        }

    sample_id = _sample_id_from_extraction_parent(
        parent_container_id,
        field_by_sample_id=field_by_sample_id,
        dried_sample_by_container=dried_sample_by_container,
        extraction_parent_by_child=extraction_parent_by_child,
    )
    if sample_id:
        return {
            "sample_id": sample_id,
            "resolution_path": "extraction_to_original_sample",
            "resolution_status": "resolved",
        }

    if parent_container_id in field_by_sample_id:
        return {
            "sample_id": parent_container_id,
            "resolution_path": "ms_parent_is_field_sample_id",
            "resolution_status": "resolved",
        }

    return {
        "sample_id": "",
        "resolution_path": "no_lineage_match",
        "resolution_status": "unresolved",
    }


def _sample_id_from_extraction_parent(
    extraction_container_id: str,
    *,
    field_by_sample_id: dict[str, dict[str, Any]],
    dried_sample_by_container: dict[str, str],
    extraction_parent_by_child: dict[str, str],
) -> str:
    original_container_id = extraction_parent_by_child.get(extraction_container_id, "")
    if original_container_id in dried_sample_by_container:
        return dried_sample_by_container[original_container_id]
    if original_container_id in field_by_sample_id:
        return original_container_id
    return ""


def _field_by_sample_id(
    client: DirectusClient,
    rows: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for row in rows:
        sample_id = client._read_nested_string(row, "sample_id")
        if sample_id is not None:
            output.setdefault(sample_id, row)
    return output


def _dried_sample_by_container(
    client: DirectusClient,
    rows: list[dict[str, Any]],
) -> dict[str, str]:
    output: dict[str, str] = {}
    for row in rows:
        container_id = client._read_nested_string(row, "sample_container", "container_id")
        sample_id = client._read_nested_string(row, "field_data", "sample_id")
        if container_id is not None and sample_id is not None:
            output.setdefault(container_id, sample_id)
    return output


def _relation_parent_by_child(
    client: DirectusClient,
    rows: list[dict[str, Any]],
) -> dict[str, str]:
    output: dict[str, str] = {}
    for row in rows:
        child_id = client._read_nested_string(row, "sample_container", "container_id")
        parent_id = client._read_nested_string(row, "parent_sample_container", "container_id")
        if child_id is not None and parent_id is not None:
            output.setdefault(child_id, parent_id)
    return output


def _write_tsv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = (
        "qfield_project",
        "sample_id",
        "ms_filename",
        "ms_data_id",
        "ms_parent_container_id",
        "resolution_path",
        "resolution_status",
        "injection_method",
        "taxon_name_unified",
        "taxon_name",
        "sample_name",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def _write_project_tsvs(project_output_dir: Path, rows: list[dict[str, Any]]) -> dict[str, str]:
    rows_by_project: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        rows_by_project.setdefault(row["qfield_project"], []).append(row)

    project_output_dir.mkdir(parents=True, exist_ok=True)
    output_paths: dict[str, str] = {}
    for project, project_rows in sorted(rows_by_project.items()):
        path = project_output_dir / f"{_safe_filename(project)}__ms_filename_sample_id.tsv"
        _write_tsv(path, project_rows)
        output_paths[project] = str(path)
    return output_paths


def _write_summary(
    path: Path,
    *,
    all_rows: list[dict[str, Any]],
    output_rows: list[dict[str, Any]],
    unresolved_rows: list[dict[str, Any]],
    output_path: Path,
    unresolved_output_path: Path,
    project_paths: dict[str, str],
) -> None:
    summary = {
        "output_path": str(output_path),
        "unresolved_output_path": str(unresolved_output_path),
        "project_output_paths": project_paths,
        "directus_ms_row_count": len(all_rows),
        "handover_row_count": len(output_rows),
        "resolved_count": sum(1 for row in all_rows if row["resolution_status"] == "resolved"),
        "unresolved_count": len(unresolved_rows),
        "rows_by_project": dict(Counter(row["qfield_project"] for row in output_rows)),
        "unresolved_rows_by_parent_container_prefix": dict(
            Counter(
                row["ms_parent_container_id"].split("_", 1)[0] or "UNKNOWN"
                for row in unresolved_rows
            )
        ),
        "rows_by_resolution_status": dict(Counter(row["resolution_status"] for row in all_rows)),
        "rows_by_resolution_path": dict(Counter(row["resolution_path"] for row in all_rows)),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _safe_filename(value: str) -> str:
    safe = "".join(character if character.isalnum() else "_" for character in value.strip())
    return safe.strip("_") or "unknown_project"


if __name__ == "__main__":
    try:
        main()
    except (DirectusError, DirectusResponseError) as exc:
        raise SystemExit(str(exc)) from exc
