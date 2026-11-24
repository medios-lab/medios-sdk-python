"""Wire-format types for the Medios HTTP v1 scheduler API.

These are pydantic v2 models whose attribute names are the wire names verbatim
(snake_case): ``node_id``, ``refuse_seconds``, ``framework_id``. They mirror the
JSON the controller emits and accepts, 1:1 with the Go SDK's ``medios`` package.

Serialization rules that match the Go SDK exactly:

  - Optional fields default to ``None`` and are omitted on serialize. Always
    serialize via ``model.to_wire()`` (``model_dump(mode="json",
    exclude_none=True)``); a ``None`` field never appears in the JSON. This is
    the Go ``omitempty`` behavior.
  - Booleans that Go models as ``*bool`` (absent / true / false all distinct,
    e.g. ``URI.extract``) are ``Optional[bool] = None``: ``None`` is omitted,
    an explicit ``False`` is serialized as ``false``.
  - Every id is its own ``ID`` model serializing to ``{"value": "..."}`` — never
    a bare string. The same type is reused for framework_id, offer_id, task_id,
    node_id.
  - Inbound events parse with ``Model.model_validate(...)``; ``extra="ignore"``
    lets unknown / forward-compat fields from the controller pass without error.

Field declaration order matches the Go structs, so the serialized JSON byte
order matches too (the one benign difference: Python renders whole floats as
``N.0`` where Go renders ``N``; both are valid JSON the controller reads as a
double).
"""

from __future__ import annotations

import base64
import re
from typing import Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from .taskstate import TaskState


class WireModel(BaseModel):
    """Base for every wire type: ignore unknown inbound fields, and serialize
    with Go's ``omitempty`` semantics via :meth:`to_wire`."""

    model_config = ConfigDict(extra="ignore")

    def to_wire(self) -> dict:
        """A JSON-ready dict with ``None`` (optional / absent) fields omitted."""
        return self.model_dump(mode="json", exclude_none=True)


# ── Ids and resources ──────────────────────────────────────────────────────


class ID(WireModel):
    """The universal ``{"value": "..."}`` wrapper used for every id on the wire
    (FrameworkID, OfferID, NodeID, TaskID, ContainerID, ...)."""

    value: str


class Scalar(WireModel):
    value: float


class Range(WireModel):
    """One inclusive ``[begin, end]`` interval (a ports range entry)."""

    begin: int
    end: int


class Ranges(WireModel):
    """The value of a RANGES resource (e.g. ports): a set of inclusive ranges.
    Mirrors the Medios ``Value.Ranges`` message."""

    range: list[Range] = []

    def ports(self) -> list[int]:
        """Flatten the ranges into the individual port numbers they cover, in
        order. A single offered range ``[31000, 32000]`` yields 1001 ports."""
        out: list[int] = []
        for r in self.range:
            out.extend(range(r.begin, r.end + 1))
        return out


class Resource(WireModel):
    """A typed quantity (cpus, mem, disk, ports, ...). ``type`` is ``"SCALAR"``
    or ``"RANGES"``; exactly one of ``scalar`` / ``ranges`` is set.

    Every other field the offer carried (``shared``, ``metadata``,
    ``reservations``, ``allocation_info`` and anything added later) is kept
    and sent back as received, because the controller matches a task's
    resource to the offered one by its full identity. A ``store/<name>``
    resource is ``shared`` and carries its declared ``readers`` and
    ``writers`` as metadata; taking it from the offer with
    :func:`find_resource` keeps all of that."""

    model_config = ConfigDict(extra="allow")

    name: str
    type: str
    scalar: Optional[Scalar] = None
    ranges: Optional[Ranges] = None


# ── Command, fetcher, environment ──────────────────────────────────────────


SECRET_REFERENCE = "REFERENCE"
SECRET_VALUE = "VALUE"


class SecretReference(WireModel):
    """Names a secret in the node's secret store; ``key`` picks one value
    inside a secret that holds several."""

    name: str
    key: Optional[str] = None


class SecretValue(WireModel):
    """The secret itself. ``data`` is a protobuf ``bytes`` field, which the
    controller's JSON parser reads as base64, so it is serialized base64."""

    data: bytes

    @field_serializer("data")
    def _base64(self, data: bytes) -> str:
        return base64.b64encode(data).decode("ascii")


class Secret(WireModel):
    """Mirrors the Medios ``Secret`` message: ``type`` is ``REFERENCE`` or
    ``VALUE`` and exactly the matching block is set. Construction refuses any
    other combination."""

    type: str
    reference: Optional[SecretReference] = None
    value: Optional[SecretValue] = None

    @model_validator(mode="after")
    def _check_union(self) -> "Secret":
        if self.type == SECRET_REFERENCE:
            if self.reference is None or self.value is not None:
                raise ValueError("Secret type REFERENCE needs reference and no value")
        elif self.type == SECRET_VALUE:
            if self.value is None or self.reference is not None:
                raise ValueError("Secret type VALUE needs value and no reference")
        else:
            raise ValueError(
                f"Secret.type {self.type!r} is not one of {SECRET_REFERENCE}, {SECRET_VALUE}"
            )
        return self


ENV_VALUE = "VALUE"
ENV_SECRET = "SECRET"


class EnvVar(WireModel):
    """One environment variable. A plain variable sets ``value`` and leaves
    ``type`` unset (the controller's default is VALUE). A secret variable sets
    ``type=ENV_SECRET`` and ``secret``, and no ``value``. Construction refuses
    a variable whose type and value/secret disagree.

    The ``medios`` containerizer honors SECRET variables; ``docker_shell``
    ignores them, so the variable is absent in the container (medios
    ``docs/containerizer-fields.md``).
    """

    name: str
    value: Optional[str] = None
    type: Optional[str] = None
    secret: Optional[Secret] = None

    @model_validator(mode="after")
    def _check_union(self) -> "EnvVar":
        if self.type is None or self.type == ENV_VALUE:
            if self.value is None or self.secret is not None:
                raise ValueError(f"EnvVar {self.name!r} of type VALUE needs value and no secret")
        elif self.type == ENV_SECRET:
            if self.secret is None or self.value is not None:
                raise ValueError(f"EnvVar {self.name!r} of type SECRET needs secret and no value")
        else:
            raise ValueError(
                f"EnvVar {self.name!r} type {self.type!r} is not one of {ENV_VALUE}, {ENV_SECRET}"
            )
        return self


class Environment(WireModel):
    variables: list[EnvVar]


class URI(WireModel):
    """A fetcher entry: the node fetches ``value`` into the task's sandbox before
    the command runs.

    ``extract`` is the ``*bool`` default-true case: ``None`` (omitted) lets the
    controller apply its default of "extract if it's an archive"; set an explicit
    ``False`` for a plain file or binary you don't want unpacked.
    """

    value: str
    executable: Optional[bool] = None
    extract: Optional[bool] = None
    cache: Optional[bool] = None
    output_file: Optional[str] = None
    # The fetched file's SHA-256 as 64 lowercase hex characters. The node fails
    # the fetch on a mismatch, before chmod or extract. None omits it.
    sha256: Optional[str] = None


class CommandInfo(WireModel):
    """How a task is launched. ``shell`` and ``value`` are always serialized
    (they have no ``omitempty`` on the Go side)."""

    shell: bool = False
    value: str = ""
    environment: Optional[Environment] = None
    uris: Optional[list[URI]] = None


# ── Container ──────────────────────────────────────────────────────────────

CONTAINER_DOCKER = "DOCKER"
CONTAINER_MEDIOS = "MEDIOS"

VOLUME_RW = "RW"
VOLUME_RO = "RO"


VOLUME_SOURCE_STORE = "STORE"


class StoreSource(WireModel):
    """The store a volume names. ``exclusive`` on an RW volume takes every
    writer unit the store declares, so no other writer is placed beside the
    task."""

    name: str
    exclusive: Optional[bool] = None


class VolumeSource(WireModel):
    type: str
    store: Optional[StoreSource] = None


class Volume(WireModel):
    """A store the node declares, at ``container_path`` with ``mode`` RW or
    RO (DR-036). A framework names the store and the access it wants; the
    node resolves the name to a path. There is no host path: the controller
    refuses a volume whose source is anything but a STORE, so build one with
    :func:`store_volume` or :func:`with_store`."""

    container_path: str
    mode: str
    source: Optional[VolumeSource] = None


NETWORK_HOST = "HOST"
NETWORK_BRIDGE = "BRIDGE"
NETWORK_NONE = "NONE"
NETWORK_USER = "USER"


class DockerInfo(WireModel):
    """``network`` is one of the proto's ``DockerInfo.Network`` names. The
    controller drops an unrecognized value of an optional enum without an
    error, so the SDK refuses one here. Leave it unset for Docker's default
    bridge: the proto's default is HOST, and docker_shell reads an unset
    field as the bridge. docker_shell gives ``--network=host`` for HOST and
    refuses NONE and USER (medios ``docs/containerizer-fields.md``).
    """

    image: str
    force_pull_image: Optional[bool] = None
    network: Optional[str] = None

    @field_validator("network")
    @classmethod
    def _check_network(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in (NETWORK_HOST, NETWORK_BRIDGE, NETWORK_NONE, NETWORK_USER):
            raise ValueError(
                f"DockerInfo.network {v!r} is not one of "
                f"{NETWORK_HOST}, {NETWORK_BRIDGE}, {NETWORK_NONE}, {NETWORK_USER}"
            )
        return v


IMAGE_DOCKER = "DOCKER"


class ImageDocker(WireModel):
    """A registry image:
    ``[REGISTRY_HOST[:REGISTRY_PORT]/]REPOSITORY[:TAG|@TYPE:DIGEST]``."""

    name: str


class Image(WireModel):
    """An OCI image for a MEDIOS container. ``type`` is ``IMAGE_DOCKER``, the
    only image type Medios defines (APPC is reserved and removed in the
    proto)."""

    type: str
    docker: ImageDocker

    @field_validator("type")
    @classmethod
    def _check_type(cls, v: str) -> str:
        if v != IMAGE_DOCKER:
            raise ValueError(f"Image.type {v!r} is not {IMAGE_DOCKER}")
        return v


class MediosInfo(WireModel):
    """Configures a MEDIOS container. ``image`` is the OCI image the medios
    containerizer provisions; absent, the task runs on the node's own
    filesystem. Same shape as medios-sdk-go ``MediosInfo``."""

    image: Optional[Image] = None


IPC_PRIVATE = "PRIVATE"
IPC_SHARE_PARENT = "SHARE_PARENT"

# shm_size is a proto uint32; the controller cannot hold a larger value.
_UINT32_MAX = 2**32 - 1


class LinuxInfo(WireModel):
    """Linux settings for the container: its IPC namespace and the size of
    its ``/dev/shm`` in MB.

    ``ipc_mode`` must be ``IPC_PRIVATE`` or ``IPC_SHARE_PARENT``. The
    controller drops an unrecognized value of an optional enum without an
    error, so the SDK refuses one here. ``shm_size`` is refused unless
    ``ipc_mode`` is PRIVATE, which is the controller's own rule. When it is
    unset, docker_shell gives Docker's default of 64 MB (medios
    ``docs/containerizer-fields.md``).
    """

    ipc_mode: Optional[str] = None
    shm_size: Optional[int] = Field(default=None, ge=0, le=_UINT32_MAX)

    @model_validator(mode="after")
    def _check(self) -> "LinuxInfo":
        if self.ipc_mode is not None and self.ipc_mode not in (IPC_PRIVATE, IPC_SHARE_PARENT):
            raise ValueError(
                f"LinuxInfo.ipc_mode {self.ipc_mode!r} is not one of "
                f"{IPC_PRIVATE}, {IPC_SHARE_PARENT}"
            )
        if self.shm_size is not None and self.ipc_mode != IPC_PRIVATE:
            raise ValueError("LinuxInfo.shm_size needs ipc_mode PRIVATE")
        return self


class ContainerInfo(WireModel):
    """How a task's process is containerized. Two types, and which one a node
    accepts depends on the containerizer it runs:

      - DOCKER: an OCI image run by the docker_shell containerizer; set
        ``docker``.
      - MEDIOS: an OCI image provisioned and run by the medios containerizer;
        set ``medios``. A DOCKER-typed task sent to such a node ends in
        TASK_ERROR.

    Exactly the block that matches ``type`` is set; construction refuses any
    other combination rather than sending a shape the node rejects.
    """

    type: str
    docker: Optional[DockerInfo] = None
    medios: Optional[MediosInfo] = None
    volumes: Optional[list[Volume]] = None
    linux_info: Optional[LinuxInfo] = None

    @model_validator(mode="after")
    def _check_union(self) -> "ContainerInfo":
        if self.type == CONTAINER_DOCKER:
            if self.docker is None or self.medios is not None:
                raise ValueError("ContainerInfo type DOCKER needs docker and no medios")
        elif self.type == CONTAINER_MEDIOS:
            if self.medios is None or self.docker is not None:
                raise ValueError("ContainerInfo type MEDIOS needs medios and no docker")
        else:
            raise ValueError(
                f"ContainerInfo.type {self.type!r} is not one of "
                f"{CONTAINER_DOCKER}, {CONTAINER_MEDIOS}"
            )
        return self


# ── Labels ─────────────────────────────────────────────────────────────────


class Label(WireModel):
    key: str
    value: str


class Labels(WireModel):
    labels: list[Label]


# ── TaskInfo ───────────────────────────────────────────────────────────────


# A task principal names the submitter a task runs on behalf of. It is a
# declared value: the framework forwards what its own caller said, medios
# records it beside the framework principal and the OS user, and nothing in
# this library verifies it against an identity provider.
#
# The shape is kind:name with three kinds. operator is a human, team is a
# Claude Code session holding a team identity, run is an agent run that submits
# work of its own, e.g. operator:alice, team:research, run:research-164-s5-r3.
# The kind prefix is the only structure; the name is opaque and at most
# PRINCIPAL_NAME_MAX_LENGTH characters. The controller checks the same shape and
# bound where the principal enters. Same pattern as medios-sdk-go
# ValidatePrincipal.
_PRINCIPAL_PATTERN = re.compile(r"(operator|team|run):[A-Za-z0-9._-]+")

# The longest name accepted after the kind, in characters. Parameter: a
# principal is a label for a human or an agent run id, and 64 fits
# run:research-164-s5-r3-shaped ids with room while bounding the string that
# lands in every /state render, log line and status update. The controller and
# medios-sdk-go (PrincipalNameMaxLength) enforce the same value.
PRINCIPAL_NAME_MAX_LENGTH = 64


class PrincipalRequiredError(ValueError):
    """The task principal is empty. There is no fallback to the framework's
    name or its user: those name the framework, not the submitter."""

    def __init__(self) -> None:
        super().__init__(
            "TaskInfo.principal is required: pass the submitter's principal as kind:name"
        )


class PrincipalMalformedError(ValueError):
    """The task principal is not ``kind:name`` with a known kind, or its name is
    too long."""


def validate_principal(p: str) -> None:
    """Raise if ``p`` is not a well-formed task principal; return None if it is.

    The check is medios-sdk-go's ``ValidatePrincipal``: an empty value raises
    :class:`PrincipalRequiredError`; a value that is not ``kind:name`` with kind
    ``operator``, ``team`` or ``run`` and a name of ``[A-Za-z0-9._-]``, or whose
    name exceeds :data:`PRINCIPAL_NAME_MAX_LENGTH`, raises
    :class:`PrincipalMalformedError`. The message names the field and the value
    so a framework can return it to its caller unchanged.
    """
    if p == "":
        raise PrincipalRequiredError()
    if _PRINCIPAL_PATTERN.fullmatch(p) is None:
        raise PrincipalMalformedError(
            f"TaskInfo.principal {p!r} is malformed: want kind:name with kind "
            "operator, team or run and a name of [A-Za-z0-9._-]"
        )
    n = len(p) - (p.index(":") + 1)
    if n > PRINCIPAL_NAME_MAX_LENGTH:
        raise PrincipalMalformedError(
            f"TaskInfo.principal has a name of {n} characters after the kind; "
            f"the limit is {PRINCIPAL_NAME_MAX_LENGTH}"
        )


class DurationInfo(WireModel):
    """A duration on the wire, as whole nanoseconds; the encoding medios uses
    for every duration. Same shape as medios-sdk-go ``DurationInfo``."""

    nanoseconds: int = Field(ge=0)


class KillPolicy(WireModel):
    """How long the node waits between the stop signal and the forced kill.
    Under docker_shell this is ``docker stop --time``, rounded up to whole
    seconds, and 10 s when no kill policy is sent (medios
    ``docs/containerizer-fields.md``)."""

    grace_period: DurationInfo


class TaskInfo(WireModel):
    """The per-task spec sent in ACCEPT/LAUNCH. Container and Labels are
    optional, so a bare-command task omits them.

    ``principal`` is required: the controller refuses a launch without one
    (TASK_ERROR). It is the submitter the task runs on behalf of, as
    ``kind:name`` (see :func:`validate_principal`), distinct from the
    framework's own name and from ``FrameworkInfo.user``. Construction runs
    :func:`validate_principal`, so a missing, empty or malformed principal
    raises ``pydantic.ValidationError`` and no TaskInfo is built.
    """

    name: str
    task_id: ID
    node_id: ID
    principal: str
    resources: list[Resource] = []
    command: CommandInfo
    container: Optional[ContainerInfo] = None
    labels: Optional[Labels] = None
    kill_policy: Optional[KillPolicy] = None

    @field_validator("principal")
    @classmethod
    def _check_principal(cls, v: str) -> str:
        validate_principal(v)
        return v


# ── Offers and attributes ──────────────────────────────────────────────────


class AttributeText(WireModel):
    value: str


class Attribute(WireModel):
    """A typed key/value on an Offer. v0.2.1 supports TEXT only."""

    name: str
    type: str
    text: Optional[AttributeText] = None

    def text_value(self) -> str:
        """The text value if the attribute is TEXT-typed, "" otherwise."""
        if self.type == "TEXT" and self.text is not None:
            return self.text.value
        return ""


class Offer(WireModel):
    """The resource grant the controller sends in OFFERS events."""

    id: ID
    framework_id: Optional[ID] = None
    node_id: ID
    hostname: str = ""
    resources: list[Resource] = []
    attributes: Optional[list[Attribute]] = None


# ── Task status ────────────────────────────────────────────────────────────


class ContainerStatus(WireModel):
    """Surfaces ``container_id`` (captured from the first TASK_RUNNING update;
    needed to construct the sandbox stdout path)."""

    container_id: ID


class TaskStatus(WireModel):
    """The per-task lifecycle update carried in UPDATE events."""

    task_id: ID
    state: TaskState
    node_id: ID
    uuid: Optional[str] = None
    source: Optional[str] = None
    message: Optional[str] = None
    timestamp: Optional[float] = None
    container_status: Optional[ContainerStatus] = None


# ── Framework identity ─────────────────────────────────────────────────────


class FrameworkInfo(WireModel):
    """The framework identity sent at SUBSCRIBE time.

    ``user`` MUST be a real OS user that exists on every node; an empty or
    nonexistent user causes silent TASK_LOST.

    ``id`` is set only on re-subscribe (to a framework_id captured from a
    previous run) so the controller treats this as the same framework (failover).
    ``failover_timeout`` is how long, in seconds, the controller keeps this
    framework's tasks alive after it disconnects; 0/None (the default) means
    tasks are killed the moment the framework disconnects.
    """

    user: str
    name: str
    id: Optional[ID] = None
    failover_timeout: Optional[float] = None


# ===========================================================================
# Events (controller -> scheduler over the SUBSCRIBE response stream)
# ===========================================================================

EVENT_SUBSCRIBED = "SUBSCRIBED"
EVENT_OFFERS = "OFFERS"
EVENT_UPDATE = "UPDATE"
EVENT_HEARTBEAT = "HEARTBEAT"
EVENT_RESCIND = "RESCIND"
EVENT_ERROR = "ERROR"


class SubscribedBody(WireModel):
    framework_id: ID
    heartbeat_interval_seconds: Optional[float] = None


class OffersBody(WireModel):
    offers: list[Offer]


class UpdateBody(WireModel):
    status: TaskStatus


class RescindBody(WireModel):
    offer_id: ID


class ErrorBody(WireModel):
    message: str


class Event(WireModel):
    """The envelope-shaped event the controller emits. ``type`` is the
    discriminator; the variant body for that type is populated and the rest are
    ``None``. HEARTBEAT carries no body."""

    type: str
    subscribed: Optional[SubscribedBody] = None
    offers: Optional[OffersBody] = None
    update: Optional[UpdateBody] = None
    rescind: Optional[RescindBody] = None
    error: Optional[ErrorBody] = None


# ===========================================================================
# Calls (scheduler -> controller, POST /api/v1/scheduler)
# ===========================================================================

CALL_SUBSCRIBE = "SUBSCRIBE"
CALL_ACCEPT = "ACCEPT"
CALL_DECLINE = "DECLINE"
CALL_KILL = "KILL"
CALL_ACKNOWLEDGE = "ACKNOWLEDGE"
CALL_SUPPRESS = "SUPPRESS"
CALL_REVIVE = "REVIVE"
CALL_RECONCILE = "RECONCILE"
CALL_TEARDOWN = "TEARDOWN"

OPERATION_LAUNCH = "LAUNCH"


class SubscribeBody(WireModel):
    framework_info: FrameworkInfo


class LaunchOp(WireModel):
    task_infos: list[TaskInfo]


class Operation(WireModel):
    """Currently always LAUNCH."""

    type: str
    launch: Optional[LaunchOp] = None


class AcceptBody(WireModel):
    offer_ids: list[ID]
    operations: list[Operation]


class Filters(WireModel):
    """Tunes how the controller treats declined / unused resources.
    ``refuse_seconds`` is how long the controller waits before re-offering them
    to this framework. The controller's own default is 5s when no Filters is
    sent."""

    refuse_seconds: Optional[float] = None


class DeclineBody(WireModel):
    offer_ids: list[ID]
    filters: Optional[Filters] = None


class SuppressBody(WireModel):
    """Empty roles suppresses offers for all of the framework's roles."""

    roles: Optional[list[str]] = None


class ReviveBody(WireModel):
    """Empty roles revives all of the framework's roles."""

    roles: Optional[list[str]] = None


class KillBody(WireModel):
    task_id: ID
    node_id: ID


class AcknowledgeBody(WireModel):
    node_id: ID
    task_id: ID
    uuid: str


class ReconcileTask(WireModel):
    """Names a task to reconcile. ``node_id`` is optional but including it (when
    known) lets the controller answer faster."""

    task_id: ID
    node_id: Optional[ID] = None


class ReconcileBody(WireModel):
    """An empty ``tasks`` list reconciles ALL of the framework's known tasks
    (implicit reconciliation)."""

    tasks: list[ReconcileTask]


class Call(WireModel):
    """The envelope-shaped request body. ``type`` is the discriminator;
    ``framework_id`` is required on every call except SUBSCRIBE; the variant
    body for the chosen type is populated. TEARDOWN carries no body beyond
    ``type`` + ``framework_id``."""

    type: str
    framework_id: Optional[ID] = None
    subscribe: Optional[SubscribeBody] = None
    accept: Optional[AcceptBody] = None
    decline: Optional[DeclineBody] = None
    kill: Optional[KillBody] = None
    acknowledge: Optional[AcknowledgeBody] = None
    suppress: Optional[SuppressBody] = None
    revive: Optional[ReviveBody] = None
    reconcile: Optional[ReconcileBody] = None


# ===========================================================================
# Constructors (less wire-shape footgun, more readable consumer code)
# ===========================================================================


def new_subscribe_call(info: FrameworkInfo) -> Call:
    """Build a SUBSCRIBE call body.

    On re-subscribe (``info.id`` set), the controller requires the top-level
    ``framework_id`` to be present AND equal to
    ``subscribe.framework_info.id`` — otherwise it rejects with HTTP 400
    "'framework_id' differs from 'subscribe.framework_info.id'" and the
    framework can never fail over to its previous registration.
    """
    return Call(
        type=CALL_SUBSCRIBE,
        framework_id=info.id,
        subscribe=SubscribeBody(framework_info=info),
    )


def new_accept_call(framework_id: str, offer_ids: list[ID], tasks: list[TaskInfo]) -> Call:
    """Build an ACCEPT with a single LAUNCH operation over the given offers and
    task list."""
    return Call(
        type=CALL_ACCEPT,
        framework_id=ID(value=framework_id),
        accept=AcceptBody(
            offer_ids=offer_ids,
            operations=[Operation(type=OPERATION_LAUNCH, launch=LaunchOp(task_infos=tasks))],
        ),
    )


def new_decline_call(framework_id: str, offer_ids: list[ID], refuse_seconds: float = 0.0) -> Call:
    """Build a DECLINE for the given offers. When ``refuse_seconds`` > 0 it is
    attached as ``Filters.refuse_seconds`` so the controller waits that long
    before re-offering these resources. A value <= 0 omits Filters (controller
    default)."""
    body = DeclineBody(offer_ids=offer_ids)
    if refuse_seconds and refuse_seconds > 0:
        body.filters = Filters(refuse_seconds=refuse_seconds)
    return Call(type=CALL_DECLINE, framework_id=ID(value=framework_id), decline=body)


def new_suppress_call(framework_id: str) -> Call:
    """Build a SUPPRESS — stop offers to this framework (single-role: all roles)."""
    return Call(type=CALL_SUPPRESS, framework_id=ID(value=framework_id), suppress=SuppressBody())


def new_revive_call(framework_id: str) -> Call:
    """Build a REVIVE — clear decline/accept filters and resume offers."""
    return Call(type=CALL_REVIVE, framework_id=ID(value=framework_id), revive=ReviveBody())


def new_reconcile_call(framework_id: str, tasks: Optional[list[ReconcileTask]] = None) -> Call:
    """Build a RECONCILE for the given tasks. Pass ``None``/empty to reconcile
    all of the framework's tasks; this emits ``"tasks":[]``, not null."""
    if tasks is None:
        tasks = []
    return Call(
        type=CALL_RECONCILE,
        framework_id=ID(value=framework_id),
        reconcile=ReconcileBody(tasks=tasks),
    )


def new_kill_call(framework_id: str, task_id: str, node_id: str) -> Call:
    """Build a KILL for the given task on the given node."""
    return Call(
        type=CALL_KILL,
        framework_id=ID(value=framework_id),
        kill=KillBody(task_id=ID(value=task_id), node_id=ID(value=node_id)),
    )


def new_acknowledge_call(framework_id: str, task_id: str, node_id: str, uuid: str) -> Call:
    """Build an ACKNOWLEDGE for a status update with a uuid. Updates without a
    uuid don't require acknowledgment."""
    return Call(
        type=CALL_ACKNOWLEDGE,
        framework_id=ID(value=framework_id),
        acknowledge=AcknowledgeBody(
            node_id=ID(value=node_id), task_id=ID(value=task_id), uuid=uuid
        ),
    )


def new_teardown_call(framework_id: str) -> Call:
    """Build a TEARDOWN."""
    return Call(type=CALL_TEARDOWN, framework_id=ID(value=framework_id))


def port_ranges_from_offer(offer: Offer) -> Optional[Ranges]:
    """Return the ``ports`` RANGES resource from an offer, or ``None`` if the
    offer carries no ports. Use ``Ranges.ports()`` to list individual ports."""
    for r in offer.resources:
        if r.name == "ports" and r.type == "RANGES" and r.ranges is not None:
            return r.ranges
    return None


def new_ports_resource(*ports: int) -> Resource:
    """Build a ``ports`` RANGES resource for a task from individual port numbers,
    emitting one ``[p, p]`` range per port. Put the result in
    ``TaskInfo.resources`` alongside cpus/mem to reserve those ports."""
    return Resource(
        name="ports",
        type="RANGES",
        ranges=Ranges(range=[Range(begin=p, end=p) for p in ports]),
    )
