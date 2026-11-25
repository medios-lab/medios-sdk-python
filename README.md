# medios-sdk-python

The Python client for the Medios HTTP v1 scheduler API. It is the substrate-facing
client: the wire types, the RecordIO stream codec, and the scheduler driver
(SUBSCRIBE plus the calls), with the surrounding machinery (task-lifecycle
states, sandbox output retrieval, a small persistence layer).

You bring the scheduling policy — what to launch and when. The SDK handles the
wire protocol, the lifecycle types, sandbox output, and persistence. It is the
Python sibling of [`medios-sdk-go`](https://github.com/medios-lab/medios-sdk-go),
ported 1:1 from tag `v0.2.1`.

What it is not: a framework's domain or policy. There are no domain concepts
(app/job/schedule/pipeline) and no task-record type — how you track a launched
task is the framework's job, not the SDK's.

Validated end-to-end against a live Medios 1.0.0 cluster: the reference framework
subscribed, launched two tasks across two nodes, saw each reach `TASK_FINISHED`,
and tore down clean (no framework left in the controller's `/state`).

## Install

Install with `uv`:

```sh
uv add "git+https://github.com/medios-lab/medios-sdk-python"
```

Or in a `pyproject.toml`:

```toml
dependencies = [
    "medios-sdk @ git+https://github.com/medios-lab/medios-sdk-python",
]
```

The only runtime dependency is `pydantic>=2`. Pin a tag or commit (not a branch)
so builds are reproducible.

## Quickstart

A framework is a `subscribe()` loop reacting to two events: `OFFERS` (launch work
or decline) and `UPDATE` (a task changed state). Wire types live in
`medios_sdk.medios`; the driver is `medios_sdk.scheduler.Client`. This is the
shape of the loop — for a complete, correct framework read and run
[`examples/reference`](examples/reference).

```python
from medios_sdk.scheduler import Client
from medios_sdk import medios

client = Client(
    "http://127.0.0.1:5150", "my-framework",
    user="alice",  # MUST be a real user on the nodes — see Notes
    principal="framework:my-framework",  # required unless the controller authenticates frameworks
)

for event in client.subscribe():
    if event.type == medios.EVENT_OFFERS:
        for offer in event.offers.offers:
            if want_to_launch:
                task = medios.TaskInfo(
                    name="hello",
                    task_id=medios.ID(value="hello-1"),
                    node_id=offer.node_id,
                    # The submitter this task runs on behalf of, kind:name with
                    # kind operator, team or run. Required: the controller
                    # refuses a task without one, and TaskInfo refuses to build.
                    principal="operator:alice",
                    resources=[medios.Resource(
                        name="cpus", type="SCALAR", scalar=medios.Scalar(value=0.1))],
                    command=medios.CommandInfo(shell=True, value="echo hi"),
                )
                client.accept([offer.id], [task])
            else:
                # ALWAYS decline what you don't launch, with a refuse filter.
                # An offer you neither accept nor decline is hoarded.
                client.decline([offer.id], refuse_seconds=120)
    elif event.type == medios.EVENT_UPDATE:
        st = event.update.status
        if st.uuid:
            client.acknowledge(st.task_id.value, st.node_id.value, st.uuid)
        if st.state.is_terminal():  # st.state is a typed medios.TaskState
            ...  # handle TASK_FINISHED / TASK_FAILED (st.state.is_failure())
```

A real framework must also **suppress** offers when it has nothing to launch and
**revive** when work appears, or it keeps taking the cluster's offers while idle.
That is the difference between this snippet and `examples/reference`.

## Packages

| Module | What it gives you |
|---|---|
| `medios_sdk.medios` | The wire format: pydantic v2 types for every Call and Event (`Offer`, `TaskInfo`, `TaskStatus`, `Call`, `Event`, …), the `new_*_call` constructors, the RecordIO codec, and `TaskState` — the typed wire state (`TASK_RUNNING`, …) with `is_active()` / `is_terminal()` / `is_failure()`. Store volumes (DR-036): `with_store(task, offer, name, container_path, mode)` adds a store the offering node declares, with its `store/<name>` resource taken from the offer; `container_path` is one name inside the sandbox (`"data"`), which every node delivers, or an absolute path, which only Linux nodes deliver. |
| `medios_sdk.scheduler` | The driver: `Client`, `subscribe()` (event iterator), `accept`, `decline` (with a refuse filter), `suppress`/`revive`, `kill`, `acknowledge`, `reconcile`, `teardown`; `framework_id` capture and `Medios-Stream-Id` threading. |
| `medios_sdk.sandbox` | Read a task's `stdout`/`stderr` from a node's `/files/read` endpoint. |
| `medios_sdk.store` | A narrow persistence contract (singletons + blob buckets), with SQLite and in-memory implementations. Use it to persist `framework_id` across restarts. |
| `medios_sdk.timestamp` | Millisecond-precision UTC time with ISO-8601 string form. |

## Reference example — start here

[`examples/reference`](examples/reference/main.py) is the canonical well-behaved
framework: it launches N tasks and demonstrates every behavior a real framework
needs — a real `user`, a task `principal`, launching on offers that fit,
declining the rest with a refuse filter, declining on accept-error (no leak),
suppress when idle / revive when work appears, acknowledging updates,
classifying terminal state via `TaskState`, and teardown.

```sh
uv run python examples/reference/main.py \
  --controller http://127.0.0.1:5150 --user alice --principal operator:alice --n 3
```

## Notes — the gotchas

1. **`user` must exist on the node.** It defaults to the local OS user, which is
   wrong for a remote cluster: the task returns
   `TASK_LOST: User '<x>' does not exist on this node`. Pass `user="<node-user>"`.
2. The SDK threads the `Medios-Stream-Id` header on every non-`SUBSCRIBE` call;
   the controller requires it. You don't set it yourself.
3. **Don't hoard offers.** Decline every offer you don't accept (with a refuse
   filter, e.g. `120`), decline on accept-error too, and `suppress()` while you
   have nothing to launch (`revive()` when work appears). An offer you neither
   accept nor decline stays outstanding to your framework and starves the others
   on the cluster. `examples/reference` implements the full lifecycle; it mirrors
   Marathon's offer-revive behavior (120s refuse, 5s revive throttle).
4. **Restart survival.** Persist `client.framework_id` (e.g. via
   `medios_sdk.store`); on restart, construct `Client(..., framework_id=saved,
   failover_timeout=60)` and call `reconcile()` to re-learn task state. Without a
   non-zero `failover_timeout` the controller kills the framework's tasks the
   instant it disconnects.
5. **Controller failover.** With more than one controller, pass every URL, as a
   list or one comma-separated string:
   `Client("http://ctl-a:5150,http://ctl-b:5150", ...)`. `subscribe()` finds the
   leader, following a standby's redirect and skipping a controller that is
   down or has no leader yet. When the stream ends, call `subscribe()` again on
   the same client: it resubscribes as the same framework, and with a non-zero
   `failover_timeout` the framework's tasks keep running. Then call
   `reconcile()` to re-learn task state.
6. **Reading task output needs the node network-reachable.** `sandbox` reads from
   `offer.hostname:node_port/files/read`; `Resolver.work_dir` must equal the
   node's `--work_dir`.

## Development

```sh
uv sync --extra dev
uv run pytest
```

## License

Apache 2.0. See [LICENSE](LICENSE).
