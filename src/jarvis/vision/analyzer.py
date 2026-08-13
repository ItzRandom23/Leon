"""Coordinate screenshot lifecycle with semantic vision providers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractContextManager, asynccontextmanager
from pathlib import Path
from typing import Protocol

from jarvis.vision.models import VisionAnalysis
from jarvis.vision.providers import VisionProvider


class CapturedImage(Protocol):
    @property
    def path(self) -> Path: ...


class ScreenCapture(Protocol):
    """Screen provider capable of temporary capture cleanup."""

    def temporary_screen(self) -> AbstractContextManager[CapturedImage]: ...


class VisionAnalyzer:
    """Capture, analyze, and remove a temporary screenshot."""

    def __init__(self, capture: ScreenCapture, provider: VisionProvider) -> None:
        self._capture = capture
        self._provider = provider

    async def analyze_screen(self, prompt: str = "Describe what is visible.") -> VisionAnalysis:
        """Analyze a temporary screenshot with deterministic cleanup."""

        with self._capture.temporary_screen() as screenshot:
            return await self._provider.analyze_image(screenshot.path, prompt)

    @asynccontextmanager
    async def grounded_screen(
        self,
        prompt: str,
    ) -> AsyncIterator[tuple[Path, VisionAnalysis]]:
        """Hold a capture while a caller safely acts on verified provider bounds."""

        with self._capture.temporary_screen() as screenshot:
            analysis = await self._provider.analyze_image(screenshot.path, prompt)
            yield screenshot.path, analysis
