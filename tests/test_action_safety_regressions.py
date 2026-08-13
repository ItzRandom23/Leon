"""Regression tests for action-boundary validation and ownership."""

from __future__ import annotations

import pytest

from jarvis.core.actions import (
    ActionParameter,
    ActionRegistry,
    ActionRequest,
    ActionValidationError,
)


def test_action_parameter_accepts_independent_numeric_and_length_bounds() -> None:
    minimum_only = ActionParameter("count", int, minimum=1)
    maximum_only = ActionParameter("count", int, maximum=3)
    minimum_length_only = ActionParameter("text", str, min_length=1)
    maximum_length_only = ActionParameter("text", str, max_length=3)

    assert minimum_only.validate(1) == ()
    assert minimum_only.validate(0)
    assert maximum_only.validate(3) == ()
    assert maximum_only.validate(4)
    assert minimum_length_only.validate("x") == ()
    assert minimum_length_only.validate("")
    assert maximum_length_only.validate("abc") == ()
    assert maximum_length_only.validate("abcd")


def test_action_request_and_validation_take_independent_deep_copies() -> None:
    registry = ActionRegistry()

    @registry.action(
        name="consume_payload",
        description="Consume a nested JSON payload",
        parameters=(ActionParameter("payload", dict),),
    )
    def consume_payload(payload: dict[str, object]) -> dict[str, object]:
        return payload

    original = {"payload": {"items": [{"name": "original"}]}}
    request = ActionRequest("consume_payload", original)
    original["payload"]["items"][0]["name"] = "mutated"  # type: ignore[index]

    assert request.arguments["payload"] == {"items": [{"name": "original"}]}

    validated = registry.validate(request)
    validated["payload"]["items"][0]["name"] = "validated mutation"
    assert request.arguments["payload"] == {"items": [{"name": "original"}]}


@pytest.mark.parametrize(
    "payload",
    [
        {"nested": {"not-json"}},
        {"nested": float("nan")},
        {"nested": float("inf")},
        {1: "non-string key"},
    ],
)
def test_action_validation_rejects_non_json_nested_values(payload: object) -> None:
    registry = ActionRegistry()

    @registry.action(
        name="consume_payload",
        description="Consume a nested JSON payload",
        parameters=(ActionParameter("payload", dict),),
    )
    def consume_payload(payload: dict[str, object]) -> None:
        raise AssertionError(f"unsafe payload reached handler: {payload!r}")

    with pytest.raises(ActionValidationError, match="JSON-compatible"):
        registry.validate(ActionRequest("consume_payload", {"payload": payload}))
