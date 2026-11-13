"""Build and compare sparse accepted-CoL ancestry for observed Directus taxa."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .taxon_backbone_directus import plan_taxa_seed
from .taxon_curation import (
    COL_DATASET_DOI,
    COL_DATASET_KEY,
    COL_DATASET_VERSION,
    ChecklistBankColClient,
    ColCurationError,
)

TAXA_FIELDS = (
    "id,col_id,scientific_name,authorship,rank,backbone_version,"
    "backbone_dataset_key,backbone_doi,parent_ref"
)
LINEAGE_STATUSES = {"accepted", "provisionally accepted"}



def observed_col_ids(
    field_rows: Iterable[dict[str, Any]], taxa_rows: Iterable[dict[str, Any]]
) -> list[str]:
    """Use only approved references whose stored CoL ID agrees with their Taxa row."""
    by_id = {str(row["id"]): row for row in taxa_rows}
    result: set[str] = set()
    for field in field_rows:
        ref = field.get("taxon_ref")
        if isinstance(ref, dict):
            ref = ref.get("id")
        if ref in (None, ""):
            continue
        taxon = by_id.get(str(ref))
        if taxon is None or not taxon.get("col_id"):
            raise ValueError(f"Field_Data {field.get('id')} references an unknown Taxa row")
        col_id = str(taxon["col_id"])
        if str(field.get("taxon_col_id") or "") != col_id:
            raise ValueError(f"Field_Data {field.get('id')} has a conflicting CoL ID")
        if (
            str(field.get("taxon_backbone_version") or "") != COL_DATASET_VERSION
            or str(taxon.get("backbone_version") or "") != COL_DATASET_VERSION
            or str(taxon.get("backbone_dataset_key") or "") != str(COL_DATASET_KEY)
        ):
            raise ValueError(f"Field_Data {field.get('id')} uses another CoL release")
        result.add(col_id)
    return sorted(result)


def build_lineage(
    client: ChecklistBankColClient,
    observed_ids: Iterable[str],
    *,
    cache_path: Path | None = None,
) -> list[dict[str, str]]:
    """Fetch accepted usages once, caching validated records for subsequent previews."""
    cache = sqlite3.connect(cache_path) if cache_path is not None else None
    if cache is not None:
        cache.execute(
            "CREATE TABLE IF NOT EXISTS usage (dataset_key INTEGER NOT NULL, "
            "col_id TEXT NOT NULL, payload TEXT NOT NULL, "
            "PRIMARY KEY (dataset_key, col_id))"
        )
    fetched: dict[str, dict[str, str]] = {}
    ordered: list[dict[str, str]] = []
    try:
        for start in sorted(set(observed_ids)):
            seen: set[str] = set()
            chain: list[dict[str, str]] = []
            current = start
            while current and current not in fetched:
                if current in seen:
                    raise ValueError(f"Cycle in CoL ancestry at {current}")
                seen.add(current)
                saved = (
                    cache.execute(
                        "SELECT payload FROM usage WHERE dataset_key = ? AND col_id = ?",
                        (client.dataset_key, current),
                    ).fetchone()
                    if cache is not None else None
                )
                if saved is not None:
                    row = json.loads(saved[0])
                    if (
                        row.get("col_id") != current
                        or row.get("backbone_version") != COL_DATASET_VERSION
                        or row.get("backbone_dataset_key") != str(COL_DATASET_KEY)
                        or row.get("backbone_doi") != COL_DATASET_DOI
                        or not row.get("scientific_name")
                        or row.get("col_status") not in LINEAGE_STATUSES
                        or not isinstance(row.get("parent_col_id"), str)
                    ):
                        raise ValueError(f"Invalid cached CoL usage {current}")
                else:
                    usage = client._get_json(
                        f"/dataset/{client.dataset_key}/nameusage/{current}"
                    )
                    name = usage.get("name")
                    if not isinstance(name, dict) or not name.get("scientificName"):
                        raise ColCurationError(f"CoL usage {current} has no scientific name")
                    status = str(usage.get("status", "")).lower()
                    if usage.get("id") != current or status not in LINEAGE_STATUSES:
                        raise ColCurationError(
                            f"CoL usage {current} is not an accepted taxon concept"
                        )
                    parent = usage.get("parentId")
                    if parent is not None and (
                        not isinstance(parent, str) or not parent.strip()
                    ):
                        raise ColCurationError(f"CoL usage {current} has an invalid parentId")
                    row = {
                        "col_id": current,
                        "scientific_name": name["scientificName"],
                        "col_status": status,
                        "authorship": name.get("authorship") or "",
                        "rank": name.get("rank") or "",
                        "backbone_version": COL_DATASET_VERSION,
                        "backbone_dataset_key": str(COL_DATASET_KEY),
                        "backbone_doi": COL_DATASET_DOI,
                        "parent_col_id": parent or "",
                    }
                    if cache is not None:
                        cache.execute(
                            "INSERT INTO usage (dataset_key, col_id, payload) VALUES (?, ?, ?)",
                            (client.dataset_key, current, json.dumps(row, ensure_ascii=False)),
                        )
                        cache.commit()
                chain.append(row)
                current = row["parent_col_id"]
            if current in seen:
                raise ValueError(f"Cycle in CoL ancestry at {current}")
            for row in reversed(chain):
                fetched[row["col_id"]] = row
                ordered.append(row)
    finally:
        if cache is not None:
            cache.close()
    return ordered


def plan_lineage(
    lineage: list[dict[str, str]], existing: Iterable[dict[str, Any]]
) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    """Find new rows and missing edges; reject existing metadata/edge disagreements."""
    existing_rows = list(existing)
    by_col_id: dict[str, dict[str, Any]] = {}
    by_id: dict[str, str] = {}
    for row in existing_rows:
        col_id = str(row.get("col_id") or "").strip()
        directus_id = str(row.get("id") or "").strip()
        if not col_id or not directus_id or col_id in by_col_id or directus_id in by_id:
            raise ValueError("Taxa has missing or duplicate CoL/Directus IDs")
        by_col_id[col_id] = row
        by_id[directus_id] = col_id
    if len({row["col_id"] for row in lineage}) != len(lineage):
        raise ValueError("Lineage contains duplicate CoL IDs")
    missing, _, metadata_conflicts = plan_taxa_seed(lineage, existing_rows)
    conflicts = [
        {"col_id": row["col_id"], "reason": "metadata_mismatch",
         "expected": row["expected"], "current": row["current"]}
        for row in metadata_conflicts
    ]
    edges: list[dict[str, str]] = []
    processed: set[str] = set()
    for row in lineage:
        col_id = row["col_id"]
        parent = row["parent_col_id"]
        if parent and parent not in processed:
            raise ValueError(f"Lineage is not parent-first at {col_id}")
        processed.add(col_id)
        current = by_col_id.get(col_id)
        if current is None:
            continue
        raw_ref = current.get("parent_ref")
        if isinstance(raw_ref, dict):
            raw_ref = raw_ref.get("id")
        actual = by_id.get(str(raw_ref)) if raw_ref not in (None, "") else ""
        if raw_ref not in (None, "") and actual is None:
            conflicts.append({"col_id": col_id, "reason": "unknown_existing_parent"})
        elif actual and actual != parent:
            conflicts.append({"col_id": col_id, "reason": f"parent_mismatch:{actual}!={parent}"})
        elif not actual and parent:
            edges.append({"col_id": col_id, "parent_col_id": parent})
    return missing, edges, conflicts
