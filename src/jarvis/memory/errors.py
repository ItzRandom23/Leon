"""Exceptions raised by JARVIS persistent memory."""

from __future__ import annotations


class MemoryStoreError(Exception):
    """Base class for errors from the persistent-memory subsystem."""


class MemoryValidationError(MemoryStoreError):
    """Raised when a memory category, key, value, or query is invalid."""


class MemoryRepositoryError(MemoryStoreError):
    """Raised when the backing memory repository cannot complete an operation."""


class MemorySchemaError(MemoryRepositoryError):
    """Raised when the database schema is incompatible with this JARVIS version."""


class MemoryClosedError(MemoryRepositoryError):
    """Raised when a closed memory repository is used."""
