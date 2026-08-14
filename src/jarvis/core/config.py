"""Typed JARVIS configuration loaded from TOML and environment variables."""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from jarvis.core.permissions import PermissionPolicy
from jarvis.utils.dotenv import load_env_file_from_default_locations


class ConfigError(ValueError):
    """Raised when configuration is malformed or internally inconsistent."""


def _text(value: Any, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"{label} must be a string")
    value = value.strip()
    if not allow_empty and not value:
        raise ConfigError(f"{label} cannot be empty")
    return value


def _optional_text(value: Any, label: str) -> str | None:
    if value is None:
        return None
    normalized = _text(value, label, allow_empty=True)
    return normalized or None


def _https_endpoint(value: Any, label: str) -> str:
    """Validate one credential-bearing HTTPS service endpoint."""

    endpoint = _text(value, label)
    if (
        any(
            character.isspace() or ord(character) < 32 or ord(character) == 127
            for character in endpoint
        )
        or "\\" in endpoint
    ):
        raise ConfigError(f"{label} must be an absolute HTTPS URL")
    try:
        parsed = urlsplit(endpoint)
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError:
        raise ConfigError(f"{label} must be an absolute HTTPS URL") from None
    if parsed.scheme != "https" or not parsed.netloc or hostname is None:
        raise ConfigError(f"{label} must be an absolute HTTPS URL")
    if parsed.username is not None or parsed.password is not None:
        raise ConfigError(f"{label} cannot contain credentials")
    if parsed.query or parsed.fragment:
        raise ConfigError(f"{label} cannot contain a query or fragment")
    return endpoint.rstrip("/")


def _positive_number(value: Any, label: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        raise ConfigError(f"{label} must be a positive number")
    return float(value)


def _positive_integer(value: Any, label: str, *, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ConfigError(f"{label} must be a positive integer")
    if maximum is not None and value > maximum:
        raise ConfigError(f"{label} must be at most {maximum}")
    return value


def _port(value: Any, label: str) -> int:
    return _positive_integer(value, label, maximum=65535)


def _choice(value: Any, label: str, allowed: AbstractSet[str]) -> str:
    normalized = _text(value, label).casefold()
    if normalized not in allowed:
        raise ConfigError(f"{label} must be one of: {', '.join(sorted(allowed))}")
    return normalized


def _path(value: str | os.PathLike[str], label: str, *, base: Path | None = None) -> Path:
    if not isinstance(value, (str, os.PathLike)):
        raise ConfigError(f"{label} must be a filesystem path")
    expanded = os.path.expandvars(os.path.expanduser(os.fspath(value).strip()))
    if not expanded:
        raise ConfigError(f"{label} cannot be empty")
    result = Path(expanded)
    if not result.is_absolute() and base is not None:
        result = base / result
    return result.resolve(strict=False)


@dataclass(frozen=True, slots=True)
class AIConfig:
    """Language-model provider settings."""

    provider: str = "openai-compatible"
    model: str = ""
    base_url: str | None = None
    api_key: str | None = field(default=None, repr=False, metadata={"secret": True})
    timeout_seconds: float = 30.0
    enabled: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", _text(self.provider, "ai.provider"))
        object.__setattr__(self, "model", _text(self.model, "ai.model", allow_empty=True))
        object.__setattr__(self, "base_url", _optional_text(self.base_url, "ai.base_url"))
        object.__setattr__(self, "api_key", _optional_text(self.api_key, "ai.api_key"))
        object.__setattr__(
            self, "timeout_seconds", _positive_number(self.timeout_seconds, "ai.timeout_seconds")
        )
        if not isinstance(self.enabled, bool):
            raise ConfigError("ai.enabled must be a boolean")
        if self.enabled and not self.model:
            raise ConfigError("ai.model is required when AI is enabled")


@dataclass(frozen=True, slots=True)
class VisionConfig:
    """Image-analysis provider settings."""

    provider: str = "openai-compatible"
    model: str = ""
    base_url: str | None = None
    api_key: str | None = field(default=None, repr=False, metadata={"secret": True})
    timeout_seconds: float = 30.0
    enabled: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", _text(self.provider, "vision.provider"))
        object.__setattr__(self, "model", _text(self.model, "vision.model", allow_empty=True))
        object.__setattr__(self, "base_url", _optional_text(self.base_url, "vision.base_url"))
        object.__setattr__(self, "api_key", _optional_text(self.api_key, "vision.api_key"))
        object.__setattr__(
            self,
            "timeout_seconds",
            _positive_number(self.timeout_seconds, "vision.timeout_seconds"),
        )
        if not isinstance(self.enabled, bool):
            raise ConfigError("vision.enabled must be a boolean")
        if self.enabled and not self.model:
            raise ConfigError("vision.model is required when vision is enabled")


@dataclass(frozen=True, slots=True)
class VoiceConfig:
    """Replaceable speech input/output settings."""

    enabled: bool = False
    tts_enabled: bool = False
    stt_provider: str = "none"
    tts_provider: str = "none"
    language: str = "en-US"

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool) or not isinstance(self.tts_enabled, bool):
            raise ConfigError("voice enabled settings must be booleans")
        object.__setattr__(self, "stt_provider", _text(self.stt_provider, "voice.stt_provider"))
        object.__setattr__(self, "tts_provider", _text(self.tts_provider, "voice.tts_provider"))
        object.__setattr__(self, "language", _text(self.language, "voice.language"))


@dataclass(frozen=True, slots=True)
class MemoryConfig:
    """Policies controlling what conversation data may persist."""

    enabled: bool = True
    auto_save: bool = False
    persist_conversations: bool = False
    allow_sensitive: bool = False

    def __post_init__(self) -> None:
        for config_field in fields(self):
            if not isinstance(getattr(self, config_field.name), bool):
                raise ConfigError(f"memory.{config_field.name} must be a boolean")


def _default_database_path() -> Path:
    return Path.home() / ".jarvis" / "jarvis.db"


@dataclass(frozen=True, slots=True)
class DatabaseConfig:
    """Persistent storage location."""

    path: Path = field(default_factory=_default_database_path)

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _path(self.path, "database.path"))


@dataclass(frozen=True, slots=True)
class LoggingConfig:
    """Structured logging settings."""

    level: str = "INFO"
    file: Path | None = None

    def __post_init__(self) -> None:
        level = _text(self.level, "logging.level").upper()
        if level not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
            raise ConfigError(f"Unsupported logging level: {self.level!r}")
        object.__setattr__(self, "level", level)
        if self.file is not None:
            object.__setattr__(self, "file", _path(self.file, "logging.file"))


@dataclass(frozen=True, slots=True)
class PermissionConfig:
    """Per-category permission behavior."""

    read: PermissionPolicy = PermissionPolicy.ALLOW
    action: PermissionPolicy = PermissionPolicy.ASK
    sensitive: PermissionPolicy = PermissionPolicy.ASK
    destructive: PermissionPolicy = PermissionPolicy.ASK

    def __post_init__(self) -> None:
        for config_field in fields(self):
            value = getattr(self, config_field.name)
            try:
                policy = (
                    value
                    if isinstance(value, PermissionPolicy)
                    else PermissionPolicy(str(value).lower())
                )
            except ValueError as exc:
                raise ConfigError(
                    f"permissions.{config_field.name} must be ask, allow, or deny"
                ) from exc
            object.__setattr__(self, config_field.name, policy)

    def as_mapping(self) -> dict[str, PermissionPolicy]:
        """Return policies keyed by RiskLevel-compatible uppercase names."""

        return {
            "READ": self.read,
            "ACTION": self.action,
            "SENSITIVE": self.sensitive,
            "DESTRUCTIVE": self.destructive,
        }


def _default_screenshot_directory() -> Path:
    return Path.home() / ".jarvis" / "screenshots"


@dataclass(frozen=True, slots=True)
class ScreenshotConfig:
    """Screenshot storage settings."""

    directory: Path = field(default_factory=_default_screenshot_directory)
    keep_temporary: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "directory", _path(self.directory, "screenshots.directory"))
        if not isinstance(self.keep_temporary, bool):
            raise ConfigError("screenshots.keep_temporary must be a boolean")

    @property
    def path(self) -> Path:
        """Compatibility alias for ``directory``."""

        return self.directory

    @property
    def storage_path(self) -> Path:
        """Compatibility alias for ``directory``."""

        return self.directory


@dataclass(frozen=True, slots=True)
class BrowserConfig:
    """Safe browser-automation settings.

    Browser support is opt-in because starting it installs a network-capable
    automation surface. Profiles are intentionally ephemeral in this release.
    """

    enabled: bool = False
    browser_type: str = "chromium"
    headless: bool = True
    profile: str = "ephemeral"
    max_sessions: int = 2
    max_tabs: int = 8

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool) or not isinstance(self.headless, bool):
            raise ConfigError("browser enabled/headless settings must be booleans")
        browser_type = _text(self.browser_type, "browser.browser_type").casefold()
        if browser_type not in {"chromium", "firefox", "webkit"}:
            raise ConfigError("browser.browser_type must be chromium, firefox, or webkit")
        profile = _text(self.profile, "browser.profile").casefold()
        if profile != "ephemeral":
            raise ConfigError("browser.profile currently supports only 'ephemeral'")
        object.__setattr__(self, "browser_type", browser_type)
        object.__setattr__(self, "profile", profile)
        object.__setattr__(
            self,
            "max_sessions",
            _positive_integer(self.max_sessions, "browser.max_sessions", maximum=8),
        )
        object.__setattr__(
            self,
            "max_tabs",
            _positive_integer(self.max_tabs, "browser.max_tabs", maximum=32),
        )


def _default_task_database_path() -> Path:
    return Path.home() / ".jarvis" / "tasks.db"


@dataclass(frozen=True, slots=True)
class SchedulerConfig:
    """Persistent reminder scheduler settings."""

    enabled: bool = True
    database_path: Path = field(default_factory=_default_task_database_path)
    timezone: str = "UTC"
    poll_interval_seconds: float = 30.0
    desktop_notifications: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool) or not isinstance(self.desktop_notifications, bool):
            raise ConfigError("scheduler enabled/notification settings must be booleans")
        object.__setattr__(
            self, "database_path", _path(self.database_path, "scheduler.database_path")
        )
        timezone = _text(self.timezone, "scheduler.timezone")
        try:
            from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

            ZoneInfo(timezone)
        except ZoneInfoNotFoundError as exc:
            raise ConfigError(f"Unknown scheduler timezone: {timezone!r}") from exc
        object.__setattr__(self, "timezone", timezone)
        object.__setattr__(
            self,
            "poll_interval_seconds",
            _positive_number(self.poll_interval_seconds, "scheduler.poll_interval_seconds"),
        )


@dataclass(frozen=True, slots=True)
class IntegrationsConfig:
    """External-service settings; credentials are always redacted on export."""

    github_enabled: bool = False
    github_token: str | None = field(default=None, repr=False, metadata={"secret": True})
    github_base_url: str = "https://api.github.com"
    email_provider: str = "none"
    email_smtp_host: str = ""
    email_smtp_port: int = 587
    email_smtp_mode: str = "starttls"
    email_imap_host: str = ""
    email_imap_port: int = 993
    email_imap_ssl: bool = True
    email_username: str = ""
    email_from: str = ""
    email_password: str | None = field(default=None, repr=False, metadata={"secret": True})
    calendar_provider: str = "none"
    calendar_url: str = ""
    calendar_username: str = ""
    calendar_password: str | None = field(default=None, repr=False, metadata={"secret": True})

    def __post_init__(self) -> None:
        if not isinstance(self.github_enabled, bool):
            raise ConfigError("integrations.github_enabled must be a boolean")
        object.__setattr__(
            self, "github_token", _optional_text(self.github_token, "integrations.github_token")
        )
        object.__setattr__(
            self,
            "github_base_url",
            _https_endpoint(self.github_base_url, "integrations.github_base_url"),
        )
        object.__setattr__(
            self,
            "email_provider",
            _text(self.email_provider, "integrations.email_provider").casefold(),
        )
        object.__setattr__(
            self,
            "email_smtp_host",
            _text(self.email_smtp_host, "integrations.email_smtp_host", allow_empty=True),
        )
        object.__setattr__(
            self,
            "email_smtp_port",
            _port(self.email_smtp_port, "integrations.email_smtp_port"),
        )
        object.__setattr__(
            self,
            "email_smtp_mode",
            _choice(
                self.email_smtp_mode,
                "integrations.email_smtp_mode",
                {"starttls", "ssl", "none"},
            ),
        )
        object.__setattr__(
            self,
            "email_imap_host",
            _text(self.email_imap_host, "integrations.email_imap_host", allow_empty=True),
        )
        object.__setattr__(
            self,
            "email_imap_port",
            _port(self.email_imap_port, "integrations.email_imap_port"),
        )
        if not isinstance(self.email_imap_ssl, bool):
            raise ConfigError("integrations.email_imap_ssl must be a boolean")
        object.__setattr__(
            self,
            "email_username",
            _text(self.email_username, "integrations.email_username", allow_empty=True),
        )
        object.__setattr__(
            self,
            "email_from",
            _text(self.email_from, "integrations.email_from", allow_empty=True),
        )
        object.__setattr__(
            self,
            "email_password",
            _optional_text(self.email_password, "integrations.email_password"),
        )
        object.__setattr__(
            self,
            "calendar_provider",
            _text(self.calendar_provider, "integrations.calendar_provider").casefold(),
        )
        calendar_url = _text(self.calendar_url, "integrations.calendar_url", allow_empty=True)
        object.__setattr__(
            self,
            "calendar_url",
            "" if not calendar_url else _https_endpoint(calendar_url, "integrations.calendar_url"),
        )
        object.__setattr__(
            self,
            "calendar_username",
            _text(self.calendar_username, "integrations.calendar_username", allow_empty=True),
        )
        object.__setattr__(
            self,
            "calendar_password",
            _optional_text(self.calendar_password, "integrations.calendar_password"),
        )


def _default_plugin_state_path() -> Path:
    return Path.home() / ".jarvis" / "plugins.db"


@dataclass(frozen=True, slots=True)
class PluginsConfig:
    """Trusted Python plugin discovery and state settings."""

    enabled: bool = False
    auto_load: bool = False
    state_path: Path = field(default_factory=_default_plugin_state_path)

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool) or not isinstance(self.auto_load, bool):
            raise ConfigError("plugins enabled/auto_load settings must be booleans")
        if self.auto_load and not self.enabled:
            raise ConfigError("plugins.auto_load requires plugins.enabled=true")
        object.__setattr__(self, "state_path", _path(self.state_path, "plugins.state_path"))


@dataclass(frozen=True, slots=True)
class GUIConfig:
    """Desktop interface preferences."""

    theme: str = "system"
    minimize_to_tray: bool = False
    show_debug_logs: bool = False

    def __post_init__(self) -> None:
        theme = _text(self.theme, "gui.theme").casefold()
        if theme not in {"system", "light", "dark"}:
            raise ConfigError("gui.theme must be system, light, or dark")
        if not isinstance(self.minimize_to_tray, bool) or not isinstance(
            self.show_debug_logs, bool
        ):
            raise ConfigError("GUI tray/debug settings must be booleans")
        object.__setattr__(self, "theme", theme)


@dataclass(frozen=True, slots=True)
class JarvisConfig:
    """Complete immutable application configuration."""

    ai: AIConfig = field(default_factory=AIConfig)
    vision: VisionConfig = field(default_factory=VisionConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    permissions: PermissionConfig = field(default_factory=PermissionConfig)
    screenshots: ScreenshotConfig = field(default_factory=ScreenshotConfig)
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    integrations: IntegrationsConfig = field(default_factory=IntegrationsConfig)
    plugins: PluginsConfig = field(default_factory=PluginsConfig)
    gui: GUIConfig = field(default_factory=GUIConfig)

    def __post_init__(self) -> None:
        expected = {
            "ai": AIConfig,
            "vision": VisionConfig,
            "voice": VoiceConfig,
            "memory": MemoryConfig,
            "database": DatabaseConfig,
            "logging": LoggingConfig,
            "permissions": PermissionConfig,
            "screenshots": ScreenshotConfig,
            "browser": BrowserConfig,
            "scheduler": SchedulerConfig,
            "integrations": IntegrationsConfig,
            "plugins": PluginsConfig,
            "gui": GUIConfig,
        }
        for name, expected_type in expected.items():
            if not isinstance(getattr(self, name), expected_type):
                raise ConfigError(f"{name} must be a {expected_type.__name__}")

    @property
    def permission(self) -> PermissionConfig:
        """Compatibility alias for ``permissions``."""

        return self.permissions

    @property
    def screenshot(self) -> ScreenshotConfig:
        """Compatibility alias for ``screenshots``."""

        return self.screenshots

    def to_dict(self, *, redact_secrets: bool = True) -> dict[str, Any]:
        """Serialize to basic values, redacting credentials by default."""

        return _serialize(self, redact_secrets=redact_secrets)

    def redacted_dict(self) -> dict[str, Any]:
        """Return a serialization safe for logs and diagnostic output."""

        return self.to_dict(redact_secrets=True)


Config = JarvisConfig
AppConfig = JarvisConfig


def _serialize(value: Any, *, redact_secrets: bool, secret: bool = False) -> Any:
    if secret and redact_secrets:
        return "***" if value else None
    if is_dataclass(value) and not isinstance(value, type):
        return {
            config_field.name: _serialize(
                getattr(value, config_field.name),
                redact_secrets=redact_secrets,
                secret=bool(config_field.metadata.get("secret")),
            )
            for config_field in fields(value)
        }
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {
            str(key): _serialize(item, redact_secrets=redact_secrets) for key, item in value.items()
        }
    return value


_SECTION_TYPES: dict[str, type[Any]] = {
    "ai": AIConfig,
    "vision": VisionConfig,
    "voice": VoiceConfig,
    "memory": MemoryConfig,
    "database": DatabaseConfig,
    "logging": LoggingConfig,
    "permissions": PermissionConfig,
    "screenshots": ScreenshotConfig,
    "browser": BrowserConfig,
    "scheduler": SchedulerConfig,
    "integrations": IntegrationsConfig,
    "plugins": PluginsConfig,
    "gui": GUIConfig,
}
_SECTION_ALIASES = {"permission": "permissions", "screenshot": "screenshots"}
_KEY_ALIASES: dict[str, dict[str, str]] = {
    "ai": {"timeout": "timeout_seconds"},
    "vision": {"timeout": "timeout_seconds"},
    "logging": {"path": "file"},
    "screenshots": {"path": "directory", "storage_path": "directory"},
}


def _parse_bool(value: str, label: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{label} must be true or false")


def _parse_float(value: str, label: str) -> float:
    try:
        result = float(value)
    except ValueError as exc:
        raise ConfigError(f"{label} must be a number") from exc
    return _positive_number(result, label)


def _parse_int(value: str, label: str) -> int:
    try:
        result = int(value)
    except ValueError as exc:
        raise ConfigError(f"{label} must be an integer") from exc
    return _positive_integer(result, label)


def _environment_overrides(environment: Mapping[str, str]) -> dict[str, dict[str, Any]]:
    specs: dict[str, tuple[str, str, str]] = {
        "JARVIS_AI_PROVIDER": ("ai", "provider", "text"),
        "JARVIS_AI_MODEL": ("ai", "model", "text"),
        "JARVIS_AI_BASE_URL": ("ai", "base_url", "optional"),
        "JARVIS_AI_API_KEY": ("ai", "api_key", "optional"),
        "JARVIS_AI_TIMEOUT_SECONDS": ("ai", "timeout_seconds", "float"),
        "JARVIS_AI_ENABLED": ("ai", "enabled", "bool"),
        "JARVIS_VISION_PROVIDER": ("vision", "provider", "text"),
        "JARVIS_VISION_MODEL": ("vision", "model", "text"),
        "JARVIS_VISION_BASE_URL": ("vision", "base_url", "optional"),
        "JARVIS_VISION_API_KEY": ("vision", "api_key", "optional"),
        "JARVIS_VISION_TIMEOUT_SECONDS": ("vision", "timeout_seconds", "float"),
        "JARVIS_VISION_ENABLED": ("vision", "enabled", "bool"),
        "JARVIS_VOICE_ENABLED": ("voice", "enabled", "bool"),
        "JARVIS_VOICE_TTS_ENABLED": ("voice", "tts_enabled", "bool"),
        "JARVIS_VOICE_STT_PROVIDER": ("voice", "stt_provider", "text"),
        "JARVIS_VOICE_TTS_PROVIDER": ("voice", "tts_provider", "text"),
        "JARVIS_VOICE_LANGUAGE": ("voice", "language", "text"),
        "JARVIS_MEMORY_ENABLED": ("memory", "enabled", "bool"),
        "JARVIS_MEMORY_AUTO_SAVE": ("memory", "auto_save", "bool"),
        "JARVIS_MEMORY_PERSIST_CONVERSATIONS": (
            "memory",
            "persist_conversations",
            "bool",
        ),
        "JARVIS_MEMORY_ALLOW_SENSITIVE": ("memory", "allow_sensitive", "bool"),
        "JARVIS_DATABASE_PATH": ("database", "path", "text"),
        "JARVIS_LOGGING_LEVEL": ("logging", "level", "text"),
        "JARVIS_LOGGING_FILE": ("logging", "file", "optional"),
        "JARVIS_PERMISSIONS_READ": ("permissions", "read", "text"),
        "JARVIS_PERMISSIONS_ACTION": ("permissions", "action", "text"),
        "JARVIS_PERMISSIONS_SENSITIVE": ("permissions", "sensitive", "text"),
        "JARVIS_PERMISSIONS_DESTRUCTIVE": ("permissions", "destructive", "text"),
        "JARVIS_PERMISSION_READ": ("permissions", "read", "text"),
        "JARVIS_PERMISSION_ACTION": ("permissions", "action", "text"),
        "JARVIS_PERMISSION_SENSITIVE": ("permissions", "sensitive", "text"),
        "JARVIS_PERMISSION_DESTRUCTIVE": ("permissions", "destructive", "text"),
        "JARVIS_SCREENSHOTS_DIRECTORY": ("screenshots", "directory", "text"),
        "JARVIS_SCREENSHOTS_PATH": ("screenshots", "directory", "text"),
        "JARVIS_SCREENSHOT_DIRECTORY": ("screenshots", "directory", "text"),
        "JARVIS_SCREENSHOT_PATH": ("screenshots", "directory", "text"),
        "JARVIS_SCREENSHOTS_KEEP_TEMPORARY": ("screenshots", "keep_temporary", "bool"),
        "JARVIS_BROWSER_ENABLED": ("browser", "enabled", "bool"),
        "JARVIS_BROWSER_TYPE": ("browser", "browser_type", "text"),
        "JARVIS_BROWSER_HEADLESS": ("browser", "headless", "bool"),
        "JARVIS_BROWSER_PROFILE": ("browser", "profile", "text"),
        "JARVIS_BROWSER_MAX_SESSIONS": ("browser", "max_sessions", "int"),
        "JARVIS_BROWSER_MAX_TABS": ("browser", "max_tabs", "int"),
        "JARVIS_SCHEDULER_ENABLED": ("scheduler", "enabled", "bool"),
        "JARVIS_SCHEDULER_DATABASE_PATH": ("scheduler", "database_path", "text"),
        "JARVIS_SCHEDULER_TIMEZONE": ("scheduler", "timezone", "text"),
        "JARVIS_SCHEDULER_POLL_INTERVAL_SECONDS": (
            "scheduler",
            "poll_interval_seconds",
            "float",
        ),
        "JARVIS_SCHEDULER_DESKTOP_NOTIFICATIONS": (
            "scheduler",
            "desktop_notifications",
            "bool",
        ),
        "JARVIS_GITHUB_ENABLED": ("integrations", "github_enabled", "bool"),
        "JARVIS_GITHUB_TOKEN": ("integrations", "github_token", "optional"),
        "JARVIS_GITHUB_BASE_URL": ("integrations", "github_base_url", "text"),
        "JARVIS_EMAIL_PROVIDER": ("integrations", "email_provider", "text"),
        "JARVIS_EMAIL_SMTP_HOST": ("integrations", "email_smtp_host", "text"),
        "JARVIS_EMAIL_SMTP_PORT": ("integrations", "email_smtp_port", "int"),
        "JARVIS_EMAIL_SMTP_MODE": ("integrations", "email_smtp_mode", "text"),
        "JARVIS_EMAIL_IMAP_HOST": ("integrations", "email_imap_host", "text"),
        "JARVIS_EMAIL_IMAP_PORT": ("integrations", "email_imap_port", "int"),
        "JARVIS_EMAIL_IMAP_SSL": ("integrations", "email_imap_ssl", "bool"),
        "JARVIS_EMAIL_USERNAME": ("integrations", "email_username", "text"),
        "JARVIS_EMAIL_FROM": ("integrations", "email_from", "text"),
        "JARVIS_EMAIL_PASSWORD": ("integrations", "email_password", "optional"),
        "JARVIS_CALENDAR_PROVIDER": ("integrations", "calendar_provider", "text"),
        "JARVIS_CALENDAR_URL": ("integrations", "calendar_url", "text"),
        "JARVIS_CALENDAR_USERNAME": ("integrations", "calendar_username", "text"),
        "JARVIS_CALENDAR_PASSWORD": ("integrations", "calendar_password", "optional"),
        "JARVIS_PLUGINS_ENABLED": ("plugins", "enabled", "bool"),
        "JARVIS_PLUGINS_AUTO_LOAD": ("plugins", "auto_load", "bool"),
        "JARVIS_PLUGINS_STATE_PATH": ("plugins", "state_path", "text"),
        "JARVIS_GUI_THEME": ("gui", "theme", "text"),
        "JARVIS_GUI_MINIMIZE_TO_TRAY": ("gui", "minimize_to_tray", "bool"),
        "JARVIS_GUI_SHOW_DEBUG_LOGS": ("gui", "show_debug_logs", "bool"),
    }
    result: dict[str, dict[str, Any]] = {}
    parsers = {
        "text": lambda value, label: _text(value, label),
        "optional": _optional_text,
        "bool": _parse_bool,
        "float": _parse_float,
        "int": _parse_int,
    }
    for variable, (section, key, parser) in specs.items():
        if variable in environment:
            result.setdefault(section, {})[key] = parsers[parser](environment[variable], variable)
    return result


def _normalize_toml(data: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    normalized: dict[str, dict[str, Any]] = {}
    for raw_section, values in data.items():
        section = _SECTION_ALIASES.get(raw_section, raw_section)
        if section not in _SECTION_TYPES:
            raise ConfigError(f"Unknown configuration section: {raw_section}")
        if not isinstance(values, Mapping):
            raise ConfigError(f"Configuration section {raw_section!r} must be a table")
        if section in normalized:
            raise ConfigError(f"Configuration section {section!r} was specified more than once")
        aliases = _KEY_ALIASES.get(section, {})
        section_values: dict[str, Any] = {}
        valid = {config_field.name for config_field in fields(_SECTION_TYPES[section])}
        for raw_key, value in values.items():
            key = aliases.get(raw_key, raw_key)
            if key not in valid:
                raise ConfigError(f"Unknown configuration setting: {raw_section}.{raw_key}")
            if key in section_values:
                raise ConfigError(f"Configuration setting {section}.{key} was specified twice")
            section_values[key] = value
        normalized[section] = section_values
    return normalized


def _build_config(values: Mapping[str, Mapping[str, Any]], *, base: Path) -> JarvisConfig:
    sections: dict[str, Any] = {}
    for section, config_type in _SECTION_TYPES.items():
        options = dict(values.get(section, {}))
        if section == "database" and "path" in options:
            options["path"] = _path(options["path"], "database.path", base=base)
        if section == "logging" and options.get("file") is not None:
            options["file"] = _path(options["file"], "logging.file", base=base)
        if section == "screenshots" and "directory" in options:
            options["directory"] = _path(options["directory"], "screenshots.directory", base=base)
        if section == "scheduler" and "database_path" in options:
            options["database_path"] = _path(
                options["database_path"], "scheduler.database_path", base=base
            )
        if section == "plugins" and "state_path" in options:
            options["state_path"] = _path(options["state_path"], "plugins.state_path", base=base)
        try:
            sections[section] = config_type(**options)
        except TypeError as exc:
            raise ConfigError(f"Invalid {section} configuration: {exc}") from exc
    return JarvisConfig(**sections)


def load_config(
    path: str | os.PathLike[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> JarvisConfig:
    """Load TOML configuration and apply ``JARVIS_*`` environment overrides.

    When no path is given, ``JARVIS_CONFIG_FILE`` is honored.  Otherwise an
    existing ``~/.jarvis/config.toml`` is loaded when present.  An explicitly
    selected missing file is an error; an absent default file simply yields
    validated defaults.
    """

    environment = os.environ if env is None else env
    if env is None:
        load_env_file_from_default_locations()
    configured_path = path if path is not None else environment.get("JARVIS_CONFIG_FILE")
    explicit = configured_path is not None
    config_path = (
        _path(configured_path, "configuration path")
        if configured_path is not None
        else (Path.home() / ".jarvis" / "config.toml").resolve(strict=False)
    )
    data: Mapping[str, Any] = {}
    if config_path.exists():
        if not config_path.is_file():
            raise ConfigError(f"Configuration path is not a file: {config_path}")
        try:
            with config_path.open("rb") as stream:
                parsed = tomllib.load(stream)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"Unable to read configuration {config_path}: {exc}") from exc
        data = parsed
    elif explicit:
        raise FileNotFoundError(config_path)

    merged = _normalize_toml(data)
    for section, overrides in _environment_overrides(environment).items():
        merged.setdefault(section, {}).update(overrides)
    base = config_path.parent if config_path.exists() else Path.cwd()
    return _build_config(merged, base=base)
