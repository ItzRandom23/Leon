"""Wake-word extension point.

No engine is bundled yet. Continuous listening remains explicit and optional;
JARVIS does not pretend to detect a wake word without a real provider.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class WakeWordDetector(ABC):
    """Contract for a future local or hosted wake-word engine."""

    @abstractmethod
    async def wait(self) -> None:
        """Wait until the configured wake word is detected."""
