"""Typed, single-use authorization for a supervised route run."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from threading import Lock
from typing import Iterable, Protocol
from uuid import uuid4


class IntentError(ValueError):
    """Base error for an invalid or unauthorized run intent."""


class IntentValidationError(IntentError):
    """The observed device, route, clock, or action does not match the intent."""


class IntentReplayError(IntentError):
    """An intent id has already authorized an action."""


class IntentPersistenceError(IntentError):
    """A durable intent-use store cannot safely complete an operation."""


def route_sha256(route: Path) -> str:
    """Hash the exact route bytes that the authorization is bound to."""
    digest = hashlib.sha256()
    with Path(route).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_route_binding(
    route: Path,
    intent: "RunIntent",
    observation: "RunObservation",
    action_id: str,
) -> str:
    """Validate an authorization against the exact route bytes about to run.

    Callers must not rely solely on the digest supplied by an external
    observation: the route selected at runtime is re-hashed here and compared
    to both immutable authorization objects before any adapter is touched.
    """
    if not isinstance(intent, RunIntent):
        raise IntentValidationError("RunIntent is required")
    if not isinstance(observation, RunObservation):
        raise IntentValidationError("RunObservation is required")
    try:
        actual_digest = route_sha256(Path(route))
    except OSError as exc:
        raise IntentValidationError("route bytes could not be hashed") from exc
    if actual_digest.lower() != intent.route_sha256.lower():
        raise IntentValidationError("actual route SHA-256 does not match intent")
    if not isinstance(observation.route_sha256, str) or actual_digest.lower() != observation.route_sha256.lower():
        raise IntentValidationError("actual route SHA-256 does not match observation")
    intent.validate(observation, action_id)
    return actual_digest


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


class IntentUseStore(Protocol):
    """Injectable backing store for issued/consumed intent bindings."""

    @property
    def is_durable(self) -> bool: ...

    def register(self, intent: RunIntent) -> None: ...

    def validate_registered(self, intent: RunIntent) -> None: ...

    def consume(self, intent: RunIntent) -> None: ...


def default_intent_store_path() -> Path:
    """Return the durable store used by production authorization bridges."""
    configured = os.environ.get("ANDROID_RUNNER_INTENT_STORE")
    return Path(configured) if configured else Path("logs") / "intent-use.sqlite3"


def _intent_binding(intent: RunIntent) -> str:
    """Serialize an immutable intent canonically for durable equality checks."""
    return json.dumps(
        {
            "intent_id": intent.intent_id,
            "adb_serial": intent.adb_serial,
            "device_fingerprint": intent.device_fingerprint,
            "current_enterprise": intent.current_enterprise,
            "target_enterprise": intent.target_enterprise,
            "route_sha256": intent.route_sha256,
            "not_before": intent.not_before.astimezone().isoformat(),
            "not_after": intent.not_after.astimezone().isoformat(),
            "max_duration": [
                intent.max_duration.days,
                intent.max_duration.seconds,
                intent.max_duration.microseconds,
            ],
            "allowed_action_ids": sorted(intent.allowed_action_ids),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


class SQLiteIntentUseStore:
    """Fail-closed, process-safe SQLite store for intent issuance and consumption.

    Reservations stay process-local because an interrupted process must not
    leave a permanent lock.  The irreversible consume operation is instead an
    immediate SQLite transaction, so two processes cannot both authorize the
    same click and a restarted process still sees consumed intent IDs.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS intent_uses (
                        intent_id TEXT PRIMARY KEY,
                        binding TEXT NOT NULL,
                        consumed_at TEXT
                    )
                    """
                )
        except (OSError, sqlite3.Error) as exc:
            raise IntentPersistenceError("durable intent-use store is unavailable") from exc

    @property
    def is_durable(self) -> bool:
        return True

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=1.0, isolation_level=None)
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    @staticmethod
    def _raise_for_row(intent: RunIntent, row: tuple[str, str | None] | None) -> None:
        if row is None:
            raise IntentValidationError(f"intent id has not been registered: {intent.intent_id}")
        binding, consumed_at = row
        if binding != _intent_binding(intent):
            raise IntentReplayError(f"intent id binding does not match issued intent: {intent.intent_id}")
        if consumed_at is not None:
            raise IntentReplayError(f"intent id already consumed: {intent.intent_id}")

    def register(self, intent: RunIntent) -> None:
        binding = _intent_binding(intent)
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT binding, consumed_at FROM intent_uses WHERE intent_id = ?",
                    (intent.intent_id,),
                ).fetchone()
                if row is None:
                    connection.execute(
                        "INSERT INTO intent_uses(intent_id, binding, consumed_at) VALUES (?, ?, NULL)",
                        (intent.intent_id, binding),
                    )
                elif row[0] != binding:
                    raise IntentReplayError(
                        f"intent id binding does not match issued intent: {intent.intent_id}"
                    )
                connection.commit()
        except (OSError, sqlite3.Error) as exc:
            raise IntentPersistenceError("durable intent-use store is unavailable") from exc

    def validate_registered(self, intent: RunIntent) -> None:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT binding, consumed_at FROM intent_uses WHERE intent_id = ?",
                    (intent.intent_id,),
                ).fetchone()
        except (OSError, sqlite3.Error) as exc:
            raise IntentPersistenceError("durable intent-use store is unavailable") from exc
        self._raise_for_row(intent, row)

    def consume(self, intent: RunIntent) -> None:
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT binding, consumed_at FROM intent_uses WHERE intent_id = ?",
                    (intent.intent_id,),
                ).fetchone()
                self._raise_for_row(intent, row)
                connection.execute(
                    "UPDATE intent_uses SET consumed_at = ? WHERE intent_id = ? AND consumed_at IS NULL",
                    (datetime.now().astimezone().isoformat(), intent.intent_id),
                )
                connection.commit()
        except (OSError, sqlite3.Error) as exc:
            raise IntentPersistenceError("durable intent-use store is unavailable") from exc


class IntentUseRegistry:
    """Concurrency-safe registry with an optional durable anti-replay store.

    The no-argument form is intentionally in-memory for narrow unit tests.
    Production authorization bridges must use :meth:`production`; runner
    entrypoints reject volatile registries before provider or UI work begins.
    """

    def __init__(self, *, store: IntentUseStore | None = None) -> None:
        self._issued: dict[str, RunIntent] = {}
        self._consumed_ids: set[str] = set()
        self._reservations: dict[str, IntentReservation] = {}
        self._reserved_ids: dict[str, str] = {}
        self._store = store
        self._lock = Lock()

    @classmethod
    def production(cls, store_path: str | Path | None = None) -> "IntentUseRegistry":
        """Create the default durable registry or raise rather than fall back.

        Callers must surface this failure as a refused run; silently replacing
        a failed persistent store with a volatile registry would re-open replay
        after process restart.
        """
        return cls(store=SQLiteIntentUseStore(store_path or default_intent_store_path()))

    @property
    def is_durable(self) -> bool:
        return bool(self._store is not None and getattr(self._store, "is_durable", False))

    def require_durable(self) -> None:
        if not self.is_durable:
            raise IntentPersistenceError("durable IntentUseRegistry is required for a production run")

    def register(self, intent: RunIntent) -> None:
        """Bind an issued id to its full immutable content before it can be consumed."""
        if not isinstance(intent, RunIntent):
            raise IntentValidationError("RunIntent is required")
        with self._lock:
            existing = self._issued.get(intent.intent_id)
            if existing is not None and existing != intent:
                raise IntentReplayError(f"intent id binding does not match issued intent: {intent.intent_id}")
            if self._store is not None:
                self._store.register(intent)
            self._issued[intent.intent_id] = intent

    def validate_registered(self, intent: RunIntent) -> None:
        """Validate an issued, unconsumed binding without changing registry state."""
        if not isinstance(intent, RunIntent):
            raise IntentValidationError("RunIntent is required")
        with self._lock:
            self._validate_registered_locked(intent, allow_reserved=False)

    def _validate_registered_locked(self, intent: RunIntent, *, allow_reserved: bool) -> None:
        issued = self._issued.get(intent.intent_id)
        if issued is None and self._store is None:
            raise IntentValidationError(f"intent id has not been registered: {intent.intent_id}")
        if issued is not None and issued != intent:
            raise IntentReplayError(f"intent id binding does not match issued intent: {intent.intent_id}")
        if self._store is not None:
            self._store.validate_registered(intent)
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

    def validate_active_reservation(
        self,
        reservation: IntentReservation,
        intents: Iterable[RunIntent],
    ) -> None:
        """Verify that this run still owns every supplied authorization."""
        if not isinstance(reservation, IntentReservation):
            raise IntentValidationError("IntentReservation is required")
        try:
            intent_batch = tuple(intents)
        except TypeError as exc:
            raise IntentValidationError("intent batch must be iterable") from exc
        if not intent_batch or any(not isinstance(intent, RunIntent) for intent in intent_batch):
            raise IntentValidationError("intent batch requires RunIntent values")
        intent_ids = tuple(intent.intent_id for intent in intent_batch)
        if len(set(intent_ids)) != len(intent_ids):
            raise IntentValidationError("intent batch contains duplicate intent IDs")

        with self._lock:
            active = self._reservations.get(reservation.reservation_id)
            if active is not reservation:
                raise IntentReplayError("reservation is not active or is not owned by this run")
            if reservation.intent_ids != intent_ids:
                raise IntentValidationError("reservation does not match this run's intent batch")
            for intent in intent_batch:
                self._validate_registered_locked(intent, allow_reserved=True)
                if self._reserved_ids.get(intent.intent_id) != reservation.reservation_id:
                    raise IntentReplayError(
                        f"intent id is not reserved by this run: {intent.intent_id}"
                    )

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
            if self._store is not None:
                self._store.consume(intent)
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
            self._validate_registered_locked(intent, allow_reserved=False)
            if self._store is not None:
                self._store.consume(intent)
            self._consumed_ids.add(intent.intent_id)
