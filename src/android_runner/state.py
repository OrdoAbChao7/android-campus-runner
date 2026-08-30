"""Explicit safety state machine for a single supervised run."""

from __future__ import annotations

from enum import Enum, auto
from typing import Protocol


class RunState(Enum):
    IDLE = auto()
    DEVICE_LOCKED = auto()
    PREFLIGHT_OK = auto()
    ACCOUNT_VERIFIED = auto()
    PAGE_READY = auto()
    PROVIDER_READY = auto()
    START_AUTHORIZED = auto()
    RUNNING_VERIFIED = auto()
    ROUTE_RUNNING = auto()
    ROUTE_COMPLETE = auto()
    APP_RESULT_VERIFIED = auto()
    PROVIDER_STOPPED = auto()
    EVIDENCE_CAPTURED = auto()
    ACCOUNT_SWITCHED = auto()
    DONE = auto()
    SAFE_STOP = auto()


class InvalidStateTransition(RuntimeError):
    """An operation attempted to skip or leave a terminal safety state."""

    def __init__(self, from_state: RunState, to_state: object) -> None:
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(f"illegal state transition: {from_state.name} -> {_state_label(to_state)}")


class EvidenceJournal(Protocol):
    def append_event(self, event: str, payload: dict[str, object] | None = None) -> None: ...


_ORDERED_STATES = (
    RunState.IDLE,
    RunState.DEVICE_LOCKED,
    RunState.PREFLIGHT_OK,
    RunState.ACCOUNT_VERIFIED,
    RunState.PAGE_READY,
    RunState.PROVIDER_READY,
    RunState.START_AUTHORIZED,
    RunState.RUNNING_VERIFIED,
    RunState.ROUTE_RUNNING,
    RunState.ROUTE_COMPLETE,
    RunState.APP_RESULT_VERIFIED,
    RunState.PROVIDER_STOPPED,
    RunState.EVIDENCE_CAPTURED,
    RunState.ACCOUNT_SWITCHED,
    RunState.DONE,
)
_NEXT_STATE = {before: after for before, after in zip(_ORDERED_STATES, _ORDERED_STATES[1:])}


def _state_label(value: object) -> str:
    if isinstance(value, RunState):
        return value.name
    return f"INVALID:{type(value).__name__}"


class StateMachine:
    """Permits only the documented workflow sequence or an immediate safe stop."""

    def __init__(self, *, journal: EvidenceJournal | None = None) -> None:
        self._state = RunState.IDLE
        self._journal = journal

    @property
    def state(self) -> RunState:
        return self._state

    def transition(self, to_state: RunState) -> None:
        """Advance one documented step, or record and reject the attempted jump."""
        from_state = self._state
        if not isinstance(to_state, RunState):
            self._journal_event(
                "transition_rejected",
                {"from_state": from_state.name, "to_state": _state_label(to_state)},
            )
            raise InvalidStateTransition(from_state, to_state)
        permitted = _NEXT_STATE.get(from_state) is to_state
        safe_stop = to_state is RunState.SAFE_STOP and from_state not in {RunState.DONE, RunState.SAFE_STOP}
        if not (permitted or safe_stop):
            self._journal_event(
                "transition_rejected",
                {"from_state": from_state.name, "to_state": to_state.name},
            )
            raise InvalidStateTransition(from_state, to_state)

        self._state = to_state
        self._journal_event("state_transition", {"from_state": from_state.name, "to_state": to_state.name})

    def safe_stop(self, reason: str) -> None:
        """Enter the terminal safe-stop state and leave a reason in the journal."""
        if self._state is RunState.SAFE_STOP:
            return
        from_state = self._state
        self.transition(RunState.SAFE_STOP)
        self._journal_event("safe_stop", {"from_state": from_state.name, "reason": reason})

    def _journal_event(self, event: str, payload: dict[str, object]) -> None:
        if self._journal is not None:
            self._journal.append_event(event, payload)
