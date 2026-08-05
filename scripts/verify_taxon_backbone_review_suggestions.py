"""Verify global gnverifier review suggestions against the pinned CoL release.

This command writes local review metadata only and never changes Directus.
"""

from __future__ import annotations

import argparse
import csv
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from directus_explorer.taxon_backbone import (
    append_resolution_cache,
    load_resolution_cache,
    write_tsv,
)
from directus_explorer.taxon_curation import (
    ChecklistBankColClient,
    ColCurationError,
    ColNameResolution,
)

DEFAULT_ROOT = Path("data/working/taxonomy_backbone/col_2026_07_17_xr")


def main() -> None:
    args = _parse_args()
    rows = _read_tsv(args.review)
    suggestion_by_name = {
        row["taxon_name_unified"]: (
            row.get("gnverifier_current_name", "").strip()
            or row.get("gnverifier_canonical_name", "").strip()
        )
        for row in rows
    }
    suggestions = sorted({value for value in suggestion_by_name.values() if value})
    ChecklistBankColClient(timeout=args.timeout).validate_pinned_dataset()
    cached = load_resolution_cache(args.cache)
    resolutions = {name: cached[name] for name in suggestions if name in cached}
    pending = [name for name in suggestions if name not in resolutions]
    print(
        f"Suggestions: {len(suggestions)}; cached: {len(resolutions)}; pending: {len(pending)}",
        flush=True,
    )
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures: dict[Future[ColNameResolution], str] = {
            executor.submit(_resolve_with_retry, name, args.timeout, args.retries): name
            for name in pending
        }
        for completed, future in enumerate(as_completed(futures), start=1):
            name = futures[future]
            resolved = future.result()
            resolutions[name] = resolved
            append_resolution_cache(args.cache, resolved)
            if completed % 100 == 0 or completed == len(pending):
                print(f"Verified {completed}/{len(pending)} pending suggestions", flush=True)

    output: list[dict[str, Any]] = []
    for row in rows:
        suggestion = suggestion_by_name[row["taxon_name_unified"]]
        suggestion_resolution = resolutions.get(suggestion)
        verified_id = suggestion_resolution.accepted_usage_id if suggestion_resolution else ""
        current_id = row.get("current_col_id", "")
        if not suggestion:
            verification = "no_suggestion"
        elif not suggestion_resolution or not suggestion_resolution.is_usable_match:
            verification = "not_verified"
        elif current_id and verified_id == current_id:
            verification = "verified_same_as_current"
        else:
            verification = "verified_candidate"
        output.append(
            {
                **row,
                "verified_suggestion_name": suggestion,
                "verified_col_id": verified_id,
                "verified_col_accepted_name": (
                    suggestion_resolution.accepted_name if suggestion_resolution else ""
                ),
                "verified_col_accepted_authorship": (
                    suggestion_resolution.accepted_authorship if suggestion_resolution else ""
                ),
                "verified_col_accepted_rank": (
                    suggestion_resolution.accepted_rank if suggestion_resolution else ""
                ),
                "verified_col_match_type": (
                    suggestion_resolution.match_type if suggestion_resolution else ""
                ),
                "verified_col_matched_status": (
                    suggestion_resolution.matched_status if suggestion_resolution else ""
                ),
                "suggestion_verification_status": verification,
            }
        )
    write_tsv(args.output, output)
    print(f"Wrote pinned suggestion verification to {args.output}")
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


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return [
            {key: value or "" for key, value in row.items()}
            for row in csv.DictReader(handle, delimiter="\t")
        ]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--review", type=Path, default=DEFAULT_ROOT / "taxon_name_unified_col_review.tsv"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_ROOT / "taxon_name_unified_col_review_verified.tsv",
    )
    parser.add_argument(
        "--cache", type=Path, default=DEFAULT_ROOT / "review_suggestion_cache.jsonl"
    )
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--retries", type=int, default=3)
    return parser.parse_args()


if __name__ == "__main__":
    main()
