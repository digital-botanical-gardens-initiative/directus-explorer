"""Tests for injection-list lineage auditing."""

from __future__ import annotations

from pathlib import Path

import pytest

from directus_explorer.injection_audit import (
    build_injection_audit_table,
    load_injection_list,
    summarize_injection_audit,
)


def test_load_injection_list_normalizes_filename_stems(tmp_path: Path) -> None:
    """The acquisition CSV reader should retain row numbers and strip mzML suffixes."""

    input_csv = tmp_path / "injections.csv"
    input_csv.write_text(
        "filename,injection,file_type,sample_id,container_id,Ionization.mode\n"
        "run_a.mzML,1,sample,dbgi_001,dbgi_001_01_01,positive\n",
        encoding="utf-8",
    )

    rows = load_injection_list(input_csv)

    assert len(rows) == 1
    assert rows[0].csv_row_number == 2
    assert rows[0].filename == "run_a.mzML"
    assert rows[0].normalized_filename == "run_a"


def test_load_injection_list_requires_expected_columns(tmp_path: Path) -> None:
    """Malformed acquisition CSVs should fail with a clear missing-column error."""

    input_csv = tmp_path / "injections.csv"
    input_csv.write_text("filename,file_type\nrun_a.mzML,sample\n", encoding="utf-8")

    with pytest.raises(ValueError, match="missing required column"):
        load_injection_list(input_csv)


def test_build_injection_audit_table_marks_ready_rows(tmp_path: Path) -> None:
    """Rows with dried, extraction, and aliquot lineage but no MS row should be import-ready."""

    input_csv = tmp_path / "injections.csv"
    input_csv.write_text(
        "filename,injection,file_type,sample_id,container_id,Ionization.mode\n"
        "run_a.mzML,1,sample,dbgi_001,dbgi_001_01_01,positive\n",
        encoding="utf-8",
    )

    table = build_injection_audit_table(
        injection_rows=load_injection_list(input_csv),
        dried_rows=[
            {
                "id": 30,
                "sample_container": {"container_id": "dbgi_001"},
                "field_data": {"sample_id": "dbgi_001"},
            }
        ],
        extraction_rows=[
            {
                "id": 20,
                "sample_container": {"container_id": "dbgi_001_01"},
                "parent_sample_container": {"container_id": "dbgi_001"},
            }
        ],
        aliquot_rows=[
            {
                "id": 10,
                "sample_container": {"container_id": "dbgi_001_01_01"},
                "parent_sample_container": {"container_id": "dbgi_001_01"},
            }
        ],
        ms_rows=[],
    )

    assert table.rows[0]["status"] == "ready"
    assert table.rows[0]["ready_for_ms_data_import"] == "true"
    assert table.rows[0]["dried_material_present"] == "true"
    assert table.rows[0]["extraction_sample_container_present"] == "true"
    assert table.rows[0]["aliquot_sample_container_present"] == "true"
    assert table.rows[0]["expected_extraction_container_id"] == "dbgi_001_01"
    assert table.rows[0]["target_ms_parent_container_id"] == "dbgi_001_01_01"


def test_build_injection_audit_table_supports_direct_extraction_ms_parent(
    tmp_path: Path,
) -> None:
    """Direct extraction mode should not require an aliquot vial row."""

    input_csv = tmp_path / "injections.csv"
    input_csv.write_text(
        "filename,injection,file_type,sample_id,container_id,Ionization.mode\n"
        "run_a.mzML,1,sample,dbgi_001,dbgi_001_01_01,positive\n",
        encoding="utf-8",
    )

    table = build_injection_audit_table(
        injection_rows=load_injection_list(input_csv),
        dried_rows=[
            {
                "id": 30,
                "sample_container": {"container_id": "dbgi_001"},
                "field_data": {"sample_id": "dbgi_001"},
            }
        ],
        extraction_rows=[
            {
                "id": 20,
                "sample_container": {"container_id": "dbgi_001_01"},
                "parent_sample_container": {"container_id": "dbgi_001"},
            }
        ],
        aliquot_rows=[],
        ms_rows=[],
        ms_parent_level="extraction",
    )

    assert table.rows[0]["status"] == "ready"
    assert table.rows[0]["ready_for_ms_data_import"] == "true"
    assert table.rows[0]["ms_parent_level"] == "extraction"
    assert table.rows[0]["target_ms_parent_container_id"] == "dbgi_001_01"
    assert table.rows[0]["aliquot_sample_container_present"] == "false"
    assert table.rows[0]["reason"] == ""


def test_build_injection_audit_table_explains_blocked_rows(tmp_path: Path) -> None:
    """Missing aliquots and existing MS rows should be visible in per-row reasons."""

    input_csv = tmp_path / "injections.csv"
    input_csv.write_text(
        "filename,injection,file_type,sample_id,container_id,Ionization.mode\n"
        "run_a.mzML,1,sample,dbgi_001,dbgi_001_01_01,positive\n"
        "run_b.mzML,2,sample,dbgi_002,dbgi_002_01_01,positive\n"
        "qc.mzML,NA,QC,NA,NA,NA\n",
        encoding="utf-8",
    )

    table = build_injection_audit_table(
        injection_rows=load_injection_list(input_csv),
        dried_rows=[
            {
                "id": 30,
                "sample_container": {"container_id": "dbgi_001"},
                "field_data": {"sample_id": "dbgi_001"},
            },
            {
                "id": 31,
                "sample_container": {"container_id": "dbgi_002"},
                "field_data": {"sample_id": "dbgi_002"},
            },
        ],
        extraction_rows=[
            {
                "id": 20,
                "sample_container": {"container_id": "dbgi_001_01"},
                "parent_sample_container": {"container_id": "dbgi_001"},
            },
            {
                "id": 21,
                "sample_container": {"container_id": "dbgi_002_01"},
                "parent_sample_container": {"container_id": "dbgi_002"},
            },
        ],
        aliquot_rows=[
            {
                "id": 10,
                "sample_container": {"container_id": "dbgi_001_01_01"},
                "parent_sample_container": {"container_id": "dbgi_001_01"},
            }
        ],
        ms_rows=[{"id": 99, "filename": "run_a"}],
    )

    assert table.rows[0]["status"] == "already_imported"
    assert table.rows[0]["reason"] == "ms_data_already_exists"
    assert table.rows[1]["status"] == "blocked"
    assert table.rows[1]["reason"] == "missing_aliquot_sample_container"
    assert table.rows[2]["status"] == "skipped"
    assert table.rows[2]["reason"] == "non_sample_file_type;missing_csv_sample_or_container_id"

    summary = summarize_injection_audit(table)
    assert summary["row_count"] == 3
    assert summary["sample_file_count"] == 2
    assert summary["ready_count"] == 0
    assert summary["blocked_count"] == 1
    assert summary["already_imported_count"] == 1
    assert summary["skipped_count"] == 1
    assert summary["missing_aliquot_sample_container_count"] == 1
