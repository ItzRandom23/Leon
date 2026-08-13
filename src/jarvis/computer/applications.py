"""Trusted application discovery and process control.

Names are resolved only through a fixed catalog.  User-provided strings are never
passed to a shell or treated as executable/process names.
"""

from __future__ import annotations

import ctypes
import importlib
import logging
import os
import platform
import subprocess
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import psutil

from jarvis.computer.errors import (
    ApplicationControlError,
    ApplicationLaunchError,
    ApplicationNotFoundError,
    ApplicationUnavailableError,
    ComputerValidationError,
    UnsupportedPlatformError,
)

logger = logging.getLogger(__name__)

_CHILD_ENV_ALLOWLIST = frozenset(
    {
        "ALLUSERSPROFILE",
        "APPDATA",
        "COMMONPROGRAMFILES",
        "COMMONPROGRAMFILES(X86)",
        "COMSPEC",
        "HOMEDRIVE",
        "HOMEPATH",
        "LANG",
        "LC_ALL",
        "LOCALAPPDATA",
        "NUMBER_OF_PROCESSORS",
        "OS",
        "PATH",
        "PATHEXT",
        "PROCESSOR_ARCHITECTURE",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "PUBLIC",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "USERDOMAIN",
        "USERNAME",
        "USERPROFILE",
        "WINDIR",
    }
)


def sanitized_child_environment(
    environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build a minimal GUI-process environment from explicit safe names.

    A denylist cannot enumerate every token, cookie, agent socket, database URL,
    or future provider secret.  Unknown variables are therefore excluded.
    """

    source = os.environ if environment is None else environment
    return {key: value for key, value in source.items() if key.upper() in _CHILD_ENV_ALLOWLIST}


def _is_windows_elevated() -> bool:
    if platform.system() != "Windows":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        # Discovery from user-writable install locations is unsafe if privilege
        # state cannot be established on Windows.
        return True


def normalize_application_name(value: str) -> str:
    """Normalize a human-entered alias while preserving exact-token matching."""

    if not isinstance(value, str):
        raise ComputerValidationError("application name must be text")
    normalized = " ".join(value.casefold().strip().split())
    if not normalized or len(normalized) > 100:
        raise ComputerValidationError("application name must contain 1 to 100 characters")
    return normalized


@dataclass(frozen=True, slots=True)
class ApplicationDefinition:
    """An application allowlist entry and its trusted executable candidates."""

    name: str
    display_name: str
    aliases: tuple[str, ...]
    executable_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ResolvedApplication:
    """An installed allowlisted application with an absolute executable path."""

    name: str
    display_name: str
    aliases: tuple[str, ...]
    executable: Path

    def __post_init__(self) -> None:
        executable = Path(self.executable)
        if not executable.is_absolute():
            raise ComputerValidationError("application executable must be absolute")
        object.__setattr__(self, "executable", executable)


@dataclass(frozen=True, slots=True)
class RunningApplication:
    """A process known to belong to an allowlisted application."""

    application: ResolvedApplication
    pid: int
    process_name: str


WINDOWS_APPLICATIONS: tuple[ApplicationDefinition, ...] = (
    ApplicationDefinition(
        "notepad",
        "Notepad",
        ("notepad", "text editor"),
        ("notepad.exe",),
    ),
    ApplicationDefinition(
        "calculator",
        "Calculator",
        ("calculator", "calc"),
        ("calc.exe",),
    ),
    ApplicationDefinition(
        "visual-studio-code",
        "Visual Studio Code",
        ("visual studio code", "vs code", "vscode", "code"),
        ("Code.exe",),
    ),
)


class TrustedPathProvider(Protocol):
    """Provides OS-derived, application-specific executable candidates."""

    def candidates(self, application: ApplicationDefinition) -> Iterable[Path]: ...


def _get_windows_system_directory() -> Path:
    """Read System32 through the Windows API rather than a user command."""

    if platform.system() != "Windows":
        raise UnsupportedPlatformError("Windows application paths require Windows")
    buffer = ctypes.create_unicode_buffer(32_768)
    length = ctypes.windll.kernel32.GetSystemDirectoryW(buffer, len(buffer))
    if not length or length >= len(buffer):
        raise OSError("Windows did not return its system directory")
    path = Path(buffer.value)
    if not path.is_absolute():
        raise OSError("Windows returned a non-absolute system directory")
    return path


class WindowsTrustedPathProvider:
    """Resolve candidates from System32, exact install roots, and known registry keys."""

    _VSCODE_REGISTRY_KEYS = (
        r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{EA457B21-F73E-494C-ACAB-524FDE069978}_is1",
        r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\{EA457B21-F73E-494C-ACAB-524FDE069978}_is1",
    )

    def __init__(
        self,
        *,
        system_directory: Path | None = None,
        local_app_data: Path | None = None,
        program_files: Sequence[Path] | None = None,
        registry_install_locations: Callable[[], Iterable[Path]] | None = None,
    ) -> None:
        self._system_directory = system_directory
        self._local_app_data = local_app_data
        self._program_files = tuple(program_files) if program_files is not None else None
        self._registry_locations = registry_install_locations or self._read_vscode_registry

    def candidates(self, application: ApplicationDefinition) -> tuple[Path, ...]:
        """Return only paths built for one fixed catalog definition."""

        if platform.system() != "Windows" and self._system_directory is None:
            raise UnsupportedPlatformError("application discovery is currently Windows-only")
        if application.name in {"notepad", "calculator"}:
            system_directory = self._system_directory or _get_windows_system_directory()
            return tuple(system_directory / name for name in application.executable_names)
        if application.name != "visual-studio-code":
            return ()

        candidates: list[Path] = []
        local_app_data = self._local_app_data
        if local_app_data is None:
            raw_local = os.environ.get("LOCALAPPDATA")
            local_app_data = Path(raw_local) if raw_local else None
        if local_app_data is not None and local_app_data.is_absolute():
            candidates.append(local_app_data / "Programs" / "Microsoft VS Code" / "Code.exe")

        program_files = self._program_files
        if program_files is None:
            program_files = tuple(
                Path(value)
                for key in ("ProgramFiles", "ProgramFiles(x86)")
                if (value := os.environ.get(key)) and Path(value).is_absolute()
            )
        candidates.extend(root / "Microsoft VS Code" / "Code.exe" for root in program_files)
        candidates.extend(location / "Code.exe" for location in self._registry_locations())
        return tuple(candidates)

    def _read_vscode_registry(self) -> tuple[Path, ...]:
        if platform.system() != "Windows":
            return ()
        try:
            winreg = importlib.import_module("winreg")
        except ImportError:
            return ()

        open_key = getattr(winreg, "OpenKey", None)
        query_value = getattr(winreg, "QueryValueEx", None)
        hives = tuple(
            hive
            for hive in (
                getattr(winreg, "HKEY_CURRENT_USER", None),
                getattr(winreg, "HKEY_LOCAL_MACHINE", None),
            )
            if hive is not None
        )
        if not callable(open_key) or not callable(query_value) or not hives:
            return ()

        locations: list[Path] = []
        for hive in hives:
            for key_name in self._VSCODE_REGISTRY_KEYS:
                try:
                    with open_key(hive, key_name) as key:
                        value, _ = query_value(key, "InstallLocation")
                except OSError:
                    continue
                location = Path(str(value))
                if location.is_absolute():
                    locations.append(location)
        return tuple(locations)


class ApplicationResolver:
    """Resolve exact normalized aliases against installed trusted candidates."""

    def __init__(
        self,
        *,
        platform_name: str | None = None,
        path_provider: TrustedPathProvider | None = None,
        definitions: Sequence[ApplicationDefinition] | None = None,
        path_exists: Callable[[Path], bool] | None = None,
    ) -> None:
        self.platform_name = platform_name or platform.system()
        self._path_provider = path_provider or WindowsTrustedPathProvider()
        self._definitions = tuple(definitions or WINDOWS_APPLICATIONS)
        self._path_exists = path_exists or Path.is_file
        self._by_alias = {
            normalize_application_name(alias): definition
            for definition in self._definitions
            for alias in definition.aliases
        }

    def resolve(self, requested_name: str) -> ResolvedApplication:
        """Resolve one allowlisted alias to its first existing absolute executable."""

        if self.platform_name != "Windows" and isinstance(
            self._path_provider, WindowsTrustedPathProvider
        ):
            raise UnsupportedPlatformError(
                f"application discovery is not supported on {self.platform_name}"
            )
        normalized = normalize_application_name(requested_name)
        definition = self._by_alias.get(normalized)
        if definition is None:
            raise ApplicationNotFoundError(f"unknown application: {requested_name!r}")

        try:
            candidates = self._path_provider.candidates(definition)
        except OSError as exc:
            raise ApplicationUnavailableError(
                f"could not discover {definition.display_name}"
            ) from exc
        for candidate in candidates:
            path = Path(candidate)
            approved_names = {name.casefold() for name in definition.executable_names}
            if (
                path.is_absolute()
                and path.name.casefold() in approved_names
                and self._path_exists(path)
            ):
                return ResolvedApplication(
                    definition.name,
                    definition.display_name,
                    definition.aliases,
                    path.resolve(),
                )
        raise ApplicationUnavailableError(f"{definition.display_name} is not installed")

    @property
    def aliases(self) -> tuple[str, ...]:
        """Return the exact aliases accepted by this resolver."""

        return tuple(sorted(self._by_alias))


class ProcessLike(Protocol):
    pid: int
    info: Mapping[str, Any]

    def terminate(self) -> None: ...

    def wait(self, timeout: float | None = None) -> int | None: ...


class PsutilApi(Protocol):
    def process_iter(self, attrs: Sequence[str]) -> Iterable[ProcessLike]: ...


ProcessFactory = Callable[..., Any]


class ApplicationController:
    """Open and control only applications resolved from the trusted catalog."""

    def __init__(
        self,
        resolver: ApplicationResolver | None = None,
        *,
        psutil_api: PsutilApi = psutil,
        process_factory: ProcessFactory = subprocess.Popen,
        elevation_checker: Callable[[], bool] = _is_windows_elevated,
    ) -> None:
        self._resolver = resolver or ApplicationResolver()
        self._psutil = psutil_api
        self._process_factory = process_factory
        self._elevation_checker = elevation_checker

    def open(self, requested_name: str) -> ResolvedApplication:
        """Start an approved executable without invoking a command shell."""

        application = self._resolver.resolve(requested_name)
        if self._resolver.platform_name == "Windows" and self._elevation_checker():
            raise ApplicationLaunchError(
                "application launching is disabled while JARVIS is elevated"
            )
        try:
            self._process_factory(
                [str(application.executable)],
                shell=False,
                env=sanitized_child_environment(),
                cwd=str(application.executable.parent),
            )
        except OSError as exc:
            raise ApplicationLaunchError(f"could not open {application.display_name}") from exc
        logger.info("application_opened", extra={"application": application.name})
        return application

    def find(self, requested_name: str) -> tuple[RunningApplication, ...]:
        """Find processes matching the resolved executable's absolute path."""

        application = self._resolver.resolve(requested_name)
        return self._find_resolved(application)

    def list_running(self) -> tuple[RunningApplication, ...]:
        """List running processes belonging to installed catalog entries."""

        installed: list[ResolvedApplication] = []
        seen_names: set[str] = set()
        for alias in self._resolver.aliases:
            try:
                application = self._resolver.resolve(alias)
            except (ApplicationUnavailableError, UnsupportedPlatformError):
                continue
            if application.name not in seen_names:
                seen_names.add(application.name)
                installed.append(application)
        return self._find_many(installed)

    def close(self, requested_name: str, *, timeout: float = 5.0) -> int:
        """Terminate matched approved processes and return the number signalled."""

        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise ComputerValidationError("timeout must be a number")
        if not 0 < float(timeout) <= 60:
            raise ComputerValidationError("timeout must be between 0 and 60 seconds")
        processes = self._matching_processes(self._resolver.resolve(requested_name))
        for process in processes:
            try:
                process.terminate()
                process.wait(timeout=float(timeout))
            except (psutil.Error, OSError, TimeoutError) as exc:
                raise ApplicationControlError(
                    f"could not close approved process {process.pid}"
                ) from exc
        return len(processes)

    def _find_resolved(self, application: ResolvedApplication) -> tuple[RunningApplication, ...]:
        return tuple(
            RunningApplication(application, process.pid, str(process.info.get("name") or ""))
            for process in self._matching_processes(application)
        )

    def _find_many(
        self, applications: Sequence[ResolvedApplication]
    ) -> tuple[RunningApplication, ...]:
        expected = {_canonical_path(item.executable): item for item in applications}
        found: list[RunningApplication] = []
        for process in self._iter_processes():
            executable = process.info.get("exe")
            if not executable:
                continue
            candidate_path = Path(str(executable))
            if not candidate_path.is_absolute():
                continue
            application = expected.get(_canonical_path(candidate_path))
            if application is not None:
                found.append(
                    RunningApplication(
                        application,
                        process.pid,
                        str(process.info.get("name") or ""),
                    )
                )
        return tuple(sorted(found, key=lambda item: item.pid))

    def _matching_processes(self, application: ResolvedApplication) -> list[ProcessLike]:
        expected = _canonical_path(application.executable)
        return [
            process
            for process in self._iter_processes()
            if process.info.get("exe")
            and Path(str(process.info["exe"])).is_absolute()
            and _canonical_path(Path(str(process.info["exe"]))) == expected
        ]

    def _iter_processes(self) -> Iterable[ProcessLike]:
        try:
            yield from self._psutil.process_iter(("pid", "name", "exe"))
        except (psutil.Error, OSError) as exc:
            raise ApplicationControlError("could not inspect running applications") from exc


def _canonical_path(path: Path) -> str:
    return os.path.normcase(str(path.resolve(strict=False)))
