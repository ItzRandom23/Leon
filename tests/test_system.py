"""Tests for portable system-information collection and formatting."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from jarvis.skills.base import RiskLevel
from jarvis.skills.system import (
    SystemInfoProvider,
    SystemInformation,
    SystemInfoSkill,
    format_uptime,
)


@dataclass
class Usage:
    percent: float


class FakePsutil:
    def __init__(self, *, boot_time: float = 100.0) -> None:
        self._boot_time = boot_time
        self.cpu_intervals: list[float | None] = []
        self.disk_paths: list[str] = []

    def cpu_percent(self, interval: float | None = None) -> float:
        self.cpu_intervals.append(interval)
        return 12.25

    def virtual_memory(self) -> Usage:
        return Usage(45.5)

    def disk_usage(self, path: str) -> Usage:
        self.disk_paths.append(path)
        return Usage(67.75)

    def boot_time(self) -> float:
        return self._boot_time


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (-10, "0m"),
        (59, "0m"),
        (60, "1m"),
        (3_600, "1h 0m"),
        (90_061, "1d 1h 1m"),
    ],
)
def test_format_uptime(seconds: int, expected: str) -> None:
    assert format_uptime(seconds) == expected


def test_provider_collects_all_metrics_through_injected_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_psutil = FakePsutil(boot_time=100.0)
    monkeypatch.setattr("jarvis.skills.system.time.time", lambda: 190.4)
    provider = SystemInfoProvider(
        psutil_api=fake_psutil,
        platform_name="TestOS",
        disk_root="test-root",
    )

    result = provider.collect()

    assert result == SystemInformation(
        operating_system="TestOS",
        cpu_percent=12.25,
        memory_percent=45.5,
        disk_percent=67.75,
        uptime_seconds=90,
    )
    assert fake_psutil.cpu_intervals == [0.1]
    assert fake_psutil.disk_paths == ["test-root"]


def test_provider_clamps_clock_skew_to_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_psutil = FakePsutil(boot_time=200.0)
    monkeypatch.setattr("jarvis.skills.system.time.time", lambda: 100.0)

    assert (
        SystemInfoProvider(psutil_api=fake_psutil, platform_name="TestOS").collect().uptime_seconds
        == 0
    )


class StaticProvider:
    def collect(self) -> SystemInformation:
        return SystemInformation("TestOS", 1.25, 2.5, 3.75, 90_061)


class BrokenProvider:
    def collect(self) -> SystemInformation:
        raise OSError("unavailable")


def test_system_skill_formats_snapshot_and_preserves_structured_data() -> None:
    skill = SystemInfoSkill(provider=StaticProvider())  # type: ignore[arg-type]

    result = skill.execute("system info")

    assert result.success is True
    assert result.message.splitlines() == [
        "Operating System: TestOS",
        "CPU Usage: 1.2%",
        "Memory Usage: 2.5%",
        "Disk Usage: 3.8%",
        "Uptime: 1d 1h 1m",
    ]
    assert result.data["operating_system"] == "TestOS"
    assert result.data["uptime_seconds"] == 90_061
    assert skill.risk_level is RiskLevel.READ


def test_system_skill_handles_collection_failure() -> None:
    skill = SystemInfoSkill(provider=BrokenProvider())  # type: ignore[arg-type]

    result = skill.execute("system information")

    assert result.success is False
    assert "couldn't read" in result.message


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("SYSTEM   INFO!", True),
        ("computer information", True),
        ("system status", True),
        ("show system info", False),
    ],
)
def test_system_skill_command_matching(command: str, expected: bool) -> None:
    assert SystemInfoSkill(provider=StaticProvider()).can_handle(command) is expected  # type: ignore[arg-type]
