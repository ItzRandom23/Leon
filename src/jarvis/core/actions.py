"""Explicit, validated actions exposed to JARVIS reasoning providers.

The registry in this module is deliberately small.  It is not a general purpose
dependency-injection framework: registered handlers are ordinary callables and the
only values accepted at the model boundary are JSON-compatible action arguments.
"""

from __future__ import annotations

import copy
import inspect
import math
import re
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any, TypeAlias
from uuid import uuid4

from jarvis.skills.base import RiskLevel

JSONValue: TypeAlias = None | bool | int | float | str | list["JSONValue"] | dict[str, "JSONValue"]
ActionHandler: TypeAlias = Callable[..., Any | Awaitable[Any]]

_ACTION_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")
_PARAMETER_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_UNSET = object()
_PYTHON_TO_JSON_TYPE: dict[type[Any], str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
    type(None): "null",
}
_JSON_TYPES = frozenset(_PYTHON_TO_JSON_TYPE.values())


class ActionError(Exception):
    """Base class for action-system errors."""


class DuplicateActionError(ActionError, ValueError):
    """Raised when a registry already contains an action name."""


class ActionNotFoundError(ActionError, KeyError):
    """Raised when an action name is not registered."""


class ActionValidationError(ActionError, ValueError):
    """Raised when action arguments do not conform to their declared schema."""

    def __init__(self, action: str, errors: Iterable[str]) -> None:
        self.action = action
        self.errors = tuple(errors)
        detail = "; ".join(self.errors) or "invalid arguments"
        super().__init__(f"Invalid arguments for action {action!r}: {detail}")


def _normalize_json_type(value: str | type[Any]) -> str:
    if isinstance(value, type):
        try:
            return _PYTHON_TO_JSON_TYPE[value]
        except KeyError as exc:
            raise ValueError(f"Unsupported parameter type: {value!r}") from exc
    if not isinstance(value, str):
        raise TypeError("parameter type must be a JSON type name or Python type")
    normalized = value.strip().lower()
    if normalized not in _JSON_TYPES:
        supported = ", ".join(sorted(_JSON_TYPES))
        raise ValueError(f"Unsupported parameter type {value!r}; expected one of {supported}")
    return normalized


def _normalize_risk_level(value: RiskLevel | str) -> RiskLevel:
    if isinstance(value, RiskLevel):
        return value
    try:
        return RiskLevel(str(value).strip().upper())
    except ValueError as exc:
        raise ValueError(f"Unknown risk level: {value!r}") from exc


def _is_json_value(value: Any) -> bool:
    if value is None or isinstance(value, (str, bool)):
        return True
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_json_value(item) for key, item in value.items())
    return False


def _json_snapshot(value: Any) -> JSONValue:
    """Return an owned JSON value, accepting tuples only as internal sequences."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TypeError("action result data must contain only finite numbers")
        return value
    if isinstance(value, (list, tuple)):
        return [_json_snapshot(item) for item in value]
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("action result object keys must be strings")
        return {key: _json_snapshot(item) for key, item in value.items()}
    raise TypeError("action result data must be JSON-compatible")


def _matches_type(value: Any, schema_type: str) -> bool:
    if schema_type == "null":
        return value is None
    if schema_type == "boolean":
        return isinstance(value, bool)
    if schema_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if schema_type == "number":
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and (not isinstance(value, float) or math.isfinite(value))
        )
    if schema_type == "string":
        return isinstance(value, str)
    if schema_type == "array":
        return isinstance(value, list)
    if schema_type == "object":
        return isinstance(value, dict) and all(isinstance(key, str) for key in value)
    return False


@dataclass(frozen=True, slots=True)
class ActionParameter:
    """One argument in an action's JSON-schema-like interface.

    ``type`` accepts a JSON type name or its obvious Python counterpart (for
    example, both ``"integer"`` and ``int`` are accepted).  Values are checked
    strictly: booleans are not integers and tuples are not JSON arrays.
    """

    name: str
    type: str | type[Any]
    description: str = ""
    required: bool = True
    enum: tuple[Any, ...] = ()
    minimum: int | float | None = None
    maximum: int | float | None = None
    min_length: int | None = None
    max_length: int | None = None
    pattern: str | None = None
    items: Mapping[str, Any] | None = None
    default: Any = field(default=_UNSET, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _PARAMETER_NAME.fullmatch(self.name):
            raise ValueError(f"Invalid action parameter name: {self.name!r}")
        object.__setattr__(self, "type", _normalize_json_type(self.type))
        if not isinstance(self.description, str):
            raise TypeError("parameter description must be a string")
        if not isinstance(self.required, bool):
            raise TypeError("parameter required must be a boolean")

        enum = tuple(self.enum)
        if any(not _is_json_value(value) for value in enum):
            raise TypeError("parameter enum values must be JSON-compatible")
        object.__setattr__(self, "enum", enum)

        if self.minimum is not None or self.maximum is not None:
            if self.type not in {"integer", "number"}:
                raise ValueError("minimum and maximum are only valid for numeric parameters")
            for label, value in (("minimum", self.minimum), ("maximum", self.maximum)):
                if value is None:
                    continue
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    raise TypeError(f"{label} must be numeric")
                if isinstance(value, float) and not math.isfinite(value):
                    raise ValueError(f"{label} must be finite")
            if self.minimum is not None and self.maximum is not None:
                if self.minimum > self.maximum:
                    raise ValueError("minimum cannot be greater than maximum")

        if self.min_length is not None or self.max_length is not None:
            if self.type not in {"string", "array", "object"}:
                raise ValueError("length constraints require a string, array, or object parameter")
            for label, value in (("min_length", self.min_length), ("max_length", self.max_length)):
                if value is None:
                    continue
                if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                    raise ValueError(f"{label} must be a non-negative integer")
            if self.min_length is not None and self.max_length is not None:
                if self.min_length > self.max_length:
                    raise ValueError("min_length cannot be greater than max_length")

        if self.pattern is not None:
            if self.type != "string":
                raise ValueError("pattern is only valid for string parameters")
            try:
                re.compile(self.pattern)
            except re.error as exc:
                raise ValueError(f"Invalid parameter pattern: {exc}") from exc

        if self.items is not None:
            if self.type != "array":
                raise ValueError("items is only valid for array parameters")
            if not isinstance(self.items, Mapping):
                raise TypeError("items must be a schema mapping")
            item_schema = dict(self.items)
            unsupported = set(item_schema) - {"type"}
            if unsupported:
                raise ValueError(
                    "array item schemas currently support only 'type'; unsupported: "
                    + ", ".join(sorted(unsupported))
                )
            if "type" not in item_schema:
                raise ValueError("array item schemas require a 'type'")
            item_schema["type"] = _normalize_json_type(item_schema["type"])
            object.__setattr__(self, "items", MappingProxyType(item_schema))

        if self.default is not _UNSET:
            if not _is_json_value(self.default):
                raise TypeError("parameter default must be JSON-compatible")
            errors = self.validate(self.default)
            if errors:
                raise ValueError(
                    f"Invalid default for parameter {self.name!r}: {'; '.join(errors)}"
                )
            object.__setattr__(self, "default", copy.deepcopy(self.default))

    @property
    def schema_type(self) -> str:
        """Return the normalized JSON type name."""

        return str(self.type)

    @property
    def has_default(self) -> bool:
        """Whether this parameter declares a default value."""

        return self.default is not _UNSET

    def validate(self, value: Any) -> tuple[str, ...]:
        """Return all validation errors for *value*."""

        errors: list[str] = []
        schema_type = self.schema_type
        if not _matches_type(value, schema_type):
            return (f"{self.name!r} must be of type {schema_type}",)

        if self.enum and not any(
            type(value) is type(choice) and value == choice for choice in self.enum
        ):
            errors.append(f"{self.name!r} must be one of {list(self.enum)!r}")
        if self.minimum is not None and value < self.minimum:
            errors.append(f"{self.name!r} must be at least {self.minimum}")
        if self.maximum is not None and value > self.maximum:
            errors.append(f"{self.name!r} must be at most {self.maximum}")
        if self.min_length is not None and len(value) < self.min_length:
            errors.append(f"{self.name!r} must contain at least {self.min_length} items")
        if self.max_length is not None and len(value) > self.max_length:
            errors.append(f"{self.name!r} must contain at most {self.max_length} items")
        if self.pattern is not None and re.search(self.pattern, value) is None:
            errors.append(f"{self.name!r} does not match the required pattern")

        if self.items is not None and isinstance(value, list):
            item_type = self.items.get("type")
            if item_type is not None:
                for index, item in enumerate(value):
                    if not _matches_type(item, str(item_type)):
                        errors.append(f"{self.name!r}[{index}] must be of type {item_type}")
        return tuple(errors)

    def to_schema(self) -> dict[str, Any]:
        """Return a fresh JSON Schema property dictionary."""

        schema: dict[str, Any] = {"type": self.schema_type}
        if self.description:
            schema["description"] = self.description
        if self.enum:
            schema["enum"] = list(self.enum)
        if self.minimum is not None:
            schema["minimum"] = self.minimum
        if self.maximum is not None:
            schema["maximum"] = self.maximum
        length_prefix = {
            "string": ("minLength", "maxLength"),
            "array": ("minItems", "maxItems"),
            "object": ("minProperties", "maxProperties"),
        }.get(self.schema_type)
        if length_prefix is not None:
            if self.min_length is not None:
                schema[length_prefix[0]] = self.min_length
            if self.max_length is not None:
                schema[length_prefix[1]] = self.max_length
        if self.pattern is not None:
            schema["pattern"] = self.pattern
        if self.items is not None:
            schema["items"] = dict(self.items)
        if self.has_default:
            schema["default"] = self.default
        return schema


@dataclass(frozen=True, slots=True)
class Action:
    """An immutable named capability and its trusted implementation."""

    name: str
    description: str
    handler: ActionHandler = field(repr=False, compare=False)
    parameters: tuple[ActionParameter, ...] = ()
    risk_level: RiskLevel = RiskLevel.READ

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _ACTION_NAME.fullmatch(self.name):
            raise ValueError(f"Invalid action name: {self.name!r}")
        if not isinstance(self.description, str) or not self.description.strip():
            raise ValueError("action description cannot be empty")
        if not callable(self.handler):
            raise TypeError("action handler must be callable")
        parameters = tuple(self.parameters)
        if not all(isinstance(parameter, ActionParameter) for parameter in parameters):
            raise TypeError("action parameters must be ActionParameter instances")
        names = [parameter.name for parameter in parameters]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"Duplicate parameter names: {', '.join(duplicates)}")
        object.__setattr__(self, "description", self.description.strip())
        object.__setattr__(self, "parameters", parameters)
        object.__setattr__(self, "risk_level", _normalize_risk_level(self.risk_level))

    @property
    def permission(self) -> RiskLevel:
        """Compatibility alias for the action's risk level."""

        return self.risk_level

    def validate_arguments(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """Validate and copy arguments, applying declared defaults.

        All failures are reported together in :class:`ActionValidationError` so an
        LLM provider can correct a malformed tool call in one pass.
        """

        if not isinstance(arguments, Mapping):
            raise ActionValidationError(self.name, ("arguments must be an object",))
        if not all(isinstance(name, str) for name in arguments):
            raise ActionValidationError(self.name, ("argument names must be strings",))

        declared = {parameter.name: parameter for parameter in self.parameters}
        errors: list[str] = []
        unknown = sorted(set(arguments) - set(declared))
        if unknown:
            errors.append(f"unknown arguments: {', '.join(unknown)}")

        validated: dict[str, Any] = {}
        for parameter in self.parameters:
            if parameter.name not in arguments:
                if parameter.required and not parameter.has_default:
                    errors.append(f"missing required argument: {parameter.name}")
                elif parameter.has_default:
                    validated[parameter.name] = copy.deepcopy(parameter.default)
                continue
            value = arguments[parameter.name]
            if not _is_json_value(value):
                errors.append(f"{parameter.name!r} must be JSON-compatible")
                continue
            errors.extend(parameter.validate(value))
            validated[parameter.name] = copy.deepcopy(value)

        if errors:
            raise ActionValidationError(self.name, errors)
        return validated

    def parameter_schema(self) -> dict[str, Any]:
        """Return this action's strict JSON object schema."""

        return {
            "type": "object",
            "properties": {parameter.name: parameter.to_schema() for parameter in self.parameters},
            "required": [parameter.name for parameter in self.parameters if parameter.required],
            "additionalProperties": False,
        }

    def tool_schema(self) -> dict[str, Any]:
        """Return the provider-neutral, flat function-tool schema."""

        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": self.parameter_schema(),
            "strict": True,
        }

    async def __call__(self, **arguments: Any) -> Any:
        """Validate and invoke the underlying handler directly."""

        result = self.handler(**self.validate_arguments(arguments))
        if inspect.isawaitable(result):
            return await result
        return result


@dataclass(frozen=True, slots=True, init=False)
class ActionRequest:
    """One request to invoke a named action with structured arguments."""

    name: str
    arguments: Mapping[str, Any]
    request_id: str

    def __init__(
        self,
        name: str | None = None,
        arguments: Mapping[str, Any] | None = None,
        request_id: str | None = None,
        *,
        action: str | None = None,
    ) -> None:
        if name is not None and action is not None and name != action:
            raise ValueError("name and action aliases must match")
        resolved_name = name if name is not None else action
        if not isinstance(resolved_name, str) or not resolved_name.strip():
            raise ValueError("action request name cannot be empty")
        values = {} if arguments is None else arguments
        if not isinstance(values, Mapping):
            raise TypeError("action request arguments must be a mapping")
        if not all(isinstance(key, str) for key in values):
            raise TypeError("action request argument names must be strings")
        resolved_id = uuid4().hex if request_id is None else request_id
        if not isinstance(resolved_id, str) or not resolved_id.strip():
            raise ValueError("action request id cannot be empty")
        object.__setattr__(self, "name", resolved_name.strip())
        snapshot = copy.deepcopy(dict(values))
        object.__setattr__(self, "arguments", MappingProxyType(snapshot))
        object.__setattr__(self, "request_id", resolved_id.strip())

    @property
    def action(self) -> str:
        """Compatibility alias for ``name``."""

        return self.name

    @property
    def action_name(self) -> str:
        """Compatibility alias for ``name``."""

        return self.name


@dataclass(frozen=True, slots=True)
class ActionResult:
    """The safe, structured outcome of an action invocation."""

    action: str = ""
    success: bool = True
    message: str = ""
    data: Any = None
    error: str | None = None
    error_code: str | None = None
    request_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.action, str):
            raise TypeError("result action must be a string")
        if not isinstance(self.success, bool):
            raise TypeError("result success must be a boolean")
        if not isinstance(self.message, str):
            raise TypeError("result message must be a string")
        if self.success and self.error is not None:
            raise ValueError("a successful result cannot contain an error")
        if not self.success and not self.error:
            raise ValueError("an unsuccessful result must contain an error")
        object.__setattr__(self, "data", _json_snapshot(self.data))

    @property
    def name(self) -> str:
        """Compatibility alias for ``action``."""

        return self.action

    @classmethod
    def succeeded(
        cls,
        action: str,
        *,
        message: str = "",
        data: Any = None,
        request_id: str | None = None,
    ) -> ActionResult:
        """Build a successful result."""

        return cls(action=action, message=message, data=data, request_id=request_id)

    @classmethod
    def failed(
        cls,
        action: str,
        error: str,
        *,
        message: str = "",
        error_code: str | None = None,
        request_id: str | None = None,
    ) -> ActionResult:
        """Build a failed result without exposing an exception object."""

        return cls(
            action=action,
            success=False,
            message=message,
            error=error,
            error_code=error_code,
            request_id=request_id,
        )


def action(
    *,
    name: str,
    description: str,
    parameters: Iterable[ActionParameter] = (),
    risk_level: RiskLevel | str = RiskLevel.READ,
    permission: RiskLevel | str | None = None,
) -> Callable[[ActionHandler], Action]:
    """Decorate a handler as an immutable :class:`Action`.

    ``permission`` is accepted as a readable alias for ``risk_level``.  Supplying
    both with conflicting values is rejected.
    """

    chosen_risk = _normalize_risk_level(risk_level)
    if permission is not None:
        permission_risk = _normalize_risk_level(permission)
        if chosen_risk != RiskLevel.READ and chosen_risk != permission_risk:
            raise ValueError("risk_level and permission must match")
        chosen_risk = permission_risk
    declared_parameters = tuple(parameters)

    def decorate(handler: ActionHandler) -> Action:
        return Action(name, description, handler, declared_parameters, chosen_risk)

    return decorate


class ActionRegistry:
    """An ordered collection of unique, explicitly exposed actions."""

    def __init__(self, actions: Iterable[Action] = ()) -> None:
        self._actions: dict[str, Action] = {}
        for registered_action in actions:
            self.register(registered_action)

    def __len__(self) -> int:
        return len(self._actions)

    def __contains__(self, name: object) -> bool:
        return name in self._actions

    @property
    def actions(self) -> tuple[Action, ...]:
        """Return registered actions in registration order."""

        return tuple(self._actions.values())

    @property
    def names(self) -> tuple[str, ...]:
        """Return registered action names in registration order."""

        return tuple(self._actions)

    @property
    def schemas(self) -> tuple[dict[str, Any], ...]:
        """Property alias for :meth:`tool_schemas`."""

        return self.tool_schemas()

    def register(self, registered_action: Action) -> Action:
        """Register an action, rejecting duplicate names."""

        if not isinstance(registered_action, Action):
            raise TypeError("register expects an Action instance")
        if registered_action.name in self._actions:
            raise DuplicateActionError(
                f"An action named {registered_action.name!r} is already registered"
            )
        self._actions[registered_action.name] = registered_action
        return registered_action

    def unregister(self, name: str) -> Action:
        """Remove and return one action by exact name.

        This narrow API supports atomic plugin rollback without exposing the
        registry's mutable storage to extension managers.
        """

        try:
            return self._actions.pop(name)
        except KeyError as exc:
            raise ActionNotFoundError(f"No action named {name!r} is registered") from exc

    def action(
        self,
        *,
        name: str,
        description: str,
        parameters: Iterable[ActionParameter] = (),
        risk_level: RiskLevel | str = RiskLevel.READ,
        permission: RiskLevel | str | None = None,
    ) -> Callable[[ActionHandler], Action]:
        """Decorate and immediately register an action handler."""

        decorate = action(
            name=name,
            description=description,
            parameters=parameters,
            risk_level=risk_level,
            permission=permission,
        )

        def register_handler(handler: ActionHandler) -> Action:
            return self.register(decorate(handler))

        return register_handler

    def get(self, name: str) -> Action:
        """Return an action by name or raise :class:`ActionNotFoundError`."""

        try:
            return self._actions[name]
        except KeyError as exc:
            raise ActionNotFoundError(f"No action named {name!r} is registered") from exc

    def tool_schemas(self) -> tuple[dict[str, Any], ...]:
        """Return fresh provider-neutral flat function schemas."""

        return tuple(
            registered_action.tool_schema() for registered_action in self._actions.values()
        )

    def validate(self, request: ActionRequest) -> dict[str, Any]:
        """Validate a request and return a new arguments dictionary."""

        if not isinstance(request, ActionRequest):
            raise TypeError("validate expects an ActionRequest")
        return self.get(request.name).validate_arguments(request.arguments)

    async def invoke(
        self,
        request: ActionRequest | str,
        arguments: Mapping[str, Any] | None = None,
        *,
        raise_errors: bool = False,
    ) -> ActionResult:
        """Validate and invoke one action.

        By default, unknown actions, invalid tool calls, and handler exceptions are
        converted to failed results suitable for user-facing orchestration.  Tests
        and trusted internal callers can request the original exception with
        ``raise_errors=True``.
        """

        if isinstance(request, str):
            request = ActionRequest(request, arguments)
        elif arguments is not None:
            raise TypeError("arguments cannot be supplied with an ActionRequest")
        elif not isinstance(request, ActionRequest):
            raise TypeError("invoke expects an ActionRequest or action name")

        try:
            registered_action = self.get(request.name)
            validated = registered_action.validate_arguments(request.arguments)
        except (ActionNotFoundError, ActionValidationError) as exc:
            if raise_errors:
                raise
            code = (
                "action_not_found" if isinstance(exc, ActionNotFoundError) else "invalid_arguments"
            )
            return ActionResult.failed(
                request.name,
                str(exc),
                message="I couldn't perform that action because the request was invalid.",
                error_code=code,
                request_id=request.request_id,
            )

        try:
            value = registered_action.handler(**validated)
            if inspect.isawaitable(value):
                value = await value
        except Exception:
            if raise_errors:
                raise
            return ActionResult.failed(
                request.name,
                "The action could not be completed.",
                message="I couldn't complete that action.",
                error_code="execution_failed",
                request_id=request.request_id,
            )

        try:
            if isinstance(value, ActionResult):
                updates: dict[str, Any] = {}
                if not value.action:
                    updates["action"] = request.name
                if value.request_id is None:
                    updates["request_id"] = request.request_id
                return replace(value, **updates) if updates else value
            if isinstance(value, str):
                return ActionResult.succeeded(
                    request.name,
                    message=value,
                    data=value,
                    request_id=request.request_id,
                )
            return ActionResult.succeeded(
                request.name,
                data=value,
                request_id=request.request_id,
            )
        except (TypeError, ValueError):
            if raise_errors:
                raise
            return ActionResult.failed(
                request.name,
                "The action returned data that cannot cross the tool boundary.",
                message="I couldn't safely use that action result.",
                error_code="invalid_result",
                request_id=request.request_id,
            )

    async def invoke_many(
        self,
        requests: Iterable[ActionRequest],
        *,
        stop_on_failure: bool = True,
        raise_errors: bool = False,
    ) -> tuple[ActionResult, ...]:
        """Invoke requests sequentially, stopping after the first failure by default."""

        results: list[ActionResult] = []
        for request in requests:
            result = await self.invoke(request, raise_errors=raise_errors)
            results.append(result)
            if stop_on_failure and not result.success:
                break
        return tuple(results)

    async def invoke_sequence(
        self,
        requests: Iterable[ActionRequest],
        *,
        stop_on_failure: bool = True,
        raise_errors: bool = False,
    ) -> tuple[ActionResult, ...]:
        """Readable alias for :meth:`invoke_many`."""

        return await self.invoke_many(
            requests,
            stop_on_failure=stop_on_failure,
            raise_errors=raise_errors,
        )
