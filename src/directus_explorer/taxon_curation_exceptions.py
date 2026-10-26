"""Project explicitly reviewed exceptions onto the existing, safe CoL backbone."""

from __future__ import annotations

import json
from difflib import SequenceMatcher

from directus_explorer.taxon_backbone_directus import build_taxa_seed

SEED_FIELDS = (
    "col_id",
    "scientific_name",
    "authorship",
    "rank",
    "backbone_version",
    "backbone_dataset_key",
    "backbone_doi",
)
BACKFILL_FIELDS = (
    "project",
    "field_data_id",
    "sample_id",
    "current_taxon_name_unified",
    "taxon_col_id",
    "taxon_resolution_status",
    "taxon_backbone_version",
)


def _safe_index(backbone_rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    safe: dict[str, dict[str, str]] = {}
    for row in backbone_rows:
        if row.get("requires_review", "").casefold() != "false":
            continue
        col_id = row.get("taxon_col_id", "").strip()
        if not col_id:
            raise ValueError(
                f"Safe backbone name {row.get('taxon_name_unified', '')!r} lacks a CoL ID"
            )
        matched_status = row.get("col_matched_status")
        if matched_status == "synonym":
            # A synonym usage ID is not the accepted ID seeded into Taxa.
            if (
                row.get("taxon_resolution_status") != "synonym"
                or not row.get("col_matched_usage_id")
                or row["col_matched_usage_id"] == col_id
                or not row.get("col_matched_name")
                or not row.get("col_matched_rank")
            ):
                raise ValueError(f"Safe synonym for CoL ID {col_id} lacks matched usage")
        elif matched_status != "accepted":
            raise ValueError(f"Safe CoL ID {col_id} lacks an accepted match")
        seed = build_taxa_seed([row])[0]
        if not all(seed[field] for field in SEED_FIELDS if field != "authorship"):
            raise ValueError(f"Safe CoL ID {col_id} lacks canonical seed metadata")
        previous = safe.get(col_id)
        if previous is not None and build_taxa_seed([previous, row])[0] != seed:
            raise ValueError(f"Conflicting metadata for safe CoL ID {col_id}")
        safe[col_id] = row
    return safe


def _option(row: dict[str, str], status: str, rationale: str) -> dict[str, str]:
    return {
        "col_id": row["taxon_col_id"],
        "scientific_name": row["col_accepted_name"],
        "rank": row["col_accepted_rank"],
        "authorship": row["col_accepted_authorship"],
        "status": status,
        "rationale": rationale,
    }


def _same_backbone(row: dict[str, str], target: dict[str, str]) -> bool:
    return all(
        row.get(field) and row[field] == target.get(field)
        for field in (
            "taxon_backbone_version",
            "taxon_backbone_dataset_key",
            "taxon_backbone_doi",
        )
    )


def exception_options(
    backbone_rows: list[dict[str, str]],
) -> dict[str, list[dict[str, str]]]:
    """Offer only evidenced candidates which already exist as safe accepted rows.

    Fuzzy matches must have the same epithet/rank and a close genus spelling;
    unrelated fuzzy hits (including authors called X) are not taxonomic evidence.
    A cultivar can only be pinned to its own explicitly matched accepted genus.
    """
    safe = _safe_index(backbone_rows)
    options: dict[str, list[dict[str, str]]] = {}
    for row in backbone_rows:
        if row.get("requires_review", "").casefold() != "true":
            continue
        name = row["taxon_name_unified"]
        if name in options:
            raise ValueError(f"Duplicate reviewed backbone name {name!r}")
        candidates: list[dict[str, str]] = []
        if row.get("parsed_cultivar") and row.get("col_match_type") == "higherrank":
            col_id = row.get("col_matched_usage_id", "")
            target = safe.get(col_id)
            parsed = row.get("parsed_name", "").lstrip("×Xx ").split()
            if (
                target is not None
                and _same_backbone(row, target)
                and row.get("col_matched_status") == "accepted"
                and target["col_accepted_rank"] == "genus"
                and row.get("col_matched_name") == target["col_accepted_name"]
                and row.get("col_matched_rank") == "genus"
                and parsed
                and parsed[0] == target["col_accepted_name"]
            ):
                candidates.append(
                    _option(
                        target,
                        "reviewed_cultivar_genus",
                        (
                            "Cultivar is absent from the accepted backbone; the pinned "
                            "higher-rank match is its accepted genus, not a cultivar "
                            "identification."
                        ),
                    )
                )
        elif row.get("parse_status") == "parsed":
            parts = name.split()
            if len(parts) == 2:
                try:
                    fuzzy = json.loads(row.get("col_fuzzy_candidates") or "[]")
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"Invalid fuzzy candidate evidence for {name!r}") from exc
                if not isinstance(fuzzy, list):
                    raise ValueError(f"Invalid fuzzy candidate evidence for {name!r}")
                for hit in fuzzy:
                    if not isinstance(hit, dict):
                        continue
                    target = safe.get(hit.get("id"))
                    if target is None:
                        continue
                    accepted = target["col_accepted_name"].split()
                    if (
                        not _same_backbone(row, target)
                        or len(accepted) != 2
                        or parts[1].casefold() != accepted[1].casefold()
                        or SequenceMatcher(
                            None, parts[0].casefold(), accepted[0].casefold()
                        ).ratio() < 0.8
                        or row.get("parsed_rank") != "species"
                        or target["col_accepted_rank"] != "species"
                        or hit.get("name") != target["col_accepted_name"]
                        or hit.get("rank") != target["col_accepted_rank"]
                        or hit.get("authorship") != target["col_accepted_authorship"]
                        or hit.get("status") != "accepted"
                    ):
                        continue
                    if not any(
                        item["col_id"] == target["taxon_col_id"] for item in candidates
                    ):
                        candidates.append(
                            _option(
                                target,
                                "reviewed_spelling",
                                (
                                    "Pinned fuzzy spelling suggestion shares the species epithet "
                                    "and matches an accepted safe-backbone taxon; "
                                    "review the genus typo."
                                ),
                            )
                        )
        options[name] = candidates
    return options


def build_upload(
    backbone_rows: list[dict[str, str]],
    mapping_rows: list[dict[str, str]],
    choices: dict[str, str],
    project: str,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Return canonical Taxa seeds and the first seven Field_Data backfill columns.

    No candidate is implicitly accepted: every exceptional name requires an
    explicit pinned CoL ID or the literal ``unresolved`` decision.
    """
    if not project:
        raise ValueError("A project is required")
    names: dict[str, dict[str, str]] = {}
    expected_ids: dict[str, tuple[str, str]] = {}
    for row in backbone_rows:
        name = row.get("taxon_name_unified", "")
        if not name or name in names:
            raise ValueError(f"Missing or duplicate backbone name {name!r}")
        projects = row.get("projects", "").split(";")
        if projects != [project]:
            raise ValueError(f"Foreign or missing project for backbone name {name!r}")
        names[name] = row
        ids = row.get("field_data_ids", "").split(";")
        samples = row.get("sample_ids", "").split(";")
        if len(ids) != len(samples) or not all(ids) or not all(samples):
            raise ValueError(f"Missing or mismatched backbone row IDs for {name!r}")
        for field_id, sample_id in zip(ids, samples, strict=True):
            if field_id in expected_ids:
                raise ValueError(f"Duplicate backbone Field_Data ID {field_id}")
            expected_ids[field_id] = (name, sample_id)

    options = exception_options(backbone_rows)
    if choices.keys() != options.keys():
        raise ValueError(
            "Choices must cover exactly the reviewed names; "
            f"missing={sorted(options.keys() - choices.keys())}, "
            f"unknown={sorted(choices.keys() - options.keys())}"
        )
    selected: dict[str, dict[str, str] | None] = {}
    safe = _safe_index(backbone_rows)
    for name, choice in choices.items():
        if choice == "unresolved":
            selected[name] = None
        else:
            selected[name] = next(
                (safe[choice] for option in options[name] if option["col_id"] == choice),
                None,
            )
            if selected[name] is None:
                raise ValueError(f"Unpinned or unsafe CoL ID {choice!r} for {name!r}")

    projected: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in mapping_rows:
        if row.get("project") != project:
            raise ValueError(
                f"Foreign project for Field_Data {row.get('field_data_id', '')}"
            )
        field_id = row.get("field_data_id", "")
        if not field_id or field_id in seen:
            raise ValueError(f"Missing or duplicate mapping Field_Data ID {field_id!r}")
        seen.add(field_id)
        name = row.get("taxon_name_unified", "")
        source = names.get(name)
        if source is None or expected_ids.get(field_id) != (name, row.get("sample_id")):
            raise ValueError(f"Unmatched mapping Field_Data ID {field_id}")
        if row.get("requires_review", "").casefold() != source.get(
            "requires_review", ""
        ).casefold():
            raise ValueError(f"Review flag mismatch for Field_Data {field_id}")
        for key in (
            "taxon_backbone_version",
            "taxon_backbone_dataset_key",
            "taxon_backbone_doi",
        ):
            if not source.get(key) or row.get(key) != source[key]:
                raise ValueError(
                    f"Backbone metadata mismatch for Field_Data {field_id}: {key}"
                )
        if source["requires_review"].casefold() == "true":
            if row.get("taxon_col_id", "") != source.get("taxon_col_id", ""):
                raise ValueError(f"Unsafe CoL ID mismatch for Field_Data {field_id}")
            choice = selected[name]
            col_id = choice["taxon_col_id"] if choice is not None else ""
            status = (
                next(
                    option["status"]
                    for option in options[name]
                    if option["col_id"] == col_id
                )
                if choice is not None
                else "reviewed_unresolved"
            )
        else:
            col_id = source.get("taxon_col_id", "")
            if not col_id or row.get("taxon_col_id") != col_id or col_id not in safe:
                raise ValueError(
                    f"Missing or mismatched safe CoL ID for Field_Data {field_id}"
                )
            if row.get("taxon_resolution_status") != source.get("taxon_resolution_status"):
                raise ValueError(f"Resolution status mismatch for Field_Data {field_id}")
            status = row["taxon_resolution_status"]
        projected.append(
            dict(
                zip(
                    BACKFILL_FIELDS,
                    (
                        project,
                        field_id,
                        row["sample_id"],
                        name,
                        col_id,
                        status,
                        source["taxon_backbone_version"],
                    ),
                    strict=True,
                )
            )
        )
    if seen != expected_ids.keys():
        raise ValueError(
            f"Unmatched backbone Field_Data IDs: {sorted(expected_ids.keys() - seen)}"
        )
    used_ids = {row["taxon_col_id"] for row in projected if row["taxon_col_id"]}
    seed = build_taxa_seed([safe[col_id] for col_id in sorted(used_ids)])
    return seed, projected
