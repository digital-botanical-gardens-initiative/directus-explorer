"""Tests for sample-count payload parsing."""

from __future__ import annotations

import pytest

from directus_explorer.samples import (
    ProfiledSample,
    classify_profile_mode,
    collapse_profile_modes,
    filter_profiled_samples,
    parse_sample_count_response,
    resolve_original_sample_container_id,
)


def test_parse_sample_count_response_returns_int() -> None:
    """A valid Directus aggregate payload should become an integer count."""

    payload = {"data": [{"count": {"id": "2540"}}]}

    assert parse_sample_count_response(payload) == 2540


def test_parse_sample_count_response_rejects_unexpected_shape() -> None:
    """Malformed payloads should fail loudly."""

    with pytest.raises(ValueError, match="count.id"):
        parse_sample_count_response({"data": [{"count": {}}]})


def test_classify_profile_mode_detects_positive_and_negative() -> None:
    """Injection method names should map cleanly to positive or negative mode."""

    assert classify_profile_mode("20250319_PMA_C18_DDA_pos") == "positive"
    assert classify_profile_mode("100mm_C18_15min_DDA_neg") == "negative"
    assert classify_profile_mode("test-method") is None


def test_resolve_original_sample_container_id_uses_relation_maps() -> None:
    """Profiled sample containers should resolve through aliquot and extraction ancestry."""

    container_id = resolve_original_sample_container_id(
        "dbgi_001195_01_01",
        aliquot_parent_by_child={"dbgi_001195_01_01": "dbgi_001195_01"},
        extraction_parent_by_child={"dbgi_001195_01": "dbgi_001195"},
        original_sample_container_ids={"dbgi_001195"},
    )

    assert container_id == "dbgi_001195"


def test_filter_profiled_samples_supports_any_and_both() -> None:
    """Mode filters should keep only the requested sample classifications."""

    samples = [
        ProfiledSample(sample_id="a", qfield_project="proj_a", mode="positive"),
        ProfiledSample(sample_id="b", qfield_project="proj_b", mode="negative"),
        ProfiledSample(sample_id="c", qfield_project="proj_a", mode="both"),
    ]

    assert [
        sample.sample_id for sample in filter_profiled_samples(samples, mode="any")
    ] == ["a", "b", "c"]
    assert [sample.sample_id for sample in filter_profiled_samples(samples, mode="both")] == ["c"]
    assert [
        sample.sample_id
        for sample in filter_profiled_samples(samples, mode="any", project="proj_a")
    ] == ["a", "c"]
    assert collapse_profile_modes({"positive", "negative"}) == "both"
