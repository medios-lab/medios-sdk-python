"""RecordIO codec tests, mirroring medios/recordio_test.go."""

import io
import json

import pytest

from medios_sdk.medios import RecordIOError, iter_records, parse_recordio

SAMPLE_PAYLOADS = [
    '{"type":"SUBSCRIBED","subscribed":{"framework_id":{"value":"abc-0001"},"heartbeat_interval_seconds":15.0,"controller_info":{}}}',
    '{"type":"HEARTBEAT"}',
    '{"type":"OFFERS","offers":{"offers":[{"id":{"value":"O-1"},"hostname":"h"}]}}',
]


def sample_stream() -> bytes:
    # <size>\n<payload> for each, back-to-back, no separator (the size prefix is
    # the only framing).
    out = bytearray()
    for p in SAMPLE_PAYLOADS:
        b = p.encode()
        out += str(len(b)).encode() + b"\n" + b
    return bytes(out)


def test_happy_path():
    got = [r.decode() for r in iter_records(io.BytesIO(sample_stream()))]
    assert got == SAMPLE_PAYLOADS


def test_empty_stream_yields_nothing():
    got = list(iter_records(io.BytesIO(b"")))
    assert got == []


def test_invalid_size_line():
    with pytest.raises(RecordIOError):
        list(iter_records(io.BytesIO(b"notanumber\n{}")))


def test_truncated_payload():
    # Claims 50 bytes but delivers far fewer.
    with pytest.raises(RecordIOError):
        list(iter_records(io.BytesIO(b"50\nshort\n")))


def test_callback_error_stops():
    sentinel = RuntimeError("stop")
    count = 0

    def on_record(_):
        nonlocal count
        count += 1
        raise sentinel

    with pytest.raises(RuntimeError) as exc:
        parse_recordio(io.BytesIO(sample_stream()), on_record)
    assert exc.value is sentinel
    assert count == 1  # stopped on the first record


def test_trims_trailing_cr():
    # A proxy injecting \r before \n must not break framing.
    got = list(iter_records(io.BytesIO(b"5\r\nhello")))
    assert got == [b"hello"]


def test_typed_decode_via_iter():
    types = [json.loads(r)["type"] for r in iter_records(io.BytesIO(sample_stream()))]
    assert types == ["SUBSCRIBED", "HEARTBEAT", "OFFERS"]


def test_bad_json_is_caller_concern():
    # The codec yields raw bytes; JSON validity is the consumer's to check.
    records = list(iter_records(io.BytesIO(b"10\nNOT_A_JSON")))
    assert records == [b"NOT_A_JSON"]
    with pytest.raises(ValueError):
        json.loads(records[0])
