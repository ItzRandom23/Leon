"""Safe, platform-specific application resolution and launching."""

from __future__ import annotations

import ctypes
import logging
import os
import platform
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jarvis.computer.applications import sanitized_child_environment
from jarvis.skills.base import RiskLevel, Skill, SkillResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Application:
    """A trusted application definition supplied by JARVIS, not the user."""

    name: str
    display_name: str
    aliases: tuple[str, ...]
    command: tuple[str, ...]

    def __post_init__(self) -> None:
        """Reject definitions that could invoke executable path searching."""

        if not self.command:
            raise ValueError("An application command cannot be empty")
        if not os.path.isabs(self.command[0]):
            raise ValueError("An application executable must use an absolute path")


_WINDOWS_APPLICATION_SPECS: tuple[tuple[str, str, tuple[str, ...], str], ...] = (
    ("notepad", "Notepad", ("notepad", "text editor"), "notepad.exe"),
    ("calculator", "Calculator", ("calculator", "calc"), "calc.exe"),
)


def _windows_system_directory() -> Path:
    """Return the system directory using Windows itself, not search paths."""

    if os.name != "nt":
        raise OSError("The Windows system directory is unavailable on this platform")

    buffer = ctypes.create_unicode_buffer(32_768)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    length = kernel32.GetSystemDirectoryW(buffer, len(buffer))
    if length == 0 or length >= len(buffer):
        raise OSError(ctypes.get_last_error(), "Could not resolve the Windows system directory")

    directory = Path(buffer.value)
    if not directory.is_absolute():
        raise OSError("Windows returned a non-absolute system directory")
    return directory


def default_applications(
    platform_name: str,
    *,
    windows_system_directory: Path | None = None,
) -> tuple[Application, ...]:
    """Build a trusted application catalog for *platform_name*."""

    if platform_name != "Windows":
        return ()

    try:
        system_directory = windows_system_directory or _windows_system_directory()
    except OSError:
        logger.exception("windows_system_directory_resolution_failed")
        return ()

    applications: list[Application] = []
    for name, display_name, aliases, executable_name in _WINDOWS_APPLICATION_SPECS:
        executable = system_directory / executable_name
        if not executable.is_file():
            logger.warning(
                "approved_application_missing",
                extra={"application": name, "path": str(executable)},
            )
            continue
        applications.append(Application(name, display_name, aliases, (str(executable.resolve()),)))
    return tuple(applications)


def normalize_application_name(value: str) -> str:
    """Normalize an application name for exact alias matching."""

    return " ".join(value.casefold().strip().split())


class ApplicationResolver:
    """Resolve names only against trusted definitions for one platform."""

    def __init__(
        self,
        platform_name: str | None = None,
        applications: Mapping[str, Sequence[Application]] | None = None,
    ) -> None:
        self.platform_name = platform_name or platform.system()
        self._applications = (
            tuple(applications.get(self.platform_name, ()))
            if applications is not None
            else default_applications(self.platform_name)
        )
        self._aliases = {
            normalize_application_name(alias): application
            for application in self._applications
            for alias in application.aliases
        }

    @property
    def available(self) -> tuple[Application, ...]:
        """Return applications approved for the selected platform."""

        return self._applications

    def resolve(self, requested_name: str) -> Application | None:
        """Return an exact allowlist match, or ``None``."""

        return self._aliases.get(normalize_application_name(requested_name))


ProcessFactory = Callable[..., Any]


class ApplicationLauncher:
    """Start a trusted application without invoking a command shell."""

    def __init__(self, process_factory: ProcessFactory = subprocess.Popen) -> None:
        self._process_factory = process_factory

    def launch(self, application: Application) -> None:
        """Launch a pre-resolved application and return immediately."""

        executable = Path(application.command[0])
        self._process_factory(
            list(application.command),
            shell=False,
            env=sanitized_child_environment(),
            cwd=str(executable.parent),
        )


class ApplicationSkill(Skill):
    """Handle open requests through an exact platform allowlist."""

    name = "applications"
    description = "Open an approved application."
    risk_level = RiskLevel.ACTION
    _request_pattern = re.compile(
        r"^(?:please\s+)?(?:open|launch|start)(?:\s+(?P<application>.*?))?(?:\s+please)?[.!?]*$",
        re.IGNORECASE,
    )

    def __init__(
        self,
        resolver: ApplicationResolver | None = None,
        launcher: ApplicationLauncher | None = None,
    ) -> None:
        self._resolver = resolver or ApplicationResolver()
        self._launcher = launcher or ApplicationLauncher()

    def can_handle(self, command: str) -> bool:
        return self._request_pattern.fullmatch(command.strip()) is not None

    def execute(self, command: str) -> SkillResult:
        match = self._request_pattern.fullmatch(command.strip())
        requested_name = (
            match.group("application").strip() if match and match.group("application") else ""
        )
        if not requested_name:
            return SkillResult("Please tell me which application to open.", success=False)

        application = self._resolver.resolve(requested_name)
        if application is None:
            if not self._resolver.available:
                return SkillResult(
                    "Application launching is not available for "
                    f"{self._resolver.platform_name} yet.",
                    success=False,
                )
            available = ", ".join(app.display_name for app in self._resolver.available)
            return SkillResult(
                f"I can't open {requested_name!r}. Approved applications: {available}.",
                success=False,
            )

        try:
            self._launcher.launch(application)
        except OSError:
            logger.exception("application_launch_failed", extra={"application": application.name})
            return SkillResult(
                f"I couldn't open {application.display_name}.",
                success=False,
            )

        logger.info("application_launched", extra={"application": application.name})
        return SkillResult(
            f"Opening {application.display_name}...",
            data={"application": application.name},
        )
