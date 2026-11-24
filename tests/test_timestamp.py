"""Timestamp tests, mirroring timestamp/timestamp_test.go's intent."""

from datetime import datetime, timezone

from medios_sdk.timestamp import Timestamp


def test_from_millis_round_trip():
    ts = Timestamp.from_millis(1700000000500)
    assert ts.millis() == 1700000000500


def test_from_seconds_truncates_fraction():
    # Controller timestamps arrive in seconds; fractional seconds truncate.
    ts = Timestamp.from_seconds(1700000000.5)
    assert ts.millis() == 1700000000000


def test_normalizes_to_utc():
    # Two instants for the same moment in different zones compare equal.
    a = Timestamp(datetime(2023, 1, 1, 12, 0, 0, tzinfo=timezone.utc))
    naive_same = datetime(2023, 1, 1, 12, 0, 0)  # treated as UTC
    b = Timestamp(naive_same)
    assert a.equal(b)
    assert a == b


def test_ordering():
    early = Timestamp.from_millis(1000)
    late = Timestamp.from_millis(2000)
    assert early.before(late)
    assert late.after(early)
    assert early.compare(late) == -1
    assert late.compare(early) == 1
    assert early.compare(early) == 0


def test_string_is_iso8601_millis():
    ts = Timestamp.from_millis(1700000000500)
    s = str(ts)
    assert s.endswith("Z")
    assert ".500Z" in s
    assert Timestamp.parse(s) == ts


def test_zero():
    assert Timestamp.zero().is_zero()
    assert Timestamp.zero().millis() == 0
    assert not Timestamp.from_millis(1).is_zero()
