"""Text-to-speech provider contracts and a local system-voice adapter."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any


class VoiceOutputError(RuntimeError):
    """Raised when speech output cannot be produced."""


class TextToSpeech(ABC):
    """Replaceable text-to-speech engine."""

    @abstractmethod
    async def speak(self, text: str) -> None:
        """Speak one response and wait for playback to finish."""


class Pyttsx3TTS(TextToSpeech):
    """Use the optional local pyttsx3 system-speech adapter."""

    def __init__(self, *, rate: int | None = None, engine: Any | None = None) -> None:
        if engine is None:
            try:
                import pyttsx3
            except ImportError as error:  # pragma: no cover - optional installation
                raise VoiceOutputError(
                    "Speech output requires the 'voice' extra: pip install -e '.[voice]'"
                ) from error
            try:
                engine = pyttsx3.init()
            except (OSError, RuntimeError) as error:
                raise VoiceOutputError("The system speech engine is unavailable") from error
        self._engine = engine
        if rate is not None:
            self._engine.setProperty("rate", rate)

    async def speak(self, text: str) -> None:
        """Speak text outside the asyncio event loop."""

        normalized = text.strip()
        if not normalized:
            return
        await asyncio.to_thread(self._speak_sync, normalized)

    def _speak_sync(self, text: str) -> None:
        try:
            self._engine.say(text)
            self._engine.runAndWait()
        except (OSError, RuntimeError) as error:
            raise VoiceOutputError("Speech output failed") from error
