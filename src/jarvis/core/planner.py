"""Deterministic natural-language plans for offline and fallback operation."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class PlannedAction:
    """One validated-by-the-registry action request from deterministic routing."""

    name: str
    arguments: Mapping[str, Any]


_OPEN_AND_TYPE = re.compile(
    r"^(?:please\s+)?(?:open|launch|start)\s+(?P<application>.+?)\s+and\s+"
    r"(?:then\s+)?type\s+(?P<text>.+?)[.!?]*$",
    re.IGNORECASE,
)
_OPEN = re.compile(
    r"^(?:please\s+)?(?:open|launch|start)\s+(?P<application>.+?)(?:\s+please)?[.!?]*$",
    re.IGNORECASE,
)
_CLOSE = re.compile(r"^(?:please\s+)?close\s+(?P<application>.+?)[.!?]*$", re.IGNORECASE)
_FIND_APP = re.compile(r"^(?:find|is)\s+(?P<application>.+?)(?:\s+running)?[?!.]*$", re.IGNORECASE)
_REMEMBER = re.compile(
    r"^remember(?:\s+that)?\s+(?P<key>.+?)\s+(?:is|=)\s+(?P<value>.+?)[.!?]*$",
    re.IGNORECASE,
)
_RECALL = re.compile(r"^(?:what|where)\s+(?:is|are)\s+(?P<key>my\s+.+?)[?!.]*$", re.IGNORECASE)
_FORGET = re.compile(r"^forget\s+(?P<key>.+?)[.!?]*$", re.IGNORECASE)
_MOVE = re.compile(
    r"^(?:move\s+(?:the\s+)?(?:mouse|cursor)(?:\s+to)?|mouse\s+move)\s+"
    r"(?P<x>\d{1,6})\s*[, ]\s*(?P<y>\d{1,6})[.!?]*$",
    re.IGNORECASE,
)
_CLICK = re.compile(
    r"^(?P<kind>click|double[ -]?click|right[ -]?click)"
    r"(?:\s+(?:at\s+)?)?(?:(?P<x>\d{1,6})\s*[, ]\s*(?P<y>\d{1,6}))?[.!?]*$",
    re.IGNORECASE,
)
_SCROLL = re.compile(
    r"^scroll(?:\s+(?P<direction>up|down))?(?:\s+(?P<amount>\d{1,5}))?[.!?]*$",
    re.IGNORECASE,
)
_TYPE = re.compile(r"^(?:type|write)\s+(?P<text>.+?)[.!?]*$", re.IGNORECASE)
_PRESS = re.compile(r"^press\s+(?P<keys>.+?)[.!?]*$", re.IGNORECASE)
_FOCUS = re.compile(
    r"^(?:focus|switch\s+to)\s+(?:the\s+)?(?:window\s+)?(?P<title>.+?)[.!?]*$",
    re.IGNORECASE,
)
_BROWSER_URL = re.compile(
    r"^(?:open|navigate|go)(?:\s+the)?(?:\s+browser)?\s+to\s+"
    r"(?P<url>https?://\S+?)[.!?]*$",
    re.IGNORECASE,
)
_WEB_SEARCH = re.compile(
    r"^(?:search(?:\s+the)?\s+web|web\s+search)(?:\s+for)?\s+(?P<query>.+?)[.!?]*$",
    re.IGNORECASE,
)
_RELATIVE_REMINDER = re.compile(
    r"^remind\s+me\s+in\s+(?P<amount>\d{1,6})\s+"
    r"(?P<unit>minutes?|hours?|days?)\s+(?:to|about)\s+(?P<message>.+?)[.!?]*$",
    re.IGNORECASE,
)
_WEEKLY_REMINDER = re.compile(
    r"^(?:every|each)\s+"
    r"(?P<weekday>monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s+"
    r"at\s+(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<period>am|pm)?\s+"
    r"remind\s+me\s+(?:to|about)\s+(?P<message>.+?)[.!?]*$",
    re.IGNORECASE,
)


class DeterministicPlanner:
    """Recognize a deliberately bounded set of useful natural requests.

    This is not presented as general language understanding. An enabled LLM can
    produce plans from the same registered action schemas; this planner keeps the
    product useful offline and makes critical behavior deterministic and testable.
    """

    def plan(self, command: str) -> tuple[PlannedAction, ...] | None:
        """Return a bounded sequential plan, or ``None`` when unrecognized."""

        text = " ".join(command.strip().split())
        if not text:
            return None
        normalized = text.casefold().rstrip(".!?")

        if normalized in {"open browser", "start browser", "launch browser"}:
            return (PlannedAction("browser_start", {}),)
        if normalized in {"close browser", "stop browser"}:
            return (PlannedAction("browser_close", {}),)
        if match := _BROWSER_URL.fullmatch(text):
            return (PlannedAction("browser_navigate", {"url": match.group("url")}),)
        if match := _WEB_SEARCH.fullmatch(text):
            return (
                PlannedAction(
                    "browser_search_web",
                    {"query": _clean_phrase(match.group("query"))},
                ),
            )
        if match := _RELATIVE_REMINDER.fullmatch(text):
            amount = int(match.group("amount"))
            factor = {"minute": 1, "hour": 60, "day": 1_440}[
                match.group("unit").casefold().rstrip("s")
            ]
            return (
                PlannedAction(
                    "create_relative_reminder",
                    {
                        "message": _clean_phrase(match.group("message")),
                        "delay_minutes": amount * factor,
                    },
                ),
            )
        if match := _WEEKLY_REMINDER.fullmatch(text):
            return (
                PlannedAction(
                    "create_weekly_reminder",
                    {
                        "message": _clean_phrase(match.group("message")),
                        "weekday": match.group("weekday").casefold(),
                        "at": _natural_time(
                            int(match.group("hour")),
                            int(match.group("minute") or "0"),
                            match.group("period"),
                        ),
                    },
                ),
            )

        if match := _OPEN_AND_TYPE.fullmatch(text):
            return (
                PlannedAction(
                    "open_application",
                    {"application": _clean_phrase(match.group("application"))},
                ),
                PlannedAction("type_text", {"text": _unquote(match.group("text"))}),
            )
        if match := _OPEN.fullmatch(text):
            return (
                PlannedAction(
                    "open_application",
                    {"application": _clean_phrase(match.group("application"))},
                ),
            )
        if match := _CLOSE.fullmatch(text):
            return (
                PlannedAction(
                    "close_application",
                    {"application": _clean_phrase(match.group("application"))},
                ),
            )
        if normalized in {"list running applications", "show running applications"}:
            return (PlannedAction("list_running_applications", {}),)
        if match := _FIND_APP.fullmatch(text):
            return (
                PlannedAction(
                    "find_running_application",
                    {"application": _clean_phrase(match.group("application"))},
                ),
            )

        system_action = _system_action(normalized)
        if system_action:
            return (PlannedAction(system_action, {}),)

        if match := _REMEMBER.fullmatch(text):
            key = _clean_phrase(match.group("key"))
            return (
                PlannedAction(
                    "remember",
                    {
                        "category": _memory_category(key),
                        "key": key,
                        "value": _unquote(match.group("value")),
                    },
                ),
            )
        if normalized in {"show memories", "list memories", "what do you remember"}:
            return (PlannedAction("list_memories", {}),)
        if normalized in {"clear memories", "forget everything"}:
            return (PlannedAction("clear_memories", {}),)
        if match := _FORGET.fullmatch(text):
            key = _clean_phrase(match.group("key"))
            return (
                PlannedAction(
                    "forget_memory",
                    {"category": _memory_category(key), "key": key},
                ),
            )
        if match := _RECALL.fullmatch(text):
            key = _clean_phrase(match.group("key"))
            return (
                PlannedAction(
                    "recall_memory",
                    {"category": _memory_category(key), "key": key},
                ),
            )

        if normalized in {"take a screenshot", "take screenshot", "capture screen"}:
            return (PlannedAction("take_screenshot", {}),)
        if normalized in {"capture active window", "screenshot active window"}:
            return (PlannedAction("capture_active_window", {}),)
        if _is_screen_analysis(normalized):
            return (PlannedAction("analyze_screen", {"prompt": text}),)

        if match := _MOVE.fullmatch(text):
            return (
                PlannedAction(
                    "move_mouse",
                    {"x": int(match.group("x")), "y": int(match.group("y"))},
                ),
            )
        if match := _CLICK.fullmatch(text):
            arguments: dict[str, Any] = {}
            if match.group("x"):
                arguments.update(x=int(match.group("x")), y=int(match.group("y")))
            action_name = {
                "click": "click_mouse",
                "doubleclick": "double_click_mouse",
                "rightclick": "right_click_mouse",
            }[match.group("kind").casefold().replace("-", "").replace(" ", "")]
            return (PlannedAction(action_name, arguments),)
        if match := _SCROLL.fullmatch(text):
            amount = int(match.group("amount") or "3")
            clicks = amount if match.group("direction") == "up" else -amount
            return (PlannedAction("scroll_mouse", {"clicks": clicks}),)
        if match := _TYPE.fullmatch(text):
            return (PlannedAction("type_text", {"text": _unquote(match.group("text"))}),)
        if match := _PRESS.fullmatch(text):
            keys = _parse_keys(match.group("keys"))
            if len(keys) == 1:
                return (PlannedAction("press_key", {"key": keys[0]}),)
            return (PlannedAction("press_hotkey", {"keys": keys}),)

        if normalized in {"active window", "current window", "what window is active"}:
            return (PlannedAction("get_active_window", {}),)
        if normalized in {"list windows", "show windows", "list visible windows"}:
            return (PlannedAction("list_visible_windows", {}),)
        if match := _FOCUS.fullmatch(text):
            return (PlannedAction("focus_window", {"title": _unquote(match.group("title"))}),)
        return None


def _system_action(normalized: str) -> str | None:
    if normalized in {"cpu", "cpu usage", "what's my cpu usage", "what is my cpu usage"}:
        return "get_cpu_usage"
    if normalized in {
        "ram",
        "ram usage",
        "memory usage",
        "what's my ram usage",
        "what is my ram usage",
    }:
        return "get_memory_usage"
    if normalized in {"storage", "storage usage", "disk usage", "show storage"}:
        return "get_storage_usage"
    if normalized in {"battery", "battery status", "battery level"}:
        return "get_battery_status"
    if normalized in {"uptime", "system uptime"}:
        return "get_uptime"
    if normalized in {"operating system", "what operating system am i using", "os info"}:
        return "get_operating_system"
    if normalized in {
        "running processes",
        "list running processes",
        "what is using my ram",
        "why is my ram usage high",
        "top memory processes",
    }:
        return "get_top_processes"
    if normalized in {"network info", "network information", "show network"}:
        return "get_network_information"
    if normalized in {"system info", "system information", "system status"}:
        return "get_system_information"
    return None


def _memory_category(key: str) -> str:
    normalized = key.casefold()
    if any(word in normalized for word in ("folder", "directory", "path", "alias")):
        return "aliases"
    if any(word in normalized for word in ("project", "repository", "repo")):
        return "projects"
    if any(word in normalized for word in ("prefer", "preference", "favorite", "favourite")):
        return "preferences"
    return "facts"


def _is_screen_analysis(normalized: str) -> bool:
    phrases = (
        "what's on my screen",
        "what's currently on my screen",
        "what is on my screen",
        "what is currently on my screen",
        "what error is visible",
        "explain this dialog",
        "where is the save button",
        "what application is open",
        "read the visible text",
        "look at my screen",
    )
    return any(phrase in normalized for phrase in phrases)


def _parse_keys(value: str) -> list[str]:
    normalized = re.sub(r"\s*\+\s*", " ", value.casefold())
    normalized = normalized.replace("control", "ctrl")
    return [key for key in normalized.split() if key not in {"and", "then"}]


def _clean_phrase(value: str) -> str:
    return _unquote(value.strip().rstrip(".!?"))


def _unquote(value: str) -> str:
    stripped = value.strip().rstrip(".!?")
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {'"', "'"}:
        return stripped[1:-1]
    return stripped


def _natural_time(hour: int, minute: int, period: str | None) -> str:
    if not 0 <= minute <= 59:
        return "invalid"
    if period is not None:
        if not 1 <= hour <= 12:
            return "invalid"
        hour = hour % 12 + (12 if period.casefold() == "pm" else 0)
    elif not 0 <= hour <= 23:
        return "invalid"
    return f"{hour:02d}:{minute:02d}"
