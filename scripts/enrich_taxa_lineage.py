"""Preview or apply CoL parent links for taxa referenced by observations.

A commit requires a previously reviewed --preview file. Directus and the
validated pinned CoL release are rechecked against it before any write;
validated CoL usages are cached locally. Field_Data is never edited.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

from directus_explorer.config import SettingsError, load_settings
from directus_explorer.directus import DirectusClient, DirectusError, DirectusResponseError
from directus_explorer.taxon_curation import (
    COL_DATASET_DOI,
    COL_DATASET_KEY,
    COL_DATASET_VERSION,
    ChecklistBankColClient,
    ColCurationError,
)
from directus_explorer.taxon_lineage import (
    TAXA_FIELDS,
    build_lineage,
    observed_col_ids,
    plan_lineage,
)

DEFAULT_OUTPUT = Path("data/working/taxonomy_backbone/lineage")


def snapshot(
    directus: DirectusClient,
    col: ChecklistBankColClient,
    *,
    cache_path: Path | None = None,
) -> dict[str, Any]:
    """Create a complete proposal from live observations and one validated CoL release."""
    col.validate_pinned_dataset()
    taxa = directus._get_items(collection="Taxa", fields=TAXA_FIELDS)
    fields = directus._get_items(
        collection="Field_Data",
        fields="id,taxon_ref,taxon_col_id,taxon_backbone_version",
    )
    observed = observed_col_ids(fields, taxa)
    lineage = build_lineage(col, observed, cache_path=cache_path)
    missing, edges, conflicts = plan_lineage(lineage, taxa)
    return {
        "dataset": {
            "key": COL_DATASET_KEY,
            "version": COL_DATASET_VERSION,
            "doi": COL_DATASET_DOI,
        },
        "observed_col_ids": observed,
        "lineage": lineage,
        "missing_col_ids": [row["col_id"] for row in missing],
        "new_parent_links": edges,
        "conflicts": conflicts,
    }


def apply(directus: DirectusClient, proposal: dict[str, Any], journal: Path) -> None:
    """Write ancestors first, then connect pre-existing leaves; verify all edges."""
    by_col_id = {
        str(row["col_id"]): row
        for row in directus._get_items(collection="Taxa", fields=TAXA_FIELDS)
    }
    missing = set(proposal["missing_col_ids"])
    journal.parent.mkdir(parents=True, exist_ok=True)
    # Open before the first write so a partial run always leaves a durable journal.
    with journal.open("x", encoding="utf-8") as handle:
        for row in proposal["lineage"]:
            col_id = row["col_id"]
            parent_id = (
                int(by_col_id[row["parent_col_id"]]["id"])
                if row["parent_col_id"] else None
            )
            if col_id in missing:
                created = directus._post_item("Taxa", {
                    **{key: row[key] for key in (
                        "col_id", "scientific_name", "authorship", "rank", "backbone_version",
                        "backbone_dataset_key", "backbone_doi",
                    )},
                    "backbone_dataset_key": int(row["backbone_dataset_key"]),
                    "parent_ref": parent_id,
                })
                if str(created.get("col_id")) != col_id or not created.get("id"):
                    raise DirectusResponseError(f"Unexpected created Taxa row for {col_id}")
                by_col_id[col_id] = created
                _record(handle, "create", col_id, created["id"], parent_id)
            elif row["parent_col_id"] and not by_col_id[col_id].get("parent_ref"):
                directus._patch_item(
                    collection="Taxa", item_id=int(by_col_id[col_id]["id"]),
                    payload={"parent_ref": parent_id},
                )
                by_col_id[col_id]["parent_ref"] = parent_id
                _record(handle, "link", col_id, by_col_id[col_id]["id"], parent_id)

    verified = directus._get_items(collection="Taxa", fields=TAXA_FIELDS)
    missing_after, edges_after, conflicts = plan_lineage(proposal["lineage"], verified)
    if missing_after or edges_after or conflicts:
        raise DirectusResponseError(
            "Lineage verification failed: "
            f"missing={len(missing_after)}, unlinked={len(edges_after)}, conflicts={conflicts}"
        )
    print(f"Verified {len(proposal['lineage'])} CoL taxa and their immediate parent links.")
    print(f"Execution journal: {journal}")


def _record(handle: Any, action: str, col_id: str, row_id: Any, parent_id: int | None) -> None:
    handle.write(json.dumps({
        "at": datetime.now(UTC).isoformat(), "action": action,
        "col_id": col_id, "directus_id": row_id, "parent_ref": parent_id,
    }) + "\n")
    handle.flush()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env.admin"))
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--preview", type=Path, help="Reviewed JSON proposal required for --commit")
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()
    if args.commit != (args.preview is not None):
        parser.error("--commit requires --preview; --preview is only for --commit")

    directus = DirectusClient(load_settings(env_file=args.env_file))
    directus._authenticate()
    fields = directus._get("/fields/Taxa", {"limit": "-1"}).get("data") or []
    if "parent_ref" not in {row.get("field") for row in fields}:
        raise ValueError("Taxa.parent_ref is missing; run the additive schema migration first")
    relations = directus._get("/relations/Taxa", {"limit": "-1"}).get("data") or []
    if not any(row.get("field") == "parent_ref" and row.get("related_collection") == "Taxa"
               for row in relations):
        raise ValueError("Taxa.parent_ref relation is missing; run the schema migration first")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    cache_path = args.output_dir / f"col_{COL_DATASET_KEY}_usage_cache.sqlite"
    proposal = snapshot(directus, ChecklistBankColClient(), cache_path=cache_path)

    if args.commit:
        reviewed = json.loads(args.preview.read_text(encoding="utf-8"))
        if proposal["conflicts"]:
            raise ValueError(f"Blocking lineage conflicts: {proposal['conflicts'][:20]}")
        if reviewed != proposal:
            raise ValueError("Reviewed preview differs from current CoL or Directus; preview again")
        journal = args.output_dir / f"lineage_execution_{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}.jsonl"
        apply(directus, proposal, journal)
    else:
        path = args.output_dir / f"lineage_preview_{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}.json"
        with path.open("x", encoding="utf-8") as handle:
            json.dump(proposal, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        print(f"Observed concepts: {len(proposal['observed_col_ids'])}")
        print(f"Ancestor tree nodes: {len(proposal['lineage'])}")
        print(f"New rows: {len(proposal['missing_col_ids'])}; existing links to set: "
              f"{len(proposal['new_parent_links'])}")
        print(f"Read-only preview: {path}")
        if proposal["conflicts"]:
            raise ValueError(f"Blocking lineage conflicts in preview: {proposal['conflicts'][:20]}")
        print("Review the JSON before running --commit --preview PATH.")


if __name__ == "__main__":
    try:
        main()
    except (DirectusError, SettingsError, ColCurationError, ValueError,
            requests.RequestException) as exc:
        raise SystemExit(str(exc)) from exc
