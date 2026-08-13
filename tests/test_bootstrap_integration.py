"""Composition-root tests with every optional external provider replaced."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from jarvis.bootstrap import create_application
from jarvis.core.config import (
    AIConfig,
    ConfigError,
    DatabaseConfig,
    JarvisConfig,
    MemoryConfig,
    ScreenshotConfig,
    VisionConfig,
    VoiceConfig,
)


def test_bootstrap_composes_enabled_providers_without_network_desktop_or_microphone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_llm = object()
    fake_vision = object()
    fake_stt = object()
    fake_tts = object()
    llm_factory = Mock(return_value=fake_llm)
    vision_factory = Mock(return_value=fake_vision)
    stt_factory = Mock(return_value=fake_stt)
    tts_factory = Mock(return_value=fake_tts)
    monkeypatch.setattr("jarvis.bootstrap.OpenAICompatibleProvider", llm_factory)
    monkeypatch.setattr("jarvis.bootstrap.OpenAICompatibleVisionProvider", vision_factory)
    monkeypatch.setattr("jarvis.bootstrap.SpeechRecognitionSTT", stt_factory)
    monkeypatch.setattr("jarvis.bootstrap.Pyttsx3TTS", tts_factory)

    config = JarvisConfig(
        ai=AIConfig(
            provider="openai-compatible",
            model="mock-ai",
            base_url="http://localhost:1234/v1",
            enabled=True,
        ),
        vision=VisionConfig(
            provider="openai-compatible",
            model="mock-vision",
            base_url="http://localhost:1234/v1",
            enabled=True,
        ),
        voice=VoiceConfig(
            enabled=True,
            tts_enabled=True,
            stt_provider="speech-recognition",
            tts_provider="pyttsx3",
        ),
        database=DatabaseConfig(tmp_path / "jarvis.sqlite3"),
        screenshots=ScreenshotConfig(tmp_path / "screenshots"),
    )

    application = create_application(config, voice_mode=True)
    try:
        assert application.runtime.llm is fake_llm
        assert application.speech_to_text is fake_stt
        assert application.text_to_speech is fake_tts
        assert "remember" in application.runtime.registry
        assert "analyze_screen" in application.runtime.registry
        assert application.memory_repository is not None
        assert application.memory_repository.closed is False
        llm_factory.assert_called_once()
        vision_factory.assert_called_once()
        stt_factory.assert_called_once_with(language="en-US")
        tts_factory.assert_called_once_with()
    finally:
        application.close()

    assert application.memory_repository is not None
    assert application.memory_repository.closed is True


def test_voice_mode_with_none_provider_fails_without_constructing_recognizer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stt_factory = Mock(side_effect=AssertionError("STT must not be inferred from provider none"))
    monkeypatch.setattr("jarvis.bootstrap.SpeechRecognitionSTT", stt_factory)
    config = JarvisConfig(
        voice=VoiceConfig(stt_provider="none"),
        memory=MemoryConfig(enabled=False),
        database=DatabaseConfig(tmp_path / "unused.sqlite3"),
        screenshots=ScreenshotConfig(tmp_path / "screenshots"),
    )

    with pytest.raises(ConfigError, match="explicit stt_provider"):
        create_application(config, voice_mode=True)

    stt_factory.assert_not_called()


def test_explicit_google_voice_provider_is_constructed_only_through_mock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_stt = object()
    stt_factory = Mock(return_value=fake_stt)
    monkeypatch.setattr("jarvis.bootstrap.SpeechRecognitionSTT", stt_factory)
    config = JarvisConfig(
        voice=VoiceConfig(stt_provider="google", language="en-IN"),
        memory=MemoryConfig(enabled=False),
        database=DatabaseConfig(tmp_path / "unused.sqlite3"),
        screenshots=ScreenshotConfig(tmp_path / "screenshots"),
    )

    application = create_application(config, voice_mode=True)
    try:
        assert application.speech_to_text is fake_stt
        stt_factory.assert_called_once_with(language="en-IN")
    finally:
        application.close()
