"""Tests for configuration loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from directus_explorer.config import Settings, SettingsError, load_settings


def test_load_settings_from_env_file(tmp_path: Path) -> None:
    """A valid env file should produce a Settings object."""

    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "DIRECTUS_INSTANCE=https://example.test/directus/",
                "DIRECTUS_USERNAME=user@example.test",
                "DIRECTUS_PASSWORD=secret",
            ]
        ),
        encoding="utf-8",
    )

    settings = load_settings(env_file=env_file)

    assert settings == Settings(
        directus_instance="https://example.test/directus",
        directus_username="user@example.test",
        directus_password="secret",
    )


def test_load_settings_requires_all_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing required env vars should raise a clear validation error."""

    monkeypatch.delenv("DIRECTUS_INSTANCE", raising=False)
    monkeypatch.delenv("DIRECTUS_USERNAME", raising=False)
    monkeypatch.delenv("DIRECTUS_PASSWORD", raising=False)

    with pytest.raises(
        SettingsError,
        match="DIRECTUS_INSTANCE, DIRECTUS_USERNAME, DIRECTUS_PASSWORD",
    ):
        load_settings(env_file=tmp_path / ".env")
