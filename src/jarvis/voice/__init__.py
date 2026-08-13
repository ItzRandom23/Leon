"""Replaceable speech input and output adapters."""

from jarvis.voice.speech_to_text import SpeechRecognitionSTT, SpeechToText
from jarvis.voice.text_to_speech import Pyttsx3TTS, TextToSpeech
from jarvis.voice.wakeword import WakeWordDetector

__all__ = [
    "Pyttsx3TTS",
    "SpeechRecognitionSTT",
    "SpeechToText",
    "TextToSpeech",
    "WakeWordDetector",
]
