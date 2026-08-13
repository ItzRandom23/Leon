"""Configuration coverage for Phases 7 through 11."""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.core.config import ConfigError, load_config


def test_extended_environment_overrides_and_secret_redaction(tmp_path: Path) -> None:
    config = load_config(
        env={
            "JARVIS_BROWSER_ENABLED": "true",
            "JARVIS_BROWSER_TYPE": "firefox",
            "JARVIS_BROWSER_MAX_TABS": "5",
            "JARVIS_SCHEDULER_DATABASE_PATH": str(tmp_path / "tasks.db"),
            "JARVIS_SCHEDULER_TIMEZONE": "UTC",
            "JARVIS_GITHUB_ENABLED": "true",
            "JARVIS_GITHUB_TOKEN": "secret-token",
            "JARVIS_PLUGINS_ENABLED": "true",
            "JARVIS_PLUGINS_AUTO_LOAD": "true",
            "JARVIS_PLUGINS_STATE_PATH": str(tmp_path / "plugins.db"),
            "JARVIS_GUI_THEME": "dark",
        }
    )

    assert config.browser.enabled is True
    assert config.browser.browser_type == "firefox"
    assert config.browser.max_tabs == 5
    assert config.scheduler.database_path == (tmp_path / "tasks.db").resolve()
    assert config.integrations.github_enabled is True
    assert config.plugins.auto_load is True
    assert config.gui.theme == "dark"
    assert config.redacted_dict()["integrations"]["github_token"] == "***"


def test_extended_toml_paths_resolve_relative_to_config(tmp_path: Path) -> None:
    path = tmp_path / "jarvis.toml"
    path.write_text(
        """
[scheduler]
database_path = "state/tasks.db"

[plugins]
state_path = "state/plugins.db"
""".strip(),
        encoding="utf-8",
    )

    config = load_config(path, env={})

    assert config.scheduler.database_path == (tmp_path / "state/tasks.db").resolve()
    assert config.plugins.state_path == (tmp_path / "state/plugins.db").resolve()


def test_github_endpoint_is_normalized_without_exposing_credentials() -> None:
    config = load_config(
        env={
            "JARVIS_GITHUB_BASE_URL": "https://github.example/api/v3/",
            "JARVIS_GITHUB_TOKEN": "private-token",
        }
    )

    assert config.integrations.github_base_url == "https://github.example/api/v3"
    assert config.redacted_dict()["integrations"]["github_token"] == "***"


@pytest.mark.parametrize(
    ("environment", "match"),
    [
        ({"JARVIS_BROWSER_PROFILE": "personal"}, "ephemeral"),
        ({"JARVIS_BROWSER_MAX_SESSIONS": "0"}, "positive integer"),
        ({"JARVIS_PLUGINS_AUTO_LOAD": "true"}, "requires"),
        ({"JARVIS_GUI_THEME": "neon"}, "system, light, or dark"),
        ({"JARVIS_GITHUB_BASE_URL": "http://github.example/api"}, "HTTPS"),
        ({"JARVIS_GITHUB_BASE_URL": "https://token@github.example/api"}, "credentials"),
        ({"JARVIS_GITHUB_BASE_URL": "https://github.example/api?q=secret"}, "query"),
    ],
)
def test_extended_configuration_fails_closed(
    environment: dict[str, str],
    match: str,
) -> None:
    with pytest.raises(ConfigError, match=match):
        load_config(env=environment)
