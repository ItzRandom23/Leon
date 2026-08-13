"""Controlled failures raised by browser capability adapters."""

from __future__ import annotations


class BrowserError(Exception):
    """Base class for browser capability failures."""


class BrowserValidationError(BrowserError, ValueError):
    """Raised before unsafe or malformed input reaches a browser backend."""


class BrowserDependencyError(BrowserError):
    """Raised when an optional browser backend is unavailable."""


class BrowserSessionError(BrowserError):
    """Raised when a session or tab cannot perform an operation."""


class BrowserLimitError(BrowserSessionError):
    """Raised when a configured browser resource limit would be exceeded."""


class BrowserNavigationError(BrowserSessionError):
    """Raised when a navigation fails or reaches an unsafe URL."""


class BrowserElementError(BrowserSessionError, LookupError):
    """Raised when an element ID is unknown, stale, or unusable."""


class BrowserTimeoutError(BrowserSessionError, TimeoutError):
    """Raised when a bounded browser operation exceeds its deadline."""
