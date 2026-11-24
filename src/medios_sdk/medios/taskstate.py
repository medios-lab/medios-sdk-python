"""Task lifecycle states, exactly as the controller puts them on the wire.

``TaskState`` is a subclass of ``str`` (not an ``Enum``) so that an unknown or
future ``TASK_*`` value from the controller still parses — matching the Go SDK's
``type TaskState string``, which never rejects an unrecognized value. The known
states are the module-level constants below; classification (active / terminal /
failure) is by set membership, so an unrecognized state reports False for all
three rather than raising.
"""

from __future__ import annotations

from typing import Any

from pydantic import GetCoreSchemaHandler
from pydantic_core import core_schema


class TaskState(str):
    __slots__ = ()

    def is_active(self) -> bool:
        """The task is in flight."""
        return self in _ACTIVE_STATES

    def is_terminal(self) -> bool:
        """The run has reached a final state; no further transitions."""
        return self in _TERMINAL_STATES

    def is_failure(self) -> bool:
        """The run ended badly (not Finished, not an intentional Kill)."""
        return self in _FAILURE_STATES

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source: Any, handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        # Validate inbound JSON as a plain string, then wrap in TaskState so the
        # classification methods are available and unknown values still parse.
        return core_schema.no_info_after_validator_function(cls, core_schema.str_schema())


TASK_STAGING = TaskState("TASK_STAGING")
TASK_STARTING = TaskState("TASK_STARTING")
TASK_RUNNING = TaskState("TASK_RUNNING")
TASK_KILLING = TaskState("TASK_KILLING")
TASK_FINISHED = TaskState("TASK_FINISHED")
TASK_FAILED = TaskState("TASK_FAILED")
TASK_KILLED = TaskState("TASK_KILLED")
TASK_ERROR = TaskState("TASK_ERROR")
TASK_LOST = TaskState("TASK_LOST")
TASK_DROPPED = TaskState("TASK_DROPPED")
TASK_UNREACHABLE = TaskState("TASK_UNREACHABLE")
TASK_GONE = TaskState("TASK_GONE")
TASK_GONE_BY_OPERATOR = TaskState("TASK_GONE_BY_OPERATOR")
TASK_UNKNOWN = TaskState("TASK_UNKNOWN")

# Classification sets, 1:1 with medios/taskstate.go.
_ACTIVE_STATES = frozenset(
    {TASK_STAGING, TASK_STARTING, TASK_RUNNING, TASK_KILLING, TASK_UNREACHABLE}
)
_TERMINAL_STATES = frozenset(
    {
        TASK_FINISHED,
        TASK_FAILED,
        TASK_KILLED,
        TASK_ERROR,
        TASK_LOST,
        TASK_DROPPED,
        TASK_GONE,
        TASK_GONE_BY_OPERATOR,
        TASK_UNKNOWN,
    }
)
_FAILURE_STATES = frozenset(
    {
        TASK_FAILED,
        TASK_ERROR,
        TASK_LOST,
        TASK_DROPPED,
        TASK_GONE,
        TASK_GONE_BY_OPERATOR,
        TASK_UNKNOWN,
    }
)
