from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from android_runner.intent import IntentUseRegistry, RunIntent, RunObservation, route_sha256
from android_runner.state import RunState
from android_runner.workflow import _run_multi_account_for_test


def test_duration_watchdog_safe_stops_a_slow_route_without_switching(tmp_path):
    route = tmp_path / "route.gpx"
    route.write_text("route", encoding="utf-8")
    now = datetime.now(timezone.utc)
    intent = RunIntent(
        intent_id="slow-route",
        adb_serial="PHONE",
        device_fingerprint="fingerprint",
        current_enterprise="企业A",
        target_enterprise="企业A",
        route_sha256=route_sha256(route),
        not_before=now - timedelta(minutes=1),
        not_after=now + timedelta(minutes=1),
        max_duration=timedelta(seconds=1),
        allowed_action_ids={"campus_run.start"},
    )
    observation = RunObservation("PHONE", "fingerprint", route_sha256(route), now)
    registry = IntentUseRegistry()
    registry.register(intent)
    reservation = registry.reserve_batch([intent])
    clock = [0.0]
    switches: list[str] = []

    class SlowProvider:
        def __init__(self) -> None:
            self.stops = 0

        def start_route(self, _route):
            clock[0] += 2.0
            return type("Result", (), {"ok": True})()

        def stop_verified(self):
            self.stops += 1
            return type("Result", (), {"ok": True})()

    provider = SlowProvider()
    try:
        result = _run_multi_account_for_test(
            provider=provider,
            route=route,
            accounts=["企业A"],
            open_campus_run_fn=lambda _device: None,
            confirm_free_run_fn=lambda _device, **_kwargs: None,
            switch_account_fn=lambda account: switches.append(account) or True,
            device=object(),
            intents={"企业A": (intent, observation)},
            intent_registry=registry,
            reservation=reservation,
            app_result_verified_fn=lambda _account: True,
            clock=lambda: clock[0],
        )
    finally:
        registry.release_reservation(reservation)

    assert result.state is RunState.SAFE_STOP
    assert result.failed == ["企业A"]
    assert provider.stops == 1
    assert switches == []
