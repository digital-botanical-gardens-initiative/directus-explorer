"""Tests for converted-file presence checks."""

from __future__ import annotations

import csv

from directus_explorer.ms_converted_check import (
    compare_metadata_to_watcher,
    filter_report,
    strip_mzml_suffix,
    write_report_csv,
)


def test_strip_mzml_suffix_removes_uppercase_extension() -> None:
    """Watcher stems should compare without the trailing mzML extension."""

    assert strip_mzml_suffix("foo.mzML") == "foo"


def test_compare_metadata_to_watcher_handles_exact_and_missing(tmp_path) -> None:
    """Rows should report exact filename presence only."""

    metadata_csv = tmp_path / "metadata.csv"
    metadata_csv.write_text(
        "\n".join(
            (
                "ms_data_id,ms_filename,ms_parent_sample_container_container_id,original_sample_id",
                "1,20250402_PMA_tp24_dbgi_007523_01_01,dbgi_007523_01_01,dbgi_007523",
                "2,20240307_EB_dbgi_001195_01_01,dbgi_001195_01_01,dbgi_001195",
                "3,control_sample,,",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    watcher_tsv = tmp_path / "watcher.tsv"
    watcher_tsv.write_text(
        "\n".join(
            (
                "file_path\tfile_name\tpolarity",
                "/converted/20250402_PMA_tp24_dbgi_007523_01_01.mzML\t20250402_PMA_tp24_dbgi_007523_01_01.mzML\tMS:1000130|positive scan",
                "/converted/control_sample.mzML\tcontrol_sample.mzML\t",
            )
        )
        + "\n",
        encoding="utf-8",
    )

    report = compare_metadata_to_watcher(metadata_csv, (watcher_tsv,))

    assert report.summary.metadata_row_count == 3
    assert report.summary.exact_match_count == 2
    assert report.summary.no_match_count == 1

    exact_row, missing_row, no_dbgi_row = report.rows
    assert exact_row.exact_filename_match is True
    assert exact_row.dbgi_sample_code == "dbgi_007523_01_01"
    assert exact_row.dbgi_id == "dbgi_007523"
    assert exact_row.exact_matched_file_name == "20250402_PMA_tp24_dbgi_007523_01_01.mzML"
    assert exact_row.exact_matched_polarity == "MS:1000130|positive scan"

    assert missing_row.exact_filename_match is False
    assert missing_row.dbgi_sample_code == "dbgi_001195_01_01"
    assert missing_row.dbgi_id == "dbgi_001195"
    assert missing_row.exact_matched_file_name is None
    assert missing_row.exact_matched_polarity is None

    assert no_dbgi_row.exact_filename_match is True
    assert no_dbgi_row.dbgi_sample_code is None
    assert no_dbgi_row.dbgi_id is None
    assert no_dbgi_row.exact_matched_polarity == ""


def test_compare_metadata_to_watcher_rejects_missing_columns(tmp_path) -> None:
    """Missing required headers should fail loudly."""

    metadata_csv = tmp_path / "metadata.csv"
    metadata_csv.write_text("ms_filename\nsample_a\n", encoding="utf-8")
    watcher_tsv = tmp_path / "watcher.tsv"
    watcher_tsv.write_text(
        "file_path\tfile_name\tpolarity\n/converted/sample_a.mzML\tsample_a.mzML\tMS:1000130|positive scan\n",
        encoding="utf-8",
    )

    try:
        compare_metadata_to_watcher(metadata_csv, (watcher_tsv,))
    except ValueError as exc:
        assert "missing required columns: ms_data_id" in str(exc)
    else:
        raise AssertionError("Expected ValueError for missing metadata columns")


def test_write_report_csv_serializes_exact_match_fields(tmp_path) -> None:
    """CSV output should contain exact-match and Directus identifier fields only."""

    metadata_csv = tmp_path / "metadata.csv"
    metadata_csv.write_text(
        "ms_data_id,ms_filename,ms_parent_sample_container_container_id,original_sample_id\n"
        "1,sample_a,,\n"
        "2,missing_sample,dbgi_007811_99_99,dbgi_007811\n",
        encoding="utf-8",
    )
    watcher_tsv = tmp_path / "watcher.tsv"
    watcher_tsv.write_text(
        "file_path\tfile_name\tpolarity\n"
        "/converted/sample_a.mzML\tsample_a.mzML\tMS:1000130|positive scan\n",
        encoding="utf-8",
    )

    report = compare_metadata_to_watcher(metadata_csv, (watcher_tsv,))
    output_csv = tmp_path / "report.csv"
    write_report_csv(report, output_csv)

    with output_csv.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert rows[0]["exact_filename_match"] == "True"
    assert rows[0]["exact_matched_polarity"] == "MS:1000130|positive scan"
    assert rows[0]["dbgi_sample_code"] == ""
    assert rows[0]["dbgi_id"] == ""
    assert rows[1]["dbgi_sample_code"] == "dbgi_007811_99_99"
    assert rows[1]["dbgi_id"] == "dbgi_007811"
    assert rows[1]["exact_filename_match"] == "False"
    assert rows[1]["exact_matched_polarity"] == ""


def test_compare_metadata_to_watcher_merges_multiple_watcher_tsvs(tmp_path) -> None:
    """Exact matches should be resolved across multiple watcher TSV sources."""

    metadata_csv = tmp_path / "metadata.csv"
    metadata_csv.write_text(
        "ms_data_id,ms_filename,ms_parent_sample_container_container_id,original_sample_id\n"
        "1,qeplus_only_hit,dbgi_000001_01_01,dbgi_000001\n",
        encoding="utf-8",
    )
    watcher_qehfx = tmp_path / "watcher_qehfx.tsv"
    watcher_qehfx.write_text(
        "file_path\tfile_name\tpolarity\n"
        "/converted/qehfx_other.mzML\tqehfx_other.mzML\tMS:1000130|positive scan\n",
        encoding="utf-8",
    )
    watcher_qeplus = tmp_path / "watcher_qeplus.tsv"
    watcher_qeplus.write_text(
        "file_path\tfile_name\tpolarity\n"
        "/converted/qeplus_only_hit.mzML\tqeplus_only_hit.mzML\tMS:1000129|negative scan\n",
        encoding="utf-8",
    )

    report = compare_metadata_to_watcher(metadata_csv, (watcher_qehfx, watcher_qeplus))

    assert report.summary.metadata_row_count == 1
    assert report.summary.exact_match_count == 1
    assert report.rows[0].exact_filename_match is True
    assert report.rows[0].exact_matched_file_name == "qeplus_only_hit.mzML"
    assert report.rows[0].exact_matched_polarity == "MS:1000129|negative scan"


def test_filter_report_supports_matches_only_and_missing_only(tmp_path) -> None:
    """Filtered reports should keep only the requested rows."""

    metadata_csv = tmp_path / "metadata.csv"
    metadata_csv.write_text(
        "ms_data_id,ms_filename,ms_parent_sample_container_container_id,original_sample_id\n"
        "1,matched,dbgi_000001_01_01,dbgi_000001\n"
        "2,missing,dbgi_000002_01_01,dbgi_000002\n",
        encoding="utf-8",
    )
    watcher_tsv = tmp_path / "watcher.tsv"
    watcher_tsv.write_text(
        "file_path\tfile_name\tpolarity\n"
        "/converted/matched.mzML\tmatched.mzML\tMS:1000130|positive scan\n",
        encoding="utf-8",
    )

    report = compare_metadata_to_watcher(metadata_csv, (watcher_tsv,))

    matched = filter_report(report, matches_only=True, missing_only=False)
    missing = filter_report(report, matches_only=False, missing_only=True)

    assert matched.summary.metadata_row_count == 1
    assert matched.rows[0].ms_filename == "matched"
    assert missing.summary.metadata_row_count == 1
    assert missing.rows[0].ms_filename == "missing"
