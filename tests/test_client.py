"""Scheduler client tests, mirroring scheduler/client_test.go.

A threaded fake controller records calls and drives the SUBSCRIBE stream by
writing RecordIO frames as the test queues events.
"""

import json
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from medios_sdk import medios as m
from medios_sdk.scheduler import Client, SchedulerError

STREAM_ID = "stream-test-12345"


class FakeController:
    def __init__(self, subscribe_status=200):
        self.subscribe_status = subscribe_status
        self.calls = []  # list of (headers, Call)
        self._lock = threading.Lock()
        self._events: "queue.Queue" = queue.Queue()
        self._stop = threading.Event()
        ctrl = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                call = m.Call.model_validate_json(body)
                with ctrl._lock:
                    ctrl.calls.append((dict(self.headers), call))

                if call.type == m.CALL_SUBSCRIBE:
                    ctrl._handle_subscribe(self)
                else:
                    self.send_response(202)
                    self.send_header("Content-Length", "0")
                    self.end_headers()

            def log_message(self, *args):
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def _handle_subscribe(self, handler):
        if self.subscribe_status != 200:
            handler.send_response(self.subscribe_status)
            handler.send_header("Content-Length", "0")
            handler.end_headers()
            return
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Medios-Stream-Id", STREAM_ID)
        handler.end_headers()
        handler.wfile.flush()  # push headers so the client's open() returns
        while not self._stop.is_set():
            try:
                ev = self._events.get(timeout=0.1)
            except queue.Empty:
                continue
            if ev is None:
                return
            payload = json.dumps(ev.to_wire(), separators=(",", ":")).encode()
            try:
                handler.wfile.write(str(len(payload)).encode() + b"\n" + payload)
                handler.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"

    def send_event(self, ev):
        self._events.put(ev)

    def recorded(self):
        with self._lock:
            return list(self.calls)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._events.put(None)
        self._server.shutdown()
        self._server.server_close()


def _subscribed(fid="fw-001"):
    return m.Event(
        type=m.EVENT_SUBSCRIBED,
        subscribed=m.SubscribedBody(framework_id=m.ID(value=fid), heartbeat_interval_seconds=15.0),
    )


def test_subscribe_captures_framework_id():
    with FakeController() as fc:
        client = Client(fc.url, "test-fw")
        events = client.subscribe()
        fc.send_event(_subscribed("fw-001"))
        ev = next(events)
        assert ev.type == m.EVENT_SUBSCRIBED
        assert client.framework_id == "fw-001"


def test_subscribe_sends_framework_info_with_current_user():
    with FakeController() as fc:
        client = Client(fc.url, "test-fw")
        client.subscribe()
        # SUBSCRIBE is recorded synchronously when open() returns.
        calls = fc.recorded()
        assert calls, "no calls recorded"
        _, first = calls[0]
        assert first.type == m.CALL_SUBSCRIBE and first.subscribe is not None
        assert first.subscribe.framework_info.user != ""
        assert first.subscribe.framework_info.name == "test-fw"


def test_principal_is_sent_in_framework_info_and_header():
    with FakeController() as fc:
        client = Client(fc.url, "test-fw", principal="framework:test-fw")
        events = client.subscribe()
        fc.send_event(_subscribed("fw-001"))
        next(events)
        client.revive()

        (sub_headers, sub), (call_headers, _) = fc.recorded()[:2]
        assert sub.subscribe.framework_info.principal == "framework:test-fw"
        for headers in (sub_headers, call_headers):
            lower = {k.lower(): v for k, v in headers.items()}
            assert lower.get("x-medios-principal") == "framework:test-fw"


def test_no_principal_omits_field_and_header():
    with FakeController() as fc:
        client = Client(fc.url, "test-fw")
        client.subscribe()
        headers, sub = fc.recorded()[0]
        assert sub.subscribe.framework_info.principal is None
        assert "x-medios-principal" not in {k.lower() for k in headers}


def test_accept_threads_stream_id_header():
    with FakeController() as fc:
        client = Client(fc.url, "test-fw")
        events = client.subscribe()
        fc.send_event(_subscribed("fw-001"))
        next(events)  # drain SUBSCRIBED so framework_id is set

        task = m.TaskInfo(
            name="hello",
            task_id=m.ID(value="t-1"),
            node_id=m.ID(value="S0"),
            principal="operator:tester",
            command=m.CommandInfo(shell=True, value="echo hi"),
        )
        client.accept([m.ID(value="O-1")], [task])

        headers, last = fc.recorded()[-1]
        assert last.type == m.CALL_ACCEPT
        # http.server lowercases header names; look it up case-insensitively.
        sid = {k.lower(): v for k, v in headers.items()}.get("medios-stream-id")
        assert sid == STREAM_ID
        assert last.framework_id is not None and last.framework_id.value == "fw-001"


def test_call_before_subscribe_fails():
    with FakeController() as fc:
        client = Client(fc.url, "test-fw")
        with pytest.raises(SchedulerError) as exc:
            client.accept([m.ID(value="O-1")], [])
        assert "Stream-Id" in str(exc.value)


def test_call_types_are_correct():
    with FakeController() as fc:
        client = Client(fc.url, "test-fw")
        events = client.subscribe()
        fc.send_event(_subscribed("fw-001"))
        next(events)

        client.decline([m.ID(value="O-2")], 120)
        client.suppress()
        client.revive()
        client.kill("t-1", "S0")
        client.acknowledge("t-1", "S0", "uuid-abc")
        client.reconcile(None)
        client.teardown()

        types = [c.type for _, c in fc.recorded()]
        assert types == [
            m.CALL_SUBSCRIBE,
            m.CALL_DECLINE,
            m.CALL_SUPPRESS,
            m.CALL_REVIVE,
            m.CALL_KILL,
            m.CALL_ACKNOWLEDGE,
            m.CALL_RECONCILE,
            m.CALL_TEARDOWN,
        ]


def test_subscribe_non_ok_bubbles_up():
    with FakeController(subscribe_status=403) as fc:
        client = Client(fc.url, "test-fw")
        with pytest.raises(SchedulerError) as exc:
            client.subscribe()
        assert "403" in str(exc.value)


def test_stream_delivers_multiple_events_then_closes():
    with FakeController() as fc:
        client = Client(fc.url, "test-fw")
        events = client.subscribe()
        fc.send_event(_subscribed("fw-001"))
        fc.send_event(m.Event(type=m.EVENT_HEARTBEAT))

        got = [next(events).type, next(events).type]
        assert got == [m.EVENT_SUBSCRIBED, m.EVENT_HEARTBEAT]
        # Breaking iteration (generator close) must not raise.
        events.close()


def test_with_framework_id_prepopulates():
    client = Client("http://x:5150", "fw", framework_id="fw-prev", failover_timeout=60)
    assert client.framework_id == "fw-prev"


class _Redirector:
    """A standby controller: answers every POST with 307 to the leader, or 503."""

    def __init__(self, location=None):
        target = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                if target.location:
                    self.send_response(307)
                    self.send_header("Location", target.location)
                else:
                    self.send_response(503)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        self.location = location
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def close(self):
        self._server.shutdown()
        self._server.server_close()


def test_subscribe_follows_standby_redirect_and_posts_to_leader():
    with FakeController() as fc:
        # Scheme-relative, as the controller sends it.
        standby = _Redirector("//127.0.0.1:%d/api/v1/scheduler" % fc.port)
        try:
            client = Client(standby.url, "test-fw")
            events = client.subscribe()
            fc.send_event(_subscribed("fw-001"))
            next(events)
            client.revive()
            assert [c.type for _, c in fc.recorded()] == [m.CALL_SUBSCRIBE, m.CALL_REVIVE]
        finally:
            standby.close()


def test_subscribe_skips_unreachable_and_leaderless_controllers():
    leaderless = _Redirector(None)
    try:
        with FakeController() as fc:
            client = Client(f"http://127.0.0.1:1,{leaderless.url},{fc.url}", "test-fw")
            client.subscribe()
            assert fc.recorded()[0][1].type == m.CALL_SUBSCRIBE
    finally:
        leaderless.close()


def test_subscribe_reports_every_controller_when_none_accepts():
    leaderless = _Redirector(None)
    try:
        client = Client(["http://127.0.0.1:1", leaderless.url], "test-fw")
        with pytest.raises(SchedulerError) as exc:
            client.subscribe()
        assert "127.0.0.1:1" in str(exc.value) and "503" in str(exc.value)
    finally:
        leaderless.close()


def test_resubscribe_reuses_assigned_framework_id():
    with FakeController() as fc:
        client = Client(fc.url, "test-fw")
        events = client.subscribe()
        fc.send_event(_subscribed("fw-001"))
        next(events)
        client.subscribe()
        first, second = [c for _, c in fc.recorded() if c.type == m.CALL_SUBSCRIBE]
        assert first.subscribe.framework_info.id is None
        assert second.subscribe.framework_info.id.value == "fw-001"


def test_empty_controller_url_is_refused():
    with pytest.raises(ValueError):
        Client(" , ", "test-fw")
