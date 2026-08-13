"""Credential resolution without config-file storage or accidental display."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from jarvis.integrations.errors import CredentialNotFoundError, IntegrationAuthError


@dataclass(frozen=True, slots=True, eq=False)
class SecretCredential:
    """An explicitly revealable secret whose string forms are always redacted."""

    _value: str = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self._value, str) or not self._value.strip():
            raise IntegrationAuthError("Credential value cannot be empty")
        if "\r" in self._value or "\n" in self._value:
            raise IntegrationAuthError("Credential value contains invalid characters")

    def reveal(self) -> str:
        """Return the value only at the authentication boundary."""

        return self._value

    def __repr__(self) -> str:
        return "SecretCredential([REDACTED])"

    def __str__(self) -> str:
        return "[REDACTED]"


class CredentialResolver(ABC):
    """Resolve named credentials supplied by the host environment."""

    @abstractmethod
    def resolve(self, credential_id: str) -> SecretCredential:
        """Return a secret or raise :class:`CredentialNotFoundError`."""


class StaticCredentialResolver(CredentialResolver):
    """Resolver for dependency injection and tests; values never appear in repr."""

    def __init__(self, credentials: Mapping[str, str | SecretCredential]) -> None:
        self._credentials = {
            _credential_id(name): (
                value if isinstance(value, SecretCredential) else SecretCredential(value)
            )
            for name, value in credentials.items()
        }

    def resolve(self, credential_id: str) -> SecretCredential:
        normalized = _credential_id(credential_id)
        try:
            return self._credentials[normalized]
        except KeyError:
            raise CredentialNotFoundError(normalized) from None

    def __repr__(self) -> str:
        return f"StaticCredentialResolver(credentials={tuple(sorted(self._credentials))!r})"


class EnvironmentCredentialResolver(CredentialResolver):
    """Read credentials from an explicit credential-id to environment mapping."""

    def __init__(
        self,
        bindings: Mapping[str, str],
        *,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._bindings = {
            _credential_id(name): _environment_name(variable) for name, variable in bindings.items()
        }
        self._environ = os.environ if environ is None else environ

    def resolve(self, credential_id: str) -> SecretCredential:
        normalized = _credential_id(credential_id)
        variable = self._bindings.get(normalized)
        if variable is None:
            raise CredentialNotFoundError(normalized)
        value = self._environ.get(variable)
        if value is None or not value.strip():
            raise CredentialNotFoundError(normalized)
        return SecretCredential(value)

    def __repr__(self) -> str:
        return f"EnvironmentCredentialResolver(bindings={self._bindings!r})"


class ChainedCredentialResolver(CredentialResolver):
    """Try resolvers in order without exposing their failures or values."""

    def __init__(self, resolvers: Sequence[CredentialResolver]) -> None:
        self._resolvers = tuple(resolvers)
        if not self._resolvers:
            raise ValueError("At least one credential resolver is required")
        if not all(isinstance(resolver, CredentialResolver) for resolver in self._resolvers):
            raise TypeError("resolvers must implement CredentialResolver")

    def resolve(self, credential_id: str) -> SecretCredential:
        normalized = _credential_id(credential_id)
        for resolver in self._resolvers:
            try:
                return resolver.resolve(normalized)
            except CredentialNotFoundError:
                continue
        raise CredentialNotFoundError(normalized)

    def __repr__(self) -> str:
        names = tuple(type(resolver).__name__ for resolver in self._resolvers)
        return f"ChainedCredentialResolver(resolvers={names!r})"


def _credential_id(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("credential_id must be text")
    normalized = value.strip()
    if not normalized or len(normalized) > 128 or any(ord(char) < 32 for char in normalized):
        raise ValueError("credential_id is invalid")
    return normalized


def _environment_name(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("environment variable name must be text")
    normalized = value.strip()
    if not normalized or not normalized.replace("_", "A").isalnum():
        raise ValueError("environment variable name is invalid")
    return normalized
