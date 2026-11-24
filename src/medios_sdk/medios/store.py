"""Store volumes (DR-036): a task names a store its node declares and the
access it wants, never a path on the node."""

import re

from .types import (
    VOLUME_RO,
    VOLUME_RW,
    VOLUME_SOURCE_STORE,
    ContainerInfo,
    MediosInfo,
    CONTAINER_MEDIOS,
    Offer,
    Resource,
    StoreSource,
    TaskInfo,
    Volume,
    VolumeSource,
)

# A node advertises each store as the shared resource ``store/<name>``.
STORE_RESOURCE_PREFIX = "store/"

# Access a task asks for on a store. Each takes a unit the node declares:
# RO one reader, RW one writer, RW-exclusive every writer.
STORE_RO = "RO"
STORE_RW = "RW"
STORE_RW_EXCLUSIVE = "RW-exclusive"

# A store name is the operator's declaration on the node: one word of
# letters, digits, dot, underscore and hyphen.
_STORE_NAME_PATTERN = re.compile(r"[A-Za-z0-9._-]+")


def check_container_path(path: str) -> None:
    """Refuse the container paths the controller refuses (medios
    ``validateStorePaths``, DR-036 revision on medios#602).

    A relative path is one name inside the task's sandbox, such as
    ``"data"``, and is the form every node delivers: filesystem/linux and
    the docker executor bind the store at ``<sandbox>/<name>``, and
    filesystem/posix on a Mac links it there. An absolute path is delivered
    on Linux only; a Mac node refuses it at launch. Raises ``ValueError``.
    """
    if path == "":
        raise ValueError(
            "Volume.container_path is empty; name a directory inside the "
            'sandbox, such as "data", or an absolute path'
        )
    if path.startswith("/"):
        return
    if path in (".", ".."):
        raise ValueError(
            f"Volume.container_path {path!r} names the sandbox or its parent; "
            'a relative container path is one name inside the sandbox, such as "data"'
        )
    if "/" in path:
        raise ValueError(
            f"Volume.container_path {path!r} is a relative path with more than "
            "one component; a relative container path is one name inside the "
            'sandbox, such as "data"'
        )


def store_volume(name: str, container_path: str, mode: str) -> Volume:
    """The volume for one store request: the store by name, at
    ``container_path`` with ``mode`` (:data:`STORE_RO`, :data:`STORE_RW` or
    :data:`STORE_RW_EXCLUSIVE`). ``container_path`` is one name inside the
    sandbox, which every node delivers, or an absolute path, which only
    Linux nodes deliver (see :func:`check_container_path`). An empty or
    malformed name, a container path the controller would refuse, or an
    unknown mode raises ``ValueError`` naming the field. There is no default
    store."""
    if name == "":
        raise ValueError(
            "Volume.source.store.name is required: name the store the task mounts"
        )
    if not _STORE_NAME_PATTERN.fullmatch(name):
        raise ValueError(
            f"Volume.source.store.name {name!r} is malformed: want one word of [A-Za-z0-9._-]"
        )
    check_container_path(container_path)
    if mode == STORE_RO:
        wire_mode, exclusive = VOLUME_RO, None
    elif mode == STORE_RW:
        wire_mode, exclusive = VOLUME_RW, None
    elif mode == STORE_RW_EXCLUSIVE:
        wire_mode, exclusive = VOLUME_RW, True
    else:
        raise ValueError(f"store mode {mode!r} is unknown: want RO, RW or RW-exclusive")
    return Volume(
        container_path=container_path,
        mode=wire_mode,
        source=VolumeSource(
            type=VOLUME_SOURCE_STORE, store=StoreSource(name=name, exclusive=exclusive)
        ),
    )


def find_resource(offer: Offer, name: str) -> Resource | None:
    """The offered resource with this name, as the offer carried it, or
    ``None``."""
    for r in offer.resources:
        if r.name == name:
            return r
    return None


def with_store(
    task: TaskInfo, offer: Offer, name: str, container_path: str, mode: str
) -> TaskInfo:
    """A copy of ``task`` with one more store volume, built by
    :func:`store_volume`, and the store's resource taken from the offer as
    the node advertised it. A task that mounts a store lists one copy of
    ``store/<name>``, which is what puts it on the controller's holder
    ledger. The offer must carry the store. Every volume already on the task
    is checked too (:func:`validate_volumes`).

    A task with no container gets a MEDIOS container with no image: a bare
    command on the node's own filesystem with the store delivered into its
    sandbox. A container already set is kept. Whether the node delivers the
    store is the node's answer at launch: a node without a filesystem
    isolator refuses the launch rather than run without it (medios#575).
    """
    volume = store_volume(name, container_path, mode)
    resource = find_resource(offer, STORE_RESOURCE_PREFIX + name)
    if resource is None:
        raise ValueError(f"store {name} is not in the offer from node {offer.hostname}")
    container = task.container or ContainerInfo(type=CONTAINER_MEDIOS, medios=MediosInfo())
    container = container.model_copy(
        update={"volumes": [*(container.volumes or []), volume]}
    )
    resources = list(task.resources)
    if not any(r.name == resource.name for r in resources):
        resources.append(resource)
    out = task.model_copy(update={"container": container, "resources": resources})
    validate_volumes(out)
    return out


def validate_volumes(task: TaskInfo) -> None:
    """Refuse, with ``ValueError``, what the controller would refuse in a
    task's volumes: a source that isn't a store, a missing or malformed
    store name, a container path it refuses, two volumes at one container
    path, exclusive on a read-only volume (which has no writer unit to
    take), and a store volume whose ``store/<name>`` resource is missing
    from the task."""
    if task.container is None:
        return
    held = {r.name for r in task.resources}
    paths: set[str] = set()
    for v in task.container.volumes or []:
        check_container_path(v.container_path)
        if v.container_path in paths:
            raise ValueError(
                f"two volumes on the task have container path {v.container_path!r}; "
                "each volume needs its own"
            )
        paths.add(v.container_path)
        p = v.container_path
        if v.source is None:
            raise ValueError(f"volume {p} has no source; frameworks name stores, not host paths")
        if v.source.type != VOLUME_SOURCE_STORE:
            raise ValueError(
                f"volume {p} has a {v.source.type} source; frameworks name stores, not host paths"
            )
        if v.source.store is None or v.source.store.name == "":
            raise ValueError(f"volume {p} has a STORE source with no store name")
        store = v.source.store.name
        if not _STORE_NAME_PATTERN.fullmatch(store):
            raise ValueError(
                f"volume {p} store name {store!r} is malformed: want one word of [A-Za-z0-9._-]"
            )
        if v.source.store.exclusive and v.mode != VOLUME_RW:
            raise ValueError(f"volume {p} is exclusive but not RW; exclusive takes writer units")
        if STORE_RESOURCE_PREFIX + store not in held:
            raise ValueError(
                f"volume {p} names store {store} but the task does not list resource "
                f"{STORE_RESOURCE_PREFIX}{store}; take it from the offer"
            )
