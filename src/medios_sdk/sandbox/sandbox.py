"""Read task stdout/stderr from a medios-node's ``/files/read`` HTTP endpoint.

A framework launches a task with the default command executor; the task's
stdout/stderr land in the node's sandbox. This turns
``(node_id, framework_id, task_id, container_id, file)`` into the node-side path
and reads the bytes back through the node's HTTP API.

Sandbox path layout::

    <work_dir>/nodes/<node_id>/frameworks/<framework_id>/executors/<executor_id>/runs/<container_id>/<file>

For default-command-executor tasks, ``executor_id == task_id``. The response is
``{data, offset}`` from the node's ``/files/read`` route.
"""

from __future__ import annotations

import json
import posixpath
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional


class SandboxError(Exception):
    """A read failed (non-200, or a malformed response)."""


class NotFoundError(SandboxError):
    """The node reports the sandbox path doesn't exist (post-GC, or the task
    never ran on this node)."""


class NodeUnreachableError(SandboxError):
    """The node could not be reached within the timeout."""


@dataclass
class ReadResult:
    """The medios-node's ``/files/read`` response (``data`` / ``offset``)."""

    data: str
    offset: int


class Resolver:
    """Computes sandbox file paths and reads bytes through a node's HTTP API.
    Construct one and share it across the framework process.

    ``work_dir`` must equal the node's ``--work_dir``. ``node_http_port`` is the
    port medios-nodes serve their HTTP API on. ``timeout`` caps each read.
    """

    def __init__(
        self,
        work_dir: str,
        node_http_port: int,
        timeout: float = 5.0,
        opener: Optional[urllib.request.OpenerDirector] = None,
    ) -> None:
        self.work_dir = work_dir
        self.node_http_port = node_http_port
        self.timeout = timeout
        self._opener = opener or urllib.request.build_opener()

    def path_for(
        self, node_id: str, framework_id: str, task_id: str, container_id: str, file: str
    ) -> str:
        """The node-side absolute path of a file inside the task's sandbox — the
        value sent as the ``?path=`` query parameter to ``/files/read``.

        ``framework_id`` is passed per-call (not held on the Resolver) because
        the controller assigns it at SUBSCRIBE time, after the Resolver is built.
        """
        return posixpath.join(
            self.work_dir,
            "nodes",
            node_id,
            "frameworks",
            framework_id,
            "executors",
            task_id,
            "runs",
            container_id,
            file,
        )

    def read(
        self, host: str, sandbox_path: str, offset: int = 0, length: int = -1
    ) -> ReadResult:
        """Fetch a slice of the sandbox file from the node. ``offset=-1`` means
        "from current end"; ``length=-1`` means "read all remaining" (length is
        omitted from the request).

        Raises :class:`NotFoundError` (404), :class:`NodeUnreachableError`
        (transport error / timeout), or :class:`SandboxError` (other non-200).
        """
        query = {"path": sandbox_path, "offset": str(offset)}
        if length >= 0:
            query["length"] = str(length)
        url = urllib.parse.urlunsplit(
            (
                "http",
                f"{host}:{self.node_http_port}",
                "/files/read",
                urllib.parse.urlencode(query),
                "",
            )
        )
        req = urllib.request.Request(url, method="GET")
        try:
            resp = self._opener.open(req, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise NotFoundError(f"sandbox: {sandbox_path} not found on node") from e
            raise SandboxError(
                f"sandbox: node returned {e.code} for {sandbox_path}"
            ) from e
        except urllib.error.URLError as e:
            raise NodeUnreachableError(
                f"sandbox: node unreachable {host}:{self.node_http_port}: {e.reason}"
            ) from e
        try:
            body = resp.read()
        finally:
            resp.close()
        try:
            obj = json.loads(body)
            return ReadResult(data=obj.get("data", ""), offset=int(obj.get("offset", 0)))
        except (ValueError, TypeError) as e:
            raise SandboxError(f"sandbox: decode response: {e}") from e
