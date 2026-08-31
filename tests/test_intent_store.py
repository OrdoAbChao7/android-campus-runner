from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from android_runner import runner
from android_runner.intent import (
    IntentPersistenceError,
    IntentReplayError,
    IntentUseRegistry,
    RunIntent,
    RunObservation,
    SQLiteIntentUseStore,
    route_sha256,
)
from android_runner.state import RunState


def _intent() -> RunIntent:
    now = datetime.now(UTC)
    return RunIntent(
        intent_id="durable-intent",
        adb_serial="PHONE",
        device_fingerprint="fingerprint",
        current_enterprise="企业A",
        target_enterprise="企业A",
        route_sha256="a" * 64,
        not_before=now - timedelta(minutes=1),
        not_after=now + timedelta(minutes=1),
        max_duration=timedelta(minutes=30),
        allowed_action_ids={"campus_run.start"},
    )


def _observation(intent: RunIntent) -> RunObservation:
    return RunObservation(
        intent.adb_serial,
        intent.device_fingerprint,
        intent.route_sha256,
        datetime.now(UTC),
    )


def test_sqlite_intent_store_rejects_replay_after_registry_restart(tmp_path):
    intent = _intent()
    store_path = tmp_path / "intent-use.sqlite3"
    first = IntentUseRegistry(store=SQLiteIntentUseStore(store_path))
    first.register(intent)
    first.consume(intent, _observation(intent), "campus_run.start")

    restarted = IntentUseRegistry(store=SQLiteIntentUseStore(store_path))

    with pytest.raises(IntentReplayError, match="already consumed"):
        restarted.validate_registered(intent)
    with pytest.raises(IntentReplayError, match="already consumed"):
        restarted.consume(intent, _observation(intent), "campus_run.start")


def test_production_registry_uses_the_configured_durable_default_path(monkeypatch, tmp_path):
    intent = _intent()
    store_path = tmp_path / "production-intent-use.sqlite3"
    monkeypatch.setenv("ANDROID_RUNNER_INTENT_STORE", str(store_path))
    issuer = IntentUseRegistry.production()
    issuer.register(intent)
    issuer.consume(intent, _observation(intent), "campus_run.start")

    restarted = IntentUseRegistry.production()

    assert restarted.is_durable is True
    with pytest.raises(IntentReplayError, match="already consumed"):
        restarted.validate_registered(intent)


def test_production_registry_fails_closed_when_the_store_cannot_be_created(tmp_path):
    blocked_parent = tmp_path / "not-a-directory"
    blocked_parent.write_text("file", encoding="utf-8")

    with pytest.raises(IntentPersistenceError, match="durable intent-use store"):
        IntentUseRegistry.production(blocked_parent / "intent-use.sqlite3")


def test_runner_refuses_a_volatile_registry_before_provider_or_ui_work(tmp_path):
    route = tmp_path / "route.gpx"
    route.write_text("route", encoding="utf-8")
    intent = replace(_intent(), route_sha256=route_sha256(route))
    observation = _observation(intent)
    registry = IntentUseRegistry()
    registry.register(intent)

    class Provider:
        calls: list[str] = []

        def prepare(self):
            self.calls.append("prepare")
            raise AssertionError("volatile registry must be rejected before provider work")

    result = runner.run_multi_account_mvp(
        device=object(),
        provider=Provider(),
        route=route,
        accounts=["企业A"],
        current_account="企业A",
        logged_in_enterprises=("企业A",),
        intents={"企业A": (intent, observation)},
        intent_registry=registry,
    )

    assert result.state is RunState.SAFE_STOP
    assert "durable" in (result.message or "").lower()
