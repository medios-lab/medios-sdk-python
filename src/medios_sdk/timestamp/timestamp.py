"""An ordered, UTC-normalized, millisecond-precision time wrapper.

Normalized to UTC and truncated to millisecond precision so two Timestamps for
the same instant compare equal regardless of source timezone and survive a JSON
round-trip. JSON form is ISO-8601 with milliseconds (``...T...:...:....000Z``).
"""

from __future__ import annotations

from datetime import datetime, timezone

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_FORMAT = "%Y-%m-%dT%H:%M:%S"


class Timestamp:
    """An immutable instant, normalized to UTC and floored to the millisecond.

    Compare with :meth:`equal` / :meth:`before` / :meth:`after` / :meth:`compare`
    (``==`` works too, since two equal instants are equal).
    """

    __slots__ = ("_t",)

    def __init__(self, t: datetime) -> None:
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        t = t.astimezone(timezone.utc)
        self._t = t.replace(microsecond=(t.microsecond // 1000) * 1000)

    @classmethod
    def from_millis(cls, ms: int) -> "Timestamp":
        sec, msec = divmod(ms, 1000)
        base = datetime.fromtimestamp(sec, tz=timezone.utc)
        return cls(base.replace(microsecond=msec * 1000))

    @classmethod
    def from_seconds(cls, s: float) -> "Timestamp":
        """A Timestamp at the given epoch-seconds offset. Fractional seconds are
        truncated; controller timestamps arrive in seconds."""
        return cls.from_millis(int(s) * 1000)

    @classmethod
    def now(cls) -> "Timestamp":
        return cls(datetime.now(timezone.utc))

    @classmethod
    def zero(cls) -> "Timestamp":
        return cls.from_millis(0)

    @classmethod
    def parse(cls, s: str) -> "Timestamp":
        """Parse an ISO-8601 / RFC-3339 datetime string."""
        text = s.replace("Z", "+00:00")
        return cls(datetime.fromisoformat(text))

    def millis(self) -> int:
        return int((self._t - _EPOCH).total_seconds() * 1000)

    def datetime(self) -> datetime:
        """The underlying ``datetime`` (always UTC)."""
        return self._t

    def compare(self, other: "Timestamp") -> int:
        if self._t < other._t:
            return -1
        if self._t > other._t:
            return 1
        return 0

    def before(self, other: "Timestamp") -> bool:
        return self._t < other._t

    def after(self, other: "Timestamp") -> bool:
        return self._t > other._t

    def equal(self, other: "Timestamp") -> bool:
        return self._t == other._t

    def is_zero(self) -> bool:
        return self._t == _EPOCH

    def __str__(self) -> str:
        return f"{self._t.strftime(_FORMAT)}.{self._t.microsecond // 1000:03d}Z"

    def __repr__(self) -> str:
        return f"Timestamp({self.__str__()!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Timestamp):
            return NotImplemented
        return self._t == other._t

    def __hash__(self) -> int:
        return hash(self._t)
