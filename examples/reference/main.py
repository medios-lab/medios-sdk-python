"""A complete reference framework on medios-sdk-python — the canonical
"how to write a well-behaved Medios framework". A 1:1 port of
medios-sdk-go/examples/reference/main.go.

Where the README snippet is minimal, this covers every behavior a real framework
needs to be correct AND a good cluster citizen:

  - user: framework_info.user must be a real user on the nodes, or every task
    silently goes TASK_LOST.
  - principal: every TaskInfo names the submitter it runs on behalf of, as
    kind:name with kind operator, team or run. The controller refuses a task
    without one (TASK_ERROR); it is checked once at startup.
  - Launch pending work on offers that fit; build a TaskInfo from the offer.
  - DECLINE every offer you do not launch against, with a refuse filter. An offer
    you neither ACCEPT nor DECLINE stays outstanding to you — that is offer
    hoarding, and it starves every other framework on the cluster.
  - DECLINE on accept error — never leak the offer.
  - SUPPRESS when you have nothing to launch; REVIVE (rate-limited) when work
    appears.
  - ACKNOWLEDGE status updates that carry a uuid (or the node retransmits).
  - Classify lifecycle via TaskState (is_terminal / is_failure).
  - TEARDOWN on exit.

The suppress/revive + decline-with-filter policy is Marathon's offer-revive
lifecycle; the parameters (120s refuse, 5s revive throttle) are Marathon's
defaults. The SDK provides the calls; this is one faithful way to drive them.

This framework runs entirely on one loop (the event iterator), so it needs no
locks. A framework that also mutates state from another thread (e.g. a REST API)
must guard shared state.

Workload: launch N short tasks, wait for all to reach a terminal state, exit.

    uv run python examples/reference/main.py \
        --controller http://127.0.0.1:5150 --user <a-real-node-user> \
        --principal operator:<you> --n 3
"""

from __future__ import annotations

import argparse
import getpass
import logging
import sys
import time
from pathlib import Path

# Allow running from a source checkout without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from medios_sdk import medios  # noqa: E402
from medios_sdk.scheduler import Client, SchedulerError  # noqa: E402

# Marathon's offer-handling defaults.
DECLINE_REFUSE_SECONDS = 120.0  # refuse re-offer of a declined offer for 120s
MIN_REVIVE_INTERVAL = 5.0  # throttle REVIVE so we don't spam the controller

log = logging.getLogger("reference")


def scalar_from_offer(offer: medios.Offer, name: str) -> float:
    total = 0.0
    for r in offer.resources:
        if r.name == name and r.scalar is not None:
            total += r.scalar.value
    return total


def offer_fits(offer: medios.Offer, cpus: float, mem: float) -> bool:
    return scalar_from_offer(offer, "cpus") >= cpus and scalar_from_offer(offer, "mem") >= mem


class Framework:
    def __init__(
        self, client: Client, cpus: float, mem: float, cmd: str, pending: int, principal: str
    ):
        self.client = client
        self.principal = principal  # forwarded as TaskInfo.principal on every launch
        self.cpus = cpus
        self.mem = mem
        self.cmd = cmd
        self.pending = pending  # tasks still to launch
        self.running = 0  # launched, awaiting a terminal update
        self.launched = 0  # total launched, for unique task ids
        self.suppressed = False
        self.last_revive_at = 0.0  # monotonic; 0 forces the first revive

    def on_offers(self, offers: list[medios.Offer]) -> None:
        """Launch pending work on offers that fit and decline the rest. The
        invariant: every offer is ACCEPTed or DECLINEd before we return — none
        is ever held."""
        for o in offers:
            if self.pending > 0 and offer_fits(o, self.cpus, self.mem):
                task_id = f"ref-{self.launched}"
                task = medios.TaskInfo(
                    name=task_id,
                    task_id=medios.ID(value=task_id),
                    node_id=o.node_id,
                    principal=self.principal,
                    resources=[
                        medios.Resource(name="cpus", type="SCALAR", scalar=medios.Scalar(value=self.cpus)),
                        medios.Resource(name="mem", type="SCALAR", scalar=medios.Scalar(value=self.mem)),
                    ],
                    command=medios.CommandInfo(shell=True, value=self.cmd),
                )
                try:
                    self.client.accept([o.id], [task])
                except SchedulerError as e:
                    log.warning("accept %s failed: %s — declining offer (no leak)", o.id.value, e)
                    self._decline(o.id)  # never leak an offer on accept error
                    continue
                self.pending -= 1
                self.running += 1
                self.launched += 1
                log.info(
                    "launched %s on node %s (pending=%d running=%d)",
                    task_id, o.node_id.value, self.pending, self.running,
                )
            else:
                # Nothing to launch against this offer → decline with a filter so
                # the controller doesn't immediately re-offer it.
                self._decline(o.id)
        self.reconcile_offers()

    def _decline(self, offer_id: medios.ID) -> None:
        try:
            self.client.decline([offer_id], DECLINE_REFUSE_SECONDS)
        except SchedulerError as e:
            log.warning("decline %s failed: %s", offer_id.value, e)

    def on_update(self, st: medios.TaskStatus) -> None:
        """Acknowledge the update and track terminal state."""
        if st.uuid:
            # Updates with a uuid must be acknowledged or the node retransmits.
            try:
                self.client.acknowledge(st.task_id.value, st.node_id.value, st.uuid)
            except SchedulerError as e:
                log.warning("ack %s failed: %s", st.task_id.value, e)
        if st.state.is_terminal():
            self.running -= 1
            outcome = "FAILURE" if st.state.is_failure() else "ok"
            log.info(
                "task %s terminal: %s (%s) (running=%d)",
                st.task_id.value, st.state, outcome, self.running,
            )
        self.reconcile_offers()

    def reconcile_offers(self) -> None:
        """Match the controller's offer flow to whether we have work: SUPPRESS
        when there's nothing to launch, REVIVE (throttled) when there is."""
        if self.pending == 0 and not self.suppressed:
            try:
                self.client.suppress()
            except SchedulerError as e:
                log.warning("suppress failed: %s", e)
                return
            self.suppressed = True
            log.info("suppressed offers (nothing to launch)")
        elif self.pending > 0 and (
            self.suppressed or (time.monotonic() - self.last_revive_at) >= MIN_REVIVE_INTERVAL
        ):
            try:
                self.client.revive()
            except SchedulerError as e:
                log.warning("revive failed: %s", e)
                return
            self.suppressed = False
            self.last_revive_at = time.monotonic()
            log.info("revived offers (pending=%d)", self.pending)

    def done(self) -> bool:
        return self.pending == 0 and self.running == 0


def main() -> int:
    parser = argparse.ArgumentParser(description="medios-sdk-python reference framework")
    parser.add_argument("--controller", default="http://127.0.0.1:5150", help="Medios controller URL")
    parser.add_argument("--user", default=getpass.getuser(),
                        help="framework_info.user — MUST be a real user on the nodes")
    parser.add_argument("--principal", required=True,
                        help="submitter the tasks run on behalf of, kind:name with kind "
                             "operator, team or run (required)")
    parser.add_argument("--n", type=int, default=3, help="number of tasks to launch")
    parser.add_argument("--cpus", type=float, default=0.1, help="cpus per task")
    parser.add_argument("--mem", type=float, default=32, help="mem (MB) per task")
    parser.add_argument("--cmd", default="echo hello-from-reference", help="shell command each task runs")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

    try:
        medios.validate_principal(args.principal)
    except ValueError as e:
        log.error("--principal: %s", e)
        return 2

    client = Client(args.controller, "reference", user=args.user,
                    principal="framework:reference")
    framework = Framework(client, args.cpus, args.mem, args.cmd, pending=args.n,
                          principal=args.principal)

    try:
        events = client.subscribe()
    except SchedulerError as e:
        log.error("subscribe: %s", e)
        return 1
    log.info("subscribed to %s as user %r; launching %d task(s)", args.controller, args.user, args.n)

    try:
        for ev in events:
            if ev.type == medios.EVENT_SUBSCRIBED:
                log.info("framework_id=%s", client.framework_id)
                # framework_id is now known: prime the offer flow (pending>0 ->
                # REVIVE). A fresh subscription receives offers anyway; this also
                # arms the suppress/revive steady state.
                framework.reconcile_offers()
            elif ev.type == medios.EVENT_OFFERS and ev.offers is not None:
                framework.on_offers(ev.offers.offers)
            elif ev.type == medios.EVENT_UPDATE and ev.update is not None:
                framework.on_update(ev.update.status)
            elif ev.type == medios.EVENT_ERROR and ev.error is not None:
                log.error("controller ERROR event: %s", ev.error.message)

            if framework.done():
                log.info("all %d task(s) terminal; tearing down", args.n)
                _teardown(client)
                return 0
    except KeyboardInterrupt:
        _teardown(client)
        log.info("interrupted; torn down")
        return 0
    except SchedulerError as e:
        log.error("stream error: %s", e)
        _teardown(client)
        return 1

    # Stream closed by the controller (e.g. after teardown elsewhere).
    log.info("stream closed")
    return 0


def _teardown(client: Client) -> None:
    try:
        client.teardown()
    except SchedulerError as e:
        log.warning("teardown failed: %s", e)


if __name__ == "__main__":
    raise SystemExit(main())
