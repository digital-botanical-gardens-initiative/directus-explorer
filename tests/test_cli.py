"""Tests for the Click CLI surface."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from directus_explorer import cli as cli_module
from directus_explorer.config import Settings, SettingsError
from directus_explorer.samples import ProfiledSample, ProjectSampleSummary


def test_cli_help() -> None:
    """The root command should expose the samples group."""

    runner = CliRunner()

    result = runner.invoke(cli_module.cli, ["--help"])

    assert result.exit_code == 0
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
    """Project summary text output should render a table-like listing."""

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
