"""Store contract suite, run against both impls — mirrors store/storetest/contract.go."""

import pytest

from medios_sdk.store import MemoryStore, SqliteStore, StoreNotFoundError


@pytest.fixture(params=["memory", "sqlite"])
def store(request, tmp_path):
    if request.param == "memory":
        s = MemoryStore()
    else:
        s = SqliteStore(str(tmp_path / "store.db"))
    yield s
    s.close()


# ── Singleton ─────────────────────────────────────────────────────────────


def test_singleton_get_empty(store):
    with pytest.raises(StoreNotFoundError):
        store.get_singleton("framework_id")


def test_singleton_delete_empty_is_noop(store):
    store.delete_singleton("framework_id")  # no raise


def test_singleton_put_then_get(store):
    want = "9e8d0946-6f25-411a-abeb-e168bbd225b0-0001"
    store.put_singleton("framework_id", want)
    assert store.get_singleton("framework_id") == want


def test_singleton_put_delete_get(store):
    store.put_singleton("framework_id", "abc")
    store.delete_singleton("framework_id")
    with pytest.raises(StoreNotFoundError):
        store.get_singleton("framework_id")


def test_singleton_put_overwrites(store):
    store.put_singleton("k", "v1")
    store.put_singleton("k", "v2")
    assert store.get_singleton("k") == "v2"


# ── Blob bucket ───────────────────────────────────────────────────────────


def test_blob_get_empty(store):
    with pytest.raises(StoreNotFoundError):
        store.get_blob("apps", "missing")


def test_blob_list_empty(store):
    assert store.list_blob("apps") == {}


def test_blob_put_then_get(store):
    store.put_blob("apps", "/foo", '{"id":"/foo"}')
    assert store.get_blob("apps", "/foo") == '{"id":"/foo"}'


def test_blob_put_overwrites(store):
    store.put_blob("apps", "/foo", "v1")
    store.put_blob("apps", "/foo", "v2")
    assert store.get_blob("apps", "/foo") == "v2"


def test_blob_delete_idempotent(store):
    store.delete_blob("apps", "missing")
    store.put_blob("apps", "/foo", "v1")
    store.delete_blob("apps", "/foo")
    store.delete_blob("apps", "/foo")
    with pytest.raises(StoreNotFoundError):
        store.get_blob("apps", "/foo")


def test_blob_list_populated(store):
    store.put_blob("apps", "/a", "1")
    store.put_blob("apps", "/b", "2")
    store.put_blob("apps", "/c", "3")
    assert store.list_blob("apps") == {"/a": "1", "/b": "2", "/c": "3"}
    store.delete_blob("apps", "/b")
    assert store.list_blob("apps") == {"/a": "1", "/c": "3"}


def test_blob_buckets_are_isolated(store):
    store.put_blob("apps", "x", "1")
    store.put_blob("tasks", "x", "2")
    assert store.get_blob("apps", "x") == "1"
    assert store.get_blob("tasks", "x") == "2"


# ── Lifecycle ─────────────────────────────────────────────────────────────


def test_close_is_idempotent(store):
    store.close()
    store.close()  # second close must be a no-op


def test_sqlite_persists_across_reopen(tmp_path):
    path = str(tmp_path / "persist.db")
    s1 = SqliteStore(path)
    s1.put_singleton("framework_id", "fw-xyz")
    s1.close()
    s2 = SqliteStore(path)
    try:
        assert s2.get_singleton("framework_id") == "fw-xyz"
    finally:
        s2.close()
