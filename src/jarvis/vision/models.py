"""Provider-independent screen-analysis models."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BoundingBox:
    """Pixel-space bounds returned by a provider that supports grounding."""

    left: int
    top: int
    right: int
    bottom: int

    def __post_init__(self) -> None:
        if min(self.left, self.top, self.right, self.bottom) < 0:
            raise ValueError("Bounding-box coordinates cannot be negative")
        if self.right <= self.left or self.bottom <= self.top:
            raise ValueError("Bounding-box edges must form a positive area")

    @property
    def center(self) -> tuple[int, int]:
        """Return the integer center coordinate."""

        return ((self.left + self.right) // 2, (self.top + self.bottom) // 2)


@dataclass(frozen=True, slots=True)
class VisionTarget:
    """A visible object optionally grounded to provider-supplied bounds."""

    label: str
    confidence: float | None = None
    bounds: BoundingBox | None = None

    def __post_init__(self) -> None:
        if not self.label.strip():
            raise ValueError("A vision target label cannot be empty")
        if self.confidence is not None and not 0 <= self.confidence <= 1:
            raise ValueError("Vision confidence must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class VisionAnalysis:
    """Semantic description of an image with optional grounded targets."""

    description: str
    visible_text: tuple[str, ...] = ()
    targets: tuple[VisionTarget, ...] = ()
    model: str | None = None

    def find_grounded_target(self, label: str) -> VisionTarget | None:
        """Return a uniquely grounded exact label match, otherwise ``None``."""

        normalized = " ".join(label.casefold().split())
        matches = [
            target
            for target in self.targets
            if " ".join(target.label.casefold().split()) == normalized and target.bounds is not None
        ]
        return matches[0] if len(matches) == 1 else None
