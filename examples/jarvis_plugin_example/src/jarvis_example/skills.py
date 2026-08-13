"""Actions contributed by the example plugin."""

from jarvis.core.actions import Action, ActionParameter
from jarvis.skills.base import RiskLevel


async def _greet(name: str) -> str:
    return f"Hello, {name}, from the JARVIS example plugin."


def greeting_action() -> Action:
    """Build the action during explicit plugin registration."""

    return Action(
        name="example_greeting",
        description="Return a local greeting from the example plugin.",
        handler=_greet,
        parameters=(
            ActionParameter(
                "name",
                "string",
                "Name to greet.",
                min_length=1,
                max_length=80,
            ),
        ),
        risk_level=RiskLevel.READ,
    )
