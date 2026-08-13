"""Immutable public models for the JARVIS plugin SDK."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Final

JARVIS_PLUGIN_API: Final = 1
PLUGIN_ENTRY_POINT_GROUP: Final = "jarvis.plugins"
TRUSTED_PLUGIN_WARNING: Final = (
    "Third-party plugins execute trusted local Python code with the permissions of JARVIS; "
    "they are not sandboxed."
)

_PLUGIN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_DECLARATION = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


class PluginStatus(StrEnum):
    """Observable lifecycle state for a discovered plugin."""

    DISCOVERED = "discovered"
    LOADED = "loaded"
    ENABLED = "enabled"
    DISABLED = "disabled"
    INCOMPATIBLE = "incompatible"
    FAILED = "failed"
    DUPLICATE = "duplicate"


def validate_plugin_id(value: str) -> str:
    """Validate and return an entry-point identifier."""

    if not isinstance(value, str) or not _PLUGIN_ID.fullmatch(value):
        raise ValueError(
            "plugin identifiers must be 1-128 characters containing only letters, "
            "numbers, dots, underscores, and hyphens"
        )
    return value


def _bounded_text(
    label: str,
    value: str,
    *,
    maximum: int,
    multiline: bool = False,
) -> str:
    if not isinstance(value, str):
        raise TypeError(f"plugin {label} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"plugin {label} cannot be empty")
    if len(normalized) > maximum:
        raise ValueError(f"plugin {label} cannot exceed {maximum} characters")
    allowed_controls = {"\t", "\n"} if multiline else set()
    if any(
        (ord(character) < 32 and character not in allowed_controls) or ord(character) == 127
        for character in normalized
    ):
        raise ValueError(f"plugin {label} cannot contain control characters")
    return normalized


def _declarations(label: str, values: tuple[str, ...]) -> tuple[str, ...]:
    if isinstance(values, str):
        raise TypeError(f"plugin {label} must be a sequence of strings, not a string")
    try:
        normalized = tuple(values)
    except TypeError as exc:
        raise TypeError(f"plugin {label} must be a sequence of strings") from exc
    for value in normalized:
        if not isinstance(value, str) or not _DECLARATION.fullmatch(value):
            raise ValueError(
                f"plugin {label} entries must be lowercase identifiers containing only "
                "letters, numbers, dots, underscores, and hyphens"
            )
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"plugin {label} cannot contain duplicates")
    return normalized


def _dependencies(values: tuple[str, ...]) -> tuple[str, ...]:
    if isinstance(values, str):
        raise TypeError("plugin dependencies must be a sequence of strings, not a string")
    try:
        normalized = tuple(values)
    except TypeError as exc:
        raise TypeError("plugin dependencies must be a sequence of strings") from exc
    checked = tuple(_bounded_text("dependency", value, maximum=256) for value in normalized)
    if len(set(checked)) != len(checked):
        raise ValueError("plugin dependencies cannot contain duplicates")
    return checked


@dataclass(frozen=True, slots=True)
class PluginMetadata:
    """Metadata a plugin must expose before JARVIS initializes it."""

    name: str
    version: str
    author: str
    description: str
    permissions: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    api_version: int = JARVIS_PLUGIN_API

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _bounded_text("name", self.name, maximum=128))
        object.__setattr__(self, "version", _bounded_text("version", self.version, maximum=64))
        object.__setattr__(self, "author", _bounded_text("author", self.author, maximum=128))
        object.__setattr__(
            self,
            "description",
            _bounded_text(
                "description",
                self.description,
                maximum=2048,
                multiline=True,
            ),
        )
        object.__setattr__(
            self,
            "permissions",
            _declarations("permissions", self.permissions),
        )
        object.__setattr__(
            self,
            "capabilities",
            _declarations("capabilities", self.capabilities),
        )
        object.__setattr__(self, "dependencies", _dependencies(self.dependencies))
        if not isinstance(self.api_version, int) or isinstance(self.api_version, bool):
            raise TypeError("plugin api_version must be an integer")
        if self.api_version < 1:
            raise ValueError("plugin api_version must be positive")

    @property
    def compatibility_version(self) -> int:
        """Readable alias used in contributor-facing documentation."""

        return self.api_version


@dataclass(frozen=True, slots=True)
class PluginInfo:
    """Safe inspection record for one entry-point plugin."""

    plugin_id: str
    entry_point: str
    status: PluginStatus
    enabled: bool = False
    loaded: bool = False
    metadata: PluginMetadata | None = None
    distribution: str | None = None
    error: str | None = None
    warning: str = TRUSTED_PLUGIN_WARNING
    details: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        validate_plugin_id(self.plugin_id)
        if not isinstance(self.entry_point, str) or not self.entry_point.strip():
            raise ValueError("entry point value cannot be empty")
        if not isinstance(self.status, PluginStatus):
            object.__setattr__(self, "status", PluginStatus(self.status))
        if not isinstance(self.enabled, bool) or not isinstance(self.loaded, bool):
            raise TypeError("plugin enabled and loaded flags must be booleans")
        if self.metadata is not None and not isinstance(self.metadata, PluginMetadata):
            raise TypeError("plugin metadata must be PluginMetadata")
        if self.details is not None:
            object.__setattr__(self, "details", MappingProxyType(dict(self.details)))


class PluginError(Exception):
    """Base class for plugin SDK errors."""


class PluginNotFoundError(PluginError, KeyError):
    """Raised when an entry-point identifier is unknown."""


class PluginCompatibilityError(PluginError):
    """Raised internally when a plugin targets another API version."""


class PluginRegistrationError(PluginError):
    """Raised when staged registrations cannot be committed atomically."""


class PluginStateError(PluginError):
    """Raised when persistent enablement state cannot be read or written."""
