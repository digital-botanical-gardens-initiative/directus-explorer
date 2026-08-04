"""Generate a read-only, project-scoped taxon conflict review queue.

This command never writes to Directus. It groups repeated source-field conflicts,
resolves each distinct verbatim name against a pinned Catalogue of Life release,
and leaves the human decision columns empty.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import subprocess
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from directus_explorer.taxon_curation import (
    COL_API_BASE_URL,
    COL_DATASET_DOI,
    COL_DATASET_KEY,
    COL_DATASET_VERSION,
    ChecklistBankColClient,
    ColNameResolution,
)
from directus_explorer.taxon_curation_review import preserve_review_decisions
from directus_explorer.taxonomy import CatalogueOfLifeTaxonResolver, TaxonResolution

DEFAULT_INPUT = Path(
    "data/output/directus_integrity/taxon_name_unified_audit/"
    "taxon_name_unified_missing_expected_conflict.tsv"
)
DEFAULT_OUTPUT_ROOT = Path("data/working/taxonomy_curation")
SOURCE_FIELDS = (
    "taxon_name_final",
    "taxon_name",
    "sample_name",
    "name_proposition",
    "match_name",
    "MatchedCanonical",
    "species_name",
)


def main() -> None:
    args = _parse_args()
    rows = _read_project_rows(args.input, args.project)
    groups = _group_conflicts(rows)
    names = sorted(
        {
            row[field].strip()
            for row in rows
            for field in SOURCE_FIELDS
            if row.get(field, "").strip()
            and not _is_placeholder_name(row[field])
        }
    )
    if not groups:
        raise SystemExit(f"No conflict rows found for project {args.project!r} in {args.input}")

    client = ChecklistBankColClient()
    dataset_metadata = client.validate_pinned_dataset()
    col_resolutions = {name: client.resolve_name(name) for name in names}
    gn_resolutions, gn_version = _run_gnverifier(names, enabled=args.gnverifier)
    verified_fuzzy_suggestions = _verify_gnverifier_suggestions(
        client, col_resolutions, gn_resolutions
    )

    output_dir = args.output_root / _release_slug() / f"{args.project}_round_{args.round:03d}"
    output_dir.mkdir(parents=True, exist_ok=True)
    review_path = output_dir / f"{args.project}_round_{args.round:03d}_review.tsv"
    resolution_path = (
        output_dir / f"{args.project}_round_{args.round:03d}_name_resolution.tsv"
    )
    metadata_path = output_dir / f"{args.project}_round_{args.round:03d}_metadata.json"

    review_rows = _build_review_rows(
        groups,
        project=args.project,
        round_number=args.round,
        col_resolutions=col_resolutions,
    )
    if review_path.exists():
        review_rows = preserve_review_decisions(review_rows, _read_tsv(review_path))
    resolution_rows = _build_resolution_rows(
        names,
        col_resolutions,
        gn_resolutions,
        verified_fuzzy_suggestions,
    )
    _write_tsv(review_path, review_rows)
    _write_tsv(resolution_path, resolution_rows)
    _write_metadata(
        metadata_path,
        project=args.project,
        round_number=args.round,
        input_path=args.input,
        dataset_metadata=dataset_metadata,
        gn_version=gn_version,
        review_rows=review_rows,
        resolution_rows=resolution_rows,
    )
    print(f"Wrote {len(review_rows)} grouped decisions to {review_path}")
    print(f"Wrote {len(resolution_rows)} distinct name resolutions to {resolution_path}")
    print("No Directus data was changed.")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--round", type=int, default=1)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument(
        "--gnverifier",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Include a moving-current CoL gnverifier diagnostic (default: enabled).",
    )
    return parser.parse_args()


def _read_project_rows(path: Path, project: str) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return [
            {key: value or "" for key, value in row.items()}
            for row in csv.DictReader(handle, delimiter="\t")
            if row.get("project") == project
        ]


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return [
            {key: value or "" for key, value in row.items()}
            for row in csv.DictReader(handle, delimiter="\t")
        ]


def _group_conflicts(
    rows: list[dict[str, str]],
) -> list[list[dict[str, str]]]:
    grouped: dict[tuple[str, ...], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[tuple(row.get(field, "").strip() for field in SOURCE_FIELDS)].append(row)
    return sorted(grouped.values(), key=lambda group: _review_id(group[0]))


def _review_id(row: dict[str, str]) -> str:
    evidence = json.dumps(
        {field: row.get(field, "").strip() for field in SOURCE_FIELDS},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(evidence.encode()).hexdigest()[:12]


def _is_placeholder_name(value: str) -> bool:
    normalized = " ".join(value.casefold().split())
    return normalized in {
        "-",
        "?",
        "aaunknown",
        "indet",
        "indet.",
        "n/a",
        "na",
        "none",
        "null",
        "unknown",
    } or "unknown" in normalized


def _build_review_rows(
    groups: list[list[dict[str, str]]],
    *,
    project: str,
    round_number: int,
    col_resolutions: dict[str, ColNameResolution],
) -> list[dict[str, str | int]]:
    output: list[dict[str, str | int]] = []
    for group in groups:
        representative = group[0]
        proposed_name = representative.get("expected_unified_taxon_name", "").strip()
        resolution = col_resolutions[proposed_name]
        output.append(
            {
                "review_id": _review_id(representative),
                "project": project,
                "round": round_number,
                "affected_row_count": len(group),
                "field_data_ids": ";".join(row["field_data_id"] for row in group),
                "sample_ids": ";".join(row["sample_id"] for row in group),
                **{field: representative.get(field, "").strip() for field in SOURCE_FIELDS},
                "proposal_source": representative.get(
                    "expected_unified_taxon_source", ""
                ).strip(),
                "proposal_verbatim_name": proposed_name,
                "proposal_basis": (
                    "existing_primary_field_precedence_requires_human_review"
                    if representative.get("source_taxon_field_conflict", "").strip()
                    else "single_non_conflicting_source_candidate"
                ),
                "col_match_type": resolution.match_type,
                "col_matched_name": resolution.matched_name,
                "col_matched_status": resolution.matched_status,
                "col_accepted_name": resolution.accepted_name,
                "col_accepted_usage_id": resolution.accepted_usage_id,
                "col_accepted_rank": resolution.accepted_rank,
                "decision": "",
                "chosen_source_field": "",
                "chosen_verbatim_name": "",
                "chosen_col_accepted_name": "",
                "chosen_col_accepted_usage_id": "",
                "chosen_col_accepted_rank": "",
                "confidence": "",
                "reviewer": "",
                "reviewed_at": "",
                "review_note": "",
            }
        )
    return output


def _build_resolution_rows(
    names: list[str],
    col_resolutions: dict[str, ColNameResolution],
    gn_resolutions: dict[str, TaxonResolution],
    verified_fuzzy_suggestions: dict[str, tuple[str, ColNameResolution]],
) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    for name in names:
        col = col_resolutions[name]
        gn = gn_resolutions.get(name)
        suggestion_name, suggestion_col = verified_fuzzy_suggestions.get(
            name, ("", _empty_col_resolution())
        )
        output.append(
            {
                "verbatim_name": name,
                "parsed_name": col.parsed_name,
                "parsed_authorship": col.parsed_authorship,
                "parsed_rank": col.parsed_rank,
                "parsed_cultivar": col.parsed_cultivar,
                "parse_status": col.parse_status,
                "col_dataset_key": str(COL_DATASET_KEY),
                "col_dataset_version": COL_DATASET_VERSION,
                "col_dataset_doi": COL_DATASET_DOI,
                "col_match": str(col.match).lower(),
                "col_match_type": col.match_type,
                "col_matched_usage_id": col.matched_usage_id,
                "col_matched_name": col.matched_name,
                "col_matched_authorship": col.matched_authorship,
                "col_matched_rank": col.matched_rank,
                "col_matched_status": col.matched_status,
                "col_accepted_usage_id": col.accepted_usage_id,
                "col_accepted_name": col.accepted_name,
                "col_accepted_authorship": col.accepted_authorship,
                "col_accepted_rank": col.accepted_rank,
                "col_issues": col.issues,
                "col_fuzzy_candidates": col.fuzzy_candidates,
                "gnverifier_taxon_id": gn.taxon_id if gn else "",
                "gnverifier_canonical_name": gn.canonical_name if gn else "",
                "gnverifier_current_name": gn.current_name if gn else "",
                "gnverifier_match_type": gn.match_type if gn else "",
                "gnverifier_sort_score": gn.sort_score if gn else "",
                "gnverifier_edit_distance": gn.edit_distance if gn else "",
                "gnverifier_error": gn.error if gn else "",
                "fuzzy_suggestion_name": suggestion_name,
                "fuzzy_suggestion_col_match_type": suggestion_col.match_type,
                "fuzzy_suggestion_col_accepted_usage_id": (
                    suggestion_col.accepted_usage_id
                ),
                "fuzzy_suggestion_col_accepted_name": suggestion_col.accepted_name,
                "fuzzy_suggestion_col_accepted_authorship": (
                    suggestion_col.accepted_authorship
                ),
                "fuzzy_suggestion_col_accepted_rank": suggestion_col.accepted_rank,
            }
        )
    return output


def _verify_gnverifier_suggestions(
    client: ChecklistBankColClient,
    col_resolutions: dict[str, ColNameResolution],
    gn_resolutions: dict[str, TaxonResolution],
) -> dict[str, tuple[str, ColNameResolution]]:
    verified: dict[str, tuple[str, ColNameResolution]] = {}
    for verbatim_name, col in col_resolutions.items():
        gn = gn_resolutions.get(verbatim_name)
        if (
            col.is_usable_match
            or not gn
            or not gn.is_catalogue_of_life_match
            or gn.match_type.lower() != "fuzzy"
        ):
            continue
        suggestion = gn.current_name or gn.matched_name or gn.canonical_name
        if not suggestion:
            continue
        verified[verbatim_name] = (suggestion, client.resolve_name(suggestion))
    return verified


def _empty_col_resolution() -> ColNameResolution:
    return ColNameResolution(
        verbatim_name="",
        parsed_name="",
        parsed_authorship="",
        parsed_rank="",
        parsed_cultivar="",
        parse_status="",
        match=False,
        match_type="",
        matched_usage_id="",
        matched_name="",
        matched_authorship="",
        matched_rank="",
        matched_status="",
        accepted_usage_id="",
        accepted_name="",
        accepted_authorship="",
        accepted_rank="",
        issues="",
        fuzzy_candidates="",
    )


def _run_gnverifier(
    names: list[str], *, enabled: bool
) -> tuple[dict[str, TaxonResolution], str]:
    if not enabled:
        return {}, "disabled"
    executable = shutil.which("gnverifier")
    if not executable:
        raise SystemExit("gnverifier was enabled but is not available on PATH")
    version = subprocess.run(
        [executable, "--version"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    resolutions = CatalogueOfLifeTaxonResolver().resolve_names(names)
    return dict(resolutions), version


def _write_tsv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def _write_metadata(
    path: Path,
    *,
    project: str,
    round_number: int,
    input_path: Path,
    dataset_metadata: dict[str, Any],
    gn_version: str,
    review_rows: list[dict[str, Any]],
    resolution_rows: list[dict[str, str]],
) -> None:
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "read_only_review_queue",
        "directus_writes": False,
        "project": project,
        "round": round_number,
        "input": str(input_path),
        "input_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
        "review_group_count": len(review_rows),
        "affected_field_data_count": sum(
            int(row["affected_row_count"]) for row in review_rows
        ),
        "distinct_name_count": len(resolution_rows),
        "col_api": COL_API_BASE_URL,
        "col_dataset": {
            key: dataset_metadata.get(key)
            for key in ("key", "alias", "title", "version", "issued", "doi", "origin")
        },
        "gnverifier": {
            "role": "unversioned_secondary_diagnostic_only",
            "version": gn_version,
        },
        "decision_values": ["accept_proposal", "override", "unresolved", "defer"],
        "confidence_values": ["high", "medium", "low"],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _release_slug() -> str:
    return f"col_{COL_DATASET_VERSION.removesuffix(' XR').replace('-', '_')}_xr"


if __name__ == "__main__":
    main()
