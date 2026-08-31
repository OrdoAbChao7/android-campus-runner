"""Typed, single-use authorization for a supervised route run."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import hashlib
from pathlib import Path
import re
from threading import Lock
from typing import Iterable
from uuid import uuid4


class IntentError(ValueError):
    """Base error for an invalid or unauthorized run intent."""


class IntentValidationError(IntentError):
    """The observed device, route, clock, or action does not match the intent."""


class IntentReplayError(IntentError):
    """An intent id has already authorized an action."""


def route_sha256(route: Path) -> str:
    """Hash the exact route bytes that the authorization is bound to."""
    digest = hashlib.sha256()
    with Path(route).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class RunObservation:
    """Facts observed immediately before the irreversible action."""

    adb_serial: str
    device_fingerprint: str
    route_sha256: str
    observed_at: datetime
    run_duration: timedelta = timedelta()

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None:
            raise IntentValidationError("observed time must be timezone-aware")
        if self.run_duration < timedelta():
            raise IntentValidationError("run duration cannot be negative")


@dataclass(frozen=True, slots=True)
class RunIntent:
    """Immutable approval bound to one device, route, time window, and action set."""

    intent_id: str
    adb_serial: str
    device_fingerprint: str
    current_enterprise: str
    target_enterprise: str
    route_sha256: str
    not_before: datetime
    not_after: datetime
    max_duration: timedelta
    allowed_action_ids: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        object.__setattr__(self, "allowed_action_ids", frozenset(self.allowed_action_ids))
        object.__setattr__(self, "route_sha256", self.route_sha256.lower())
        for name in (
            "intent_id",
            "adb_serial",
            "device_fingerprint",
            "current_enterprise",
            "target_enterprise",
        ):
            if not getattr(self, name):
                raise IntentValidationError(f"{name} is required")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", self.route_sha256):
            raise IntentValidationError("route_sha256 must be a SHA-256 hex digest")
        if self.not_before.tzinfo is None or self.not_after.tzinfo is None:
            raise IntentValidationError("intent timestamps must be timezone-aware")
        if self.not_before >= self.not_after:
            raise IntentValidationError("not_before must be earlier than not_after")
        if self.max_duration <= timedelta():
            raise IntentValidationError("max_duration must be positive")
        if not self.allowed_action_ids or any(not action_id for action_id in self.allowed_action_ids):
            raise IntentValidationError("at least one non-empty action id is required")

    def validate(self, observation: RunObservation, action_id: str) -> None:
        """Raise when the observed preconditions do not match this authorization."""
        if observation.adb_serial != self.adb_serial:
            raise IntentValidationError("ADB serial does not match intent")
        if observation.device_fingerprint != self.device_fingerprint:
            raise IntentValidationError("device fingerprint does not match intent")
        if observation.route_sha256.lower() != self.route_sha256.lower():
            raise IntentValidationError("route SHA-256 does not match intent")
        if not self.not_before <= observation.observed_at <= self.not_after:
            raise IntentValidationError("observed time is outside the intent window")
        if observation.run_duration > self.max_duration:
            raise IntentValidationError("run duration exceeds intent maximum")
        if action_id not in self.allowed_action_ids:
            raise IntentValidationError("action id is not authorized by intent")


@dataclass(frozen=True, slots=True)
class IntentReservation:
    """Opaque run-owned claim over one or more issued intent IDs."""

    reservation_id: str
    owner_id: str
    intent_ids: tuple[str, ...]


class IntentUseRegistry:
    """In-memory, concurrency-safe registry of issued and consumed intent bindings."""

    def __init__(self) -> None:
        self._issued: dict[str, RunIntent] = {}
        self._consumed_ids: set[str] = set()
        self._reservations: dict[str, IntentReservation] = {}
        self._reserved_ids: dict[str, str] = {}
        self._lock = Lock()

    def register(self, intent: RunIntent) -> None:
        """Bind an issued id to its full immutable content before it can be consumed."""
        with self._lock:
            existing = self._issued.get(intent.intent_id)
            if existing is not None and existing != intent:
                raise IntentReplayError(f"intent id binding does not match issued intent: {intent.intent_id}")
            self._issued[intent.intent_id] = intent

    def validate_registered(self, intent: RunIntent) -> None:
        """Validate an issued, unconsumed binding without changing registry state."""
        if not isinstance(intent, RunIntent):
            raise IntentValidationError("RunIntent is required")
        with self._lock:
            self._validate_registered_locked(intent, allow_reserved=False)

    def _validate_registered_locked(self, intent: RunIntent, *, allow_reserved: bool) -> None:
        issued = self._issued.get(intent.intent_id)
        if issued is None:
            raise IntentValidationError(f"intent id has not been registered: {intent.intent_id}")
        if issued != intent:
            raise IntentReplayError(f"intent id binding does not match issued intent: {intent.intent_id}")
        if intent.intent_id in self._consumed_ids:
            raise IntentReplayError(f"intent id already consumed: {intent.intent_id}")
        if not allow_reserved and intent.intent_id in self._reserved_ids:
            raise IntentReplayError(f"intent id already reserved: {intent.intent_id}")

    def reserve_batch(
        self,
        intents: Iterable[RunIntent],
        *,
        owner_id: str | None = None,
    ) -> IntentReservation:
        """Atomically reserve all *intents* for one run without consuming them."""
        try:
            intent_batch = tuple(intents)
        except TypeError as exc:
            raise IntentValidationError("intent batch must be iterable") from exc
        if not intent_batch:
            raise IntentValidationError("intent batch cannot be empty")
        if any(not isinstance(intent, RunIntent) for intent in intent_batch):
            raise IntentValidationError("intent batch requires RunIntent values")
        intent_ids = tuple(intent.intent_id for intent in intent_batch)
        if len(set(intent_ids)) != len(intent_ids):
            raise IntentValidationError("intent batch contains duplicate intent IDs")
        if owner_id is not None and (not isinstance(owner_id, str) or not owner_id):
            raise IntentValidationError("reservation owner_id must be a non-empty string")

        with self._lock:
            # Check the complete batch while holding the same lock used for
            # consumption/reservation. No partial reservation can escape.
            for intent in intent_batch:
                self._validate_registered_locked(intent, allow_reserved=False)
            reservation = IntentReservation(
                reservation_id=uuid4().hex,
                owner_id=owner_id or uuid4().hex,
                intent_ids=intent_ids,
            )
            self._reservations[reservation.reservation_id] = reservation
            for intent_id in intent_ids:
                self._reserved_ids[intent_id] = reservation.reservation_id
            return reservation

    def consume_reserved(
        self,
        reservation: IntentReservation,
        intent: RunIntent,
        observation: RunObservation,
        action_id: str,
    ) -> None:
        """Finalize one intent from an active reservation owned by this run."""
        # Validate observed device/UI-bound facts before changing registry state.
        if not isinstance(intent, RunIntent):
            raise IntentValidationError("RunIntent is required")
        if not isinstance(observation, RunObservation):
            raise IntentValidationError("RunObservation is required")
        intent.validate(observation, action_id)
        if not isinstance(reservation, IntentReservation):
            raise IntentValidationError("IntentReservation is required")
        with self._lock:
            active = self._reservations.get(reservation.reservation_id)
            if active is not reservation:
                raise IntentReplayError("reservation is not active or is not owned by this run")
            if intent.intent_id not in reservation.intent_ids:
                raise IntentValidationError("intent is not part of this reservation")
            if self._reserved_ids.get(intent.intent_id) != reservation.reservation_id:
                raise IntentReplayError(f"intent id is not reserved by this run: {intent.intent_id}")
            self._validate_registered_locked(intent, allow_reserved=True)
            self._consumed_ids.add(intent.intent_id)
            self._reserved_ids.pop(intent.intent_id, None)
            if not any(
                owner_id == reservation.reservation_id for owner_id in self._reserved_ids.values()
            ):
                self._reservations.pop(reservation.reservation_id, None)

    def release_reservation(self, reservation: IntentReservation) -> None:
        """Release all unconsumed claims from a run-owned reservation."""
        if not isinstance(reservation, IntentReservation):
            raise IntentValidationError("IntentReservation is required")
        with self._lock:
            active = self._reservations.get(reservation.reservation_id)
            if active is None:
                return
            if active is not reservation:
                raise IntentReplayError("reservation is not active or is not owned by this run")
            for intent_id in reservation.intent_ids:
                if self._reserved_ids.get(intent_id) == reservation.reservation_id:
                    self._reserved_ids.pop(intent_id, None)
            self._reservations.pop(reservation.reservation_id, None)

    def release(self, reservation: IntentReservation) -> None:
        """Alias for releasing unconsumed claims from a run-owned reservation."""
        self.release_reservation(reservation)

    def consume(self, intent: RunIntent, observation: RunObservation, action_id: str) -> None:
        """Validate and atomically consume an intent, rejecting replayed ids."""
        intent.validate(observation, action_id)
        with self._lock:
            issued = self._issued.get(intent.intent_id)
            if issued is None:
                raise IntentValidationError(f"intent id has not been registered: {intent.intent_id}")
            if issued != intent:
                raise IntentReplayError(f"intent id binding does not match issued intent: {intent.intent_id}")
            if intent.intent_id in self._consumed_ids:
                raise IntentReplayError(f"intent id already consumed: {intent.intent_id}")
            if intent.intent_id in self._reserved_ids:
                raise IntentReplayError(f"intent id already reserved: {intent.intent_id}")
            self._consumed_ids.add(intent.intent_id)
