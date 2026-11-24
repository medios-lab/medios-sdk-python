"""The persistence contract: small named singletons + namespaced blob buckets.

The interface is intentionally narrow. It does NOT expose generic KV or SQL
semantics; framework-specific typed accessors live in each framework's wrapper
over this Store, never on the contract itself (that would bake one framework's
object model into the SDK every other framework imports). The scheduler client
uses it for one thing: persisting ``framework_id`` so a restarted framework can
re-subscribe under the same identity (failover).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


class StoreNotFoundError(Exception):
    """Raised by ``get_singleton`` / ``get_blob`` when no value is stored for
    the requested key."""


@runtime_checkable
class Store(Protocol):
    """The persistence contract. Implementations: :mod:`medios_sdk.store.memory`
    (in-process, for tests) and :mod:`medios_sdk.store.sqlite` (durable)."""

    def put_singleton(self, key: str, value: str) -> None:
        """Insert or replace the value for ``key``."""
        ...

    def get_singleton(self, key: str) -> str:
        """Return the value for ``key``; raise :class:`StoreNotFoundError` if
        unset."""
        ...

    def delete_singleton(self, key: str) -> None:
        """Remove the value for ``key``. Idempotent (deleting an absent key is
        not an error)."""
        ...

    def put_blob(self, bucket: str, key: str, value: str) -> None:
        """Insert or replace a value in ``bucket`` under ``key``."""
        ...

    def get_blob(self, bucket: str, key: str) -> str:
        """Return the value for ``(bucket, key)``; raise
        :class:`StoreNotFoundError` if absent."""
        ...

    def delete_blob(self, bucket: str, key: str) -> None:
        """Remove ``(bucket, key)``. Idempotent."""
        ...

    def list_blob(self, bucket: str) -> dict[str, str]:
        """Return all ``(key, value)`` entries in ``bucket`` — an empty dict for
        an unknown / empty bucket."""
        ...

    def close(self) -> None:
        """Release underlying resources. Idempotent."""
        ...
