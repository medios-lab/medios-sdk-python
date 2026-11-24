"""medios-sdk — Python client for the Medios HTTP v1 scheduler API.

The substrate-facing client only: wire types, the RecordIO codec, and the
scheduler driver. It is not a framework's domain or policy — there are no domain
concepts and no task-record type. See ``docs/boundary.md`` in the Go SDK for the
boundary this mirrors.

Top-level convenience: the wire types and TaskState live in
``medios_sdk.medios``; the driver is ``medios_sdk.scheduler.Client``.
"""

from . import medios, sandbox, scheduler, store, timestamp
from .scheduler import Client, SchedulerError

__version__ = "0.5.0"

__all__ = [
    "medios",
    "scheduler",
    "sandbox",
    "store",
    "timestamp",
    "Client",
    "SchedulerError",
    "__version__",
]
