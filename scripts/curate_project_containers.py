"""Preview or update original sample Containers for one Field_Data project."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from directus_explorer.config import load_settings
from directus_explorer.directus import DirectusClient, DirectusResponseError


def select_containers(
    field_rows: list[dict[str, Any]],
    dried_rows: list[dict[str, Any]],
    code_rows: list[dict[str, Any]],
    expected: int,
) -> list[tuple[str, int, str, str]]:
    """Prefer explicit dried links; otherwise require an exact sample-code match."""
    if len(field_rows) != expected:
        raise ValueError(f"Expected {expected} Field_Data rows, found {len(field_rows)}")

    by_field: dict[int, set[tuple[int, str]]] = {}
    for row in dried_rows:
        field = row.get("field_data")
        container = row.get("sample_container")
        if not isinstance(field, dict) or not isinstance(field.get("id"), int):
            raise ValueError("Dried_Samples_Data row has no Field_Data link")
        if container is None:
            continue
        if not isinstance(container, dict) or not isinstance(container.get("id"), int):
            raise ValueError(f"Field_Data {field['id']} has an invalid sample container link")
        code = container.get("container_id")
        if not isinstance(code, str) or not code:
            raise ValueError(f"Field_Data {field['id']} has a container without a code")
        by_field.setdefault(field["id"], set()).add((container["id"], code))

    by_code: dict[str, list[dict[str, Any]]] = {}
    for container in code_rows:
        code = container.get("container_id")
        if isinstance(code, str):
            by_code.setdefault(code, []).append(container)

    selected: list[tuple[str, int, str, str]] = []
    for field in field_rows:
        field_id = field.get("id")
        sample_id = field.get("sample_id")
        if not isinstance(field_id, int) or not isinstance(sample_id, str) or not sample_id:
            raise ValueError("Field_Data row has no numeric id or sample_id")
        matches = by_field.get(field_id, set())
        code_matches = by_code.get(sample_id, [])
        if len(matches) > 1 or len(code_matches) > 1:
            raise ValueError(f"Sample {sample_id}: ambiguous original sample containers")
        if matches:
            container_id, code = next(iter(matches))
            if code_matches and code_matches[0].get("id") != container_id:
                raise ValueError(
                    f"Sample {sample_id}: dried link conflicts with exact container code"
                )
            source = "dried_sample_link"
        elif code_matches and isinstance(code_matches[0].get("id"), int):
            container_id = code_matches[0]["id"]
            code = sample_id
            source = "sample_id_exact_container_id"
        else:
            raise ValueError(f"Sample {sample_id}: no linked or exact-code container")
        selected.append((sample_id, container_id, code, source))
    if len({container_id for _, container_id, _, _ in selected}) != expected:
        raise ValueError(
            "Some project samples share a container; refusing a partial/ambiguous batch"
        )
    return sorted(selected)


def parse_updates(entries: list[str]) -> dict[str, Any]:
    """Parse explicit, scalar JSON values for writable Containers attributes."""
    updates: dict[str, Any] = {}
    for entry in entries:
        if "=" not in entry:
            raise ValueError(f"Expected FIELD=JSON_VALUE, got {entry!r}")
        field, value = entry.split("=", 1)
        if not field or field in updates:
            raise ValueError(f"Missing or repeated field: {field!r}")
        try:
            updates[field] = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON value for {field}: {value!r}") from exc
        if isinstance(updates[field], dict | list):
            raise ValueError(f"Only scalar values are supported for {field}")
    return updates


def validate_updates(client: DirectusClient, updates: dict[str, Any]) -> None:
    """Refuse identity and unavailable/readonly fields before any write."""
    fields = client._get("/fields/Containers", params={}).get("data")
    if not isinstance(fields, list):
        raise DirectusResponseError("Containers schema did not contain a fields list")
    writable = {
        row.get("field")
        for row in fields
        if isinstance(row, dict)
        and isinstance(row.get("meta"), dict)
        and not row["meta"].get("readonly")
    }
    forbidden = {"id", "uuid_container", "container_id", "old_id"}
    invalid = updates.keys() - (writable - forbidden)
    if invalid:
        raise ValueError("Unsupported or identity Containers fields: " + ", ".join(sorted(invalid)))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, help="Exact Field_Data.qfield_project key")
    parser.add_argument(
        "--expect-count", type=int, required=True, help="Expected sample/container count"
    )
    parser.add_argument(
        "--set", dest="updates", action="append", required=True, metavar="FIELD=JSON"
    )
    parser.add_argument("--env-file", type=Path, default=None)
    parser.add_argument(
        "--commit", action="store_true", help="Write after preflight (default: dry run)"
    )
    parser.add_argument("--backup", type=Path, help="Required on commit; new JSON backup path")
    args = parser.parse_args(argv)
    if args.expect_count < 1:
        parser.error("--expect-count must be positive")
    if args.commit and args.backup is None:
        parser.error("--backup is required with --commit")
    try:
        updates = parse_updates(args.updates)
    except ValueError as exc:
        parser.error(str(exc))

    client = DirectusClient(load_settings(env_file=args.env_file))
    client._authenticate()
    validate_updates(client, updates)
    field_rows = client._get(
        "/items/Field_Data",
        params={
            "filter[qfield_project][_eq]": args.project,
            "fields": "id,sample_id",
            "limit": "-1",
        },
    )["data"]
    dried_rows = client._get(
        "/items/Dried_Samples_Data",
        params={
            "filter[field_data][qfield_project][_eq]": args.project,
            "fields": "field_data.id,sample_container.id,sample_container.container_id",
            "limit": "-1",
        },
    )["data"]
    if not field_rows:
        raise ValueError(
            f"No Field_Data rows for project {args.project!r}; check the exact "
            "qfield_project key with 'uv run directus-explorer utils projects'"
        )
    if len(field_rows) != args.expect_count:
        raise ValueError(f"Expected {args.expect_count} Field_Data rows, found {len(field_rows)}")
    codes = ",".join(row["sample_id"] for row in field_rows)
    code_rows = client._get(
        "/items/Containers",
        params={"filter[container_id][_in]": codes, "fields": "id,container_id", "limit": "-1"},
    )["data"]
    selected = select_containers(field_rows, dried_rows, code_rows, args.expect_count)
    ids = ",".join(str(container_id) for _, container_id, _, _ in selected)
    current_rows = client._get(
        "/items/Containers",
        params={
            "filter[id][_in]": ids,
            "fields": ",".join(("id", "container_id", *updates)),
            "limit": "-1",
        },
    )["data"]
    current = {row["id"]: row for row in current_rows}
    if len(current) != len(selected) or any(
        current.get(container_id, {}).get("container_id") != code
        for _, container_id, code, _ in selected
    ):
        raise ValueError("Container IDs or codes changed since sample linkage; nothing was written")

    preview: list[dict[str, Any]] = [
        {
            "sample_id": sample_id,
            "container_id": code,
            "selection_source": source,
            "directus_id": container_id,
            "before": {key: current[container_id].get(key) for key in updates},
            "after": updates,
        }
        for sample_id, container_id, code, source in selected
    ]
    print(json.dumps(preview, indent=2))
    if not args.commit:
        print(f"Dry run: {len(preview)} containers; no changes made.")
        return

    assert args.backup is not None
    with args.backup.open("x", encoding="utf-8") as handle:
        json.dump(
            {"project": args.project, "saved_at": datetime.now(UTC).isoformat(), "rows": preview},
            handle,
            indent=2,
        )
        handle.write("\n")
    print(f"Backup: {args.backup}", flush=True)
    for row in preview:
        container_id = row["directus_id"]
        live = client._get(
            f"/items/Containers/{container_id}",
            params={"fields": ",".join(("id", "container_id", *updates))},
        )["data"]
        if live.get("container_id") != row["container_id"] or any(
            live.get(key) != value for key, value in row["before"].items()
        ):
            raise ValueError(
                f"Container {container_id} changed after preview; stopped before patch"
            )
        if row["before"] != updates:
            client._patch_item(collection="Containers", item_id=container_id, payload=updates)
        verified = client._get(
            f"/items/Containers/{container_id}", params={"fields": ",".join(updates)}
        )["data"]
        if any(verified.get(key) != value for key, value in updates.items()):
            raise DirectusResponseError(f"Container {container_id} did not retain requested values")
        print(f"Verified {row['container_id']} ({container_id})", flush=True)


if __name__ == "__main__":
    try:
        main()
    except ValueError as exc:
        raise SystemExit(f"Error: {exc}") from exc
