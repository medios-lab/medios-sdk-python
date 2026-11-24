"""Durable Store implementation backed by stdlib ``sqlite3`` (WAL mode)."""

from __future__ import annotations

import sqlite3
import threading

from .store import StoreNotFoundError

_SCHEMA = """
CREATE TABLE IF NOT EXISTS singletons (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS blobs (
    bucket TEXT NOT NULL,
    key    TEXT NOT NULL,
    value  TEXT NOT NULL,
    PRIMARY KEY (bucket, key)
);
"""


class SqliteStore:
    """SQLite-backed :class:`~medios_sdk.store.store.Store`.

    ``path`` may be a file path or ``":memory:"`` for an ephemeral DB. WAL is
    enabled for many-reader/one-writer concurrency on file-backed databases.
    """

    def __init__(self, path: str) -> None:
        self._lock = threading.RLock()
        self._closed = False
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def put_singleton(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO singletons (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            self._conn.commit()

    def get_singleton(self, key: str) -> str:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM singletons WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            raise StoreNotFoundError(f"store: singleton {key!r} not found")
        return row[0]

    def delete_singleton(self, key: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM singletons WHERE key = ?", (key,))
            self._conn.commit()

    def put_blob(self, bucket: str, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO blobs (bucket, key, value) VALUES (?, ?, ?) "
                "ON CONFLICT(bucket, key) DO UPDATE SET value = excluded.value",
                (bucket, key, value),
            )
            self._conn.commit()

    def get_blob(self, bucket: str, key: str) -> str:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM blobs WHERE bucket = ? AND key = ?", (bucket, key)
            ).fetchone()
        if row is None:
            raise StoreNotFoundError(f"store: blob ({bucket!r}, {key!r}) not found")
        return row[0]

    def delete_blob(self, bucket: str, key: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM blobs WHERE bucket = ? AND key = ?", (bucket, key)
            )
            self._conn.commit()

    def list_blob(self, bucket: str) -> dict[str, str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT key, value FROM blobs WHERE bucket = ?", (bucket,)
            ).fetchall()
        return {k: v for k, v in rows}

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._conn.close()
                self._closed = True
