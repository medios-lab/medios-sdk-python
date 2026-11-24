"""A narrow persistence contract (singletons + blob buckets) with in-memory and
SQLite implementations."""

from .memory import MemoryStore
from .sqlite import SqliteStore
from .store import Store, StoreNotFoundError

__all__ = ["Store", "StoreNotFoundError", "MemoryStore", "SqliteStore"]
