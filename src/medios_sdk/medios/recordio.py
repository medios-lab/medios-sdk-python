"""RecordIO codec for the framed SUBSCRIBE response stream.

Framing per the Medios HTTP v1 scheduler API::

    records      = *record
    record       = record-size LF record-data
    record-size  = 1*DIGIT     (ASCII decimal)
    record-data  = record-size bytes (a JSON object for this client)

Records sit back-to-back with no separator; the size prefix is the only framing.
Parsing stays plain bytes — pydantic is for the JSON inside each frame, not the
framing itself.
"""

from __future__ import annotations

from typing import BinaryIO, Callable, Iterator


class RecordIOError(Exception):
    """A framing error: a non-numeric size line or a truncated payload."""


def _trim_newline(line: bytes) -> bytes:
    """Strip a trailing LF and a stray CR (a proxy may inject ``\\r`` before
    ``\\n``)."""
    n = len(line)
    while n > 0 and line[n - 1] in (0x0A, 0x0D):
        n -= 1
    return line[:n]


def _read_full(reader: BinaryIO, size: int) -> bytes:
    """Read exactly ``size`` bytes, or fewer at EOF."""
    buf = bytearray()
    while len(buf) < size:
        chunk = reader.read(size - len(buf))
        if not chunk:
            break
        buf.extend(chunk)
    return bytes(buf)


def iter_records(reader: BinaryIO) -> Iterator[bytes]:
    """Yield each RecordIO record from ``reader`` as raw bytes.

    ``reader`` is any binary file-like object with ``readline()`` and ``read(n)``
    (an :class:`io.BytesIO`, or an ``http.client.HTTPResponse`` for the live
    stream). EOF at a record boundary ends iteration cleanly; EOF mid-payload or
    a non-numeric size line raises :class:`RecordIOError`.
    """
    while True:
        line = reader.readline()
        if line == b"":
            return  # clean end at a record boundary
        size_field = _trim_newline(line)
        if not size_field or not size_field.isdigit():
            raise RecordIOError(f"recordio: invalid size line {line!r}")
        size = int(size_field)
        payload = _read_full(reader, size)
        if len(payload) < size:
            raise RecordIOError(
                f"recordio: read {len(payload)}-byte payload, want {size}"
            )
        yield payload


def parse_recordio(reader: BinaryIO, on_record: Callable[[bytes], None]) -> None:
    """Read RecordIO records from ``reader`` and hand each to ``on_record``.

    The callback may raise to stop reading early; the exception propagates to the
    caller (mirroring the Go parser, where the yield callback returns an error to
    stop). This is the callback form; :func:`iter_records` is the generator form.
    """
    for record in iter_records(reader):
        on_record(record)
