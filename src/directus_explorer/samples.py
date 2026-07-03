"""Sample-related request builders and response parsers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

ProfileMode = str

PROJECT_GROUPS = {
    "dbgi": (
        "jbc",
        "jbn",
        "jbp",
        "jbp-new",
        "jbuf",
        "kew-botanical-gardens",
    ),
}


@dataclass(frozen=True, slots=True)
class SampleCountResult:
    """Structured result for a sample count query."""

    qfield_project: str
    sample_count: int


@dataclass(frozen=True, slots=True)
class ProfiledSample:
    """Structured result for a mass-spec-profiled sample."""

    sample_id: str
    qfield_project: str
    mode: str
    species: str | None = None
    species_names: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProjectSampleSummary:
    """Per-project summary of collected and profiled samples."""

    qfield_project: str
    collected_count: int
    profiled_count: int
    positive_count: int
    negative_count: int
    both_count: int


@dataclass(frozen=True, slots=True)
class ProjectSpeciesSummary:
    """Per-project summary of collected and profiled species."""

    qfield_project: str
    collected_count: int
    profiled_count: int
    positive_count: int
    negative_count: int
    both_count: int


def build_sample_count_params(qfield_project: str) -> dict[str, str]:
    """Build Directus query parameters for counting samples in a project."""

    return {
        "filter[qfield_project][_eq]": qfield_project,
        "aggregate[count]": "id",
    }


def parse_sample_count_response(payload: dict[str, Any]) -> int:
    """Extract an integer count from a Directus aggregate response payload."""

    data = payload.get("data")
    if not isinstance(data, list) or len(data) != 1:
        raise ValueError("Directus response did not contain a single aggregate row")

    row = data[0]
    if not isinstance(row, dict):
        raise ValueError("Directus aggregate row is not an object")

    count_data = row.get("count")
    if not isinstance(count_data, dict):
        raise ValueError("Directus response did not contain a count object")

    count_value = count_data.get("id")
    if count_value is None:
        raise ValueError("Directus response did not contain count.id")

    try:
        return int(str(count_value))
    except ValueError as exc:
        raise ValueError(f"Directus count.id value is not an integer: {count_value!r}") from exc


def classify_profile_mode(method_name: str) -> str | None:
    """Classify an injection method name into a polarity mode."""

    normalized = method_name.strip().lower()
    if normalized.endswith("_pos") or "_pos_" in normalized:
        return "positive"
    if normalized.endswith("_neg") or "_neg_" in normalized:
        return "negative"
    return None


def resolve_original_sample_container_id(
    profiled_container_id: str,
    *,
    aliquot_parent_by_child: dict[str, str],
    extraction_parent_by_child: dict[str, str],
    original_sample_container_ids: set[str],
) -> str | None:
    """Resolve a profiled container back to the original sample container id."""

    if profiled_container_id in original_sample_container_ids:
        return profiled_container_id

    extracted_container_id = aliquot_parent_by_child.get(
        profiled_container_id,
        profiled_container_id,
    )
    original_container_id = extraction_parent_by_child.get(
        extracted_container_id,
        extracted_container_id,
    )
    if original_container_id in original_sample_container_ids:
        return original_container_id
    return None


def collapse_profile_modes(modes: set[str]) -> str:
    """Collapse a set of polarity flags into a single display mode."""

    if modes == {"positive", "negative"}:
        return "both"
    if modes == {"positive"}:
        return "positive"
    if modes == {"negative"}:
        return "negative"
    raise ValueError(f"Unexpected profile mode set: {sorted(modes)!r}")


def filter_profiled_samples(
    samples: list[ProfiledSample],
    *,
    mode: str,
    project: str | None = None,
) -> list[ProfiledSample]:
    """Filter profiled samples by a requested mode."""

    filtered = samples if mode == "any" else [sample for sample in samples if sample.mode == mode]
    if project is None:
        return filtered
    return [sample for sample in filtered if sample.qfield_project == project]
