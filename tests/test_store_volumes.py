"""Store volumes (DR-036) — mirrors medios-sdk-go medios/store_test.go."""

import json

import pytest

import medios_sdk.medios as m


def store_resource(name: str, readers: str, writers: str) -> m.Resource:
    return m.Resource.model_validate_json(
        '{"name":"store/' + name + '","type":"SCALAR","scalar":{"value":1},"shared":{},'
        '"metadata":{"labels":[{"key":"class","value":"local"},'
        '{"key":"readers","value":"' + readers + '"},{"key":"writers","value":"' + writers + '"}]}}'
    )


FLEET_OFFER = m.Offer(
    id=m.ID(value="O-1"),
    node_id=m.ID(value="S0"),
    hostname="host-0",
    resources=[store_resource("tabicl-tables", "4", "0"), store_resource("results", "4", "1")],
)


def image_task() -> m.TaskInfo:
    return m.TaskInfo(
        name="train",
        task_id=m.ID(value="train-1"),
        node_id=m.ID(value="S0"),
        principal="team:research",
        command=m.CommandInfo(shell=True, value="python train.py"),
        container=m.ContainerInfo(
            type=m.CONTAINER_MEDIOS,
            medios=m.MediosInfo(
                image=m.Image(type=m.IMAGE_DOCKER, docker=m.ImageDocker(name="pytorch:24.09"))
            ),
        ),
    )


def wire(model) -> str:
    return json.dumps(model.to_wire(), separators=(",", ":"))


@pytest.mark.parametrize(
    "mode,want",
    [
        (m.STORE_RO, '{"container_path":"data","mode":"RO","source":{"type":"STORE","store":{"name":"tabicl-tables"}}}'),
        (m.STORE_RW, '{"container_path":"data","mode":"RW","source":{"type":"STORE","store":{"name":"tabicl-tables"}}}'),
        (
            m.STORE_RW_EXCLUSIVE,
            '{"container_path":"data","mode":"RW","source":{"type":"STORE","store":{"name":"tabicl-tables","exclusive":true}}}',
        ),
    ],
)
def test_store_volume_modes(mode, want):
    assert wire(m.store_volume("tabicl-tables", "data", mode)) == want


@pytest.mark.parametrize(
    "name,path,mode,want",
    [
        ("", "data", m.STORE_RO, "store.name is required"),
        ("tabicl tables", "data", m.STORE_RO, "malformed"),
        ("tabicl/tables", "data", m.STORE_RO, "malformed"),
        ("tabicl-tables", "", m.STORE_RO, "is empty"),
        ("tabicl-tables", ".", m.STORE_RO, "names the sandbox or its parent"),
        ("tabicl-tables", "..", m.STORE_RO, "names the sandbox or its parent"),
        ("tabicl-tables", "data/tables", m.STORE_RO, "more than one component"),
        ("tabicl-tables", "./data", m.STORE_RO, "more than one component"),
        ("tabicl-tables", "data", "RWX", "unknown"),
    ],
)
def test_store_volume_refusals(name, path, mode, want):
    with pytest.raises(ValueError, match=want):
        m.store_volume(name, path, mode)


@pytest.mark.parametrize("path", ["data", "tabicl.tables", "/data", "/mnt/data/tables"])
def test_store_volume_keeps_the_container_path(path):
    assert m.store_volume("tabicl-tables", path, m.STORE_RW).container_path == path


def test_with_store_appends_and_takes_the_resource_from_the_offer():
    t = m.with_store(image_task(), FLEET_OFFER, "tabicl-tables", "tables", m.STORE_RO)
    t = m.with_store(t, FLEET_OFFER, "results", "out", m.STORE_RW_EXCLUSIVE)
    assert [r.name for r in t.resources] == ["store/tabicl-tables", "store/results"]
    got = wire(t)
    # The offered resource goes back with its identity: shared and metadata.
    assert (
        '{"name":"store/tabicl-tables","type":"SCALAR","scalar":{"value":1.0},"shared":{},'
        '"metadata":{"labels":[{"key":"class","value":"local"},'
        '{"key":"readers","value":"4"},{"key":"writers","value":"0"}]}}'
    ) in got
    assert (
        '"volumes":[{"container_path":"tables","mode":"RO","source":{"type":"STORE","store":{"name":"tabicl-tables"}}},'
        '{"container_path":"out","mode":"RW","source":{"type":"STORE","store":{"name":"results","exclusive":true}}}]'
    ) in got
    assert "host_path" not in got
    again = m.with_store(t, FLEET_OFFER, "results", "scratch", m.STORE_RW)
    assert len(again.resources) == 2, "a second volume on one store must not list the resource twice"


def test_with_store_leaves_the_input_task_alone():
    t = image_task()
    m.with_store(t, FLEET_OFFER, "results", "out", m.STORE_RW)
    assert t.container.volumes is None and t.resources == []


def test_with_store_needs_the_store_in_the_offer():
    with pytest.raises(ValueError, match="not in the offer from node host-0"):
        m.with_store(image_task(), FLEET_OFFER, "ollama-models", "models", m.STORE_RO)


def test_with_store_gives_a_bare_command_an_imageless_medios_container():
    t = image_task().model_copy(update={"container": None})
    t = m.with_store(t, FLEET_OFFER, "tabicl-tables", "data", m.STORE_RO)
    assert '"container":{"type":"MEDIOS","medios":{},"volumes":[{"container_path":"data"' in wire(t)


def test_validate_volumes_refuses_a_shared_container_path():
    t = m.with_store(image_task(), FLEET_OFFER, "tabicl-tables", "data", m.STORE_RO)
    with pytest.raises(ValueError, match="container path 'data'"):
        m.with_store(t, FLEET_OFFER, "results", "data", m.STORE_RW)
    m.with_store(t, FLEET_OFFER, "tabicl-tables", "tables", m.STORE_RO)


def _with_volume(volume: m.Volume, resources=()) -> m.TaskInfo:
    t = image_task()
    return t.model_copy(
        update={
            "container": t.container.model_copy(update={"volumes": [volume]}),
            "resources": list(resources),
        }
    )


@pytest.mark.parametrize(
    "volume,want",
    [
        (m.Volume(container_path="out/run1", mode=m.VOLUME_RW,
                  source=m.VolumeSource(type="STORE", store=m.StoreSource(name="results"))),
         "more than one component"),
        (m.Volume(container_path="/data", mode=m.VOLUME_RW), "has no source"),
        (m.Volume(container_path="/data", mode=m.VOLUME_RW, source=m.VolumeSource(type="HOST_PATH")),
         "has a HOST_PATH source"),
        (m.Volume(container_path="/data", mode=m.VOLUME_RO,
                  source=m.VolumeSource(type="STORE", store=m.StoreSource(name="results", exclusive=True))),
         "exclusive but not RW"),
    ],
)
def test_validate_volumes_refusals(volume, want):
    with pytest.raises(ValueError, match=want):
        m.validate_volumes(_with_volume(volume, [store_resource("results", "4", "1")]))


def test_validate_volumes_needs_the_store_resource():
    v = m.store_volume("results", "out", m.STORE_RW)
    with pytest.raises(ValueError, match="does not list resource store/results"):
        m.validate_volumes(_with_volume(v))


def test_resource_round_trips_fields_it_does_not_model():
    raw = '{"name":"gpus","type":"SCALAR","scalar":{"value":1.0},"reservations":[{"type":"STATIC","role":"gpu"}]}'
    assert wire(m.Resource.model_validate_json(raw)) == raw
