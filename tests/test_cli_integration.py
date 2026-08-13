"""Side-effect-free integration tests for the professional CLI."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from jarvis import __version__
from jarvis.bootstrap import JarvisApplication
from jarvis.cli import make_console_confirmer, run_cli, run_doctor, run_session
from jarvis.core.config import AIConfig, DatabaseConfig, IntegrationsConfig, JarvisConfig
from jarvis.core.events import EventBus
from jarvis.core.permissions import PermissionRequest
from jarvis.core.runtime import RuntimeResponse
from jarvis.skills.base import RiskLevel


class FakeRuntime:
    def __init__(self, responses: dict[str, RuntimeResponse]) -> None:
        self.events = EventBus()
        self.responses = responses
        self.commands: list[str] = []

    async def process(self, command: str) -> RuntimeResponse:
        self.commands.append(command)
        return self.responses[command]


class FakeSpeechToText:
    def __init__(self, *commands: str) -> None:
        self.commands = list(commands)
        self.calls = 0

    async def listen(self) -> str:
        self.calls += 1
        return self.commands.pop(0)


class FakeTextToSpeech:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def speak(self, text: str) -> None:
        self.messages.append(text)


def test_version_subcommand_needs_no_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "jarvis.cli.load_config",
        Mock(side_effect=AssertionError("version must not load configuration")),
    )
    output: list[str] = []

    status = run_cli(["version"], output_fn=output.append)

    assert status == 0
    assert output == [f"JARVIS {__version__}"]


def test_config_subcommand_prints_effective_configuration_with_secrets_redacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = JarvisConfig(
        ai=AIConfig(
            provider="openai-compatible",
            model="mock-model",
            api_key="never-print-this-secret",
            enabled=True,
        )
    )
    monkeypatch.setattr("jarvis.cli.load_config", lambda _path: config)
    output: list[str] = []

    status = run_cli(["config"], output_fn=output.append)

    assert status == 0
    serialized = json.loads(output[0])
    assert serialized["ai"]["api_key"] == "***"
    assert "never-print-this-secret" not in output[0]


def test_doctor_is_noninvasive_and_reports_disabled_optional_providers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = JarvisConfig(database=DatabaseConfig(tmp_path / "memory.sqlite3"))
    monkeypatch.setattr("jarvis.cli.os.access", lambda _path, _mode: True)
    monkeypatch.setattr("jarvis.cli.importlib.util.find_spec", lambda _module: None)
    monkeypatch.setattr("jarvis.cli.platform.system", lambda: "TestOS")
    monkeypatch.setattr("jarvis.cli.platform.release", lambda: "1.0")
    output: list[str] = []

    status = run_doctor(config, output_fn=output.append)

    assert status == 0
    assert any("Memory storage parent" in line for line in output)
    assert "[warn] AI provider is disabled" in output
    assert "[warn] Vision provider is disabled" in output
    assert any("screenshots: not installed" in line for line in output)
    assert any("Windows application/window control is unavailable" in line for line in output)


def test_doctor_reports_smtp_and_caldav_integration_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = JarvisConfig(
        database=DatabaseConfig(tmp_path / "memory.sqlite3"),
        integrations=IntegrationsConfig(
            email_provider="smtp",
            email_smtp_host="smtp.example.com",
            email_imap_host="imap.example.com",
            email_username="leon@example.com",
            email_password="super-secret",
            calendar_provider="caldav",
            calendar_url="https://caldav.example.com/dav/",
            calendar_username="leon",
            calendar_password="super-secret",
        ),
    )
    monkeypatch.setattr("jarvis.cli.os.access", lambda _path, _mode: True)
    monkeypatch.setattr("jarvis.cli.importlib.util.find_spec", lambda _module: object())
    monkeypatch.setattr("jarvis.cli.platform.system", lambda: "TestOS")
    monkeypatch.setattr("jarvis.cli.platform.release", lambda: "1.0")
    output: list[str] = []

    status = run_doctor(config, output_fn=output.append)

    assert status == 0
    assert "[ok] Email provider: smtp" in output
    assert any("Email SMTP/IMAP host configured" in line for line in output)
    assert any("Email has SMTP and IMAP hosts (read and send)" in line for line in output)
    assert any("Email password (JARVIS_EMAIL_PASSWORD) configured" in line for line in output)
    assert "[ok] Calendar provider: caldav" in output
    assert any("CalDAV endpoint configured" in line for line in output)
    assert any("Calendar password (JARVIS_CALENDAR_PASSWORD) configured" in line for line in output)
    assert "super-secret" not in output[0]


def test_doctor_fails_on_missing_or_unsupported_integrations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = JarvisConfig(
        database=DatabaseConfig(tmp_path / "memory.sqlite3"),
        integrations=IntegrationsConfig(
            email_provider="smtp",
            email_smtp_host="smtp.example.com",
            email_username="leon@example.com",
            calendar_provider="pop-server",
        ),
    )
    monkeypatch.setattr("jarvis.cli.os.access", lambda _path, _mode: True)
    monkeypatch.setattr("jarvis.cli.importlib.util.find_spec", lambda _module: None)
    monkeypatch.setattr("jarvis.cli.platform.system", lambda: "TestOS")
    monkeypatch.setattr("jarvis.cli.platform.release", lambda: "1.0")
    output: list[str] = []

    status = run_doctor(config, output_fn=output.append)

    assert status == 1
    assert any("Email password (JARVIS_EMAIL_PASSWORD) configured" in line for line in output)
    assert any(line.startswith("[error]") for line in output)
    assert any("Unsupported calendar provider: pop-server" in line for line in output)


def test_text_session_routes_commands_and_sanitizes_terminal_output() -> None:
    runtime = FakeRuntime(
        {
            "hello": RuntimeResponse("Hello\x00 there."),
            "exit": RuntimeResponse("Goodbye.", should_exit=True),
        }
    )
    commands = iter(("hello", "exit"))
    prompts: list[str] = []

    def read(prompt: str) -> str:
        prompts.append(prompt)
        return next(commands)

    output: list[str] = []
    application = JarvisApplication(JarvisConfig(), runtime)  # type: ignore[arg-type]

    status = asyncio.run(run_session(application, input_fn=read, output_fn=output.append))

    assert status == 0
    assert runtime.commands == ["hello", "exit"]
    assert prompts == ["You > ", "You > "]
    assert "Jarvis > Hello there." in output
    assert not any("\x00" in line for line in output)


def test_voice_session_uses_only_mocked_stt_and_tts() -> None:
    runtime = FakeRuntime(
        {
            "voice hello": RuntimeResponse("I heard you."),
            "exit": RuntimeResponse("Goodbye.", should_exit=True),
        }
    )
    stt = FakeSpeechToText("voice hello", "exit")
    tts = FakeTextToSpeech()
    output: list[str] = []
    application = JarvisApplication(
        JarvisConfig(),
        runtime,  # type: ignore[arg-type]
        speech_to_text=stt,  # type: ignore[arg-type]
        text_to_speech=tts,  # type: ignore[arg-type]
    )

    status = asyncio.run(run_session(application, voice_mode=True, output_fn=output.append))

    assert status == 0
    assert stt.calls == 2
    assert runtime.commands == ["voice hello", "exit"]
    assert output.count("Listening…") == 2
    assert "You > voice hello" in output
    assert tts.messages == ["I heard you.", "Goodbye."]


def test_console_confirmation_shows_bounded_context_and_is_fail_closed() -> None:
    prompts: list[str] = []
    output: list[str] = []

    def approve(prompt: str) -> str:
        prompts.append(prompt)
        return "yes"

    request = PermissionRequest(
        RiskLevel.SENSITIVE,
        "type_text",
        "Type text into the active application",
        {"content": "Hello world"},
    )
    confirmer = make_console_confirmer(input_fn=approve, output_fn=output.append)

    assert asyncio.run(confirmer(request)) is True
    assert prompts == ["Allow? [y/N] "]
    assert "Action: Type Text" in output
    assert "Risk: SENSITIVE" in output
    assert 'Content: "Hello world"' in output

    def interrupted(_prompt: str) -> str:
        raise EOFError

    fail_closed = make_console_confirmer(input_fn=interrupted, output_fn=lambda _text: None)
    assert asyncio.run(fail_closed(request)) is False
