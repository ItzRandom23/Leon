"""Tests for optional replaceable voice adapters."""

from __future__ import annotations

import asyncio
from unittest.mock import Mock

from jarvis.voice.speech_to_text import SpeechToText
from jarvis.voice.text_to_speech import Pyttsx3TTS, TextToSpeech
from jarvis.voice.wakeword import WakeWordDetector


def test_voice_contracts_are_abstract() -> None:
    for provider in (SpeechToText, TextToSpeech, WakeWordDetector):
        try:
            provider()  # type: ignore[abstract]
        except TypeError:
            pass
        else:  # pragma: no cover - defensive contract assertion
            raise AssertionError(f"{provider.__name__} must remain abstract")


def test_pyttsx3_adapter_speaks_through_injected_engine() -> None:
    engine = Mock()
    provider = Pyttsx3TTS(rate=180, engine=engine)

    asyncio.run(provider.speak(" Hello world "))

    engine.setProperty.assert_called_once_with("rate", 180)
    engine.say.assert_called_once_with("Hello world")
    engine.runAndWait.assert_called_once_with()


def test_pyttsx3_adapter_ignores_empty_output() -> None:
    engine = Mock()
    asyncio.run(Pyttsx3TTS(engine=engine).speak("   "))
    engine.say.assert_not_called()
