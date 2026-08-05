"""Resolve every distinct taxon_name_unified against a pinned CoL release.

This command is resumable and local-write-only. It never changes Directus.
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from directus_explorer.taxon_backbone import (
    append_resolution_cache,
    build_backbone_rows,
    build_field_data_mapping,
    inventory_unified_names,
    load_resolution_cache,
    normalize_global_lookup_name,
    read_unified_taxon_rows,
    resolution_status,
    write_tsv,
)
from directus_explorer.taxon_curation import (
    COL_DATASET_DOI,
    COL_DATASET_KEY,
    COL_DATASET_VERSION,
    ChecklistBankColClient,
    ColCurationError,
    ColNameResolution,
)

DEFAULT_INPUT = Path(
    "data/output/directus_integrity/taxon_name_unified_audit/"
    "taxon_name_unified_audit_all.tsv"
)
DEFAULT_OUTPUT_ROOT = Path("data/working/taxonomy_backbone")


def main() -> None:
    args = _parse_args()
    output_dir = args.output_root / _release_slug()
    cache_path = output_dir / "col_resolution_cache_unrestricted_normalized_v1.jsonl"

    field_rows = read_unified_taxon_rows(args.input)
    inventory = inventory_unified_names(field_rows)
    if args.limit:
        inventory = inventory[: args.limit]
        selected_names = {str(row["taxon_name_unified"]) for row in inventory}
        field_rows = [
            row for row in field_rows if row["stored_taxon_name_unified"].strip() in selected_names
        ]

    client = ChecklistBankColClient(timeout=args.timeout)
    client.validate_pinned_dataset()
    cached = {} if args.refresh else load_resolution_cache(cache_path)
    names = [str(row["taxon_name_unified"]) for row in inventory]
    resolutions = {name: cached[name] for name in names if name in cached}
    pending = [name for name in names if name not in resolutions]
    print(
        f"Distinct names: {len(names)}; cached: {len(resolutions)}; "
        f"pending: {len(pending)}",
        flush=True,
    )

    if pending:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures: dict[Future[ColNameResolution], str] = {
                executor.submit(_resolve_global_name, name, args.timeout, args.retries): name
                for name in pending
            }
            for completed, future in enumerate(as_completed(futures), start=1):
                name = futures[future]
                try:
                    resolution = future.result()
                except ColCurationError as exc:
                    raise SystemExit(
                        f"Resolution failed after retries for {name!r}: {exc}. "
                        "Rerun the same command to resume from the cache."
                    ) from exc
                resolutions[name] = resolution
                append_resolution_cache(cache_path, resolution)
                if completed % args.progress_every == 0 or completed == len(pending):
                    print(
                        f"Resolved {completed}/{len(pending)} pending names "
                        f"({len(resolutions)}/{len(names)} total)",
                        flush=True,
                    )

    backbone_rows = build_backbone_rows(inventory, resolutions)
    mapping_rows = build_field_data_mapping(field_rows, resolutions)
    backbone_path = output_dir / "taxon_name_unified_col_backbone.tsv"
    mapping_path = output_dir / "field_data_col_mapping.tsv"
    summary_path = output_dir / "taxon_name_unified_col_summary.json"
    write_tsv(backbone_path, backbone_rows)
    write_tsv(mapping_path, mapping_rows)

    statuses = Counter(resolution_status(resolutions[name]) for name in names)
    record_statuses = Counter(
        {
            status: sum(
                int(row["field_data_count"])
                for row in backbone_rows
                if row["taxon_resolution_status"] == status
            )
            for status in statuses
        }
    )
    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "directus_writes": False,
        "input": str(args.input),
        "col_dataset": {
            "key": COL_DATASET_KEY,
            "version": COL_DATASET_VERSION,
            "doi": COL_DATASET_DOI,
        },
        "matching_scope": "all_kingdoms",
        "lookup_normalization": "underscores_to_spaces_then_parsed_name_fallback",
        "distinct_name_count": len(names),
        "field_data_count": len(mapping_rows),
        "distinct_names_with_col_id": sum(
            bool(resolutions[name].accepted_usage_id) for name in names
        ),
        "field_data_with_col_id": sum(
            bool(row["taxon_col_id"]) for row in mapping_rows
        ),
        "resolution_status_by_distinct_name": dict(sorted(statuses.items())),
        "resolution_status_by_field_data": dict(sorted(record_statuses.items())),
        "backbone_tsv": str(backbone_path),
        "field_data_mapping_tsv": str(mapping_path),
        "cache": str(cache_path),
    }
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {backbone_path}")
    print(f"Wrote {mapping_path}")
    print(f"Wrote {summary_path}")
    print("No Directus data was changed.")


def _resolve_with_retry(name: str, timeout: float, retries: int) -> ColNameResolution:
    client = ChecklistBankColClient(timeout=timeout)
    last_error: ColCurationError | None = None
    for attempt in range(retries + 1):
        try:
            return client.resolve_name(name, kingdom=None)
        except ColCurationError as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(2**attempt)
    assert last_error is not None
    raise last_error


def _resolve_global_name(name: str, timeout: float, retries: int) -> ColNameResolution:
    """Resolve globally, retrying safe normalizations when the raw match is unusable."""

    initial = _resolve_with_retry(name, timeout, retries)
    if initial.is_usable_match:
        return initial

    candidates = [normalize_global_lookup_name(name), initial.parsed_name]
    seen = {name}
    for candidate in candidates:
        candidate = normalize_global_lookup_name(candidate)
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        fallback = _resolve_with_retry(candidate, timeout, retries)
        if fallback.is_usable_match:
            return replace(
                fallback,
                verbatim_name=name,
                lookup_name=candidate,
                parsed_cultivar=initial.parsed_cultivar or fallback.parsed_cultivar,
            )
    return initial


def _release_slug() -> str:
    return "col_" + COL_DATASET_VERSION.lower().replace("-", "_").replace(" ", "_")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--progress-every", type=int, default=100)
    parser.add_argument("--limit", type=int, help="Resolve only the first N names for diagnostics.")
    parser.add_argument(
        "--refresh", action="store_true", help="Ignore existing cached resolutions."
    )
    args = parser.parse_args()
    if args.workers < 1 or args.progress_every < 1 or args.retries < 0:
        parser.error("workers and progress-every must be positive; retries cannot be negative")
    return args


if __name__ == "__main__":
    main()
