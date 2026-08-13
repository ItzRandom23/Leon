"""Tests for bounded deterministic natural-language action planning."""

from __future__ import annotations

import pytest

from jarvis.core.planner import DeterministicPlanner, PlannedAction


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("Could you open calculator", None),
        ("open calculator", (PlannedAction("open_application", {"application": "calculator"}),)),
        (
            'Open Notepad and type "Hello world".',
            (
                PlannedAction("open_application", {"application": "Notepad"}),
                PlannedAction("type_text", {"text": "Hello world"}),
            ),
        ),
        ("close Notepad", (PlannedAction("close_application", {"application": "Notepad"}),)),
        ("what is using my RAM?", (PlannedAction("get_top_processes", {}),)),
        ("what's my CPU usage?", (PlannedAction("get_cpu_usage", {}),)),
        (
            r"remember that my development folder is D:\Projects",
            (
                PlannedAction(
                    "remember",
                    {
                        "category": "aliases",
                        "key": "my development folder",
                        "value": r"D:\Projects",
                    },
                ),
            ),
        ),
        (
            "what is my development folder?",
            (
                PlannedAction(
                    "recall_memory",
                    {"category": "aliases", "key": "my development folder"},
                ),
            ),
        ),
        ("take a screenshot", (PlannedAction("take_screenshot", {}),)),
        (
            "what's currently on my screen?",
            (PlannedAction("analyze_screen", {"prompt": "what's currently on my screen?"}),),
        ),
        (
            "move the cursor to 500, 300",
            (PlannedAction("move_mouse", {"x": 500, "y": 300}),),
        ),
        ("press Control S", (PlannedAction("press_hotkey", {"keys": ["ctrl", "s"]}),)),
        ('type "hello world"', (PlannedAction("type_text", {"text": "hello world"}),)),
        ("click the search box", None),
        ("run arbitrary command", None),
    ],
)
def test_plans_only_bounded_actions(
    command: str,
    expected: tuple[PlannedAction, ...] | None,
) -> None:
    assert DeterministicPlanner().plan(command) == expected


def test_blank_input_has_no_plan() -> None:
    assert DeterministicPlanner().plan("   ") is None
