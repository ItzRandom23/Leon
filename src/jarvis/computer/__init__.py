"""Safe, mockable adapters for local computer capabilities."""

from jarvis.computer._pyautogui import PyAutoGUIAdapter
from jarvis.computer.applications import (
    ApplicationController,
    ApplicationDefinition,
    ApplicationResolver,
    ResolvedApplication,
    RunningApplication,
    WindowsTrustedPathProvider,
)
from jarvis.computer.errors import (
    ApplicationControlError,
    ApplicationError,
    ApplicationLaunchError,
    ApplicationNotFoundError,
    ApplicationUnavailableError,
    AutomationUnavailableError,
    ComputerError,
    ComputerValidationError,
    ScreenshotError,
    UnsupportedPlatformError,
    WindowError,
    WindowFocusError,
    WindowNotFoundError,
)
from jarvis.computer.keyboard import KeyboardController
from jarvis.computer.mouse import MouseController, Point
from jarvis.computer.screen import (
    PillowImageGrabProvider,
    ScreenController,
    Screenshot,
    ScreenshotStore,
)
from jarvis.computer.system import SystemInfoProvider, SystemInformation
from jarvis.computer.windows import WindowBounds, WindowInformation, WindowsController

__all__ = [
    "ApplicationControlError",
    "ApplicationController",
    "ApplicationDefinition",
    "ApplicationError",
    "ApplicationLaunchError",
    "ApplicationNotFoundError",
    "ApplicationResolver",
    "ApplicationUnavailableError",
    "AutomationUnavailableError",
    "ComputerError",
    "ComputerValidationError",
    "KeyboardController",
    "MouseController",
    "PillowImageGrabProvider",
    "Point",
    "PyAutoGUIAdapter",
    "ResolvedApplication",
    "RunningApplication",
    "ScreenController",
    "Screenshot",
    "ScreenshotError",
    "ScreenshotStore",
    "SystemInfoProvider",
    "SystemInformation",
    "UnsupportedPlatformError",
    "WindowBounds",
    "WindowError",
    "WindowFocusError",
    "WindowInformation",
    "WindowNotFoundError",
    "WindowsController",
    "WindowsTrustedPathProvider",
]
