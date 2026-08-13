"""Tests for safe application resolution and process launching."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from jarvis.skills.applications import (
    Application,
    ApplicationLauncher,
    ApplicationResolver,
    ApplicationSkill,
    default_applications,
    normalize_application_name,
)
from jarvis.skills.base import RiskLevel


@pytest.fixture
def editor(tmp_path: Path) -> Application:
    executable = tmp_path / "trusted-editor"
    executable.touch()
    return Application(
        name="editor",
        display_name="Safe Editor",
        aliases=("editor", "safe editor"),
        command=(str(executable.resolve()), "--new-window"),
    )


@pytest.fixture
def resolver(editor: Application) -> ApplicationResolver:
    return ApplicationResolver(
        platform_name="TestOS",
        applications={"TestOS": (editor,)},
    )


def test_normalize_application_name_is_case_insensitive_and_collapses_space() -> None:
    assert normalize_application_name("  SAFE   Editor  ") == "safe editor"


def test_application_definition_rejects_relative_executable() -> None:
    with pytest.raises(ValueError, match="absolute"):
        Application("unsafe", "Unsafe", ("unsafe",), ("unsafe.exe",))


def test_default_windows_catalog_uses_existing_absolute_system_paths(tmp_path: Path) -> None:
    for filename in ("notepad.exe", "calc.exe"):
        (tmp_path / filename).touch()

    applications = default_applications("Windows", windows_system_directory=tmp_path)

    assert [application.name for application in applications] == ["notepad", "calculator"]
    assert all(Path(application.command[0]).is_absolute() for application in applications)
    assert all(
        Path(application.command[0]).parent == tmp_path.resolve() for application in applications
    )


def test_default_catalog_is_empty_on_unsupported_platform() -> None:
    assert default_applications("TestOS") == ()


@pytest.mark.parametrize("alias", ["editor", "EDITOR", " safe   editor "])
def test_resolver_accepts_only_exact_normalized_aliases(
    resolver: ApplicationResolver,
    editor: Application,
    alias: str,
) -> None:
    assert resolver.resolve(alias) is editor


@pytest.mark.parametrize(
    "untrusted",
    [
        "editor --unsafe",
        "editor && whoami",
        "editor; whoami",
        "trusted-editor",
        "",
    ],
)
def test_resolver_rejects_commands_and_non_aliases(
    resolver: ApplicationResolver,
    untrusted: str,
) -> None:
    assert resolver.resolve(untrusted) is None


def test_resolver_detects_platform_when_not_explicit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("jarvis.skills.applications.platform.system", lambda: "TestOS")
    app = Application("app", "App", ("app",), (str(tmp_path / "app-bin"),))

    resolver = ApplicationResolver(applications={"TestOS": (app,)})

    assert resolver.platform_name == "TestOS"
    assert resolver.available == (app,)


def test_launcher_passes_a_sanitized_process_context(
    editor: Application,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JARVIS_AI_API_KEY", "jarvis-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")
    monkeypatch.setenv("PATH", "safe-path")
    monkeypatch.setenv("JARVIS_TEST_SAFE", "unknown-variable")
    monkeypatch.setenv("GITHUB_TOKEN", "github-secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-secret")
    monkeypatch.setenv("DATABASE_URL", "database-secret")
    monkeypatch.setenv("SSH_AUTH_SOCK", "agent-socket")
    process_factory = Mock()
    launcher = ApplicationLauncher(process_factory=process_factory)

    launcher.launch(editor)

    process_factory.assert_called_once()
    args, kwargs = process_factory.call_args
    assert args == (list(editor.command),)
    assert kwargs["shell"] is False
    assert kwargs["cwd"] == str(Path(editor.command[0]).parent)
    assert kwargs["env"]["PATH"] == "safe-path"
    for excluded in (
        "JARVIS_AI_API_KEY",
        "OPENAI_API_KEY",
        "JARVIS_TEST_SAFE",
        "GITHUB_TOKEN",
        "AWS_SECRET_ACCESS_KEY",
        "DATABASE_URL",
        "SSH_AUTH_SOCK",
    ):
        assert excluded not in kwargs["env"]


def test_application_skill_launches_resolved_application(
    resolver: ApplicationResolver,
    editor: Application,
) -> None:
    launcher = Mock(spec=ApplicationLauncher)
    skill = ApplicationSkill(resolver=resolver, launcher=launcher)

    result = skill.execute("please open SAFE editor please!")

    launcher.launch.assert_called_once_with(editor)
    assert result.success is True
    assert result.message == "Opening Safe Editor..."
    assert result.data == {"application": "editor"}
    assert skill.risk_level is RiskLevel.ACTION


def test_application_skill_asks_for_a_name_without_launching(
    resolver: ApplicationResolver,
) -> None:
    launcher = Mock(spec=ApplicationLauncher)
    skill = ApplicationSkill(resolver=resolver, launcher=launcher)

    result = skill.execute("open")

    assert result.success is False
    assert "which application" in result.message
    launcher.launch.assert_not_called()


def test_application_skill_reports_unknown_app_without_launching(
    resolver: ApplicationResolver,
) -> None:
    launcher = Mock(spec=ApplicationLauncher)
    skill = ApplicationSkill(resolver=resolver, launcher=launcher)

    result = skill.execute("open terminal && delete everything")

    assert result.success is False
    assert "Approved applications: Safe Editor" in result.message
    launcher.launch.assert_not_called()


def test_application_skill_reports_unsupported_platform() -> None:
    resolver = ApplicationResolver(platform_name="UnsupportedOS", applications={})
    launcher = Mock(spec=ApplicationLauncher)

    result = ApplicationSkill(resolver=resolver, launcher=launcher).execute("open editor")

    assert result.success is False
    assert "not available for UnsupportedOS" in result.message
    launcher.launch.assert_not_called()


def test_application_skill_converts_process_error_to_failed_result(
    resolver: ApplicationResolver,
) -> None:
    launcher = Mock(spec=ApplicationLauncher)
    launcher.launch.side_effect = OSError("missing executable")

    result = ApplicationSkill(resolver=resolver, launcher=launcher).execute("launch editor")

    assert result.success is False
    assert "couldn't open Safe Editor" in result.message


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("open editor", True),
        ("Launch editor!", True),
        ("please start editor please", True),
        ("reopen editor", False),
        ("run editor", False),
    ],
)
def test_application_skill_matches_only_supported_open_verbs(
    resolver: ApplicationResolver,
    command: str,
    expected: bool,
) -> None:
    assert ApplicationSkill(resolver=resolver).can_handle(command) is expected
