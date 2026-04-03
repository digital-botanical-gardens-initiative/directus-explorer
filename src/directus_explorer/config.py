"""Configuration loading for the directus-explorer package."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values


class SettingsError(ValueError):
    """Raised when required settings are missing or invalid."""


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime settings required to connect to Directus."""

    directus_instance: str
    directus_username: str
    directus_password: str


def load_settings(env_file: str | Path | None = None) -> Settings:
    """Load Directus settings from a local env file and process environment."""

    env_path = Path(env_file) if env_file is not None else Path(".env")
    env_values = _read_env_file(env_path)
    merged_env = {**env_values, **os.environ}

    instance = merged_env.get("DIRECTUS_INSTANCE", "").strip()
    username = merged_env.get("DIRECTUS_USERNAME", "").strip()
    password = merged_env.get("DIRECTUS_PASSWORD", "").strip()

    missing = [
        key
        for key, value in (
            ("DIRECTUS_INSTANCE", instance),
            ("DIRECTUS_USERNAME", username),
            ("DIRECTUS_PASSWORD", password),
        )
        if not value
    ]
    if missing:
        joined = ", ".join(missing)
        raise SettingsError(f"Missing required environment variables: {joined}")

    return Settings(
        directus_instance=instance.rstrip("/"),
        directus_username=username,
        directus_password=password,
    )


def _read_env_file(env_path: Path) -> dict[str, str]:
    """Read a dotenv file when present and return normalized string values."""

    if not env_path.exists():
        return {}

    values = dotenv_values(env_path)
    return {key: value for key, value in values.items() if value is not None}
