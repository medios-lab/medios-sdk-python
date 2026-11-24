"""In-process Store implementation. Not durable — for tests and as the contract
reference."""

from __future__ import annotations

import threading

from .store import StoreNotFoundError


class MemoryStore:
    """Thread-safe in-memory :class:`~medios_sdk.store.store.Store`."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._singletons: dict[str, str] = {}
        self._blobs: dict[str, dict[str, str]] = {}

    def put_singleton(self, key: str, value: str) -> None:
        with self._lock:
            self._singletons[key] = value

    def get_singleton(self, key: str) -> str:
        with self._lock:
            try:
                return self._singletons[key]
            except KeyError:
                raise StoreNotFoundError(f"store: singleton {key!r} not found") from None

    def delete_singleton(self, key: str) -> None:
        with self._lock:
            self._singletons.pop(key, None)

    def put_blob(self, bucket: str, key: str, value: str) -> None:
        with self._lock:
            self._blobs.setdefault(bucket, {})[key] = value

    def get_blob(self, bucket: str, key: str) -> str:
        with self._lock:
            try:
                return self._blobs[bucket][key]
            except KeyError:
                raise StoreNotFoundError(
                    f"store: blob ({bucket!r}, {key!r}) not found"
                ) from None

    def delete_blob(self, bucket: str, key: str) -> None:
        with self._lock:
            self._blobs.get(bucket, {}).pop(key, None)

    def list_blob(self, bucket: str) -> dict[str, str]:
        with self._lock:
            return dict(self._blobs.get(bucket, {}))

    def close(self) -> None:
        pass
