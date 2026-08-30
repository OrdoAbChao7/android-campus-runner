from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta

import pytest

from android_runner.evidence import EvidenceWriter
from android_runner.intent import (
    IntentReplayError,
    IntentUseRegistry,
    RunIntent,
    RunObservation,
    route_sha256,
)
from android_runner.state import InvalidStateTransition, RunState, StateMachine


NOW = datetime(2026, 8, 31, 9, 0, tzinfo=UTC)
ROUTE_SHA256 = "a" * 64


def make_intent(**changes) -> RunIntent:
    values = {
        "intent_id": "intent-001",
        "adb_serial": "emulator-5554",
        "device_fingerprint": "google/pixel/pixel:15/test-keys",
        "current_enterprise": "Engineering",
        "target_enterprise": "Engineering",
        "route_sha256": ROUTE_SHA256,
        "not_before": NOW - timedelta(minutes=1),
        "not_after": NOW + timedelta(minutes=5),
        "max_duration": timedelta(minutes=20),
        "allowed_action_ids": frozenset({"start-route"}),
    }
    values.update(changes)
    return RunIntent(**values)


def observation(**changes) -> RunObservation:
    values = {
        "adb_serial": "emulator-5554",
        "device_fingerprint": "google/pixel/pixel:15/test-keys",
        "route_sha256": ROUTE_SHA256,
        "observed_at": NOW,
        "run_duration": timedelta(minutes=2),
    }
    values.update(changes)
    return RunObservation(**values)


def test_intent_rejects_device_drift_before_authorizing_action():
    """A changed connected-device fingerprint must not authorize the start action."""
    intent = make_intent()

    with pytest.raises(ValueError, match="device fingerprint"):
        intent.validate(observation(device_fingerprint="other/device"), "start-route")


def test_intent_consumption_rejects_replay_and_duplicate_intent_id():
    """A consumed id must not authorize a second start, even from a copied intent."""
    registry = IntentUseRegistry()
    intent = make_intent()
    registry.register(intent)

    registry.consume(intent, observation(), "start-route")

    with pytest.raises(IntentReplayError):
        registry.consume(intent, observation(), "start-route")
    with pytest.raises(IntentReplayError):
        registry.consume(replace(intent, route_sha256="b" * 64), observation(route_sha256="b" * 64), "start-route")


def test_intent_consumption_rejects_mutated_copy_before_original_is_consumed():
    """A serial or route changed via ``replace`` cannot stand in for the issued intent."""
    registry = IntentUseRegistry()
    intent = make_intent()
    registry.register(intent)
    changed = replace(intent, adb_serial="other-device", route_sha256="b" * 64)

    with pytest.raises(IntentReplayError, match="binding"):
        registry.consume(changed, observation(adb_serial="other-device", route_sha256="b" * 64), "start-route")

    registry.consume(intent, observation(), "start-route")


def test_intent_is_immutable():
    """Changing an approved device binding after issuance must fail at the object boundary."""
    intent = make_intent()

    with pytest.raises(FrozenInstanceError):
        intent.adb_serial = "different-device"  # type: ignore[misc]


def test_route_hash_uses_exact_route_bytes(tmp_path):
    """Changing the approved route file must produce a different SHA-256 binding."""
    route = tmp_path / "route.gpx"
    route.write_bytes(b"<gpx><trkpt lat='30' lon='120'/></gpx>")

    assert route_sha256(route) == "229cd537fa4e1817720fc4380656e1df2d4fbb145a649005bedf0112ac5060b6"
    route.write_bytes(b"<gpx><trkpt lat='30.1' lon='120'/></gpx>")
    assert route_sha256(route) != "229cd537fa4e1817720fc4380656e1df2d4fbb145a649005bedf0112ac5060b6"


def test_state_machine_rejects_illegal_transition_and_journals_failure(tmp_path):
    """Skipping preflight must leave an evidence record rather than silently advancing state."""
    writer = EvidenceWriter(tmp_path, "run-001")
    machine = StateMachine(journal=writer)

    with pytest.raises(InvalidStateTransition):
        machine.transition(RunState.PREFLIGHT_OK)

    event = json.loads((tmp_path / "run-001" / "events.jsonl").read_text(encoding="utf-8").strip())
    assert event["event"] == "transition_rejected"
    assert event["payload"]["from_state"] == "IDLE"
    assert event["payload"]["to_state"] == "PREFLIGHT_OK"


def test_state_machine_can_enter_safe_stop_from_any_in_progress_state():
    """An ambiguous operation must be able to terminate safely before route execution."""
    machine = StateMachine()
    machine.transition(RunState.DEVICE_LOCKED)

    machine.safe_stop("unknown UI")

    assert machine.state is RunState.SAFE_STOP
    with pytest.raises(InvalidStateTransition):
        machine.transition(RunState.PREFLIGHT_OK)


def test_state_machine_journals_non_state_input_as_a_rejected_transition(tmp_path):
    """An adapter bug must produce the typed transition error rather than an AttributeError."""
    writer = EvidenceWriter(tmp_path, "run-003")
    machine = StateMachine(journal=writer)

    with pytest.raises(InvalidStateTransition):
        machine.transition("not-a-state")  # type: ignore[arg-type]

    event = json.loads((tmp_path / "run-003" / "events.jsonl").read_text(encoding="utf-8").strip())
    assert event["event"] == "transition_rejected"
    assert event["payload"]["to_state"] == "INVALID:str"


def test_evidence_writer_redacts_sensitive_values_from_events_and_snapshots(tmp_path):
    """Secrets passed by an adapter must never reach persisted evidence artifacts."""
    writer = EvidenceWriter(tmp_path, "run-002")

    writer.append_event(
        "adapter_failed",
        {
            "password": "hunter2",
            "message": "request failed: Bearer event-bearer-token token=event-token credential: event-credential",
        },
    )
    snapshot_path = writer.write_snapshot(
        "ui-state",
        {
            "authorization": "Bearer secret-token",
            "exception_text": "provider failed with Bearer snapshot-bearer-token; token: snapshot-token; credential=snapshot-credential",
            "visible_text": "Campus Run",
        },
    )

    artifacts = (tmp_path / "run-002" / "events.jsonl").read_text(encoding="utf-8") + snapshot_path.read_text(encoding="utf-8")
    assert "hunter2" not in artifacts
    assert "secret-token" not in artifacts
    assert "event-bearer-token" not in artifacts
    assert "event-token" not in artifacts
    assert "event-credential" not in artifacts
    assert "snapshot-bearer-token" not in artifacts
    assert "snapshot-token" not in artifacts
    assert "snapshot-credential" not in artifacts
    assert "Campus Run" in artifacts
