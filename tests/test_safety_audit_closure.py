from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

from android_runner import runner
from android_runner.intent import IntentUseRegistry, IntentValidationError, RunIntent, RunObservation, route_sha256
from android_runner.location.provider import GpsLocatorProvider, ProviderResult
from android_runner.state import RunState
from android_runner.workflow import MultiRunResult, _run_multi_account_for_test, stop_provider_verified


try:
    import flask  # noqa: F401
except ImportError:
    dashboard = None
else:
    _SPEC = importlib.util.spec_from_file_location(
        "dashboard_safety_audit",
        Path(__file__).parents[1] / "dashboard" / "app.py",
    )
dashboard = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
sys.modules[_SPEC.name] = dashboard
_SPEC.loader.exec_module(dashboard)


def _intent_and_observation(route: Path, *, enterprise: str = "企业A", max_duration: timedelta = timedelta(minutes=30)):
    now = datetime.now(timezone.utc)
    intent = RunIntent(
        intent_id=f"audit-{enterprise}",
        adb_serial="PHONE",
        device_fingerprint="fingerprint",
        current_enterprise=enterprise,
        target_enterprise=enterprise,
        route_sha256=route_sha256(route),
        not_before=now - timedelta(minutes=1),
        not_after=now + timedelta(minutes=1),
        max_duration=max_duration,
        allowed_action_ids={"campus_run.start"},
    )
    return intent, RunObservation("PHONE", "fingerprint", route_sha256(route), now)


class _PreparedProvider:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def prepare(self):
        self.calls.append("prepare")
        return type("Result", (), {"ok": True})()

    def ready(self) -> bool:
        self.calls.append("ready")
        return True

    def start_route(self, _route: Path):
        self.calls.append("route")
        return type("Result", (), {"ok": True})()

    def stop_verified(self):
        self.calls.append("verified-stop")
        return type("Result", (), {"ok": True})()


class _NoUiDevice:
    def start_app(self, _package: str) -> None:
        raise AssertionError("route authorization must be rejected before UI")


def test_runner_rejects_actual_route_bytes_that_do_not_match_authorized_observation(tmp_path):
    authorized_route = tmp_path / "authorized.gpx"
    authorized_route.write_text("authorized route", encoding="utf-8")
    actual_route = tmp_path / "actual.gpx"
    actual_route.write_text("different route", encoding="utf-8")
    intent, observation = _intent_and_observation(authorized_route)
    registry = IntentUseRegistry.production(tmp_path / "intent-use.sqlite3")
    registry.register(intent)
    provider = _PreparedProvider()

    result = runner.run_multi_account_mvp(
        device=_NoUiDevice(),
        provider=provider,
        route=actual_route,
        accounts=["企业A"],
        intents={"企业A": (intent, observation)},
        intent_registry=registry,
        current_account="企业A",
        logged_in_enterprises=("企业A",),
    )

    assert result.state is RunState.SAFE_STOP
    assert result.failed == ["企业A"]
    assert provider.calls == []


@pytest.mark.skipif(dashboard is None, reason="Flask is not installed")
def test_dashboard_rejects_actual_route_bytes_before_constructing_adapters(monkeypatch, tmp_path):
    accounts = tmp_path / "accounts.yaml"
    accounts.write_text(
        yaml.safe_dump({"accounts": [{"enterprise": "企业A", "phone": "13800138000", "current": True}]}),
        encoding="utf-8",
    )
    routes_dir = tmp_path / "routes"
    routes_dir.mkdir()
    actual_route = routes_dir / "actual.gpx"
    actual_route.write_text(
        '<gpx><trk><trkseg><trkpt lat="1" lon="2"/><trkpt lat="3" lon="4"/></trkseg></trk></gpx>',
        encoding="utf-8",
    )
    authorized_route = tmp_path / "authorized.gpx"
    authorized_route.write_text("authorized route", encoding="utf-8")
    intent, observation = _intent_and_observation(authorized_route)
    registry = IntentUseRegistry.production(tmp_path / "intent-use.sqlite3")
    registry.register(intent)
    constructed: list[str] = []
    monkeypatch.setattr(dashboard, "ACCOUNTS_PATH", accounts)
    monkeypatch.setattr(dashboard, "ROUTES_DIR", routes_dir)
    monkeypatch.setattr(dashboard, "GpsLocatorProvider", lambda *args, **kwargs: constructed.append("provider"))
    monkeypatch.setattr(dashboard, "AndroidDevice", lambda *args, **kwargs: constructed.append("device"))

    dashboard._run_task(
        serial="PHONE",
        route="actual.gpx",
        intents={"企业A": (intent, observation)},
        intent_registry=registry,
    )

    assert constructed == []


@pytest.mark.skipif(dashboard is None, reason="Flask is not installed")
def test_dashboard_runs_one_guarded_multi_account_session_instead_of_single_account_subruns(monkeypatch, tmp_path):
    accounts = tmp_path / "accounts.yaml"
    accounts.write_text(
        yaml.safe_dump({"accounts": [
            {"enterprise": "企业A", "phone": "13800138000", "current": True},
            {"enterprise": "企业B", "phone": "13900139000"},
        ]}),
        encoding="utf-8",
    )
    routes_dir = tmp_path / "routes"
    routes_dir.mkdir()
    route = routes_dir / "safe.gpx"
    route.write_text(
        '<gpx><trk><trkseg><trkpt lat="1" lon="2"/><trkpt lat="3" lon="4"/></trkseg></trk></gpx>',
        encoding="utf-8",
    )
    intent_a, observation_a = _intent_and_observation(route, enterprise="企业A")
    intent_b, observation_b = _intent_and_observation(route, enterprise="企业B")
    registry = IntentUseRegistry.production(tmp_path / "intent-use.sqlite3")
    registry.register(intent_a)
    registry.register(intent_b)
    calls: list[list[str]] = []
    monkeypatch.setattr(dashboard, "ACCOUNTS_PATH", accounts)
    monkeypatch.setattr(dashboard, "ROUTES_DIR", routes_dir)
    monkeypatch.setattr(dashboard, "load_provider_config", lambda _path: {"commands": {}, "serial": "PHONE"})
    monkeypatch.setattr(dashboard, "GpsLocatorProvider", lambda *args, **kwargs: object())
    monkeypatch.setattr(dashboard, "AndroidDevice", lambda *args, **kwargs: object())

    def run_all_accounts(**kwargs):
        calls.append(list(kwargs["accounts"]))
        return MultiRunResult(completed=list(kwargs["accounts"]))

    monkeypatch.setattr(dashboard, "run_multi_account_mvp", run_all_accounts)

    dashboard._run_task(
        serial="PHONE",
        route="safe.gpx",
        intents={"企业A": (intent_a, observation_a), "企业B": (intent_b, observation_b)},
        intent_registry=registry,
    )

    assert calls == [["企业A", "企业B"]]


def test_ready_rejects_an_already_active_simulation():
    provider = GpsLocatorProvider({"status": ["gps", "status"]})
    provider._run = lambda command: ProviderResult(
        command,
        0,
        '{"available": true, "mockLocationReady": true, "commandReady": true, "simulationActive": true}',
        "",
    )

    assert provider.ready() is False


def test_workflow_does_not_treat_a_plain_stop_as_a_verified_shutdown():
    calls: list[str] = []

    class StopOnlyProvider:
        def stop(self):
            calls.append("stop")
            return type("Result", (), {"ok": True})()

    assert stop_provider_verified(StopOnlyProvider()) is False
    assert calls == []
