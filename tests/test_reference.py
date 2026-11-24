"""End-to-end test of the reference example's Framework against the fake
controller: subscribe -> OFFERS -> launch + decline -> TASK_FINISHED -> teardown.

This exercises the example's offer-handling, suppress/revive, ack, and terminal
classification logic without a live controller, so the live run is the only
remaining unknown.
"""

import importlib.util
from pathlib import Path

from medios_sdk import medios as m
from medios_sdk.scheduler import Client

from test_client import STREAM_ID, FakeController  # reuse the fake controller

_REF = Path(__file__).resolve().parents[1] / "examples" / "reference" / "main.py"
_spec = importlib.util.spec_from_file_location("reference_main", _REF)
reference = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(reference)


def _offer(node="S0", offer_id="O-1", cpus=1.0, mem=512.0):
    return m.Offer(
        id=m.ID(value=offer_id),
        framework_id=m.ID(value="fw-001"),
        node_id=m.ID(value=node),
        hostname="localhost",
        resources=[
            m.Resource(name="cpus", type="SCALAR", scalar=m.Scalar(value=cpus)),
            m.Resource(name="mem", type="SCALAR", scalar=m.Scalar(value=mem)),
        ],
    )


def test_offer_fits():
    o = _offer(cpus=0.5, mem=64)
    assert reference.offer_fits(o, 0.1, 32)
    assert not reference.offer_fits(o, 0.6, 32)
    assert not reference.offer_fits(o, 0.1, 128)


def test_reference_lifecycle_one_task():
    with FakeController() as fc:
        client = Client(fc.url, "reference", user="tester")
        fw = reference.Framework(client, cpus=0.1, mem=32, cmd="echo hi", pending=1,
                                 principal="operator:tester")

        events = client.subscribe()
        fc.send_event(m.Event(
            type=m.EVENT_SUBSCRIBED,
            subscribed=m.SubscribedBody(framework_id=m.ID(value="fw-001")),
        ))
        next(events)  # SUBSCRIBED
        fw.reconcile_offers()  # initial revive (pending>0)

        # One fitting offer -> launch one task, no pending left.
        fc.send_event(m.Event(type=m.EVENT_OFFERS, offers=m.OffersBody(offers=[_offer()])))
        next(events)
        fw.on_offers([_offer()])
        assert fw.pending == 0 and fw.running == 1
        assert fw.suppressed  # nothing left to launch -> SUPPRESS

        # Task reaches a terminal state -> running drops to 0, framework done.
        st = m.TaskStatus(
            task_id=m.ID(value="ref-0"),
            state=m.TASK_FINISHED,
            node_id=m.ID(value="S0"),
            uuid="u-1",
        )
        fw.on_update(st)
        assert fw.running == 0
        assert fw.done()

        client.teardown()

        # Calls recorded: SUBSCRIBE, REVIVE (prime), ACCEPT, SUPPRESS, ACK, TEARDOWN.
        types = [c.type for _, c in fc.recorded()]
        assert types[0] == m.CALL_SUBSCRIBE
        assert m.CALL_REVIVE in types
        assert m.CALL_ACCEPT in types
        assert m.CALL_SUPPRESS in types
        assert m.CALL_ACKNOWLEDGE in types
        assert types[-1] == m.CALL_TEARDOWN
        # The ACCEPT carried our task and stream-id was threaded.
        accept = next(c for _, c in fc.recorded() if c.type == m.CALL_ACCEPT)
        task = accept.accept.operations[0].launch.task_infos[0]
        assert task.task_id.value == "ref-0"
        assert task.node_id.value == "S0"
        # The principal reached the controller in the ACCEPT body.
        assert task.principal == "operator:tester"


def test_reference_declines_unfit_offer_no_leak():
    with FakeController() as fc:
        client = Client(fc.url, "reference", user="tester")
        fw = reference.Framework(client, cpus=4.0, mem=8192, cmd="echo hi", pending=1,
                                 principal="operator:tester")
        events = client.subscribe()
        fc.send_event(m.Event(
            type=m.EVENT_SUBSCRIBED,
            subscribed=m.SubscribedBody(framework_id=m.ID(value="fw-001")),
        ))
        next(events)

        # Offer too small to fit -> must be DECLINEd (never held), with a filter.
        fw.on_offers([_offer(cpus=0.5, mem=64)])
        assert fw.pending == 1 and fw.running == 0
        decline = next(c for _, c in fc.recorded() if c.type == m.CALL_DECLINE)
        assert decline.decline.offer_ids[0].value == "O-1"
        assert decline.decline.filters.refuse_seconds == 120.0
