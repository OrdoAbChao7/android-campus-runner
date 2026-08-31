"""Typed, single-use authorization for a supervised route run."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import hashlib
from pathlib import Path
import re
from threading import Lock


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


class IntentUseRegistry:
    """In-memory, concurrency-safe registry of issued and consumed intent bindings."""

    def __init__(self) -> None:
        self._issued: dict[str, RunIntent] = {}
        self._consumed_ids: set[str] = set()
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
            issued = self._issued.get(intent.intent_id)
            if issued is None:
                raise IntentValidationError(f"intent id has not been registered: {intent.intent_id}")
            if issued != intent:
                raise IntentReplayError(f"intent id binding does not match issued intent: {intent.intent_id}")
            if intent.intent_id in self._consumed_ids:
                raise IntentReplayError(f"intent id already consumed: {intent.intent_id}")

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
            self._consumed_ids.add(intent.intent_id)
