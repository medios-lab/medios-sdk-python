"""Wire-format tests, mirroring medios/types_test.go.

These pin the JSON wire shape: if the controller (or our models) change a field
name, omission rule, or nesting, these fail loudly here rather than silently at
runtime. Whole-number floats render as ``N.0`` in Python (vs ``N`` in Go); both
are valid JSON the controller reads as a double, so the assertions below use the
Python form.
"""

import json

import pytest
from pydantic import ValidationError

from medios_sdk import medios as m


def to_json(model) -> str:
    return json.dumps(model.to_wire(), separators=(",", ":"))


def must_contain(haystack: str, needle: str) -> None:
    assert needle in haystack, f"missing {needle!r} in:\n  {haystack}"


# ── Event decoding ──────────────────────────────────────────────────────────


def test_decode_event_subscribed():
    raw = '{"type":"SUBSCRIBED","subscribed":{"framework_id":{"value":"abc-0001"},"heartbeat_interval_seconds":15.0}}'
    ev = m.Event.model_validate_json(raw)
    assert ev.type == m.EVENT_SUBSCRIBED
    assert ev.subscribed is not None
    assert ev.subscribed.framework_id.value == "abc-0001"
    assert ev.subscribed.heartbeat_interval_seconds == 15.0


def test_decode_event_heartbeat_leaves_bodies_none():
    ev = m.Event.model_validate_json('{"type":"HEARTBEAT"}')
    assert ev.type == m.EVENT_HEARTBEAT
    assert ev.subscribed is None
    assert ev.offers is None
    assert ev.update is None
    assert ev.rescind is None
    assert ev.error is None


def test_decode_event_offers():
    raw = """{
        "type": "OFFERS",
        "offers": {"offers": [{
            "id":          {"value": "O-1"},
            "framework_id":{"value": "abc-0001"},
            "node_id":     {"value": "abc-S0"},
            "hostname":    "localhost",
            "resources": [
                {"name": "cpus", "type": "SCALAR", "scalar": {"value": 2.0}},
                {"name": "mem",  "type": "SCALAR", "scalar": {"value": 1024.0}}
            ]
        }]}
    }"""
    ev = m.Event.model_validate_json(raw)
    assert ev.type == m.EVENT_OFFERS and ev.offers is not None
    assert len(ev.offers.offers) == 1
    o = ev.offers.offers[0]
    assert o.id.value == "O-1"
    assert o.node_id.value == "abc-S0"
    assert o.hostname == "localhost"
    assert len(o.resources) == 2
    cpus = o.resources[0]
    assert cpus.name == "cpus" and cpus.type == "SCALAR"
    assert cpus.scalar is not None and cpus.scalar.value == 2.0


def test_decode_event_update_with_container_status():
    raw = """{
        "type": "UPDATE",
        "update": {"status": {
            "task_id":  {"value": "t-1"},
            "state":    "TASK_RUNNING",
            "node_id":  {"value": "S0"},
            "uuid":     "abcd-uuid",
            "source":   "SOURCE_EXECUTOR",
            "timestamp": 1700000000.5,
            "container_status": {"container_id": {"value": "ctr-xyz"}}
        }}
    }"""
    ev = m.Event.model_validate_json(raw)
    assert ev.type == m.EVENT_UPDATE and ev.update is not None
    s = ev.update.status
    assert s.state == "TASK_RUNNING" and s.state.is_active()
    assert s.task_id.value == "t-1" and s.node_id.value == "S0"
    assert s.uuid == "abcd-uuid" and s.source == "SOURCE_EXECUTOR"
    assert s.container_status is not None
    assert s.container_status.container_id.value == "ctr-xyz"


def test_decode_event_error():
    ev = m.Event.model_validate_json(
        '{"type":"ERROR","error":{"message":"validation failed"}}'
    )
    assert ev.type == m.EVENT_ERROR
    assert ev.error is not None and ev.error.message == "validation failed"


def test_decode_event_ignores_unknown_fields():
    # Forward-compat: controller-added fields must not raise (extra="ignore").
    raw = '{"type":"SUBSCRIBED","subscribed":{"framework_id":{"value":"x"},"controller_info":{"id":"c0"}},"future_field":1}'
    ev = m.Event.model_validate_json(raw)
    assert ev.subscribed.framework_id.value == "x"


# ── Call encoding ────────────────────────────────────────────────────────────


def test_encode_subscribe():
    got = to_json(m.new_subscribe_call(m.FrameworkInfo(user="alice", name="test-fw")))
    must_contain(got, '"type":"SUBSCRIBE"')
    must_contain(got, '"subscribe":')
    must_contain(got, '"framework_info":')
    must_contain(got, '"user":"alice"')
    must_contain(got, '"name":"test-fw"')
    # SUBSCRIBE must NOT carry a top-level framework_id (controller assigns it).
    assert '"framework_id"' not in got


def test_encode_accept():
    tasks = [
        m.TaskInfo(
            name="hello",
            task_id=m.ID(value="t-1"),
            node_id=m.ID(value="S0"),
            principal="operator:tester",
            resources=[
                m.Resource(name="cpus", type="SCALAR", scalar=m.Scalar(value=0.5)),
                m.Resource(name="mem", type="SCALAR", scalar=m.Scalar(value=128)),
            ],
            command=m.CommandInfo(shell=True, value="echo hello"),
        )
    ]
    got = to_json(m.new_accept_call("fw-001", [m.ID(value="O-1")], tasks))
    for want in [
        '"type":"ACCEPT"',
        '"framework_id":{"value":"fw-001"}',
        '"offer_ids":[{"value":"O-1"}]',
        '"type":"LAUNCH"',
        '"task_infos":',
        '"name":"hello"',
        '"task_id":{"value":"t-1"}',
        '"node_id":{"value":"S0"}',
        '"principal":"operator:tester"',
        '"command":{"shell":true,"value":"echo hello"}',
    ]:
        must_contain(got, want)


def test_encode_decline_no_filter():
    got = to_json(
        m.new_decline_call("fw-001", [m.ID(value="O-1"), m.ID(value="O-2")], 0)
    )
    must_contain(got, '"type":"DECLINE"')
    must_contain(got, '"offer_ids":[{"value":"O-1"},{"value":"O-2"}]')
    assert "filters" not in got  # refuse 0 omits filters (controller default)


def test_encode_decline_with_filter():
    got = to_json(m.new_decline_call("fw-001", [m.ID(value="O-1")], 120))
    must_contain(got, '"type":"DECLINE"')
    must_contain(got, '"filters":{"refuse_seconds":120.0}')


def test_encode_suppress():
    got = to_json(m.new_suppress_call("fw-001"))
    must_contain(got, '"type":"SUPPRESS"')
    must_contain(got, '"framework_id":{"value":"fw-001"}')


def test_encode_revive():
    got = to_json(m.new_revive_call("fw-001"))
    must_contain(got, '"type":"REVIVE"')
    must_contain(got, '"framework_id":{"value":"fw-001"}')


def test_encode_reconcile():
    got = to_json(
        m.new_reconcile_call(
            "fw-001",
            [m.ReconcileTask(task_id=m.ID(value="t-1"), node_id=m.ID(value="S0"))],
        )
    )
    must_contain(got, '"type":"RECONCILE"')
    must_contain(
        got,
        '"reconcile":{"tasks":[{"task_id":{"value":"t-1"},"node_id":{"value":"S0"}}]}',
    )


def test_encode_reconcile_all():
    # None tasks -> reconcile every known task; must encode "tasks":[] not null.
    got = to_json(m.new_reconcile_call("fw-001", None))
    must_contain(got, '"type":"RECONCILE"')
    must_contain(got, '"reconcile":{"tasks":[]}')


def test_encode_subscribe_resubscribe():
    # The controller requires the TOP-LEVEL framework_id to be present and
    # equal to subscribe.framework_info.id on re-subscribe; omitting it is
    # rejected with HTTP 400 "'framework_id' differs from
    # 'subscribe.framework_info.id'" (observed live 2026-07-25; root cause of
    # jinkies' resubscribe crash-loop via the Go sibling).
    got = to_json(
        m.new_subscribe_call(
            m.FrameworkInfo(user="u", name="fw", id=m.ID(value="fw-prev"), failover_timeout=60)
        )
    )
    must_contain(got, '"framework_id":{"value":"fw-prev"}')
    must_contain(got, '"id":{"value":"fw-prev"}')
    must_contain(got, '"failover_timeout":60.0')


def test_encode_subscribe_first_time_omits_id_and_failover():
    got = to_json(m.new_subscribe_call(m.FrameworkInfo(user="u", name="fw")))
    assert '"id":{' not in got
    assert "failover_timeout" not in got


def test_encode_taskinfo_bare_command_omits_new_fields():
    ti = m.TaskInfo(
        name="t",
        task_id=m.ID(value="t-1"),
        node_id=m.ID(value="S0"),
        principal="operator:tester",
        command=m.CommandInfo(shell=True, value="echo hi"),
    )
    got = to_json(ti)
    for absent in ["container", "labels", "environment"]:
        assert absent not in got, f"bare command must omit {absent!r}: {got}"


def test_encode_taskinfo_container_labels_env():
    ti = m.TaskInfo(
        name="build-compile",
        task_id=m.ID(value="b1-compile"),
        node_id=m.ID(value="S0"),
        principal="operator:tester",
        command=m.CommandInfo(
            shell=True,
            value="make",
            environment=m.Environment(variables=[m.EnvVar(name="CI", value="1")]),
        ),
        container=m.ContainerInfo(
            type=m.CONTAINER_DOCKER,
            docker=m.DockerInfo(image="alpine:latest"),
            volumes=[
                m.store_volume("workspace", "/abs/ws", m.STORE_RW)
            ],
        ),
        labels=m.Labels(
            labels=[
                m.Label(key="jinkies.build_id", value="b1"),
                m.Label(key="jinkies.stage", value="compile"),
            ]
        ),
    )
    got = to_json(ti)
    for want in [
        '"container":{"type":"DOCKER","docker":{"image":"alpine:latest"}',
        '"volumes":[{"container_path":"/abs/ws","mode":"RW","source":{"type":"STORE","store":{"name":"workspace"}}}]',
        '"labels":{"labels":[{"key":"jinkies.build_id","value":"b1"},{"key":"jinkies.stage","value":"compile"}]}',
        '"environment":{"variables":[{"name":"CI","value":"1"}]}',
    ]:
        must_contain(got, want)


def test_encode_commandinfo_uris():
    # default-extract archive omits `extract`; a plain binary sends explicit
    # extract:false alongside executable:true (the *bool distinction).
    ci = m.CommandInfo(
        shell=True,
        value="./run.sh",
        uris=[
            m.URI(value="https://x/app.tar.gz"),
            m.URI(value="file:///bin/tool", executable=True, extract=False),
        ],
    )
    got = to_json(ci)
    must_contain(got, '{"value":"https://x/app.tar.gz"}')
    must_contain(got, '{"value":"file:///bin/tool","executable":true,"extract":false}')


def test_encode_kill():
    got = to_json(m.new_kill_call("fw-001", "t-1", "S0"))
    must_contain(got, '"type":"KILL"')
    must_contain(got, '"task_id":{"value":"t-1"}')
    must_contain(got, '"node_id":{"value":"S0"}')


def test_encode_acknowledge():
    got = to_json(m.new_acknowledge_call("fw-001", "t-1", "S0", "abcd-uuid"))
    must_contain(got, '"type":"ACKNOWLEDGE"')
    must_contain(got, '"task_id":{"value":"t-1"}')
    must_contain(got, '"node_id":{"value":"S0"}')
    must_contain(got, '"uuid":"abcd-uuid"')


def test_encode_teardown():
    got = to_json(m.new_teardown_call("fw-001"))
    must_contain(got, '"type":"TEARDOWN"')
    must_contain(got, '"framework_id":{"value":"fw-001"}')
    for must_not in ['"subscribe"', '"accept"', '"decline"', '"kill"', '"acknowledge"']:
        assert must_not not in got


def test_round_trip_call():
    original = m.new_accept_call(
        "fw-001",
        [m.ID(value="O-1")],
        [
            m.TaskInfo(
                name="hello",
                task_id=m.ID(value="t-1"),
                node_id=m.ID(value="S0"),
                principal="operator:tester",
                resources=[m.Resource(name="cpus", type="SCALAR", scalar=m.Scalar(value=0.5))],
                command=m.CommandInfo(shell=True, value="echo hi"),
            )
        ],
    )
    decoded = m.Call.model_validate(json.loads(to_json(original)))
    assert decoded.type == original.type
    assert decoded.accept is not None and len(decoded.accept.operations) == 1
    op = decoded.accept.operations[0]
    assert op.type == m.OPERATION_LAUNCH
    assert op.launch is not None and len(op.launch.task_infos) == 1


def test_decode_offer_ports_ranges():
    raw = (
        '{"type":"OFFERS","offers":{"offers":[{'
        '"id":{"value":"O-1"},"framework_id":{"value":"abc-0001"},'
        '"node_id":{"value":"abc-S0"},"hostname":"node1.local","resources":['
        '{"name":"cpus","type":"SCALAR","scalar":{"value":2.0}},'
        '{"name":"ports","type":"RANGES","ranges":{"range":['
        '{"begin":31000,"end":31002},{"begin":31010,"end":31010}]}}'
        "]}]}}"
    )
    ev = m.Event.model_validate_json(raw)
    offer = ev.offers.offers[0]

    ports = m.port_ranges_from_offer(offer)
    assert ports is not None
    assert ports.ports() == [31000, 31001, 31002, 31010]


def test_encode_ports_resource_round_trip():
    r = m.new_ports_resource(31000, 31001, 31004)
    assert r.name == "ports" and r.type == "RANGES" and r.ranges is not None

    wire = to_json(r)
    assert '"type":"RANGES"' in wire
    assert '"begin":31004' in wire
    # scalar is None -> omitted (omitempty parity)
    assert "scalar" not in wire

    back = m.Resource.model_validate(json.loads(wire))
    assert back.ranges is not None
    assert back.ranges.ports() == [31000, 31001, 31004]


def test_port_ranges_from_offer_none_when_absent():
    offer = m.Offer(
        id=m.ID(value="O-2"),
        node_id=m.ID(value="S0"),
        resources=[m.Resource(name="cpus", type="SCALAR", scalar=m.Scalar(value=1.0))],
    )
    assert m.port_ranges_from_offer(offer) is None


# ── TaskInfo.principal (medios-sdk-go ValidatePrincipal parity) ────────────


def _task(principal):
    return m.TaskInfo(
        name="t",
        task_id=m.ID(value="t-1"),
        node_id=m.ID(value="S0"),
        principal=principal,
        command=m.CommandInfo(shell=True, value="echo hi"),
    )


@pytest.mark.parametrize(
    "p", ["operator:alice", "team:batch", "run:research-164-s5-r3", "team:a.b_c-d"]
)
def test_taskinfo_principal_serialized(p):
    ti = _task(p)
    assert ti.principal == p
    got = to_json(ti)
    must_contain(got, f'"principal":"{p}"')
    # Wire order matches the Go struct: node_id, principal, resources.
    assert got.index('"node_id"') < got.index('"principal"') < got.index('"resources"')
    # It survives the ACCEPT envelope, the body the controller checks.
    accept = to_json(m.new_accept_call("fw-001", [m.ID(value="O-1")], [ti]))
    must_contain(accept, f'"principal":"{p}"')


def test_taskinfo_principal_absent_rejected():
    with pytest.raises(ValidationError) as e:
        m.TaskInfo(
            name="t",
            task_id=m.ID(value="t-1"),
            node_id=m.ID(value="S0"),
            command=m.CommandInfo(shell=True, value="echo hi"),
        )
    assert "principal" in str(e.value)


def test_taskinfo_principal_empty_rejected():
    with pytest.raises(ValidationError) as e:
        _task("")
    assert "TaskInfo.principal is required" in str(e.value)
    with pytest.raises(m.PrincipalRequiredError):
        m.validate_principal("")


@pytest.mark.parametrize(
    "p",
    [
        "alice",    # no kind
        "user:alice",  # unknown kind
        "TEAM:research",  # kind is case-sensitive
        "team:",  # empty name
        "team:cue eng",  # space in name
        "team:cue/eng",  # slash in name
        ":research",  # empty kind
        "team:research:",  # trailing separator
        "team:research\n",  # trailing newline (Python's $ would accept it)
    ],
)
def test_taskinfo_principal_malformed_rejected(p):
    with pytest.raises(ValidationError) as e:
        _task(p)
    msg = str(e.value)
    assert "TaskInfo.principal" in msg and "malformed" in msg
    with pytest.raises(m.PrincipalMalformedError) as e2:
        m.validate_principal(p)
    assert repr(p) in str(e2.value)


def test_taskinfo_principal_name_length_bound():
    at_limit = "run:" + "a" * m.PRINCIPAL_NAME_MAX_LENGTH
    assert _task(at_limit).principal == at_limit
    over = "run:" + "a" * (m.PRINCIPAL_NAME_MAX_LENGTH + 1)
    with pytest.raises(ValidationError) as e:
        _task(over)
    assert "the limit is 64" in str(e.value)


# ── URI.sha256 ─────────────────────────────────────────────────────────────


def test_uri_sha256_serialized_when_set():
    digest = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    got = to_json(m.URI(value="https://x/tool", extract=False, sha256=digest))
    assert got == f'{{"value":"https://x/tool","extract":false,"sha256":"{digest}"}}'


def test_uri_sha256_omitted_when_unset():
    got = to_json(m.URI(value="https://x/tool"))
    assert got == '{"value":"https://x/tool"}'
    assert "sha256" not in got


def test_uri_sha256_decodes():
    u = m.URI.model_validate({"value": "https://x/tool", "sha256": "ab" * 32})
    assert u.sha256 == "ab" * 32



# ── Container, kill policy and secret fields (issue #7) ─────────────────────
#
# Expected JSON is the controller's HTTP v1 encoding: medios-sdk-go's for
# ContainerInfo.medios and DurationInfo, and the controller's protobuf JSON
# mapping (stout protobuf.hpp: field names verbatim, enums by name, bytes as
# base64) for the fields medios-sdk-go does not have.


def _command_task(**kw):
    return m.TaskInfo(
        name="t",
        task_id=m.ID(value="t-1"),
        node_id=m.ID(value="S0"),
        principal="operator:tester",
        command=m.CommandInfo(shell=True, value="true"),
        **kw,
    )


def test_encode_container_medios_image():
    c = m.ContainerInfo(
        type=m.CONTAINER_MEDIOS,
        medios=m.MediosInfo(
            image=m.Image(type=m.IMAGE_DOCKER, docker=m.ImageDocker(name="alpine:3.20"))
        ),
    )
    assert to_json(c) == (
        '{"type":"MEDIOS","medios":{"image":{"type":"DOCKER","docker":{"name":"alpine:3.20"}}}}'
    )


def test_encode_container_medios_without_image():
    c = m.ContainerInfo(type=m.CONTAINER_MEDIOS, medios=m.MediosInfo())
    assert to_json(c) == '{"type":"MEDIOS","medios":{}}'


def test_encode_container_docker_unchanged():
    c = m.ContainerInfo(type=m.CONTAINER_DOCKER, docker=m.DockerInfo(image="alpine:latest"))
    assert to_json(c) == '{"type":"DOCKER","docker":{"image":"alpine:latest"}}'


@pytest.mark.parametrize(
    "kw",
    [
        {"type": "MEDIOS"},
        {"type": "MEDIOS", "docker": {"image": "a"}},
        {"type": "MEDIOS", "medios": {}, "docker": {"image": "a"}},
        {"type": "DOCKER"},
        {"type": "DOCKER", "medios": {}},
        {"type": "DOCKER", "docker": {"image": "a"}, "medios": {}},
        {"type": "MESOS", "medios": {}},
    ],
)
def test_container_type_and_block_must_agree(kw):
    with pytest.raises(ValidationError):
        m.ContainerInfo(**kw)


def test_image_type_must_be_docker():
    with pytest.raises(ValidationError):
        m.Image(type="APPC", docker=m.ImageDocker(name="a"))


def test_encode_kill_policy_grace_period():
    ti = _command_task(kill_policy=m.KillPolicy(grace_period=m.DurationInfo(nanoseconds=30_000_000_000)))
    must_contain(to_json(ti), '"kill_policy":{"grace_period":{"nanoseconds":30000000000}}')


def test_kill_policy_omitted_when_unset():
    assert "kill_policy" not in to_json(_command_task())


def test_duration_negative_rejected():
    with pytest.raises(ValidationError):
        m.DurationInfo(nanoseconds=-1)


def test_encode_linux_info_ipc_and_shm():
    c = m.ContainerInfo(
        type=m.CONTAINER_DOCKER,
        docker=m.DockerInfo(image="rayproject/ray:2.40.0"),
        linux_info=m.LinuxInfo(ipc_mode=m.IPC_PRIVATE, shm_size=8192),
    )
    assert to_json(c) == (
        '{"type":"DOCKER","docker":{"image":"rayproject/ray:2.40.0"},'
        '"linux_info":{"ipc_mode":"PRIVATE","shm_size":8192}}'
    )


def test_encode_linux_info_share_parent():
    li = m.LinuxInfo(ipc_mode=m.IPC_SHARE_PARENT)
    assert to_json(li) == '{"ipc_mode":"SHARE_PARENT"}'


@pytest.mark.parametrize(
    "kw",
    [
        {"ipc_mode": "PRIVAT"},
        {"ipc_mode": "UNKNOWN"},
        {"shm_size": 64},
        {"ipc_mode": "SHARE_PARENT", "shm_size": 64},
        {"ipc_mode": "PRIVATE", "shm_size": -1},
        {"ipc_mode": "PRIVATE", "shm_size": 2**32},
    ],
)
def test_linux_info_invalid_rejected(kw):
    with pytest.raises(ValidationError):
        m.LinuxInfo(**kw)


def test_encode_docker_network_host():
    d = m.DockerInfo(image="rayproject/ray:2.40.0", network=m.NETWORK_HOST)
    assert to_json(d) == '{"image":"rayproject/ray:2.40.0","network":"HOST"}'


def test_docker_network_unset_is_omitted():
    # Unset must stay off the wire: the proto default is HOST, and
    # docker_shell reads an absent field as the bridge.
    assert to_json(m.DockerInfo(image="alpine:latest")) == '{"image":"alpine:latest"}'


@pytest.mark.parametrize("network", ["host", "HOSTS", "CNI", ""])
def test_docker_network_invalid_rejected(network):
    with pytest.raises(ValidationError):
        m.DockerInfo(image="alpine:latest", network=network)


def test_encode_env_secret_reference():
    v = m.EnvVar(
        name="RAY_AUTH_TOKEN",
        type=m.ENV_SECRET,
        secret=m.Secret(
            type=m.SECRET_REFERENCE,
            reference=m.SecretReference(name="ray", key="token"),
        ),
    )
    assert to_json(v) == (
        '{"name":"RAY_AUTH_TOKEN","type":"SECRET",'
        '"secret":{"type":"REFERENCE","reference":{"name":"ray","key":"token"}}}'
    )


def test_encode_env_secret_value_is_base64():
    v = m.EnvVar(
        name="TOKEN",
        type=m.ENV_SECRET,
        secret=m.Secret(type=m.SECRET_VALUE, value=m.SecretValue(data=b"hunter2")),
    )
    assert to_json(v) == (
        '{"name":"TOKEN","type":"SECRET","secret":{"type":"VALUE","value":{"data":"aHVudGVyMg=="}}}'
    )


def test_encode_env_plain_value_unchanged():
    assert to_json(m.EnvVar(name="CI", value="1")) == '{"name":"CI","value":"1"}'


@pytest.mark.parametrize(
    "kw",
    [
        {"name": "X"},
        {"name": "X", "type": "VALUE"},
        {"name": "X", "type": "SECRET", "value": "v"},
        {"name": "X", "type": "SECRET"},
        {
            "name": "X",
            "value": "v",
            "secret": {"type": "VALUE", "value": {"data": b"s"}},
        },
        {"name": "X", "type": "OTHER", "value": "v"},
    ],
)
def test_env_type_and_value_must_agree(kw):
    with pytest.raises(ValidationError):
        m.EnvVar(**kw)


@pytest.mark.parametrize(
    "kw",
    [
        {"type": "REFERENCE"},
        {"type": "VALUE"},
        {"type": "REFERENCE", "value": {"data": b"s"}},
        {"type": "VALUE", "reference": {"name": "n"}},
        {"type": "UNKNOWN", "reference": {"name": "n"}},
    ],
)
def test_secret_type_and_block_must_agree(kw):
    with pytest.raises(ValidationError):
        m.Secret(**kw)
