"""The Medios HTTP v1 scheduler client/driver.

A single-framework client: subscribe to the event stream, then react with
accept / decline / suppress / revive / kill / acknowledge / reconcile / teardown.
The ``Medios-Stream-Id`` header is threaded on every non-SUBSCRIBE call and the
``framework_id`` is captured from the SUBSCRIBED event, both automatically.

Wire-protocol requirements baked into defaults:

  - ``framework_info.user`` defaults to the current OS user. An empty or
    nonexistent user causes silent TASK_LOST on default node config, so pass
    ``user=`` a real user on the nodes for a remote cluster.
  - ``Medios-Stream-Id`` (read from the SUBSCRIBE response) is sent on every
    other call; the controller requires it.
"""

from __future__ import annotations

import getpass
import json
import urllib.error
import urllib.request
from typing import Iterator, Optional

from ..medios import (
    EVENT_SUBSCRIBED,
    Call,
    Event,
    FrameworkInfo,
    ID,
    TaskInfo,
    iter_records,
    new_accept_call,
    new_acknowledge_call,
    new_decline_call,
    new_kill_call,
    new_reconcile_call,
    new_revive_call,
    new_subscribe_call,
    new_suppress_call,
    new_teardown_call,
)
from ..medios.store import validate_volumes
from ..medios.types import ReconcileTask

_SCHEDULER_PATH = "/api/v1/scheduler"


class SchedulerError(Exception):
    """A call failed: a transport error, a non-2xx response, or a call made
    before :meth:`Client.subscribe`."""


def _to_json_bytes(call: Call) -> bytes:
    # Compact separators match the Go SDK's wire bytes (no spaces after : or ,).
    return json.dumps(call.to_wire(), separators=(",", ":")).encode("utf-8")


class Client:
    """A single-framework Medios HTTP v1 scheduler client.

    Lifecycle:

      1. Construct with ``Client(controller_url, name, user=...)``.
      2. ``for event in client.subscribe():`` — the SUBSCRIBE request is sent
         when ``subscribe()`` is called (so ``Medios-Stream-Id`` is captured
         before you iterate); the first event is SUBSCRIBED, which carries the
         framework_id the client captures internally. The stream then continues
         with OFFERS / UPDATE / HEARTBEAT / RESCIND / ERROR until the server
         hangs up or you stop iterating.
      3. Call accept / decline / suppress / revive / kill / acknowledge /
         reconcile in response to events.
      4. Call teardown when finished.

    For restart survival, construct with ``framework_id=`` a value captured from
    a previous run (via :attr:`framework_id`) plus a non-zero
    ``failover_timeout=``: the controller then treats this as the same framework
    (failover) and holds its tasks across the disconnect. After re-subscribing,
    call :meth:`reconcile` to re-learn task state; replies arrive as UPDATE
    events on the stream.
    """

    def __init__(
        self,
        controller_url: str,
        name: str,
        *,
        user: Optional[str] = None,
        framework_id: Optional[str] = None,
        failover_timeout: float = 0.0,
        opener: Optional[urllib.request.OpenerDirector] = None,
    ) -> None:
        info = FrameworkInfo(user=user or getpass.getuser(), name=name)
        self._framework_id = ""
        if framework_id:
            # Re-subscribe under the persisted id, and pre-populate it so calls
            # work before the SUBSCRIBED reply arrives.
            info.id = ID(value=framework_id)
            self._framework_id = framework_id
        if failover_timeout and failover_timeout > 0:
            info.failover_timeout = failover_timeout
        self._framework_info = info
        self._controller_url = controller_url.rstrip("/")
        self._stream_id = ""
        self._opener = opener or urllib.request.build_opener()

    @property
    def framework_id(self) -> str:
        """The framework_id assigned by the controller (captured from the
        SUBSCRIBED event), or the persisted id passed at construction. ``""``
        until either is known. Persist this across restarts for failover."""
        return self._framework_id

    def subscribe(self) -> Iterator[Event]:
        """Open the SUBSCRIBE stream and return an iterator of events.

        The request is sent before this returns, so ``Medios-Stream-Id`` is
        captured and follow-up calls are valid as soon as you start iterating.
        The framework_id is captured from the SUBSCRIBED event (the first one)
        before it is yielded.
        """
        call = new_subscribe_call(self._framework_info)
        req = urllib.request.Request(
            self._url(),
            data=_to_json_bytes(call),
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            resp = self._opener.open(req)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace").strip()
            raise SchedulerError(f"SUBSCRIBE got HTTP {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            raise SchedulerError(f"SUBSCRIBE request: {e.reason}") from e

        self._stream_id = resp.headers.get("Medios-Stream-Id", "") or ""
        return self._events(resp)

    def _events(self, resp) -> Iterator[Event]:
        try:
            for raw in iter_records(resp):
                event = Event.model_validate_json(raw)
                if event.type == EVENT_SUBSCRIBED and event.subscribed is not None:
                    self._framework_id = event.subscribed.framework_id.value
                yield event
        finally:
            resp.close()

    # ── Calls ──────────────────────────────────────────────────────────────

    def accept(self, offer_ids: list[ID], tasks: list[TaskInfo]) -> None:
        """ACCEPT with a single LAUNCH operation. Per the HTTP v1 API, all
        offers in one ACCEPT must come from the same node. Each task's volumes
        are checked first (:func:`validate_volumes`), so a volume the
        controller would refuse raises ``ValueError`` before anything is
        sent."""
        for t in tasks:
            validate_volumes(t)
        self._post(new_accept_call(self._framework_id, offer_ids, tasks))

    def decline(self, offer_ids: list[ID], refuse_seconds: float = 0.0) -> None:
        """DECLINE one or more offers. ``refuse_seconds`` attaches a refuse
        filter so the controller waits before re-offering (pass 0 for the
        controller default, 5s). Always decline what you don't accept — an offer
        that is neither accepted nor declined stays outstanding to you (offer
        hoarding)."""
        self._post(new_decline_call(self._framework_id, offer_ids, refuse_seconds))

    def suppress(self) -> None:
        """SUPPRESS — stop the controller sending offers. Use when there is
        nothing to launch; pair with :meth:`revive`."""
        self._post(new_suppress_call(self._framework_id))

    def revive(self) -> None:
        """REVIVE — clear active decline/accept filters and resume offers."""
        self._post(new_revive_call(self._framework_id))

    def kill(self, task_id: str, node_id: str) -> None:
        """KILL the given task on the given node."""
        self._post(new_kill_call(self._framework_id, task_id, node_id))

    def acknowledge(self, task_id: str, node_id: str, uuid: str) -> None:
        """ACKNOWLEDGE a status update. Required for updates that carry a uuid,
        or the node retransmits them."""
        self._post(new_acknowledge_call(self._framework_id, task_id, node_id, uuid))

    def reconcile(self, tasks: Optional[list[ReconcileTask]] = None) -> None:
        """RECONCILE — ask the controller to resend the current status of the
        given tasks; pass ``None`` to reconcile all of this framework's tasks.
        Replies arrive as UPDATE events on the subscription stream. This is how
        a framework re-learns task state after a restart."""
        self._post(new_reconcile_call(self._framework_id, tasks))

    def teardown(self) -> None:
        """TEARDOWN — remove the framework from the controller. The SUBSCRIBE
        stream closes afterward."""
        self._post(new_teardown_call(self._framework_id))

    # ── Internals ──────────────────────────────────────────────────────────

    def _url(self) -> str:
        return self._controller_url + _SCHEDULER_PATH

    def _post(self, call: Call) -> None:
        if not self._stream_id:
            raise SchedulerError(
                "scheduler: no Medios-Stream-Id (call subscribe first)"
            )
        req = urllib.request.Request(
            self._url(),
            data=_to_json_bytes(call),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Medios-Stream-Id": self._stream_id,
            },
        )
        try:
            resp = self._opener.open(req)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace").strip()
            raise SchedulerError(
                f"scheduler: {call.type} got HTTP {e.code}: {detail}"
            ) from e
        except urllib.error.URLError as e:
            raise SchedulerError(f"scheduler: {call.type}: {e.reason}") from e
        try:
            resp.read()
        finally:
            resp.close()
