"""Portable, read-only system telemetry behind a small psutil boundary."""

from __future__ import annotations

import platform
import socket
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import psutil


@dataclass(frozen=True, slots=True)
class CpuInformation:
    percent: float
    logical_count: int | None
    physical_count: int | None


@dataclass(frozen=True, slots=True)
class MemoryInformation:
    total_bytes: int
    available_bytes: int
    used_bytes: int
    percent: float


@dataclass(frozen=True, slots=True)
class DiskInformation:
    device: str
    mountpoint: str
    filesystem: str
    total_bytes: int
    used_bytes: int
    free_bytes: int
    percent: float


@dataclass(frozen=True, slots=True)
class BatteryInformation:
    percent: float
    plugged_in: bool
    seconds_remaining: int | None


@dataclass(frozen=True, slots=True)
class ProcessInformation:
    pid: int
    name: str
    memory_bytes: int
    memory_percent: float


@dataclass(frozen=True, slots=True)
class NetworkInterfaceInformation:
    name: str
    is_up: bool
    speed_mbps: int
    addresses: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OperatingSystemInformation:
    system: str
    release: str
    version: str
    machine: str


@dataclass(frozen=True, slots=True)
class SystemInformation:
    cpu: CpuInformation
    memory: MemoryInformation
    disks: tuple[DiskInformation, ...]
    battery: BatteryInformation | None
    uptime_seconds: int
    operating_system: OperatingSystemInformation
    top_processes: tuple[ProcessInformation, ...]
    network_interfaces: tuple[NetworkInterfaceInformation, ...]


class _Memory(Protocol):
    total: int
    available: int
    used: int
    percent: float


class _DiskPartition(Protocol):
    device: str
    mountpoint: str
    fstype: str


class _DiskUsage(Protocol):
    total: int
    used: int
    free: int
    percent: float


class _Battery(Protocol):
    percent: float
    power_plugged: bool
    secsleft: int


class _Address(Protocol):
    family: Any
    address: str


class _InterfaceStats(Protocol):
    isup: bool
    speed: int


class _Process(Protocol):
    info: Mapping[str, Any]


class PsutilApi(Protocol):
    """The psutil surface used by :class:`SystemInfoProvider`."""

    def cpu_percent(self, interval: float | None = None) -> float: ...

    def cpu_count(self, logical: bool = True) -> int | None: ...

    def virtual_memory(self) -> _Memory: ...

    def disk_partitions(self, all: bool = False) -> Iterable[_DiskPartition]: ...

    def disk_usage(self, path: str) -> _DiskUsage: ...

    def sensors_battery(self) -> _Battery | None: ...

    def boot_time(self) -> float: ...

    def process_iter(self, attrs: Sequence[str]) -> Iterable[_Process]: ...

    def net_if_addrs(self) -> Mapping[str, Sequence[_Address]]: ...

    def net_if_stats(self) -> Mapping[str, _InterfaceStats]: ...


class SystemInfoProvider:
    """Collect deterministic local telemetry without external network access."""

    def __init__(
        self,
        *,
        psutil_api: PsutilApi = psutil,
        clock: Any = time.time,
        platform_module: Any = platform,
    ) -> None:
        self._psutil = psutil_api
        self._clock = clock
        self._platform = platform_module

    def collect(self, *, top_process_limit: int = 5) -> SystemInformation:
        """Collect all supported metrics in one immutable snapshot."""

        if isinstance(top_process_limit, bool) or not isinstance(top_process_limit, int):
            raise ValueError("top_process_limit must be an integer")
        if not 0 <= top_process_limit <= 100:
            raise ValueError("top_process_limit must be between 0 and 100")
        return SystemInformation(
            cpu=self.cpu(),
            memory=self.memory(),
            disks=self.disks(),
            battery=self.battery(),
            uptime_seconds=self.uptime(),
            operating_system=self.operating_system(),
            top_processes=self.top_processes(limit=top_process_limit),
            network_interfaces=self.network_interfaces(),
        )

    def cpu(self) -> CpuInformation:
        return CpuInformation(
            percent=float(self._psutil.cpu_percent(interval=0.1)),
            logical_count=self._psutil.cpu_count(logical=True),
            physical_count=self._psutil.cpu_count(logical=False),
        )

    def memory(self) -> MemoryInformation:
        memory = self._psutil.virtual_memory()
        return MemoryInformation(
            total_bytes=int(memory.total),
            available_bytes=int(memory.available),
            used_bytes=int(memory.used),
            percent=float(memory.percent),
        )

    def disks(self) -> tuple[DiskInformation, ...]:
        disks: list[DiskInformation] = []
        for partition in self._psutil.disk_partitions(all=False):
            try:
                usage = self._psutil.disk_usage(partition.mountpoint)
            except (OSError, PermissionError, psutil.Error):
                continue
            disks.append(
                DiskInformation(
                    device=str(partition.device),
                    mountpoint=str(partition.mountpoint),
                    filesystem=str(partition.fstype),
                    total_bytes=int(usage.total),
                    used_bytes=int(usage.used),
                    free_bytes=int(usage.free),
                    percent=float(usage.percent),
                )
            )
        return tuple(disks)

    def battery(self) -> BatteryInformation | None:
        try:
            battery = self._psutil.sensors_battery()
        except (AttributeError, OSError, psutil.Error):
            return None
        if battery is None:
            return None
        unknown_values = {
            getattr(psutil, "POWER_TIME_UNKNOWN", -1),
            getattr(psutil, "POWER_TIME_UNLIMITED", -2),
        }
        seconds = int(battery.secsleft)
        return BatteryInformation(
            percent=float(battery.percent),
            plugged_in=bool(battery.power_plugged),
            seconds_remaining=None if seconds in unknown_values or seconds < 0 else seconds,
        )

    def uptime(self) -> int:
        return max(0, round(float(self._clock()) - float(self._psutil.boot_time())))

    def operating_system(self) -> OperatingSystemInformation:
        return OperatingSystemInformation(
            system=str(self._platform.system() or "Unknown"),
            release=str(self._platform.release() or "Unknown"),
            version=str(self._platform.version() or "Unknown"),
            machine=str(self._platform.machine() or "Unknown"),
        )

    def top_processes(self, *, limit: int = 5) -> tuple[ProcessInformation, ...]:
        """Return the highest resident-memory processes, with no command lines."""

        if isinstance(limit, bool) or not isinstance(limit, int) or not 0 <= limit <= 100:
            raise ValueError("limit must be an integer between 0 and 100")
        processes: list[ProcessInformation] = []
        try:
            iterator = self._psutil.process_iter(("pid", "name", "memory_info", "memory_percent"))
            for process in iterator:
                try:
                    info = process.info
                    memory_info = info.get("memory_info")
                    rss = int(getattr(memory_info, "rss", 0))
                    processes.append(
                        ProcessInformation(
                            pid=int(info.get("pid", 0)),
                            name=str(info.get("name") or "Unknown"),
                            memory_bytes=max(0, rss),
                            memory_percent=max(0.0, float(info.get("memory_percent") or 0.0)),
                        )
                    )
                except (TypeError, ValueError, AttributeError, psutil.Error):
                    continue
        except (OSError, psutil.Error):
            return ()
        processes.sort(key=lambda item: (item.memory_bytes, item.pid), reverse=True)
        return tuple(processes[:limit])

    def network_interfaces(self) -> tuple[NetworkInterfaceInformation, ...]:
        """Summarize local interfaces without inspecting traffic or connections."""

        try:
            addresses = self._psutil.net_if_addrs()
            statistics = self._psutil.net_if_stats()
        except (OSError, psutil.Error):
            return ()
        result: list[NetworkInterfaceInformation] = []
        permitted_families = {socket.AF_INET, socket.AF_INET6}
        for name in sorted(set(addresses) | set(statistics)):
            stats = statistics.get(name)
            safe_addresses = tuple(
                str(address.address).split("%", maxsplit=1)[0]
                for address in addresses.get(name, ())
                if address.family in permitted_families and address.address
            )
            result.append(
                NetworkInterfaceInformation(
                    name=name,
                    is_up=bool(stats.isup) if stats else False,
                    speed_mbps=max(0, int(stats.speed)) if stats else 0,
                    addresses=safe_addresses,
                )
            )
        return tuple(result)
