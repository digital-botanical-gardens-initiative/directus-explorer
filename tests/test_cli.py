"""Tests for the Click CLI surface."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from directus_explorer import cli as cli_module
from directus_explorer.config import Settings, SettingsError
from directus_explorer.ms_metadata import MsMetadataTable
from directus_explorer.samples import ProfiledSample, ProjectSampleSummary, ProjectSpeciesSummary


def test_cli_help() -> None:
    """The root command should expose the samples group."""

    runner = CliRunner()

    result = runner.invoke(cli_module.cli, ["--help"])

    assert result.exit_code == 0
    assert "ms" in result.output
    assert "samples" in result.output
    assert "utils" in result.output


def test_count_samples_text_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """Text output should match the expected sentence."""

    runner = CliRunner()

    monkeypatch.setattr(
        cli_module,
        "load_settings",
        lambda env_file=None: Settings(
            directus_instance="https://example.test/directus",
            directus_username="user@example.test",
            directus_password="secret",
        ),
    )

    class FakeClient:
        def __init__(self, settings: Settings) -> None:
            self.settings = settings

        def count_field_samples(self, qfield_project: str) -> int:
            return 12

    monkeypatch.setattr(cli_module, "DirectusClient", FakeClient)

    result = runner.invoke(cli_module.cli, ["samples", "count", "jbuf"])

    assert result.exit_code == 0
    assert result.output.strip() == "Project jbuf: 12 samples"


def test_count_samples_json_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """JSON output should match the expected structure."""

    runner = CliRunner()

    monkeypatch.setattr(
        cli_module,
        "load_settings",
        lambda env_file=None: Settings(
            directus_instance="https://example.test/directus",
            directus_username="user@example.test",
            directus_password="secret",
        ),
    )

    class FakeClient:
        def __init__(self, settings: Settings) -> None:
            self.settings = settings

        def count_field_samples(self, qfield_project: str) -> int:
            return 2540

    monkeypatch.setattr(cli_module, "DirectusClient", FakeClient)

    result = runner.invoke(cli_module.cli, ["samples", "count", "jbuf", "--format", "json"])

    assert result.exit_code == 0
    assert result.output.strip() == '{"qfield_project": "jbuf", "sample_count": 2540}'


def test_count_samples_reports_missing_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing config should produce a non-zero Click failure."""

    runner = CliRunner()

    def raise_settings_error(env_file: object = None) -> Settings:
        raise SettingsError("Missing required environment variables: DIRECTUS_PASSWORD")

    monkeypatch.setattr(cli_module, "load_settings", raise_settings_error)

    result = runner.invoke(cli_module.cli, ["samples", "count", "jbuf"])

    assert result.exit_code != 0
    assert "DIRECTUS_PASSWORD" in result.output


def test_export_ms_metadata_writes_status_line(monkeypatch: pytest.MonkeyPatch) -> None:
    """The MS metadata export command should report the output path and row count."""

    runner = CliRunner()

    monkeypatch.setattr(
        cli_module,
        "load_settings",
        lambda env_file=None: Settings(
            directus_instance="https://example.test/directus",
            directus_username="user@example.test",
            directus_password="secret",
        ),
    )

    class FakeClient:
        def __init__(self, settings: Settings) -> None:
            self.settings = settings

        def export_ms_metadata_csv(
            self,
            output_path: str,
            project: str | None = None,
            project_group: str | None = None,
            watcher_tsv_paths: tuple[object, ...] = (),
            view: str = "full",
        ) -> int:
            assert str(output_path) == "metadata.csv"
            assert project == "jbuf"
            assert project_group is None
            assert watcher_tsv_paths == ()
            assert view == "full"
            return 42

    monkeypatch.setattr(cli_module, "DirectusClient", FakeClient)

    result = runner.invoke(
        cli_module.cli,
        ["ms", "export-metadata", "--output", "metadata.csv", "--project", "jbuf"],
    )

    assert result.exit_code == 0
    assert result.output.strip() == "Wrote 42 metadata rows for project jbuf to metadata.csv"


def test_export_ms_metadata_passes_compact_view(monkeypatch: pytest.MonkeyPatch) -> None:
    """The export command should forward the requested compact view."""

    runner = CliRunner()

    monkeypatch.setattr(
        cli_module,
        "load_settings",
        lambda env_file=None: Settings(
            directus_instance="https://example.test/directus",
            directus_username="user@example.test",
            directus_password="secret",
        ),
    )

    class FakeClient:
        def __init__(self, settings: Settings) -> None:
            self.settings = settings

        def export_ms_metadata_csv(
            self,
            output_path: str,
            project: str | None = None,
            project_group: str | None = None,
            watcher_tsv_paths: tuple[object, ...] = (),
            view: str = "full",
        ) -> int:
            assert str(output_path) == "compact.tsv"
            assert project == "jbuf"
            assert project_group is None
            assert view == "compact"
            return 5

    monkeypatch.setattr(cli_module, "DirectusClient", FakeClient)

    result = runner.invoke(
        cli_module.cli,
        [
            "ms",
            "export-metadata",
            "--output",
            "compact.tsv",
            "--project",
            "jbuf",
            "--view",
            "compact",
        ],
    )

    assert result.exit_code == 0
    assert result.output.strip() == "Wrote 5 metadata rows for project jbuf to compact.tsv"


def test_export_ms_metadata_passes_project_group_sample_compact_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The export command should support one-row-per-sample group metadata."""

    runner = CliRunner()

    monkeypatch.setattr(
        cli_module,
        "load_settings",
        lambda env_file=None: Settings(
            directus_instance="https://example.test/directus",
            directus_username="user@example.test",
            directus_password="secret",
        ),
    )

    class FakeClient:
        def __init__(self, settings: Settings) -> None:
            self.settings = settings

        def export_ms_metadata_csv(
            self,
            output_path: str,
            project: str | None = None,
            project_group: str | None = None,
            watcher_tsv_paths: tuple[object, ...] = (),
            view: str = "full",
        ) -> int:
            assert str(output_path) == "dbgi.tsv"
            assert project is None
            assert project_group == "dbgi"
            assert watcher_tsv_paths == ()
            assert view == "sample-compact"
            return 6644

    monkeypatch.setattr(cli_module, "DirectusClient", FakeClient)

    result = runner.invoke(
        cli_module.cli,
        [
            "ms",
            "export-metadata",
            "--output",
            "dbgi.tsv",
            "--project-group",
            "dbgi",
            "--view",
            "sample-compact",
        ],
    )

    assert result.exit_code == 0
    assert result.output.strip() == "Wrote 6644 metadata rows for project group dbgi to dbgi.tsv"


def test_audit_injection_list_writes_report_and_summary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The injection-list audit command should write TSV reports and summarize rows."""

    runner = CliRunner()
    input_csv = tmp_path / "injections.csv"
    output_tsv = tmp_path / "audit.tsv"
    input_csv.write_text(
        "filename,injection,file_type,sample_id,container_id,Ionization.mode\n"
        "run_a.mzML,1,sample,dbgi_001,dbgi_001_01_01,positive\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        cli_module,
        "load_settings",
        lambda env_file=None: Settings(
            directus_instance="https://example.test/directus",
            directus_username="user@example.test",
            directus_password="secret",
        ),
    )

    class FakeClient:
        def __init__(self, settings: Settings) -> None:
            self.settings = settings

        def build_injection_audit_table(
            self,
            *,
            input_path: Path,
            required_file_type: str = "sample",
            ms_parent_level: str = "aliquot",
        ) -> MsMetadataTable:
            assert input_path == input_csv
            assert required_file_type == "sample"
            assert ms_parent_level == "extraction"
            return MsMetadataTable(
                fieldnames=("is_sample_file", "ready_for_ms_data_import", "status", "reason"),
                rows=(
                    {
                        "is_sample_file": "true",
                        "ready_for_ms_data_import": "true",
                        "status": "ready",
                        "reason": "",
                    },
                ),
            )

    monkeypatch.setattr(cli_module, "DirectusClient", FakeClient)

    result = runner.invoke(
        cli_module.cli,
        [
            "ms",
            "audit-injection-list",
            str(input_csv),
            "--output",
            str(output_tsv),
            "--ms-parent-level",
            "extraction",
        ],
    )

    assert result.exit_code == 0
    assert "ready_count\t1" in result.output
    assert output_tsv.exists()


def test_sample_locations_text_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """The locations command should print a TSV row for one sample."""

    runner = CliRunner()

    monkeypatch.setattr(
        cli_module,
        "load_settings",
        lambda env_file=None: Settings(
            directus_instance="https://example.test/directus",
            directus_username="user@example.test",
            directus_password="secret",
        ),
    )

    class FakeClient:
        def __init__(self, settings: Settings) -> None:
            self.settings = settings

        def build_sample_locations_table(
            self,
            *,
            sample_id: str | None = None,
            project: str | None = None,
        ):
            assert sample_id == "dbgi_001195_01_01"
            assert project is None
            from directus_explorer.ms_metadata import MsMetadataTable

            return MsMetadataTable(
                fieldnames=(
                    "original_sample_id",
                    "ms_container_id",
                    "ms_storage_level_1_container_id",
                ),
                rows=(
                    {
                        "original_sample_id": "dbgi_001195",
                        "ms_container_id": "dbgi_001195_01_01",
                        "ms_storage_level_1_container_id": "box_001",
                    },
                ),
            )

    monkeypatch.setattr(cli_module, "DirectusClient", FakeClient)

    result = runner.invoke(
        cli_module.cli,
        ["samples", "locations", "--sample-id", "dbgi_001195_01_01"],
    )

    assert result.exit_code == 0
    assert result.output.splitlines() == [
        "original_sample_id\tms_container_id\tms_storage_level_1_container_id",
        "dbgi_001195\tdbgi_001195_01_01\tbox_001",
    ]


def test_sample_locations_tsv_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """The locations command should write a TSV for one project."""

    runner = CliRunner()

    monkeypatch.setattr(
        cli_module,
        "load_settings",
        lambda env_file=None: Settings(
            directus_instance="https://example.test/directus",
            directus_username="user@example.test",
            directus_password="secret",
        ),
    )

    class FakeClient:
        def __init__(self, settings: Settings) -> None:
            self.settings = settings

        def build_sample_locations_table(
            self,
            *,
            sample_id: str | None = None,
            project: str | None = None,
        ):
            assert sample_id is None
            assert project == "jbuf"
            from directus_explorer.ms_metadata import MsMetadataTable

            return MsMetadataTable(
                fieldnames=("original_sample_id", "original_sample_container_id"),
                rows=(
                    {
                        "original_sample_id": "dbgi_001195",
                        "original_sample_container_id": "dbgi_001195",
                    },
                ),
            )

    monkeypatch.setattr(cli_module, "DirectusClient", FakeClient)

    with runner.isolated_filesystem():
        result = runner.invoke(
            cli_module.cli,
            [
                "samples",
                "locations",
                "--project",
                "jbuf",
                "--format",
                "tsv",
                "--output",
                "locations.tsv",
            ],
        )
        assert result.exit_code == 0
    assert result.output.strip() == "Wrote 1 location rows to locations.tsv"


def test_sample_locations_pretty_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """The locations command should render top-down retrieval guidance."""

    runner = CliRunner()

    monkeypatch.setattr(
        cli_module,
        "load_settings",
        lambda env_file=None: Settings(
            directus_instance="https://example.test/directus",
            directus_username="user@example.test",
            directus_password="secret",
        ),
    )

    class FakeClient:
        def __init__(self, settings: Settings) -> None:
            self.settings = settings

        def build_sample_locations_table(
            self,
            *,
            sample_id: str | None = None,
            project: str | None = None,
        ):
            assert sample_id == "dbgi_003088_01_01"
            assert project is None
            from directus_explorer.ms_metadata import MsMetadataTable

            return MsMetadataTable(
                fieldnames=(
                    "original_sample_id",
                    "ms_container_id",
                    "ms_container_type",
                    "ms_storage_level_1_container_id",
                    "ms_storage_level_1_type",
                    "extraction_container_id",
                    "extraction_container_type",
                    "extraction_storage_level_1_container_id",
                    "extraction_storage_level_1_type",
                    "extraction_storage_level_2_container_id",
                    "extraction_storage_level_2_type",
                    "original_sample_container_id",
                    "original_sample_container_type",
                    "original_storage_level_1_container_id",
                    "original_storage_level_1_type",
                    "original_storage_level_2_container_id",
                    "original_storage_level_2_type",
                    "original_storage_level_3_container_id",
                    "original_storage_level_3_type",
                    "qfield_project",
                ),
                rows=(
                    {
                        "original_sample_id": "dbgi_003088",
                        "ms_container_id": "dbgi_003088_01_01",
                        "ms_container_type": "Glass Vial",
                        "ms_storage_level_1_container_id": "container_000200",
                        "ms_storage_level_1_type": "Glass Vial Box",
                        "extraction_container_id": "dbgi_003088_01",
                        "extraction_container_type": "Glass Vial",
                        "extraction_storage_level_1_container_id": "container_000195",
                        "extraction_storage_level_1_type": "Glass Vial Box",
                        "extraction_storage_level_2_container_id": "container_000682",
                        "extraction_storage_level_2_type": "Freezer Rack",
                        "original_sample_container_id": "dbgi_003088",
                        "original_sample_container_type": "Conical Centrifugal Tube",
                        "original_storage_level_1_container_id": "container_000044",
                        "original_storage_level_1_type": "Conical Centrifugal Tube Rack",
                        "original_storage_level_2_container_id": "container_000677",
                        "original_storage_level_2_type": "Shelf",
                        "original_storage_level_3_container_id": "container_000685",
                        "original_storage_level_3_type": "Cupboard",
                        "qfield_project": "jbuf",
                    },
                ),
            )

    monkeypatch.setattr(cli_module, "DirectusClient", FakeClient)

    result = runner.invoke(
        cli_module.cli,
        ["samples", "locations", "--sample-id", "dbgi_003088_01_01", "--format", "pretty"],
    )

    assert result.exit_code == 0
    assert result.output.splitlines() == [
        "Sample dbgi_003088 (jbuf)",
        "MS aliquot:",
        "- Find Glass Vial Box container_000200.",
        "- Retrieve Glass Vial dbgi_003088_01_01.",
        "Extract:",
        "- Find Freezer Rack container_000682.",
        "- Find Glass Vial Box container_000195.",
        "- Retrieve Glass Vial dbgi_003088_01.",
        "Original sample:",
        "- Find Cupboard container_000685.",
        "- Find Shelf container_000677.",
        "- Find Conical Centrifugal Tube Rack container_000044.",
        "- Retrieve Conical Centrifugal Tube dbgi_003088.",
    ]


def test_sample_locations_requires_one_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    """The locations command should require exactly one selector."""

    runner = CliRunner()

    result = runner.invoke(cli_module.cli, ["samples", "locations"])

    assert result.exit_code != 0
    assert "Exactly one of --sample-id or --project must be provided" in result.output


def test_check_converted_text_output(tmp_path) -> None:
    """Text output should print the compact comparison summary."""

    runner = CliRunner()
    metadata_csv = tmp_path / "metadata.csv"
    watcher_tsv = tmp_path / "watcher.tsv"
    metadata_csv.write_text(
        "ms_data_id,ms_filename,ms_parent_sample_container_container_id,original_sample_id\n"
        "1,exact_hit,,\n"
        "2,20240307_EB_dbgi_001195_01_01,dbgi_001195_01_01,dbgi_001195\n",
        encoding="utf-8",
    )
    watcher_tsv.write_text(
        "file_path\tfile_name\tpolarity\n"
        "/converted/exact_hit.mzML\texact_hit.mzML\tMS:1000130|positive scan\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        cli_module.cli,
        [
            "ms",
            "check-converted",
            "--metadata-csv",
            str(metadata_csv),
            "--watcher-tsv",
            str(watcher_tsv),
        ],
    )

    assert result.exit_code == 0
    assert result.output.splitlines() == [
        "metadata_row_count\t2",
        "exact_match_count\t1",
        "no_match_count\t1",
    ]


def test_check_converted_json_output(tmp_path) -> None:
    """JSON output should include summary and per-row comparison payloads."""

    runner = CliRunner()
    metadata_csv = tmp_path / "metadata.csv"
    watcher_tsv = tmp_path / "watcher.tsv"
    metadata_csv.write_text(
        "ms_data_id,ms_filename,ms_parent_sample_container_container_id,original_sample_id\n"
        "1,20250402_PMA_tp24_dbgi_007523_01_01,dbgi_007523_01_01,dbgi_007523\n",
        encoding="utf-8",
    )
    watcher_tsv.write_text(
        "file_path\tfile_name\tpolarity\n"
        "/converted/20250402_PMA_tp24_dbgi_007523_01_01.mzML\t"
        "20250402_PMA_tp24_dbgi_007523_01_01.mzML\t"
        "MS:1000130|positive scan\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        cli_module.cli,
        [
            "ms",
            "check-converted",
            "--metadata-csv",
            str(metadata_csv),
            "--watcher-tsv",
            str(watcher_tsv),
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["summary"] == {
        "metadata_row_count": 1,
        "exact_match_count": 1,
        "no_match_count": 0,
    }
    assert payload["rows"] == [
        {
            "ms_data_id": "1",
            "ms_filename": "20250402_PMA_tp24_dbgi_007523_01_01",
            "exact_filename_match": True,
            "exact_matched_file_name": "20250402_PMA_tp24_dbgi_007523_01_01.mzML",
            "exact_matched_file_path": "/converted/20250402_PMA_tp24_dbgi_007523_01_01.mzML",
            "exact_matched_polarity": "MS:1000130|positive scan",
            "dbgi_sample_code": "dbgi_007523_01_01",
            "dbgi_id": "dbgi_007523",
        }
    ]


def test_check_converted_accepts_multiple_watcher_tsvs(tmp_path) -> None:
    """CLI should merge exact-match candidates from repeated watcher TSV options."""

    runner = CliRunner()
    metadata_csv = tmp_path / "metadata.csv"
    watcher_qehfx = tmp_path / "watcher_qehfx.tsv"
    watcher_qeplus = tmp_path / "watcher_qeplus.tsv"
    metadata_csv.write_text(
        "ms_data_id,ms_filename,ms_parent_sample_container_container_id,original_sample_id\n"
        "1,qeplus_only_hit,dbgi_000001_01_01,dbgi_000001\n",
        encoding="utf-8",
    )
    watcher_qehfx.write_text(
        "file_path\tfile_name\tpolarity\n"
        "/converted/qehfx_other.mzML\tqehfx_other.mzML\tMS:1000130|positive scan\n",
        encoding="utf-8",
    )
    watcher_qeplus.write_text(
        "file_path\tfile_name\tpolarity\n"
        "/converted/qeplus_only_hit.mzML\tqeplus_only_hit.mzML\tMS:1000129|negative scan\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        cli_module.cli,
        [
            "ms",
            "check-converted",
            "--metadata-csv",
            str(metadata_csv),
            "--watcher-tsv",
            str(watcher_qehfx),
            "--watcher-tsv",
            str(watcher_qeplus),
        ],
    )

    assert result.exit_code == 0
    assert result.output.splitlines() == [
        "metadata_row_count\t1",
        "exact_match_count\t1",
        "no_match_count\t0",
    ]


def test_check_converted_csv_requires_output(tmp_path) -> None:
    """CSV mode should reject missing output paths."""

    runner = CliRunner()
    metadata_csv = tmp_path / "metadata.csv"
    watcher_tsv = tmp_path / "watcher.tsv"
    metadata_csv.write_text(
        "ms_data_id,ms_filename,ms_parent_sample_container_container_id,original_sample_id\n"
        "1,sample_a,,\n",
        encoding="utf-8",
    )
    watcher_tsv.write_text(
        "file_path\tfile_name\tpolarity\n"
        "/converted/sample_a.mzML\tsample_a.mzML\tMS:1000130|positive scan\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        cli_module.cli,
        [
            "ms",
            "check-converted",
            "--metadata-csv",
            str(metadata_csv),
            "--watcher-tsv",
            str(watcher_tsv),
            "--format",
            "csv",
        ],
    )

    assert result.exit_code != 0
    assert "--output is required when --format csv" in result.output


def test_check_converted_matches_only_filters_output_rows(tmp_path) -> None:
    """Matches-only mode should only export exact hits."""

    runner = CliRunner()
    metadata_csv = tmp_path / "metadata.csv"
    watcher_tsv = tmp_path / "watcher.tsv"
    output_csv = tmp_path / "matches.csv"
    metadata_csv.write_text(
        "ms_data_id,ms_filename,ms_parent_sample_container_container_id,original_sample_id\n"
        "1,matched,dbgi_000001_01_01,dbgi_000001\n"
        "2,missing,dbgi_000002_01_01,dbgi_000002\n",
        encoding="utf-8",
    )
    watcher_tsv.write_text(
        "file_path\tfile_name\tpolarity\n"
        "/converted/matched.mzML\tmatched.mzML\tMS:1000130|positive scan\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        cli_module.cli,
        [
            "ms",
            "check-converted",
            "--metadata-csv",
            str(metadata_csv),
            "--watcher-tsv",
            str(watcher_tsv),
            "--format",
            "csv",
            "--output",
            str(output_csv),
            "--matches-only",
        ],
    )

    assert result.exit_code == 0
    assert result.output.strip() == f"Wrote 1 comparison rows to {output_csv}"
    assert output_csv.read_text(encoding="utf-8").splitlines()[1].startswith("1,matched,True,")


def test_check_converted_missing_only_filters_output_rows(tmp_path) -> None:
    """Missing-only mode should only export unmatched rows."""

    runner = CliRunner()
    metadata_csv = tmp_path / "metadata.csv"
    watcher_tsv = tmp_path / "watcher.tsv"
    output_csv = tmp_path / "missing.csv"
    metadata_csv.write_text(
        "ms_data_id,ms_filename,ms_parent_sample_container_container_id,original_sample_id\n"
        "1,matched,dbgi_000001_01_01,dbgi_000001\n"
        "2,missing,dbgi_000002_01_01,dbgi_000002\n",
        encoding="utf-8",
    )
    watcher_tsv.write_text(
        "file_path\tfile_name\tpolarity\n"
        "/converted/matched.mzML\tmatched.mzML\tMS:1000130|positive scan\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        cli_module.cli,
        [
            "ms",
            "check-converted",
            "--metadata-csv",
            str(metadata_csv),
            "--watcher-tsv",
            str(watcher_tsv),
            "--format",
            "csv",
            "--output",
            str(output_csv),
            "--missing-only",
        ],
    )

    assert result.exit_code == 0
    assert result.output.strip() == f"Wrote 1 comparison rows to {output_csv}"
    assert output_csv.read_text(encoding="utf-8").splitlines()[1].startswith("2,missing,False,")


def test_check_converted_rejects_conflicting_filters(tmp_path) -> None:
    """Matches-only and missing-only should be mutually exclusive."""

    runner = CliRunner()
    metadata_csv = tmp_path / "metadata.csv"
    watcher_tsv = tmp_path / "watcher.tsv"
    metadata_csv.write_text(
        "ms_data_id,ms_filename,ms_parent_sample_container_container_id,original_sample_id\n"
        "1,matched,dbgi_000001_01_01,dbgi_000001\n",
        encoding="utf-8",
    )
    watcher_tsv.write_text(
        "file_path\tfile_name\tpolarity\n"
        "/converted/matched.mzML\tmatched.mzML\tMS:1000130|positive scan\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        cli_module.cli,
        [
            "ms",
            "check-converted",
            "--metadata-csv",
            str(metadata_csv),
            "--watcher-tsv",
            str(watcher_tsv),
            "--matches-only",
            "--missing-only",
        ],
    )

    assert result.exit_code != 0
    assert "--matches-only and --missing-only cannot be used together" in result.output


def test_list_projects_text_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """Text output should print one project key per line."""

    runner = CliRunner()

    monkeypatch.setattr(
        cli_module,
        "load_settings",
        lambda env_file=None: Settings(
            directus_instance="https://example.test/directus",
            directus_username="user@example.test",
            directus_password="secret",
        ),
    )

    class FakeClient:
        def __init__(self, settings: Settings) -> None:
            self.settings = settings

        def list_projects(self) -> list[str]:
            return ["artemisia", "jbuf"]

    monkeypatch.setattr(cli_module, "DirectusClient", FakeClient)

    result = runner.invoke(cli_module.cli, ["utils", "projects"])

    assert result.exit_code == 0
    assert result.output.splitlines() == ["artemisia", "jbuf"]


def test_list_projects_json_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """JSON output should expose the project list."""

    runner = CliRunner()

    monkeypatch.setattr(
        cli_module,
        "load_settings",
        lambda env_file=None: Settings(
            directus_instance="https://example.test/directus",
            directus_username="user@example.test",
            directus_password="secret",
        ),
    )

    class FakeClient:
        def __init__(self, settings: Settings) -> None:
            self.settings = settings

        def list_projects(self) -> list[str]:
            return ["artemisia", "jbuf"]

    monkeypatch.setattr(cli_module, "DirectusClient", FakeClient)

    result = runner.invoke(cli_module.cli, ["utils", "projects", "--format", "json"])

    assert result.exit_code == 0
    assert result.output.strip() == '{"projects": ["artemisia", "jbuf"]}'


def test_profiled_samples_text_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """Text output should list sample ids with their resolved mode."""

    runner = CliRunner()

    monkeypatch.setattr(
        cli_module,
        "load_settings",
        lambda env_file=None: Settings(
            directus_instance="https://example.test/directus",
            directus_username="user@example.test",
            directus_password="secret",
        ),
    )

    class FakeClient:
        def __init__(self, settings: Settings) -> None:
            self.settings = settings

        def list_profiled_samples(
            self,
            mode: str = "any",
            project: str | None = None,
        ) -> list[ProfiledSample]:
            return [ProfiledSample(sample_id="dbgi_001195", qfield_project="jbuf", mode="both")]

    monkeypatch.setattr(cli_module, "DirectusClient", FakeClient)

    result = runner.invoke(cli_module.cli, ["samples", "profiled"])

    assert result.exit_code == 0
    assert result.output.strip() == "dbgi_001195\tjbuf\tboth"


def test_profiled_samples_json_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """JSON output should include the filter, count, and sample list."""

    runner = CliRunner()

    monkeypatch.setattr(
        cli_module,
        "load_settings",
        lambda env_file=None: Settings(
            directus_instance="https://example.test/directus",
            directus_username="user@example.test",
            directus_password="secret",
        ),
    )

    class FakeClient:
        def __init__(self, settings: Settings) -> None:
            self.settings = settings

        def list_profiled_samples(
            self,
            mode: str = "any",
            project: str | None = None,
        ) -> list[ProfiledSample]:
            return [
                ProfiledSample(sample_id="dbgi_001195", qfield_project="jbuf", mode="both"),
                ProfiledSample(sample_id="dbgi_001199", qfield_project="jbuf", mode="positive"),
            ]

    monkeypatch.setattr(cli_module, "DirectusClient", FakeClient)

    result = runner.invoke(
        cli_module.cli,
        ["samples", "profiled", "--mode", "any", "--format", "json"],
    )

    assert result.exit_code == 0
    assert result.output.strip() == (
        '{"mode_filter": "any", "project_filter": null, "sample_count": 2, "samples": '
        '[{"sample_id": "dbgi_001195", "qfield_project": "jbuf", "mode": "both"}, '
        '{"sample_id": "dbgi_001199", "qfield_project": "jbuf", "mode": "positive"}]}'
    )


def test_profiled_samples_count_text_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """Count mode should print only the total number of matching samples."""

    runner = CliRunner()

    monkeypatch.setattr(
        cli_module,
        "load_settings",
        lambda env_file=None: Settings(
            directus_instance="https://example.test/directus",
            directus_username="user@example.test",
            directus_password="secret",
        ),
    )

    class FakeClient:
        def __init__(self, settings: Settings) -> None:
            self.settings = settings

        def list_profiled_samples(
            self,
            mode: str = "any",
            project: str | None = None,
        ) -> list[ProfiledSample]:
            return [
                ProfiledSample(sample_id="dbgi_001195", qfield_project="jbuf", mode="both"),
                ProfiledSample(sample_id="dbgi_001199", qfield_project="jbuf", mode="both"),
            ]

    monkeypatch.setattr(cli_module, "DirectusClient", FakeClient)

    result = runner.invoke(
        cli_module.cli,
        ["samples", "profiled", "--mode", "both", "--count"],
    )

    assert result.exit_code == 0
    assert result.output.strip() == "Mode both: 2 profiled samples"


def test_profiled_samples_count_json_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """JSON count mode should omit the sample list payload."""

    runner = CliRunner()

    monkeypatch.setattr(
        cli_module,
        "load_settings",
        lambda env_file=None: Settings(
            directus_instance="https://example.test/directus",
            directus_username="user@example.test",
            directus_password="secret",
        ),
    )

    class FakeClient:
        def __init__(self, settings: Settings) -> None:
            self.settings = settings

        def list_profiled_samples(
            self,
            mode: str = "any",
            project: str | None = None,
        ) -> list[ProfiledSample]:
            return [ProfiledSample(sample_id="dbgi_001195", qfield_project="jbuf", mode="negative")]

    monkeypatch.setattr(cli_module, "DirectusClient", FakeClient)

    result = runner.invoke(
        cli_module.cli,
        ["samples", "profiled", "--mode", "negative", "--format", "json", "--count"],
    )

    assert result.exit_code == 0
    assert result.output.strip() == (
        '{"mode_filter": "negative", "project_filter": null, "sample_count": 1}'
    )


def test_profiled_samples_project_filter_is_forwarded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Project filter should be passed to the client and reflected in output."""

    runner = CliRunner()
    captured: dict[str, str | None] = {"project": None}

    monkeypatch.setattr(
        cli_module,
        "load_settings",
        lambda env_file=None: Settings(
            directus_instance="https://example.test/directus",
            directus_username="user@example.test",
            directus_password="secret",
        ),
    )

    class FakeClient:
        def __init__(self, settings: Settings) -> None:
            self.settings = settings

        def list_profiled_samples(
            self,
            mode: str = "any",
            project: str | None = None,
        ) -> list[ProfiledSample]:
            captured["project"] = project
            return [ProfiledSample(sample_id="dbgi_001195", qfield_project="jbuf", mode="both")]

    monkeypatch.setattr(cli_module, "DirectusClient", FakeClient)

    result = runner.invoke(
        cli_module.cli,
        ["samples", "profiled", "--project", "jbuf", "--count"],
    )

    assert result.exit_code == 0
    assert captured["project"] == "jbuf"
    assert result.output.strip() == "Project jbuf, mode any: 1 profiled samples"


def test_samples_summary_text_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """Project summary text output should render a readable terminal table."""

    runner = CliRunner()

    monkeypatch.setattr(
        cli_module,
        "load_settings",
        lambda env_file=None: Settings(
            directus_instance="https://example.test/directus",
            directus_username="user@example.test",
            directus_password="secret",
        ),
    )

    class FakeClient:
        def __init__(self, settings: Settings) -> None:
            self.settings = settings

        def summarize_samples_by_project(self) -> list[ProjectSampleSummary]:
            return [
                ProjectSampleSummary(
                    qfield_project="jbuf",
                    collected_count=10,
                    profiled_count=4,
                    positive_count=1,
                    negative_count=1,
                    both_count=2,
                )
            ]

    monkeypatch.setattr(cli_module, "DirectusClient", FakeClient)

    result = runner.invoke(cli_module.cli, ["samples", "summary"])

    assert result.exit_code == 0
    assert "Directus samples summary" in result.output
    assert "qfield_project" in result.output
    assert "profiled %" in result.output
    assert "jbuf" in result.output
    assert "40.0%" in result.output


def test_samples_summary_tsv_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """Project summary TSV output should preserve copy-friendly output."""

    runner = CliRunner()

    monkeypatch.setattr(
        cli_module,
        "load_settings",
        lambda env_file=None: Settings(
            directus_instance="https://example.test/directus",
            directus_username="user@example.test",
            directus_password="secret",
        ),
    )

    class FakeClient:
        def __init__(self, settings: Settings) -> None:
            self.settings = settings

        def summarize_samples_by_project(self) -> list[ProjectSampleSummary]:
            return [
                ProjectSampleSummary(
                    qfield_project="jbuf",
                    collected_count=10,
                    profiled_count=4,
                    positive_count=1,
                    negative_count=1,
                    both_count=2,
                )
            ]

    monkeypatch.setattr(cli_module, "DirectusClient", FakeClient)

    result = runner.invoke(cli_module.cli, ["samples", "summary", "--format", "tsv"])

    assert result.exit_code == 0
    assert result.output.splitlines() == [
        "qfield_project\tcollected\tprofiled\tpositive\tnegative\tboth",
        "jbuf\t10\t4\t1\t1\t2",
    ]


def test_samples_summary_json_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """Project summary JSON should include grouped collected and profiled counts."""

    runner = CliRunner()

    monkeypatch.setattr(
        cli_module,
        "load_settings",
        lambda env_file=None: Settings(
            directus_instance="https://example.test/directus",
            directus_username="user@example.test",
            directus_password="secret",
        ),
    )

    class FakeClient:
        def __init__(self, settings: Settings) -> None:
            self.settings = settings

        def summarize_samples_by_project(self) -> list[ProjectSampleSummary]:
            return [
                ProjectSampleSummary(
                    qfield_project="jbuf",
                    collected_count=10,
                    profiled_count=4,
                    positive_count=1,
                    negative_count=1,
                    both_count=2,
                )
            ]

    monkeypatch.setattr(cli_module, "DirectusClient", FakeClient)

    result = runner.invoke(
        cli_module.cli,
        ["samples", "summary", "--format", "json"],
    )

    assert result.exit_code == 0
    assert result.output.strip() == (
        '{"group_by": "project", "projects": '
        '[{"qfield_project": "jbuf", "collected_count": 10, "profiled_count": 4, '
        '"positive_count": 1, "negative_count": 1, "both_count": 2}]}'
    )


def test_samples_summary_species_text_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """Project summary can count distinct species instead of samples."""

    runner = CliRunner()

    monkeypatch.setattr(
        cli_module,
        "load_settings",
        lambda env_file=None: Settings(
            directus_instance="https://example.test/directus",
            directus_username="user@example.test",
            directus_password="secret",
        ),
    )

    class FakeClient:
        def __init__(self, settings: Settings) -> None:
            self.settings = settings

        def summarize_species_by_project(self) -> list[ProjectSpeciesSummary]:
            return [
                ProjectSpeciesSummary(
                    qfield_project="jbuf",
                    collected_count=7,
                    profiled_count=3,
                    positive_count=1,
                    negative_count=1,
                    both_count=1,
                )
            ]

    monkeypatch.setattr(cli_module, "DirectusClient", FakeClient)

    result = runner.invoke(
        cli_module.cli,
        ["samples", "summary", "--group-by", "project", "--count", "species"],
    )

    assert result.exit_code == 0
    assert "Directus species summary" in result.output
    assert "qfield_project" in result.output
    assert "profiled %" in result.output
    assert "jbuf" in result.output
    assert "42.9%" in result.output


def test_samples_summary_project_group_text_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """Project summary can render a configured project group."""

    runner = CliRunner()

    monkeypatch.setattr(
        cli_module,
        "load_settings",
        lambda env_file=None: Settings(
            directus_instance="https://example.test/directus",
            directus_username="user@example.test",
            directus_password="secret",
        ),
    )

    class FakeClient:
        def __init__(self, settings: Settings) -> None:
            self.settings = settings

        def summarize_samples_by_project_group(self, group_name: str) -> list[ProjectSampleSummary]:
            assert group_name == "dbgi"
            return [
                ProjectSampleSummary(
                    qfield_project="dbgi",
                    collected_count=6644,
                    profiled_count=1170,
                    positive_count=11,
                    negative_count=0,
                    both_count=1159,
                )
            ]

    monkeypatch.setattr(cli_module, "DirectusClient", FakeClient)

    result = runner.invoke(
        cli_module.cli,
        ["samples", "summary", "--project-group", "dbgi"],
    )

    assert result.exit_code == 0
    assert "Directus samples summary" in result.output
    assert "project_group" in result.output
    assert "dbgi" in result.output
    assert "17.6%" in result.output


def test_samples_summary_species_project_group_json_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Species summary JSON can render a configured project group."""

    runner = CliRunner()

    monkeypatch.setattr(
        cli_module,
        "load_settings",
        lambda env_file=None: Settings(
            directus_instance="https://example.test/directus",
            directus_username="user@example.test",
            directus_password="secret",
        ),
    )

    class FakeClient:
        def __init__(self, settings: Settings) -> None:
            self.settings = settings

        def summarize_species_by_project_group(
            self,
            group_name: str,
        ) -> list[ProjectSpeciesSummary]:
            assert group_name == "dbgi"
            return [
                ProjectSpeciesSummary(
                    qfield_project="dbgi",
                    collected_count=3921,
                    profiled_count=939,
                    positive_count=11,
                    negative_count=0,
                    both_count=935,
                )
            ]

    monkeypatch.setattr(cli_module, "DirectusClient", FakeClient)

    result = runner.invoke(
        cli_module.cli,
        [
            "samples",
            "summary",
            "--project-group",
            "dbgi",
            "--count",
            "species",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    assert result.output.strip() == (
        '{"group_by": "project_group", "projects": '
        '[{"project_group": "dbgi", "collected_species_count": 3921, '
        '"profiled_species_count": 939, "positive_species_count": 11, '
        '"negative_species_count": 0, "both_species_count": 935}], '
        '"count_by": "species"}'
    )
