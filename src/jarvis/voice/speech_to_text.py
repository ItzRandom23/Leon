"""Speech-to-text provider contracts and a practical microphone adapter."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any


class VoiceInputError(RuntimeError):
    """Raised when microphone input or transcription fails."""


class SpeechToText(ABC):
    """Replaceable speech-to-text engine."""

    @abstractmethod
    async def listen(self) -> str:
        """Capture one utterance and return normalized text."""


class SpeechRecognitionSTT(SpeechToText):
    """Use the optional SpeechRecognition package and its Google recognizer."""

    def __init__(
        self,
        *,
        language: str = "en-US",
        timeout_seconds: float = 8.0,
        phrase_time_limit_seconds: float = 20.0,
        recognizer: Any | None = None,
        microphone_factory: Any | None = None,
    ) -> None:
        if timeout_seconds <= 0 or phrase_time_limit_seconds <= 0:
            raise ValueError("Voice timeouts must be positive")
        try:
            import speech_recognition as speech_recognition
        except ImportError as error:  # pragma: no cover - depends on optional installation
            raise VoiceInputError(
                "Voice input requires the 'voice' extra: pip install -e '.[voice]'"
            ) from error

        self._speech_recognition = speech_recognition
        self._recognizer = recognizer or speech_recognition.Recognizer()
        self._microphone_factory = microphone_factory or speech_recognition.Microphone
        self._language = language
        self._timeout = timeout_seconds
        self._phrase_time_limit = phrase_time_limit_seconds

    async def listen(self) -> str:
        """Capture one microphone utterance outside the asyncio event loop."""

        return await asyncio.to_thread(self._listen_sync)

    def _listen_sync(self) -> str:
        try:
            with self._microphone_factory() as source:
                audio = self._recognizer.listen(
                    source,
                    timeout=self._timeout,
                    phrase_time_limit=self._phrase_time_limit,
                )
            text = self._recognizer.recognize_google(audio, language=self._language)
        except self._speech_recognition.WaitTimeoutError as error:
            raise VoiceInputError("No speech was detected before the timeout") from error
        except self._speech_recognition.UnknownValueError as error:
            raise VoiceInputError("I couldn't understand that audio") from error
        except self._speech_recognition.RequestError as error:
            raise VoiceInputError("The speech recognition service is unavailable") from error
        except (OSError, AttributeError) as error:
            raise VoiceInputError("The microphone is unavailable") from error

        normalized = " ".join(str(text).split())
        if not normalized:
            raise VoiceInputError("The speech recognizer returned no text")
        return normalized
