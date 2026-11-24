"""TaskState classification tests, mirroring the sets in medios/taskstate.go."""

from medios_sdk.medios import (
    TASK_DROPPED,
    TASK_ERROR,
    TASK_FAILED,
    TASK_FINISHED,
    TASK_GONE,
    TASK_GONE_BY_OPERATOR,
    TASK_KILLED,
    TASK_KILLING,
    TASK_LOST,
    TASK_RUNNING,
    TASK_STAGING,
    TASK_STARTING,
    TASK_UNKNOWN,
    TASK_UNREACHABLE,
    TaskState,
)

ACTIVE = {TASK_STAGING, TASK_STARTING, TASK_RUNNING, TASK_KILLING, TASK_UNREACHABLE}
TERMINAL = {
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
FAILURE = {
    TASK_FAILED,
    TASK_ERROR,
    TASK_LOST,
    TASK_DROPPED,
    TASK_GONE,
    TASK_GONE_BY_OPERATOR,
    TASK_UNKNOWN,
}
ALL = ACTIVE | TERMINAL


def test_active_classification():
    for s in ALL:
        assert s.is_active() == (s in ACTIVE), s


def test_terminal_classification():
    for s in ALL:
        assert s.is_terminal() == (s in TERMINAL), s


def test_failure_classification():
    for s in ALL:
        assert s.is_failure() == (s in FAILURE), s


def test_active_and_terminal_are_disjoint():
    assert ACTIVE.isdisjoint(TERMINAL)


def test_lost_is_a_failure_not_active():
    assert TASK_LOST.is_failure()
    assert TASK_LOST.is_terminal()
    assert not TASK_LOST.is_active()


def test_finished_is_terminal_not_failure():
    assert TASK_FINISHED.is_terminal()
    assert not TASK_FINISHED.is_failure()


def test_unknown_future_state_classifies_as_nothing():
    # An unrecognized wire string still parses (str subclass) and reports False
    # for every classification — it is not silently coerced to a known state.
    s = TaskState("TASK_FROM_THE_FUTURE")
    assert not s.is_active()
    assert not s.is_terminal()
    assert not s.is_failure()
    assert s == "TASK_FROM_THE_FUTURE"
