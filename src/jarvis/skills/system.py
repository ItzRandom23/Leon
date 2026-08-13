"""Cross-platform system information collection and presentation."""

from __future__ import annotations

import logging
import os
import platform
import time
from dataclasses import asdict, dataclass
from typing import Protocol

import psutil

from jarvis.skills.base import RiskLevel, Skill, SkillResult

logger = logging.getLogger(__name__)


class _Usage(Protocol):
    percent: float


class PsutilApi(Protocol):
    """The small part of psutil used by Phase 1."""

    def cpu_percent(self, interval: float | None = None) -> float: ...

    def virtual_memory(self) -> _Usage: ...

    def disk_usage(self, path: str) -> _Usage: ...

    def boot_time(self) -> float: ...


@dataclass(frozen=True, slots=True)
class SystemInformation:
    """A portable snapshot of basic machine resource information."""

    operating_system: str
    cpu_percent: float
    memory_percent: float
    disk_percent: float
    uptime_seconds: int


class SystemInfoProvider:
    """Collect a basic system snapshot behind a testable boundary."""

    def __init__(
        self,
        *,
        psutil_api: PsutilApi = psutil,
        platform_name: str | None = None,
        disk_root: str | None = None,
    ) -> None:
        self._psutil = psutil_api
        self._platform_name = platform_name
        self._disk_root = disk_root or os.path.abspath(os.sep)

    def collect(self) -> SystemInformation:
        """Read current utilization and uptime from the local machine."""

        uptime_seconds = max(0, round(time.time() - self._psutil.boot_time()))
        return SystemInformation(
            operating_system=self._platform_name or platform.system() or "Unknown",
            cpu_percent=float(self._psutil.cpu_percent(interval=0.1)),
            memory_percent=float(self._psutil.virtual_memory().percent),
            disk_percent=float(self._psutil.disk_usage(self._disk_root).percent),
            uptime_seconds=uptime_seconds,
        )


def format_uptime(seconds: int | float) -> str:
    """Render an uptime duration without unnecessary zero-valued units."""

    remaining = max(0, int(seconds))
    days, remaining = divmod(remaining, 86_400)
    hours, remaining = divmod(remaining, 3_600)
    minutes, _ = divmod(remaining, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


class SystemInfoSkill(Skill):
    """Show a read-only snapshot of basic system information."""

    name = "system_information"
    description = "Show operating system, resource usage, and uptime."
    risk_level = RiskLevel.READ
    _commands = frozenset(
        {
            "system info",
            "system information",
            "computer info",
            "computer information",
            "system status",
        }
    )

    def __init__(self, provider: SystemInfoProvider | None = None) -> None:
        self._provider = provider or SystemInfoProvider()

    def can_handle(self, command: str) -> bool:
        normalized = " ".join(command.casefold().strip().rstrip(".!?").split())
        return normalized in self._commands

    def execute(self, command: str) -> SkillResult:
        try:
            information = self._provider.collect()
        except (OSError, RuntimeError):
            logger.exception("system_information_collection_failed")
            return SkillResult("I couldn't read the system information.", success=False)

        return SkillResult(
            "\n".join(
                (
                    f"Operating System: {information.operating_system}",
                    f"CPU Usage: {information.cpu_percent:.1f}%",
                    f"Memory Usage: {information.memory_percent:.1f}%",
                    f"Disk Usage: {information.disk_percent:.1f}%",
                    f"Uptime: {format_uptime(information.uptime_seconds)}",
                )
            ),
            data=asdict(information),
        )
