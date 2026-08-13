"""Configurable safety policy for explicit JARVIS actions."""

from __future__ import annotations

import copy
import inspect
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, TypeAlias

from jarvis.skills.base import RiskLevel

if TYPE_CHECKING:
    from jarvis.core.actions import Action


class PermissionPolicy(StrEnum):
    """Configured behavior for a risk category."""

    ASK = "ask"
    ALLOW = "allow"
    DENY = "deny"


class PermissionDecision(StrEnum):
    """Final decision after applying policy and optional confirmation."""

    ALLOW = "allow"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class PermissionRequest:
    """Context shown to an injected confirmation user interface."""

    risk_level: RiskLevel
    action_name: str
    summary: str
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.risk_level, RiskLevel):
            object.__setattr__(self, "risk_level", _risk(self.risk_level))
        if not isinstance(self.action_name, str) or not self.action_name.strip():
            raise ValueError("permission action name cannot be empty")
        if not isinstance(self.summary, str) or not self.summary.strip():
            raise ValueError("permission summary cannot be empty")
        if not isinstance(self.details, Mapping):
            raise TypeError("permission details must be a mapping")
        object.__setattr__(self, "action_name", self.action_name.strip())
        object.__setattr__(self, "summary", self.summary.strip())
        object.__setattr__(self, "details", MappingProxyType(copy.deepcopy(dict(self.details))))


@dataclass(frozen=True, slots=True)
class PermissionResult:
    """A non-throwing permission outcome safe for orchestration code."""

    allowed: bool
    decision: PermissionDecision
    policy: PermissionPolicy
    risk_level: RiskLevel
    reason: str
    prompted: bool = False

    def __bool__(self) -> bool:
        return self.allowed


Confirmer: TypeAlias = Callable[[PermissionRequest], bool | Awaitable[bool]]


def _risk(value: RiskLevel | str) -> RiskLevel:
    if isinstance(value, RiskLevel):
        return value
    try:
        return RiskLevel(str(value).strip().upper())
    except ValueError as exc:
        raise ValueError(f"Unknown risk level: {value!r}") from exc


def _policy(value: PermissionPolicy | str) -> PermissionPolicy:
    if isinstance(value, PermissionPolicy):
        return value
    try:
        return PermissionPolicy(str(value).strip().lower())
    except ValueError as exc:
        raise ValueError(f"Unknown permission policy: {value!r}") from exc


DEFAULT_POLICIES: Mapping[RiskLevel, PermissionPolicy] = MappingProxyType(
    {
        RiskLevel.READ: PermissionPolicy.ALLOW,
        RiskLevel.ACTION: PermissionPolicy.ASK,
        RiskLevel.SENSITIVE: PermissionPolicy.ASK,
        RiskLevel.DESTRUCTIVE: PermissionPolicy.ASK,
    }
)

# Generic keyboard input can produce effects far beyond the currently focused
# application.  Configuration may make normal ACTION tools automatic, but these
# tools retain a non-overridable confirmation floor.
ALWAYS_CONFIRM_ACTIONS = frozenset(
    {
        "type_text",
        "press_key",
        "press_hotkey",
        "browser_click",
        "browser_type",
        "browser_press_key",
        "github_create_issue",
        "email_send_message",
        "calendar_create_event",
        "calendar_update_event",
        "cancel_reminder",
        "edit_reminder",
        "plugin_enable",
        "plugin_inspect",
    }
)


class PermissionManager:
    """Apply per-risk policies using an optional async confirmation callback.

    A destructive policy configured as ``allow`` is deliberately treated as
    ``ask``.  Without a confirmer, every request that needs confirmation is denied.
    """

    def __init__(
        self,
        policies: Mapping[RiskLevel | str, PermissionPolicy | str] | None = None,
        *,
        confirmer: Confirmer | None = None,
    ) -> None:
        normalized = dict(DEFAULT_POLICIES)
        if policies is not None:
            for risk_level, policy in policies.items():
                normalized[_risk(risk_level)] = _policy(policy)
        self._policies = MappingProxyType(normalized)
        self._confirmer = confirmer

    @property
    def policies(self) -> Mapping[RiskLevel, PermissionPolicy]:
        """Return the immutable configured policy mapping."""

        return self._policies

    @property
    def confirmer(self) -> Confirmer | None:
        """Return the configured interface callback for composition validation."""

        return self._confirmer

    def policy_for(self, risk_level: RiskLevel | str) -> PermissionPolicy:
        """Return the configured policy for a category."""

        return self._policies[_risk(risk_level)]

    async def check(
        self,
        subject: Action | RiskLevel | str,
        *,
        action_name: str | None = None,
        summary: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> PermissionResult:
        """Resolve permission for an action object or explicit risk category."""

        if hasattr(subject, "risk_level") and hasattr(subject, "name"):
            risk_level = _risk(subject.risk_level)
            resolved_name = str(subject.name)
            resolved_summary = summary or str(getattr(subject, "description", resolved_name))
        else:
            risk_level = _risk(subject)
            resolved_name = action_name or risk_level.value.lower()
            resolved_summary = summary or resolved_name

        configured = self.policy_for(risk_level)
        effective = configured
        if (
            risk_level is RiskLevel.DESTRUCTIVE or resolved_name in ALWAYS_CONFIRM_ACTIONS
        ) and configured is PermissionPolicy.ALLOW:
            effective = PermissionPolicy.ASK

        if effective is PermissionPolicy.ALLOW:
            return PermissionResult(
                True,
                PermissionDecision.ALLOW,
                configured,
                risk_level,
                "Allowed by permission policy.",
            )
        if effective is PermissionPolicy.DENY:
            return PermissionResult(
                False,
                PermissionDecision.DENY,
                configured,
                risk_level,
                "Denied by permission policy.",
            )
        if self._confirmer is None:
            return PermissionResult(
                False,
                PermissionDecision.DENY,
                configured,
                risk_level,
                "Confirmation is required, but no confirmer is available.",
            )

        request = PermissionRequest(
            risk_level,
            resolved_name,
            resolved_summary,
            {} if details is None else details,
        )
        try:
            answer = self._confirmer(request)
            if inspect.isawaitable(answer):
                answer = await answer
            allowed = answer is True
        except Exception:
            allowed = False
        reason = "Approved by the user." if allowed else "Not approved by the user."
        return PermissionResult(
            allowed,
            PermissionDecision.ALLOW if allowed else PermissionDecision.DENY,
            configured,
            risk_level,
            reason,
            prompted=True,
        )

    async def authorize(
        self,
        subject: Action | RiskLevel | str,
        **context: Any,
    ) -> PermissionResult:
        """Readable alias for :meth:`check`; denial is returned, not raised."""

        return await self.check(subject, **context)
