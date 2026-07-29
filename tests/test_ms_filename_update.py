"""Tests for validated MS filename corrections."""

from __future__ import annotations

from pathlib import Path

import pytest

from directus_explorer.ms_filename_update import (
    build_ms_filename_update_table,
    load_ms_filename_updates,
    summarize_ms_filename_updates,
)


def test_load_ms_filename_updates_normalizes_optional_assertions(tmp_path: Path) -> None:
    input_csv = tmp_path / "updates.csv"
    input_csv.write_text(
        "ms_data_id,ms_filename,ms_parent_container_id,injection_method\n"
        "42,run_001.mzML,dbgi_001_01_01,general_ms2_pos\n",
        encoding="utf-8",
    )

    rows = load_ms_filename_updates(input_csv)

    assert len(rows) == 1
    assert rows[0].ms_data_id == 42
    assert rows[0].normalized_filename == "run_001"
    assert rows[0].parent_container_id == "dbgi_001_01_01"
    assert rows[0].injection_method == "general_ms2_pos"


def test_load_ms_filename_updates_requires_id_and_filename(tmp_path: Path) -> None:
    input_csv = tmp_path / "updates.csv"
    input_csv.write_text("filename\nrun_001.mzML\n", encoding="utf-8")

    with pytest.raises(ValueError, match="ms_data_id, ms_filename"):
        load_ms_filename_updates(input_csv)


def test_build_ms_filename_update_table_validates_relationships_and_collisions(
    tmp_path: Path,
) -> None:
    input_csv = tmp_path / "updates.csv"
    input_csv.write_text(
        "ms_data_id,ms_filename,ms_parent_container_id,injection_method\n"
        "42,new_run.mzML,dbgi_001_01_01,general_ms2_pos\n"
        "43,existing_run.mzML,wrong_parent,general_ms2_pos\n"
        "44,used_elsewhere.mzML,dbgi_003_01_01,general_ms2_pos\n"
        "bad,missing.mzML,,\n",
        encoding="utf-8",
    )
    updates = load_ms_filename_updates(input_csv)
    ms_rows = [
        {
            "id": 42,
            "filename": "old_run",
            "parent_sample_container": {"container_id": "dbgi_001_01_01"},
            "injection_method": {"method_name": "general_ms2_pos"},
        },
        {
            "id": 43,
            "filename": "existing_run",
            "parent_sample_container": {"container_id": "dbgi_002_01_01"},
            "injection_method": {"method_name": "general_ms2_pos"},
        },
        {
            "id": 44,
            "filename": "old_other_run",
            "parent_sample_container": {"container_id": "dbgi_003_01_01"},
            "injection_method": {"method_name": "general_ms2_pos"},
        },
        {"id": 45, "filename": "used_elsewhere"},
    ]

    table = build_ms_filename_update_table(updates=updates, ms_rows=ms_rows)

    assert [row["status"] for row in table.rows] == [
        "ready",
        "blocked",
        "blocked",
        "blocked",
    ]
    assert table.rows[1]["reason"] == "parent_container_mismatch"
    assert table.rows[2]["reason"] == "ms_filename_already_used"
    assert table.rows[3]["reason"] == "invalid_ms_data_id"
    assert summarize_ms_filename_updates(table) == {
        "row_count": 4,
        "ready_count": 1,
        "unchanged_count": 0,
        "blocked_count": 3,
    }


def test_build_ms_filename_update_table_marks_identical_filename_unchanged(
    tmp_path: Path,
) -> None:
    input_csv = tmp_path / "updates.csv"
    input_csv.write_text("ms_data_id,ms_filename\n42,current.mzML\n", encoding="utf-8")

    table = build_ms_filename_update_table(
        updates=load_ms_filename_updates(input_csv),
        ms_rows=[{"id": 42, "filename": "current"}],
    )

    assert table.rows[0]["status"] == "unchanged"
