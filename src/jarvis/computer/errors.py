"""Exceptions raised by the computer capability adapters."""

from __future__ import annotations


class ComputerError(Exception):
    """Base class for controlled computer-capability failures."""


class UnsupportedPlatformError(ComputerError):
    """Raised when a capability is unavailable on the current platform."""


class ComputerValidationError(ComputerError, ValueError):
    """Raised before unsafe or malformed input reaches a desktop API."""


class ApplicationError(ComputerError):
    """Base class for application discovery and control failures."""


class ApplicationNotFoundError(ApplicationError, LookupError):
    """Raised when a name is not an exact alias in the trusted catalog."""


class ApplicationUnavailableError(ApplicationError):
    """Raised when a trusted application has no installed executable."""


class ApplicationLaunchError(ApplicationError):
    """Raised when an approved executable could not be started."""


class ApplicationControlError(ApplicationError):
    """Raised when an approved running application could not be controlled."""


class AutomationUnavailableError(ComputerError):
    """Raised when the optional desktop automation backend is unavailable."""


class ScreenshotError(ComputerError):
    """Raised when a screenshot cannot be captured or stored."""


class WindowError(ComputerError):
    """Base class for native window-management failures."""


class WindowNotFoundError(WindowError, LookupError):
    """Raised when no visible window has the requested exact title."""


class WindowFocusError(WindowError):
    """Raised when the operating system refuses to focus a window."""
