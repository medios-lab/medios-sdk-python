"""Sandbox Resolver tests, mirroring sandbox/sandbox_test.go.

Uses a threaded http.server fake node to exercise the /files/read path, status
mapping, and the length-omission rule.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from medios_sdk.sandbox import NodeUnreachableError, NotFoundError, Resolver, SandboxError


class _FakeNode:
    """A throwaway HTTP server standing in for a medios-node's /files/read."""

    def __init__(self, status=200, payload=None, on_request=None):
        self.status = status
        self.payload = payload if payload is not None else {"data": "", "offset": 0}
        self.on_request = on_request
        node = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if node.on_request is not None:
                    node.on_request(self)
                if node.status != 200:
                    self.send_response(node.status)
                    self.end_headers()
                    return
                body = json.dumps(node.payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()


def test_path_for_composes_medios_layout():
    r = Resolver(work_dir="/var/lib/medios", node_http_port=0)
    got = r.path_for("node-7", "fw-123", "task-abc", "ctr-xyz", "stdout")
    assert got == (
        "/var/lib/medios/nodes/node-7/frameworks/fw-123/"
        "executors/task-abc/runs/ctr-xyz/stdout"
    )


def test_read_happy_path():
    seen = {}

    def capture(handler):
        seen["path"] = handler.path

    with _FakeNode(payload={"data": "5050\n", "offset": 5}, on_request=capture) as node:
        r = Resolver(work_dir="/var/lib/medios", node_http_port=node.port)
        out = r.read("127.0.0.1", "/x/stdout", offset=100, length=-1)
    assert out.data == "5050\n" and out.offset == 5
    assert "/files/read" in seen["path"]
    assert "path=%2Fx%2Fstdout" in seen["path"]
    assert "offset=100" in seen["path"]


def test_read_not_found_maps_to_error():
    with _FakeNode(status=404) as node:
        r = Resolver(work_dir="", node_http_port=node.port)
        with pytest.raises(NotFoundError):
            r.read("127.0.0.1", "/missing", 0, -1)


def test_read_other_status_raises_sandbox_error():
    with _FakeNode(status=500) as node:
        r = Resolver(work_dir="", node_http_port=node.port)
        with pytest.raises(SandboxError) as exc:
            r.read("127.0.0.1", "/path", 0, -1)
    assert "500" in str(exc.value)


def test_read_unreachable_node():
    # Port 1 refuses; the resolver maps the transport error.
    r = Resolver(work_dir="", node_http_port=1, timeout=1.0)
    with pytest.raises(NodeUnreachableError):
        r.read("127.0.0.1", "/anything", 0, -1)


def test_read_omits_length_when_negative():
    seen = {}

    def capture(handler):
        seen["path"] = handler.path

    with _FakeNode(on_request=capture) as node:
        r = Resolver(work_dir="", node_http_port=node.port)
        r.read("127.0.0.1", "/x", 0, -1)
    assert "length=" not in seen["path"]
