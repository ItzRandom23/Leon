"""Coverage for the dependency-free dotenv loader and its config integration."""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.core.config import load_config
from jarvis.utils.dotenv import load_env_file, load_env_file_from_default_locations, parse_dotenv


def test_parse_dotenv_ignores_comments_blanks_and_malformed_lines() -> None:
    values = parse_dotenv(
        """
        # a comment
        JARVIS_AI_ENABLED=true
        JARVIS_AI_MODEL="google/gemma-4-e2b"
        JARVIS_AI_BASE_URL='http://127.0.0.1:1234/v1'
        EMPTY=

        JARVIS_AI_API_KEY=
        not-an-assignment
        """.strip()
    )

    assert values == {
        "JARVIS_AI_ENABLED": "true",
        "JARVIS_AI_MODEL": "google/gemma-4-e2b",
        "JARVIS_AI_BASE_URL": "http://127.0.0.1:1234/v1",
        "EMPTY": "",
        "JARVIS_AI_API_KEY": "",
    }


def test_load_env_file_applies_missing_values_only(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("JARVIS_AI_ENABLED=false\nJARVIS_AI_MODEL=local\n", encoding="utf-8")

    environment = {"JARVIS_AI_MODEL": "already-set"}
    applied = load_env_file(env_file, environment=environment)

    assert applied == {"JARVIS_AI_ENABLED": "false"}
    assert environment["JARVIS_AI_ENABLED"] == "false"
    assert environment["JARVIS_AI_MODEL"] == "already-set"


def test_load_env_file_skips_empty_values(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("JARVIS_EMAIL_SMTP_HOST=\nJARVIS_AI_MODEL=local\n", encoding="utf-8")

    applied = load_env_file(env_file, environment={})

    assert applied == {"JARVIS_AI_MODEL": "local"}
    assert "JARVIS_EMAIL_SMTP_HOST" not in applied


def test_load_env_file_missing_path_is_a_noop(tmp_path: Path) -> None:
    assert load_env_file(tmp_path / "missing.env", environment={}) == {}


def test_load_env_file_from_default_locations_uses_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("JARVIS_LOGGING_LEVEL=DEBUG\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    applied = load_env_file_from_default_locations(environment={})

    assert applied == {"JARVIS_LOGGING_LEVEL": "DEBUG"}


def test_dotenv_overrides_are_applied_in_config_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "JARVIS_AI_ENABLED=true\n"
        "JARVIS_AI_PROVIDER=openai-compatible\n"
        "JARVIS_AI_MODEL=google/gemma-4-e2b\n"
        "JARVIS_AI_BASE_URL=http://127.0.0.1:1234/v1\n"
        "JARVIS_SCHEDULER_TIMEZONE=Asia/Kolkata\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    config = load_config()

    assert config.ai.enabled is True
    assert config.ai.provider == "openai-compatible"
    assert config.ai.model == "google/gemma-4-e2b"
    assert config.ai.base_url == "http://127.0.0.1:1234/v1"
    assert config.scheduler.timezone == "Asia/Kolkata"
